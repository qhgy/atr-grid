"""CLI entrypoint for the ETF ATR grid MVP."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .backtest import run_backtest
from .config import DEFAULT_CONFIG, for_profile
from .engine import generate_plan, replay_symbol
from .fund_eastmoney import fetch_fund_meta
from .report import (
    beijing_now_str,
    beijing_today_str,
    build_notify_content,
    default_report_paths,
    fmt_levels,
    send_serverchan,
    should_notify,
    write_html_report,
    write_json_report,
    write_markdown_report,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI parser."""
    parser = argparse.ArgumentParser(description="ETF ATR 网格 MVP")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan", help="生成单个 ETF 的 ATR 网格计划")
    plan_parser.add_argument("symbol", help="ETF 代码，如 SH515880 或 515880")
    plan_parser.add_argument("--shares", type=int, default=2000, help="参考持仓股数")
    plan_parser.add_argument("--json-out", help="JSON 输出文件路径")
    plan_parser.add_argument("--md-out", help="Markdown 输出文件路径")
    plan_parser.add_argument("--no-save", action="store_true", help="只打印结果，不自动保存默认报告")
    plan_parser.add_argument("--notify", action="store_true", help="价格临近网格档位时推送 Server酱通知")
    plan_parser.add_argument("--notify-always", action="store_true", help="无论价格位置都推送 Server酱通知")
    plan_parser.add_argument("--no-fund", action="store_true", help="不拉取东财 ETF 基金元数据")

    replay_parser = subparsers.add_parser("replay", help="滚动回放最近 lookback 个交易日")
    replay_parser.add_argument("symbol", help="ETF 代码，如 SH515880 或 515880")
    replay_parser.add_argument("--lookback", type=int, default=60, help="回放交易日数量")
    replay_parser.add_argument("--shares", type=int, default=2000, help="参考持仓股数")

    multi_parser = subparsers.add_parser("multi", help="生成多个 ETF 的汇总 Dashboard")
    multi_parser.add_argument("symbols", nargs="+", help="ETF 代码列表，空格分隔")
    multi_parser.add_argument("--shares", type=int, default=2000, help="参考持仓股数")
    multi_parser.add_argument("--notify", action="store_true", help="有临近档位的标的推送通知")
    multi_parser.add_argument("--notify-always", action="store_true", help="推送所有标的的通知")

    backtest_parser = subparsers.add_parser("backtest", help="walk-forward 回测 + KPI")
    backtest_parser.add_argument("symbol", help="ETF 代码，如 SH515880 或 515880")
    backtest_parser.add_argument(
        "--profile",
        default="default",
        choices=["default", "stable", "dev", "aggressive"],
        help="参数 profile，默认 default",
    )
    backtest_parser.add_argument("--kline-count", type=int, default=900, help="拉取 K 线根数，默认 900")
    backtest_parser.add_argument("--warmup-bars", type=int, default=60, help="预热根数，默认 60")
    backtest_parser.add_argument("--initial-cash", type=float, default=100_000.0, help="初始现金")
    backtest_parser.add_argument("--initial-shares", type=int, default=2000, help="初始持股数")
    backtest_parser.add_argument(
        "--trade-shares",
        type=int,
        default=None,
        help="每次交易单位股数；不传时从 profile (cfg.reference_tranche_shares) 兑底，默认 200",
    )
    backtest_parser.add_argument("--json-out", help="JSON 输出路径")
    backtest_parser.add_argument("--no-save", action="store_true", help="只打印不写 JSON")

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "plan":
        plan = generate_plan(args.symbol, shares=args.shares)
        fund_meta = None if args.no_fund else _safe_fetch_fund_meta(plan.symbol)
        print(_plan_summary(plan, fund_meta=fund_meta))
        json_target, md_target = _resolve_output_paths(plan, args.json_out, args.md_out, args.no_save)
        if json_target is not None:
            write_json_report(plan, json_target)
            print(f"JSON 已写出: {json_target}")
        if md_target is not None:
            write_markdown_report(plan, md_target)
            print(f"Markdown 已写出: {md_target}")
        html_target = write_html_report(plan)
        print(f"HTML 已写出: {html_target}")
        _maybe_notify(plan, notify=args.notify, notify_always=args.notify_always)
        return 0

    if args.command == "multi":
        plans = []
        for sym in args.symbols:
            try:
                p = generate_plan(sym, shares=args.shares)
                plans.append(p)
                fund_meta = _safe_fetch_fund_meta(p.symbol)
                print(_plan_summary(p, fund_meta=fund_meta))
            except Exception as exc:
                print(f"[{sym}] 生成失败: {exc}")
        if plans:
            html_target = _write_multi_html(plans)
            print(f"Multi-ETF HTML 已写出: {html_target}")
            for p in plans:
                _maybe_notify(p, notify=args.notify, notify_always=args.notify_always)
        return 0

    if args.command == "backtest":
        cfg = for_profile(args.profile) if args.profile != "default" else DEFAULT_CONFIG
        result = run_backtest(
            symbol=args.symbol,
            cfg=cfg,
            profile_name=args.profile,
            initial_cash=args.initial_cash,
            initial_shares=args.initial_shares,
            trade_shares=args.trade_shares,
            warmup_bars=args.warmup_bars,
            kline_count=args.kline_count,
        )
        print(_backtest_summary(result))
        if not args.no_save:
            json_target = Path(args.json_out) if args.json_out else _default_backtest_json_path(result)
            json_target.parent.mkdir(parents=True, exist_ok=True)
            json_target.write_text(
                json.dumps(_backtest_result_to_dict(result), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"JSON 已写出: {json_target}")
        return 0

    replay = replay_symbol(args.symbol, lookback=args.lookback, shares=args.shares)
    print(_replay_summary(replay))
    return 0


def _safe_fetch_fund_meta(symbol: str) -> dict | None:
    """拉东财 ETF 基金元数据，失败时返回 None 不阻断主流程。"""
    try:
        return fetch_fund_meta(symbol)
    except Exception:  # noqa: BLE001 - 非关键路径
        return None


def _format_fund_meta(fund_meta: dict) -> str:
    """格式化东财基金元数据为 2-3 行摘要。"""
    code = fund_meta.get("code") or ""
    name = fund_meta.get("name") or fund_meta.get("full_name") or "—"
    nav = fund_meta.get("latest_nav")
    nav_date = fund_meta.get("latest_nav_date") or "—"
    est_price = fund_meta.get("estimate_price")
    est_pct = fund_meta.get("estimate_percent")
    est_time = fund_meta.get("estimate_time") or "—"
    size_b = fund_meta.get("size_billion")
    idx_name = fund_meta.get("tracking_index_name") or "—"
    manager = fund_meta.get("manager") or "—"
    nav_text = f"¥{nav:.4f}" if isinstance(nav, (int, float)) else "—"
    est_text = (
        f"¥{est_price:.4f}({est_pct:+.2f}%)"
        if isinstance(est_price, (int, float)) and isinstance(est_pct, (int, float))
        else "—"
    )
    size_text = f"{size_b:.2f}亿" if isinstance(size_b, (int, float)) else "—"
    return "\n".join(
        [
            f"基金名称：{name}（{code}） | 跟踪：{idx_name}",
            f"最新净值：{nav_text} @ {nav_date} | 实时估值：{est_text} @ {est_time}",
            f"规模：{size_text} | 基金经理：{manager}",
        ]
    )


def _plan_summary(plan, *, fund_meta: dict | None = None) -> str:
    buy_text = f"¥{plan.primary_buy:.3f}" if plan.primary_buy is not None else "N/A"
    sell_text = f"¥{plan.primary_sell:.3f}" if plan.primary_sell is not None else "N/A"
    lower_text = f"¥{plan.lower_invalidation:.3f}" if plan.lower_invalidation is not None else "N/A"
    upper_text = f"¥{plan.upper_breakout:.3f}" if plan.upper_breakout is not None else "N/A"
    risk_tip = _risk_tip(plan.regime, plan.grid_enabled)
    trim_text = f"{plan.trim_shares}股" if plan.trim_shares else "N/A"
    rebuy_text = f"¥{plan.rebuy_price:.3f}" if plan.rebuy_price is not None else "N/A"
    lines = [
        f"[{plan.symbol}] ETF ATR 网格结论",
        f"当前价：¥{plan.current_price:.3f} | 数据：{plan.data_source} | 最后交易日：{plan.last_trade_date}",
        f"市场状态：{plan.regime} | 当前模式：{plan.mode} | 网格启用：{'是' if plan.grid_enabled else '否'}",
        f"策略名称：{plan.strategy_name}",
        f"现在该做什么：{plan.headline_action}",
        f"标准模板：按 {plan.reference_position_shares} 股、每档 {plan.reference_tranche_shares} 股",
        f"机械卖出网格：{fmt_levels(plan.reference_sell_ladder)}",
        f"机械接回网格：{fmt_levels(plan.reference_rebuy_ladder)}",
        f"趋势修正：最多卖 {plan.trend_sell_limit_shares} 股（{plan.trend_sell_limit_tranches} 档）",
        f"趋势说明：{plan.trend_adjustment_note}",
        f"主买点：{buy_text} | 主卖点：{sell_text}",
        f"建议减仓：{trim_text} | 建议接回：{rebuy_text}",
        f"失效下沿：{lower_text} | 突破上沿：{upper_text}",
        f"结论：{plan.reason}",
        f"风险提示：{risk_tip}",
    ]
    if fund_meta:
        lines.append(_format_fund_meta(fund_meta))
    return "\n".join(lines)


def _replay_summary(replay: dict) -> str:
    return "\n".join(
        [
            f"[{replay['symbol']}] ETF ATR 网格回放",
            f"回放窗口: {replay['lookback']} | 数据来源: {replay['data_source']}",
            f"启用天数: {replay['days_grid_enabled']}",
            f"买点命中: {replay['buy_hits']} | 卖点命中: {replay['sell_hits']}",
            f"下沿失效: {replay['invalidations']} | 上沿突破: {replay['breakouts']}",
        ]
    )


def _backtest_summary(r) -> str:
    """格式化 BacktestResult 为人类可读的 KPI 块。"""
    payoff = f"{r.payoff_ratio:.2f}" if r.payoff_ratio != float("inf") else "inf"
    pf = f"{r.profit_factor:.2f}" if r.profit_factor != float("inf") else "inf"
    lines = [
        f"[{r.symbol}] ATR 网格 walk-forward 回测 (profile={r.profile})",
        f"窗口: {r.start_date} → {r.end_date} | bars={r.bars}",
        f"初始: cash=¥{r.initial_cash:.2f} shares={r.initial_shares} price=¥{r.initial_price:.3f}",
        f"结末: cash=¥{r.final_cash:.2f} shares={r.final_shares} price=¥{r.final_price:.3f} equity=¥{r.final_equity:.2f}",
        f"-- KPI --",
        f"交易: total={r.trade_count} buy={r.buy_count} sell={r.sell_count} round_trips={r.round_trip_count} (win={r.win_count} loss={r.loss_count})",
        f"胜率: {r.win_rate * 100:.2f}% | 赔率(payoff): {payoff} | profit_factor: {pf}",
        f"均赢: ¥{r.avg_win:.2f} | 均输: ¥{r.avg_loss:.2f}",
        f"总收益: {r.total_return_pct:+.2f}% | benchmark: {r.benchmark_return_pct:+.2f}% | excess: {r.excess_return_pct:+.2f}%",
        f"MDD: {r.max_drawdown_pct:.2f}% | Sharpe: {r.sharpe_ratio:.3f}",
        f"events: {r.events_summary}",
    ]
    if r.warnings:
        lines.append(f"警告: {r.warnings}")
    return "\n".join(lines)


def _backtest_result_to_dict(r) -> dict:
    """把 BacktestResult 序列化为 JSON-ready dict，处理 inf 不可 JSON 的值。"""
    def safe(x):
        return "inf" if x == float("inf") else x
    return {
        "symbol": r.symbol,
        "profile": r.profile,
        "start_date": r.start_date,
        "end_date": r.end_date,
        "bars": r.bars,
        "initial_cash": r.initial_cash,
        "initial_shares": r.initial_shares,
        "initial_price": r.initial_price,
        "final_cash": r.final_cash,
        "final_shares": r.final_shares,
        "final_price": r.final_price,
        "final_equity": r.final_equity,
        "benchmark_equity": r.benchmark_equity,
        "total_return_pct": r.total_return_pct,
        "benchmark_return_pct": r.benchmark_return_pct,
        "excess_return_pct": r.excess_return_pct,
        "trade_count": r.trade_count,
        "buy_count": r.buy_count,
        "sell_count": r.sell_count,
        "round_trip_count": r.round_trip_count,
        "win_count": r.win_count,
        "loss_count": r.loss_count,
        "win_rate": r.win_rate,
        "avg_win": r.avg_win,
        "avg_loss": r.avg_loss,
        "payoff_ratio": safe(r.payoff_ratio),
        "profit_factor": safe(r.profit_factor),
        "max_drawdown_pct": r.max_drawdown_pct,
        "sharpe_ratio": r.sharpe_ratio,
        "events_summary": r.events_summary,
        "trades": r.trades,
        "round_trips": [
            {
                "buy_date": rt.buy_date,
                "buy_price": rt.buy_price,
                "sell_date": rt.sell_date,
                "sell_price": rt.sell_price,
                "shares": rt.shares,
                "gross_pnl": rt.gross_pnl,
                "fees": rt.fees,
                "net_pnl": rt.net_pnl,
                "return_pct": rt.return_pct,
            }
            for rt in r.round_trips
        ],
        "equity_curve": r.equity_curve,
        "warnings": r.warnings,
    }


def _default_backtest_json_path(r) -> Path:
    """默认 JSON 路径: output/backtest/{symbol}_{profile}_{end_date}.json"""
    safe_date = (r.end_date or "unknown").replace("-", "")
    return Path("output") / "backtest" / f"{r.symbol}_{r.profile}_{safe_date}.json"


def _resolve_output_paths(plan, json_out: str | None, md_out: str | None, no_save: bool) -> tuple[Path | None, Path | None]:
    if json_out or md_out:
        json_target = Path(json_out) if json_out else None
        md_target = Path(md_out) if md_out else None
        return json_target, md_target
    if no_save:
        return None, None
    return default_report_paths(plan)


def _risk_tip(regime: str, grid_enabled: bool) -> str:
    if not grid_enabled and regime == "trend_up":
        return "当前偏多头单边，优先把上涨中的机动仓卖一小部分，不用怕卖飞。"
    if not grid_enabled and regime == "trend_down":
        return "当前偏空头单边，先避免抄底型双向网格。"
    if not grid_enabled:
        return "关键指标不完整或价格脱离区间，先等数据恢复或重新回到布林通道。"
    return "先按主买卖点执行，小于失效下沿就停止均值回归假设。"


def _maybe_notify(plan, *, notify: bool, notify_always: bool) -> None:
    """Send Server酱 notification if conditions are met."""
    import sys
    if not (notify or notify_always):
        return
    key = os.environ.get("SERVERCHAN_KEY", "")
    if not key:
        print("[notify] 未设置 SERVERCHAN_KEY，跳过", file=sys.stderr)
        return
    if notify_always or should_notify(plan):
        title, content = build_notify_content(plan)
        ok = send_serverchan(key, title, content)
        print(f"[{plan.symbol}] Server酱推送: {'✅ 成功' if ok else '❌ 失败'}")


def _write_multi_html(plans: list) -> Path:
    """Write a combined multi-ETF HTML dashboard and a dated snapshot."""
    from .report import render_html, _load_paper_state  # local import to avoid circular
    from core.paths import project_path

    now_str = beijing_now_str()
    today = beijing_today_str()
    sections = []
    summary_rows = []

    for plan in plans:
        paper = _load_paper_state(plan.symbol)
        single_html = render_html(plan, paper_state=paper)
        body_start = single_html.find("<body")
        body_end = single_html.rfind("</body>")
        if body_start != -1 and body_end != -1:
            body_inner = single_html[single_html.find(">", body_start) + 1:body_end]
        else:
            body_inner = single_html

        action_color = "#22c55e" if "卖" not in plan.headline_action else "#f59e0b"
        near_level = should_notify(plan)
        alert_badge = ' <span style="background:#ef4444;color:#fff;padding:2px 6px;border-radius:4px;font-size:11px">⚡ 临近档位</span>' if near_level else ""
        summary_rows.append(
            f'<tr onclick="document.getElementById(\'sec-{plan.symbol}\').scrollIntoView({{behavior:\'smooth\'}})" style="cursor:pointer">'
            f'<td style="font-weight:700;color:#60a5fa">{plan.symbol}</td>'
            f'<td>¥{plan.current_price:.3f}</td>'
            f'<td style="color:{action_color}">{plan.headline_action[:20]}{alert_badge}</td>'
            f'<td>{"是" if plan.grid_enabled else "否"}</td>'
            f'<td>{plan.last_trade_date}</td>'
            f'</tr>'
        )
        sections.append(
            f'<div id="sec-{plan.symbol}" style="margin-top:32px">'
            f'<h2 style="color:#94a3b8;font-size:14px;font-weight:600;text-transform:uppercase;letter-spacing:.5px;margin-bottom:12px">'
            f'▸ {plan.symbol}</h2>'
            f'{body_inner}'
            f'</div>'
        )

    # Collect existing snapshots for the nav bar (scan output/snapshots/)
    snap_dir = project_path("output", "snapshots")
    snap_dir.mkdir(parents=True, exist_ok=True)
    existing_dates = sorted(
        [p.stem for p in snap_dir.glob("????-??-??.html") if p.stem != today],
        reverse=True,
    )[:30]  # keep last 30

    # Build snapshot date picker
    if existing_dates:
        options = "".join(f'<option value="{d}">{d}</option>' for d in existing_dates)
        snapshot_nav = f"""
<div class="card" style="display:flex;align-items:center;gap:12px;padding:12px 20px">
  <span style="color:#64748b;font-size:13px">📅 历史快照：</span>
  <select id="snap-picker" onchange="window.location.href='snapshots/'+this.value+'.html'"
    style="background:#0f172a;color:#94a3b8;border:1px solid #334155;border-radius:6px;padding:4px 8px;font-size:13px">
    <option value="">— 选择日期 —</option>
    {options}
  </select>
  <span style="color:#374151;font-size:11px;margin-left:auto">今日：{today}</span>
</div>"""
    else:
        snapshot_nav = ""

    summary_table = f"""
<div class="card">
  <div style="color:#94a3b8;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;margin-bottom:12px">
    📊 多标的汇总 &nbsp;·&nbsp; 更新于 {now_str}
  </div>
  <table style="width:100%;border-collapse:collapse;font-size:14px">
    <thead>
      <tr style="color:#64748b;text-align:left">
        <th style="padding:4px 8px">代码</th>
        <th style="padding:4px 8px">当前价</th>
        <th style="padding:4px 8px">当前动作</th>
        <th style="padding:4px 8px">网格</th>
        <th style="padding:4px 8px">交易日</th>
      </tr>
    </thead>
    <tbody>
      {"".join(summary_rows)}
    </tbody>
  </table>
</div>"""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ETF ATR 网格 · 多标的</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #0f172a; color: #f1f5f9; font-family: -apple-system, "PingFang SC", sans-serif; padding: 16px; max-width: 900px; margin: 0 auto; }}
  .card {{ background: #1e293b; border-radius: 14px; padding: 20px; margin-bottom: 16px; border: 1px solid #334155; }}
  table td, table th {{ padding: 6px 8px; }}
  table tbody tr:hover {{ background: #1e293b44; }}
</style>
</head>
<body>
{snapshot_nav}
{summary_table}
{"".join(sections)}
<p style="color:#374151;font-size:11px;text-align:center;margin-top:24px">生成于 {now_str} · atr-grid</p>
</body>
</html>"""

    # Write main dashboard
    out_path = project_path("output", "atr_grid.html")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")

    # Write dated snapshot (uses relative path for snapshot links — same dir)
    snap_html = html.replace(
        "href='snapshots/",
        "href='../snapshots/",
    ).replace(
        "<title>ETF ATR 网格 · 多标的</title>",
        f"<title>ETF ATR 网格 {today}</title>",
    )
    snap_path = snap_dir / f"{today}.html"
    snap_path.write_text(snap_html, encoding="utf-8")

    return out_path
