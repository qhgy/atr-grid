"""Phase 2.2 profile 验证：证明 balanced / yield 的 trade_shares 从 cfg 兑底生效。

不传 trade_shares 参数，让 run_backtest 内部从 cfg.reference_tranche_shares 兑底。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from atr_grid.backtest import run_backtest
from atr_grid.config import DEFAULT_CONFIG, for_profile

SYMBOL = "SH515880"
KLINE = 900
WARMUP = 60

PROFILES = ["stable", "dev", "aggressive", "balanced", "yield"]


def main() -> None:
    print(
        f"{'profile':>12} | {'tsh':>4} {'trades':>6} {'rt':>3} {'win%':>6} {'payoff':>6} "
        f"{'PF':>6} {'total%':>7} {'excess%':>8} {'MDD%':>5} {'Sharpe':>6}"
    )
    print("-" * 96)
    for name in PROFILES:
        cfg = DEFAULT_CONFIG if name == "stable" else for_profile(name)
        r = run_backtest(
            symbol=SYMBOL,
            cfg=cfg,
            profile_name=name,
            initial_cash=100_000.0,
            initial_shares=2000,
            # 故意不传 trade_shares，验证 profile 兑底
            warmup_bars=WARMUP,
            kline_count=KLINE,
        )
        payoff = r.payoff_ratio if r.payoff_ratio != float("inf") else 999.0
        pf = r.profit_factor if r.profit_factor != float("inf") else 999.0
        print(
            f"{name:>12} | {cfg.reference_tranche_shares:>4} "
            f"{r.trade_count:>6} {r.round_trip_count:>3} "
            f"{r.win_rate*100:>6.2f} {payoff:>6.2f} {pf:>6.2f} "
            f"{r.total_return_pct:>7.2f} {r.excess_return_pct:>8.2f} "
            f"{r.max_drawdown_pct:>5.2f} {r.sharpe_ratio:>6.3f}"
        )


if __name__ == "__main__":
    main()
