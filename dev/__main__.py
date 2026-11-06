"""CLI 入口：

    uv run python -m dev backtest [--offline] [--start D] [--end D] [--no-save]
    uv run python -m dev signal   [--offline]
    uv run python -m dev scan     [--split 2023-12-31] [--offline]
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from .backtest.metrics import compute_metrics
from .backtest.report import render_report, save_report
from .backtest.runner import run_backtest
from .backtest.walkforward import render_walkforward, run_walkforward
from .config import DEFAULT_CONFIG
from .datafeed import latest_price, load_bundle

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

STATE_DIR = Path(__file__).resolve().parent / "state"


def cmd_backtest(args: argparse.Namespace) -> int:
    cfg = DEFAULT_CONFIG
    bundle, warnings = load_bundle(cfg, offline=args.offline)
    result = run_backtest(bundle, cfg, start=args.start, end=args.end)
    result.warnings.extend(warnings)
    content = render_report(result, cfg)
    print(content)
    if not args.no_save:
        path = save_report(content, cfg.symbol)
        print(f"报告已保存：{path}")
    return 0


def cmd_signal(args: argparse.Namespace) -> int:
    """每日信号 = 全历史重放后的最后一次决策（确定性，无需手工维护状态）。"""
    cfg = DEFAULT_CONFIG
    bundle, warnings = load_bundle(cfg, offline=args.offline)
    result = run_backtest(bundle, cfg)
    decision = result.last_decision
    if decision is None:
        print("无法生成决策（数据不足）")
        return 1

    print(f"== {cfg.symbol} 每日信号（基于 {result.end} 收盘）==")
    live = None if args.offline else latest_price(cfg.symbol)
    if live is not None:
        print(f"实时价：¥{live:.3f}")
    d = decision.diagnostics
    print(
        f"趋势：{'上行确认' if d['trend_on'] else '未确认/转弱'}　"
        f"状态机：{d['tactical_state']}　波动率系数：{d['vol_scalar']}"
    )
    print(
        f"模拟组合：底仓 {result.final_portfolio.base_shares} 股 / "
        f"机动仓 {result.final_portfolio.tactical_shares} 股 / "
        f"现金 ¥{result.final_portfolio.cash:,.0f}（净值 ¥{d['equity']:,.0f}）"
    )
    print("\n-- 明日操作单 --")
    if decision.orders:
        for o in decision.orders:
            kind = "开盘市价" if o.kind == "open" else f"限价 ¥{o.price:.3f}"
            side = "买入" if o.side == "buy" else "卖出"
            layer = "底仓" if o.layer == "base" else "机动仓"
            print(f"  [{layer}] {side} {o.shares} 股 @ {kind} — {o.note}")
    else:
        print("  今日无操作。")
    print("\n-- 判断依据 --")
    for r in decision.reasons:
        print(f"  · {r}")
    for w in warnings:
        print(f"  ⚠ {w}")

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    snapshot = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "as_of": result.end,
        "diagnostics": d,
        "orders": [asdict(o) for o in decision.orders],
        "reasons": decision.reasons,
        "portfolio": {
            "cash": result.final_portfolio.cash,
            "base_shares": result.final_portfolio.base_shares,
            "tactical_shares": result.final_portfolio.tactical_shares,
        },
    }
    (STATE_DIR / f"{cfg.symbol}.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    cfg = DEFAULT_CONFIG
    bundle, _ = load_bundle(cfg, offline=args.offline)
    wf = run_walkforward(
        bundle, cfg, regime_split=args.split, holdout_days=args.holdout
    )
    content = render_walkforward(wf)
    print(content)
    if not args.no_save:
        path = save_report(content, cfg.symbol, suffix="walkforward")
        print(f"报告已保存：{path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dev", description="515880 交易系统（胜率赔率最大化）")
    sub = parser.add_subparsers(dest="command", required=True)

    p_bt = sub.add_parser("backtest", help="全历史带成本回测，输出绩效报告")
    p_bt.add_argument("--start", help="回测起始日 YYYY-MM-DD")
    p_bt.add_argument("--end", help="回测结束日 YYYY-MM-DD")
    p_bt.add_argument("--offline", action="store_true", help="仅用 dev/cache 离线快照")
    p_bt.add_argument("--no-save", action="store_true", help="只打印不保存报告")
    p_bt.set_defaults(func=cmd_backtest)

    p_sig = sub.add_parser("signal", help="每日信号：当前状态 + 明日操作单 + 原因链")
    p_sig.add_argument("--offline", action="store_true")
    p_sig.set_defaults(func=cmd_signal)

    p_scan = sub.add_parser("scan", help="双机制参数检验（压力段约束 + AI时代选参 + 样本外）")
    p_scan.add_argument("--split", default="2024-01-01", help="机制切分日（成分股换血分界）")
    p_scan.add_argument("--holdout", type=int, default=120, help="样本外留出交易日数")
    p_scan.add_argument("--offline", action="store_true")
    p_scan.add_argument("--no-save", action="store_true")
    p_scan.set_defaults(func=cmd_scan)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
