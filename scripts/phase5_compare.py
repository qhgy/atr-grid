"""Phase 5 小窗口 profile 对比：hybrid 全链贯通后的效果。

严守 U4 红线：只跑用户明确申请的短窗口。这里是 30 日的同行情对比
（和 Phase 4 replay_last_30d 一致），不做跨 profile 的压力测试。

三个 profile：
    stable       —— 无 hybrid，原生网格（对照组 A）
    balanced     —— 无 hybrid，调过的参数（对照组 B）
    trend_hybrid —— Phase 5 全链 hybrid（实验组）

用近似的起情（¥20000 总权益，7100 股）以同起点比。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from atr_grid.backtest import run_backtest
from atr_grid.config import DEFAULT_CONFIG, for_profile

SYMBOL = "SH515880"
KLINE = 240            # 拉 240 根 K 线
# warmup=199 使得回测窗口 = KLINE-199 = 41 根；实际日期以回测结果为准。
WARMUP = 199
INITIAL_CASH = 12076.40
INITIAL_SHARES = 7100

PROFILES = [
    ("stable",       "原生网格、无 hybrid"),
    ("balanced",     "调过的参数、无 hybrid"),
    ("trend_hybrid", "Phase 5 全链 hybrid"),
]


def main() -> None:
    results = []
    for name, note in PROFILES:
        cfg = DEFAULT_CONFIG if name == "stable" else for_profile(name)
        result = run_backtest(
            symbol=SYMBOL,
            cfg=cfg,
            profile_name=name,
            initial_cash=INITIAL_CASH,
            initial_shares=INITIAL_SHARES,
            warmup_bars=WARMUP,
            kline_count=KLINE,
        )
        results.append((name, note, result))

    first = results[0][2]
    print(f"== Phase 5 profile 对比（{SYMBOL}、{first.start_date} → {first.end_date}、{first.bars} 交易日） ==")
    print(f"起情：现金 ¥{INITIAL_CASH:,.2f} + {INITIAL_SHARES} 股，约 ¥20000 总权益\n")
    header = (
        f"{'profile':>14} | {'终权益':>10} {'策略%':>7} {'持有%':>7} "
        f"{'超额%':>7} {'交易':>4} {'回合':>4} {'胜%':>5} "
        f"{'PF':>5} {'MDD%':>5} {'终股':>5}"
    )
    print(header)
    print("-" * len(header))
    for name, _note, r in results:
        pf = r.profit_factor if r.profit_factor != float("inf") else 999.0
        print(
            f"{name:>14} | {r.final_equity:>10.2f} {r.total_return_pct:>7.2f} "
            f"{r.benchmark_return_pct:>7.2f} {r.excess_return_pct:>7.2f} "
            f"{r.trade_count:>4} {r.round_trip_count:>4} "
            f"{r.win_rate*100:>5.1f} {pf:>5.2f} {r.max_drawdown_pct:>5.2f} "
            f"{r.final_shares:>5}"
        )
    print("\n读图指南：")
    print("  终股  = 回测终了的持仓股数。hybrid 启用时不低于起始底仓（7100）")
    print("  超额% = 策略收益 − 纯持有收益。单边上涨行情里网格本能跑输，接近 0 就差不多")
    print("  MDD%  = 最大回撤。持有的本对记 MDD 约 7.4%，网格 ≤ 4% 就算制住波动")


if __name__ == "__main__":
    main()
