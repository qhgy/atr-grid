"""Phase 3.1 一次性扫描：非对称 ladder (sell_mult, rebuy_mult) 组合。

不再保存，目的是选出 dev / aggressive profile 的参数。
"""

from __future__ import annotations

import sys
from pathlib import Path
from dataclasses import replace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from atr_grid.backtest import run_backtest
from atr_grid.config import DEFAULT_CONFIG

SYMBOL = "SH515880"
KLINE = 900
WARMUP = 60

# (sell_mult, rebuy_mult)
COMBOS = [
    (1.0, 1.0),   # baseline (v3 default)
    (1.0, 0.8),   # 卖档不变，买档密
    (1.0, 0.7),
    (1.1, 0.9),
    (1.2, 0.8),   # 典型不对称
    (1.3, 0.7),
    (1.5, 0.7),   # 激进不对称
    (1.2, 1.0),   # 卖档疏，买档不变
    (1.5, 1.0),
]

def main() -> None:
    print(f"{'sell':>5} {'rebuy':>5} | {'trades':>6} {'rt':>3} {'win%':>6} {'payoff':>6} {'PF':>6} {'total%':>7} {'excess%':>8} {'MDD%':>5} {'Sharpe':>6}")
    print("-" * 96)
    for sell_mult, rebuy_mult in COMBOS:
        cfg = replace(
            DEFAULT_CONFIG,
            ladder_sell_step_multiplier=sell_mult,
            ladder_rebuy_step_multiplier=rebuy_mult,
        )
        r = run_backtest(
            symbol=SYMBOL,
            cfg=cfg,
            profile_name=f"sell{sell_mult}_rebuy{rebuy_mult}",
            initial_cash=100_000.0,
            initial_shares=2000,
            trade_shares=200,
            warmup_bars=WARMUP,
            kline_count=KLINE,
        )
        payoff = r.payoff_ratio if r.payoff_ratio != float("inf") else 999.0
        pf = r.profit_factor if r.profit_factor != float("inf") else 999.0
        print(
            f"{sell_mult:>5.2f} {rebuy_mult:>5.2f} | "
            f"{r.trade_count:>6} {r.round_trip_count:>3} "
            f"{r.win_rate*100:>6.2f} {payoff:>6.2f} {pf:>6.2f} "
            f"{r.total_return_pct:>7.2f} {r.excess_return_pct:>8.2f} "
            f"{r.max_drawdown_pct:>5.2f} {r.sharpe_ratio:>6.3f}"
        )

if __name__ == "__main__":
    main()
