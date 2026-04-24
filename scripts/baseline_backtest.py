"""一次性脚本：跑 SH515880 的 baseline backtest 并打印 KPI + 写文件。

目的：为后续优化建立可比对的基线。
- 使用 load_market_context 的雪球完整 cookies 通道拉 K 线
- 不依赖 CLI，避免大改可以先拿数字
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from atr_grid.backtest import run_backtest
from atr_grid.config import DEFAULT_CONFIG, for_profile


def main() -> None:
    symbol = "SH515880"
    profiles = ["default", "stable", "dev", "aggressive"]
    kline_count = 900          # 拉 ~3.6 年数据，跨周期验证
    warmup_bars = 60

    all_results = []
    print(f"\n=== Baseline backtest on {symbol} (walk-forward, close-to-close, kline_count={kline_count}) ===\n")
    print(
        "| profile | bars | trades | round_trips | win_rate | payoff | PF | total_ret% | bench_ret% | excess% | MDD% | Sharpe |"
    )
    print(
        "|---------|------|--------|-------------|----------|--------|----|-----------|-----------|---------|------|--------|"
    )

    for profile in profiles:
        if profile == "default":
            cfg = DEFAULT_CONFIG
        else:
            cfg = for_profile(profile)

        try:
            result = run_backtest(
                symbol=symbol,
                cfg=cfg,
                profile_name=profile,
                initial_cash=100_000.0,
                initial_shares=2000,
                trade_shares=200,
                warmup_bars=warmup_bars,
                kline_count=kline_count,
            )
        except Exception as exc:
            print(f"| {profile} | ERROR: {exc} |")
            continue

        all_results.append({
            "profile": profile,
            "symbol": result.symbol,
            "start_date": result.start_date,
            "end_date": result.end_date,
            "bars": result.bars,
            "initial_cash": result.initial_cash,
            "initial_shares": result.initial_shares,
            "initial_price": result.initial_price,
            "final_cash": result.final_cash,
            "final_shares": result.final_shares,
            "final_price": result.final_price,
            "final_equity": result.final_equity,
            "benchmark_equity": result.benchmark_equity,
            "total_return_pct": result.total_return_pct,
            "benchmark_return_pct": result.benchmark_return_pct,
            "excess_return_pct": result.excess_return_pct,
            "trade_count": result.trade_count,
            "buy_count": result.buy_count,
            "sell_count": result.sell_count,
            "round_trip_count": result.round_trip_count,
            "win_count": result.win_count,
            "loss_count": result.loss_count,
            "win_rate": result.win_rate,
            "avg_win": result.avg_win,
            "avg_loss": result.avg_loss,
            "payoff_ratio": (
                result.payoff_ratio if result.payoff_ratio != float("inf") else "inf"
            ),
            "profit_factor": (
                result.profit_factor if result.profit_factor != float("inf") else "inf"
            ),
            "max_drawdown_pct": result.max_drawdown_pct,
            "sharpe_ratio": result.sharpe_ratio,
            "events_summary": result.events_summary,
        })

        payoff_str = (
            f"{result.payoff_ratio:.2f}" if result.payoff_ratio != float("inf") else "inf"
        )
        pf_str = (
            f"{result.profit_factor:.2f}" if result.profit_factor != float("inf") else "inf"
        )
        print(
            f"| {profile:8s} | {result.bars:4d} | {result.trade_count:6d} | {result.round_trip_count:11d} "
            f"| {result.win_rate * 100:7.2f}% | {payoff_str:>6s} | {pf_str:>4s} "
            f"| {result.total_return_pct:9.2f} | {result.benchmark_return_pct:9.2f} "
            f"| {result.excess_return_pct:7.2f} | {result.max_drawdown_pct:4.2f} | {result.sharpe_ratio:6.3f} |"
        )

    # 详细 dump 一份 JSON 到 output/
    out_dir = ROOT / "output"
    out_dir.mkdir(exist_ok=True)
    dump_path = out_dir / "baseline_backtest.json"
    dump_path.write_text(
        json.dumps({"results": all_results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n详细数据已写入: {dump_path}")

    # 拿 default profile 记录一份逆发格式的 events_summary
    if all_results:
        print("\nDefault profile events:", json.dumps(all_results[0]["events_summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
