# Phase 3.1 — 非对称 ladder 能力（sell疏 / rebuy密）

## 动机

v3 baseline (ADX 升级后) 仍是对称 ladder：卖档与买档步长均为 1×ATR14。
猜想：卖档疏 (>1×ATR) 拉大单笔赚幅 + 买档密 (<1×ATR) 提高接回命中率 → 双提胜率/赔率。

## 实现 (MVP、向后兼容)

- `GridConfig` 新增两个参数（默认 1.0，等价于旧行为）：
  - `ladder_sell_step_multiplier: float = 1.0`
  - `ladder_rebuy_step_multiplier: float = 1.0`
- `engine._build_reference_ladder`：
  ```python
  sell_step  = atr14 * cfg.ladder_sell_step_multiplier
  rebuy_step = atr14 * cfg.ladder_rebuy_step_multiplier
  sell[i]    = anchor + i * sell_step
  rebuy[i]   = sell[i] - rebuy_step
  ```
- paper/backtest 直接消费 `plan.reference_sell_ladder / reference_rebuy_ladder`，无需改动。

## 扫描结果 (SH515880, 840 bars, ADX v3 baseline)

参数：stable profile、`initial_cash=100_000, initial_shares=2000, trade_shares=200, warmup=60, kline=900`

| sell | rebuy | trades | rt | win% | payoff | PF | total% | excess% | MDD% | Sharpe |
|-----:|------:|-------:|---:|-----:|-------:|-----:|-------:|--------:|-----:|-------:|
| **1.00** | **1.00** | 181 | 74 | 54.05 | 7.37 | 8.67 | 7.61 | **5.52** | 1.51 | 1.206 |
| 1.00 | 0.80 | 177 | 75 | 54.67 | 6.97 | 8.40 | 7.08 | 4.98 | 1.41 | 1.216 |
| 1.00 | 0.70 | 180 | 75 | 54.67 | 7.19 | 8.67 | 7.40 | 5.31 | 1.51 | 1.205 |
| 1.10 | 0.90 | 179 | 74 | 54.05 | 6.84 | 8.55 | 7.43 | 5.34 | 1.47 | 1.215 |
| 1.20 | 0.80 | 177 | 75 | 54.67 | 7.03 | 8.48 | 7.09 | 4.99 | **1.41** | **1.218** |
| 1.30 | 0.70 | 180 | 75 | 54.67 | 7.26 | 8.75 | 7.41 | 5.32 | 1.51 | 1.206 |
| 1.50 | 0.70 | 179 | 75 | 54.67 | 7.26 | 8.75 | 7.28 | 5.19 | 1.48 | 1.208 |
| 1.20 | 1.00 | 176 | 74 | 54.05 | 6.89 | 8.61 | 6.85 | 4.75 | 1.34 | 1.205 |
| 1.50 | 1.00 | 175 | 74 | 54.05 | 7.38 | 8.94 | 6.67 | 4.58 | **1.31** | 1.207 |

## 结论

1. **所有组合 excess 均不及 baseline 5.52%**—非对称 ladder 不是免费午餐，是风险/收益的交换。
2. `rebuy_mult < 1.0` (买档密) → win% 54.05% → 54.67%，但 payoff 下降（接回快 → 单笔利润小）。
3. `sell_mult > 1.0` (卖档疏) → MDD 显著下降 (5.52 → 4.58)，因卖出后留更多现金。
4. **帕累托最优：1.20/0.80**—Sharpe 最高 (1.218)、MDD -0.10pp、win% +0.62pp，excess 仅 -0.53pp。
5. **PF 最高：1.50/1.00** (8.94)—但 excess 最差，不推荐。

## 默认策略 (MVP)

暂不改 `_PROFILES`、保持默认 1.0/1.0（对称）。能力已就位，用户可按风格定制：

- 偏收益：`sell=1.0, rebuy=1.0` (baseline v3)
- 偏风控 / Sharpe：`sell=1.20, rebuy=0.80`
- 偏方向性（牻牛市）：`sell=1.50, rebuy=1.00`

## 后续

- Phase 2.2 扫参：把 `(sell_mult, rebuy_mult)` 纳入 `ladder_pct×atr_mult×trade_shares` 联合扫描，看交互项。
- Phase 3.3 chandelier + cost_stop 后回评非对称【stop 可能改变 MDD 与非对称的性价比】。
