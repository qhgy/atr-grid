"""CLI entrypoint for the ETF ATR grid MVP."""

from __future__ import annotations

import argparse
import os
from datetime import datetime
from pathlib import Path

from .engine import generate_plan, replay_symbol
from .report import (
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
    plan_parser.add_argument("--shares", type=int, default=200, help="参考持仓股数")
    plan_parser.add_argument("--json-out", help="JSON 输出文件路径")
    plan_parser.add_argument("--md-out", help="Markdown 输出文件路径")
    plan_parser.add_argument("--no-save", action="store_true", help="只打印结果，不自动保存默认报告")
    plan_parser.add_argument("--notify", action="store_true", help="价格临近网格档位时推送 Server酱通知")
    plan_parser.add_argument("--notify-always", action="store_true", help="无论价格位置都推送 Server酱通知")

    replay_parser = subparsers.add_parser("replay", help="滚动回放最近 lookback 个交易日")
    replay_parser.add_argument("symbol", help="ETF 代码，如 SH515880 或 515880")
    replay_parser.add_argument("--lookback", type=int, default=60, help="回放交易日数量")
    replay_parser.add_argument("--shares", type=int, default=200, help="参考持仓股数")

    multi_parser = subparsers.add_parser("multi", help="生成多个 ETF 的汇总 Dashboard")
    multi_parser.add_argument("symbols", nargs="+", help="ETF 代码列表，空格分隔")
    multi_parser.add_argument("--shares", type=int, default=200, help="参考持仓股数")
    multi_parser.add_argument("--notify", action="store_true", help="有临近档位的标的推送通知")
    multi_parser.add_argument("--notify-always", action="store_true", help="推送所有标的的通知")

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "plan":
        plan = generate_plan(args.symbol, shares=args.shares)
        print(_plan_summary(plan))
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
                print(_plan_summary(p))
            except Exception as exc:
                print(f"[{sym}] 生成失败: {exc}")
        if plans:
            html_target = _write_multi_html(plans)
            print(f"Multi-ETF HTML 已写出: {html_target}")
            for p in plans:
                _maybe_notify(p, notify=args.notify, notify_always=args.notify_always)
        return 0

    replay = replay_symbol(args.symbol, lookback=args.lookback, shares=args.shares)
    print(_replay_summary(replay))
    return 0


def _plan_summary(plan) -> str:
    buy_text = f"¥{plan.primary_buy:.3f}" if plan.primary_buy is not None else "N/A"
    sell_text = f"¥{plan.primary_sell:.3f}" if plan.primary_sell is not None else "N/A"
    lower_text = f"¥{plan.lower_invalidation:.3f}" if plan.lower_invalidation is not None else "N/A"
    upper_text = f"¥{plan.upper_breakout:.3f}" if plan.upper_breakout is not None else "N/A"
    risk_tip = _risk_tip(plan.regime, plan.grid_enabled)
    trim_text = f"{plan.trim_shares}股" if plan.trim_shares else "N/A"
    rebuy_text = f"¥{plan.rebuy_price:.3f}" if plan.rebuy_price is not None else "N/A"
    return "\n".join(
        [
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
    )


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
    """Write a combined multi-ETF HTML dashboard to docs/index.html."""
    from .report import render_html, _load_paper_state  # local import to avoid circular
    from core.paths import project_path

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    sections = []
    summary_rows = []

    for plan in plans:
        paper = _load_paper_state(plan.symbol)
        single_html = render_html(plan, paper_state=paper)
        # Extract body content from single HTML (between <body> tags)
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
{summary_table}
{"".join(sections)}
<p style="color:#374151;font-size:11px;text-align:center;margin-top:24px">生成于 {now_str} · atr-grid</p>
</body>
</html>"""

    out_path = project_path("output", "atr_grid.html")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path
