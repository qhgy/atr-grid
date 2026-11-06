"""事后诸葛亮：从 2026-03-01 入场，2w 总资按 hybrid 分配，看 30+ 交易日成绩。

使用：
    uv run python scripts/replay_last_30d.py
    uv run python scripts/replay_last_30d.py --symbol SH515880 --total-equity 20000 --start 2026-03-01

重要说明（诚实泼冷水）：
- 当前 run_backtest 跑的是 trend_hybrid profile 的「基础网格策略」（ATR 步长 +
  ladder 非对称 + reference_tranche_shares=300）。
- hybrid 新增的「动态位置分档 only_sell / 现金地板 / 应急补仓」尚未接入
  simulate_day，所以这次数字是「暴露 profile 的下限」，不是完整 hybrid 效果。
- 底仓按 40% 预建，但回测引擎会把它当「可交易库存」，高位 trim 可能动底仓——
  实盘 hybrid 语义底仓是应被锁的。这个偏差会预期使收益数字偏小（切得太早）。
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from atr_grid.backtest import run_backtest
from atr_grid.config import DEFAULT_CONFIG, for_profile
from atr_grid.data import load_market_context


def _ts_to_date(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def _find_start_index(rows: list[dict], start_date: str) -> int:
    """返回 rows 中第一个 timestamp 对应日期 >= start_date 的下标。"""
    for i, r in enumerate(rows):
        if _ts_to_date(int(r["timestamp"])) >= start_date:
            return i
    raise ValueError(f"在 K 线中没找到 >= {start_date} 的行，默认拉的 K 线不够长。")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="从指定日期入场的 30+ 日事后诸葛亮回测")
    ap.add_argument("--symbol", default="SH515880")
    ap.add_argument("--profile", default="trend_hybrid")
    ap.add_argument("--total-equity", type=float, default=20000.0, help="总资金（元）")
    ap.add_argument("--start", default="2026-03-01", help="入场日期（YYYY-MM-DD）")
    ap.add_argument("--kline-count", type=int, default=240, help="拉多少根 K 线（要 warmup+回测够用）")
    ap.add_argument("--warmup", type=int, default=60, help="指标热身行数")
    ap.add_argument("--base-ratio", type=float, default=0.40, help="底仓比例")
    ap.add_argument("--lot", type=int, default=100, help="最小股数单位")
    ap.add_argument("--show-trades", type=int, default=20, help="打印前 N 笔回合，0=不打印")
    args = ap.parse_args(argv)

    cfg = DEFAULT_CONFIG if args.profile == "stable" else for_profile(args.profile)

    # 1) 拉全量 K 线
    ctx = load_market_context(
        args.symbol, shares=0, kline_count=args.kline_count, cfg=cfg
    )
    rows = list(ctx.rows)
    print(f"已拉 K 线：{len(rows)} 根，"
          f"{_ts_to_date(rows[0]['timestamp'])} → {_ts_to_date(rows[-1]['timestamp'])}")

    # 2) 定位入场日
    idx_start = _find_start_index(rows, args.start)
    start_date_actual = _ts_to_date(rows[idx_start]["timestamp"])
    price_start = float(rows[idx_start]["close"])
    end_date_actual = _ts_to_date(rows[-1]["timestamp"])
    price_end = float(rows[-1]["close"])
    print(f"入场日：{start_date_actual}  当日收：¥{price_start:.3f}")
    print(f"终止日：{end_date_actual}  当日收：¥{price_end:.3f}")
    print(f"回测天数（包含入场当天）：{len(rows) - idx_start}")

    # 3) warmup 切片：让 run_backtest 的 start_index 落在 入场日
    if idx_start < args.warmup:
        print(f"⚠ warmup 不够：入场前只有 {idx_start} 根，< {args.warmup}。请加大 --kline-count。")
        return 2
    rows_sliced = rows[idx_start - args.warmup:]

    # 4) hybrid 文字分配（人读用，仅提示；回测引擎无法执行现金地板）
    base_budget = args.total_equity * args.base_ratio
    base_shares = int(base_budget // price_start // args.lot) * args.lot
    base_cost = base_shares * price_start
    cash_after_base = args.total_equity - base_cost
    print()
    print(f"== hybrid 资金分配（2w × {int(args.base_ratio*100)}% 底仓）、仅示意 ==")
    print(f"  底仓：{base_shares} 股 × ¥{price_start:.3f} = ¥{base_cost:.2f}")
    print(f"  现金：¥{cash_after_base:.2f}（含名义上 ¥{args.total_equity*0.20:.0f} 现金地板）")
    print(f"  交易单位（cfg.reference_tranche_shares）：{cfg.reference_tranche_shares} 股")
    print()

    # 5) 回测
    print("正在回测…")
    result = run_backtest(
        rows=rows_sliced,
        symbol=args.symbol,
        cfg=cfg,
        profile_name=args.profile,
        initial_cash=cash_after_base,
        initial_shares=base_shares,
        warmup_bars=args.warmup,
        kline_count=None,
    )

    # 6) 渲染 KPI
    print()
    print("== KPI 概览 ==")
    print(f"  回测区间：{result.start_date} → {result.end_date}（{result.bars} 根 K 线）")
    print(f"  初始权益：¥{result.initial_cash + result.initial_shares * result.initial_price:.2f}"
          f"  （现金¥{result.initial_cash:.0f} + {result.initial_shares}股×¥{result.initial_price:.3f}）")
    print(f"  最终权益：¥{result.final_equity:.2f}"
          f"  （现金¥{result.final_cash:.0f} + {result.final_shares}股×¥{result.final_price:.3f}）")
    print(f"  策略回报：{result.total_return_pct:+.2f}%")
    print(f"  买入持有：{result.benchmark_return_pct:+.2f}%")
    print(f"  超额收益：{result.excess_return_pct:+.2f}%")
    print()
    print(f"  交易次数：{result.trade_count}（买 {result.buy_count} / 卖 {result.sell_count}）")
    print(f"  完整回合：{result.round_trip_count}  胜率：{result.win_rate*100:.1f}%"
          f"  赔率：{_fmt_ratio(result.payoff_ratio)}  PF：{_fmt_ratio(result.profit_factor)}")
    print(f"  最大回撤：{result.max_drawdown_pct:.2f}%   Sharpe：{result.sharpe_ratio:.3f}")
    if result.events_summary:
        print(f"  事件汇总：{result.events_summary}")
    if result.warnings:
        print(f"  warnings：{result.warnings}")

    # 7) 前 N 笔回合
    if args.show_trades and result.round_trips:
        n = min(args.show_trades, len(result.round_trips))
        print()
        print(f"== 前 {n} 笔回合（FIFO）==")
        print(f"{'#':>3} {'买入日':>10} {'买价':>6} {'卖出日':>10} {'卖价':>6} {'股数':>5} {'净盈':>8} {'收益%':>6}")
        for i, rt in enumerate(result.round_trips[:n], 1):
            print(f"{i:>3} {rt.buy_date:>10} {rt.buy_price:>6.3f} "
                  f"{rt.sell_date:>10} {rt.sell_price:>6.3f} "
                  f"{rt.shares:>5} {rt.net_pnl:>+8.2f} {rt.return_pct:>+6.2f}")

    # 8) equity 曲线抽稀
    if result.equity_curve:
        print()
        print("== 权益曲线（隔 5 行抽稀）==")
        for i, pt in enumerate(result.equity_curve):
            if i % 5 == 0 or i == len(result.equity_curve) - 1:
                print(f"  {pt.get('date', '?'):>10}  权益¥{pt.get('equity', 0):.2f}"
                      f"  价¥{pt.get('price', 0):.3f}"
                      f"  股{pt.get('shares', 0)}  现金¥{pt.get('cash', 0):.0f}")

    return 0


def _fmt_ratio(x: float) -> str:
    if x == float("inf"):
        return "∞"
    return f"{x:.2f}"


if __name__ == "__main__":
    raise SystemExit(main())
