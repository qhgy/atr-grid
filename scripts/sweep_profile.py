"""Phase 2.2 扫参第二轮：trade_shares 为主轴 + grid step 组合。

第一轮发现：trade_shares=300 独立将 Sharpe 从 1.22 拉到 1.27, PF 8.67→13.60
但 MDD 2.05% 略超 M3限 1.80。
第二轮究 tsh 上限 + 组合，目标 Sharpe≥1.4 且 MDD≤1.80。
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from atr_grid.backtest import run_backtest
from atr_grid.config import DEFAULT_CONFIG

SYMBOL = "SH515880"
KLINE = 900
WARMUP = 60

# (label, step_min_fraction, step_max_fraction, min_step_pct, trade_shares)
COMBOS = [
    ("baseline_tsh200",    1/8, 1/3, 0.000, 200),
    # tsh 纵扫
    ("tsh_250",            1/8, 1/3, 0.000, 250),
    ("tsh_300",            1/8, 1/3, 0.000, 300),
    ("tsh_400",            1/8, 1/3, 0.000, 400),
    ("tsh_500",            1/8, 1/3, 0.000, 500),
    # tsh_300 + step 组合
    ("tsh300+smax_1/4",    1/8, 1/4, 0.000, 300),
    ("tsh300+smax_1/5",    1/8, 1/5, 0.000, 300),
    ("tsh300+smin_1/7",    1/7, 1/3, 0.000, 300),
    ("tsh300+smin_1/6",    1/6, 1/3, 0.000, 300),
    ("tsh300+minpct05",    1/8, 1/3, 0.005, 300),
    ("tsh300+minpct08",    1/8, 1/3, 0.008, 300),
    # tsh_400 + step 组合
    ("tsh400+smax_1/4",    1/8, 1/4, 0.000, 400),
    ("tsh400+smax_1/5",    1/8, 1/5, 0.000, 400),
    ("tsh400+smin_1/6",    1/6, 1/3, 0.000, 400),
    # 初始仓位不够大时防爆仓 - 需要确认 initial_shares=2000 能胜任 tsh_500
    ("tsh500+smax_1/4",    1/8, 1/4, 0.000, 500),
]


def main() -> None:
    print(
        f"{'label':>20} | {'trades':>6} {'rt':>3} {'win%':>6} {'payoff':>6} "
        f"{'PF':>6} {'total%':>7} {'excess%':>8} {'MDD%':>5} {'Sharpe':>6}"
    )
    print("-" * 96)
    for label, smin, smax, minpct, tsh in COMBOS:
        cfg = replace(
            DEFAULT_CONFIG,
            step_min_fraction=smin,
            step_max_fraction=smax,
            min_step_pct=minpct,
        )
        r = run_backtest(
            symbol=SYMBOL,
            cfg=cfg,
            profile_name=label,
            initial_cash=100_000.0,
            initial_shares=2000,
            trade_shares=tsh,
            warmup_bars=WARMUP,
            kline_count=KLINE,
        )
        payoff = r.payoff_ratio if r.payoff_ratio != float("inf") else 999.0
        pf = r.profit_factor if r.profit_factor != float("inf") else 999.0
        print(
            f"{label:>20} | {r.trade_count:>6} {r.round_trip_count:>3} "
            f"{r.win_rate*100:>6.2f} {payoff:>6.2f} {pf:>6.2f} "
            f"{r.total_return_pct:>7.2f} {r.excess_return_pct:>8.2f} "
            f"{r.max_drawdown_pct:>5.2f} {r.sharpe_ratio:>6.3f}"
        )


if __name__ == "__main__":
    main()
