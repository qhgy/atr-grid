"""Phase 3.3 一次性扫描：cost_stop (固定%) + chandelier (ATR 追踪) 组合。

对照组：(None, None) = v3 baseline 无止损。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from atr_grid.backtest import run_backtest
from atr_grid.config import DEFAULT_CONFIG

SYMBOL = "SH515880"
KLINE = 900
WARMUP = 60

# (label, stop_pct, chandelier_atr_mult, chandelier_lookback)
COMBOS = [
    ("baseline",      None, None, 22),
    ("cost_3pct",     0.03, None, 22),
    ("cost_5pct",     0.05, None, 22),
    ("cost_8pct",     0.08, None, 22),
    ("chand_3atr_22", None, 3.0, 22),
    ("chand_3atr_44", None, 3.0, 44),
    ("chand_4atr_22", None, 4.0, 22),
    ("chand_4atr_44", None, 4.0, 44),
    ("chand_5atr_22", None, 5.0, 22),
    ("chand_6atr_44", None, 6.0, 44),
    ("cost5_chand4",  0.05, 4.0, 22),
    ("cost5_chand5",  0.05, 5.0, 22),
]

def main() -> None:
    print(f"{'label':>16} | {'trades':>6} {'rt':>3} {'win%':>6} {'payoff':>6} {'PF':>6} {'total%':>7} {'excess%':>8} {'MDD%':>5} {'Sharpe':>6} {'stops':>5} {'invalid':>6}")
    print("-" * 112)
    for label, stop_pct, chand_mult, chand_lb in COMBOS:
        r = run_backtest(
            symbol=SYMBOL,
            cfg=DEFAULT_CONFIG,
            profile_name=label,
            initial_cash=100_000.0,
            initial_shares=2000,
            trade_shares=200,
            warmup_bars=WARMUP,
            kline_count=KLINE,
            stop_pct=stop_pct,
            chandelier_atr_mult=chand_mult,
            chandelier_lookback=chand_lb,
        )
        payoff = r.payoff_ratio if r.payoff_ratio != float("inf") else 999.0
        pf = r.profit_factor if r.profit_factor != float("inf") else 999.0
        stops = r.events_summary.get("stop_loss_trigger", 0)
        invals = r.events_summary.get("invalidation", 0)
        print(
            f"{label:>16} | {r.trade_count:>6} {r.round_trip_count:>3} "
            f"{r.win_rate*100:>6.2f} {payoff:>6.2f} {pf:>6.2f} "
            f"{r.total_return_pct:>7.2f} {r.excess_return_pct:>8.2f} "
            f"{r.max_drawdown_pct:>5.2f} {r.sharpe_ratio:>6.3f} "
            f"{stops:>5} {invals:>6}"
        )

if __name__ == "__main__":
    main()
