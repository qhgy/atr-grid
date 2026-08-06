# Phase 3.3 — 止损机制（cost_stop + chandelier）

## 背景和假设

v3 baseline (ADX 升级后)：excess +5.56% / MDD 1.51% / Sharpe 1.216。
猜想：加入止损或 chandelier trailing 可以进一步压 MDD → 提升 Sharpe。

## 实现两条路径

### 1. cost_stop（走 paper.frozen）

- `run_backtest(stop_pct=0.05)` → `state.stop_price = initial_price * 0.95`
- 跳破 → `simulate_day` 内触发 `stop_loss_trigger` → `frozen=True` → 永久停接回（仅允许卖）。
- 决策：用现有 paper 实现，不扩展 simulate_day。

### 2. chandelier（独立路径，强制减仓）

- `run_backtest(chandelier_atr_mult=4.0, chandelier_lookback=22)`
- 每日 `simulate_day` 之后单独评估：
  ```python
  highest = max(high[i - lookback + 1 : i + 1])
  trailing = highest - M * ATR14
  chand_line = max(chand_line, trailing)   # 只上移
  if close < chand_line and shares > 0:
      强制卖 trade_shares 股  # 不触发 paper.frozen，次日自然反弹的 grid 下买可接回
  ```
- 不污染 `state.stop_price`，不走 paper 冻结路径。

## 扫描结果 (SH515880, 840 bars, v3 baseline)

| 策略 | trades | rt | win% | payoff | PF | excess% | MDD% | Sharpe |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **baseline (无止损)** | 181 | 74 | 54.05 | 7.37 | 8.67 | **+5.56** | 1.51 | **1.216** |
| cost_3pct (frozen) | 10 | 0 | 0 | 0 | 0 | -2.13 | 0.06 | -0.358 |
| cost_5pct (frozen) | 16 | 3 | 0 | 0 | 0 | -2.12 | 0.09 | -0.170 |
| cost_8pct (frozen) | 22 | 6 | 0 | 0 | 0 | -2.09 | 0.11 | +0.060 |
| chand 3ATR/22 | 219 | 104 | 13.46 | 1.96 | 0.30 | -2.91 | 1.01 | -2.031 |
| chand 4ATR/22 | 219 | 104 | 13.46 | 2.02 | 0.32 | -2.89 | 1.03 | -1.625 |
| chand 4ATR/44 | 219 | 104 | 13.46 | 2.04 | 0.32 | -2.88 | 1.01 | -1.619 |
| chand 5ATR/22 | 219 | 104 | 16.35 | 1.90 | 0.37 | -2.77 | 0.94 | -1.259 |
| chand 6ATR/44 | 219 | 104 | 18.27 | 1.83 | 0.41 | -2.65 | 0.88 | -0.955 |
| cost5+chand4 | 16 | 3 | 0 | 0 | 0 | -2.23 | 0.13 | -1.371 |

## 结论

**止损在 SH515880 当前窗口上是破坏性的**，三类方案全部变负：

1. **cost_stop**：ETF 在 840 bars 内几乎必然有跳超 3% 的天 → 冻结 → 后续只能卖 → 表现等同空仓持现金。永久冻结适合 live paper（人工 resume），不适合自动回测。

2. **chandelier**：MDD 从 5.56 pp 压到 0.88 pp，但 excess 从 +5.56% 变成 **-2.91%**，PF 0.30 、Sharpe -2。
   - 根因：SH515880 (红利宽基 ETF) 震荡振幅与 ATR 同阶，3-6×ATR trailing 触发的是噪声假跌破；强制卖出后 grid 在更低位买回，而该 ETF 会反弹 → 一次次小亏叠加。
   - win% 从 54% 崩至 13-18%，立刻看出“高卖低买回”的 mean-reversion 优势被止损直接打碎。

3. **v3 ADX 已足够**：真下跌趋势被 ADX 确认 → `trend_down_hold` (36 天) 就不交易，Phase 3.3 的止损层是冗余的。

## MVP 决策

- **能力保留**：`run_backtest` 参数 `stop_pct / chandelier_atr_mult / chandelier_lookback` 保留，默认 `None`。
- **默认关闭**：不进 profile，不进 baseline_backtest。
- **Paper live 场景**：cost_stop + resume 机制仍适用于人工监控的系统性风险（例如行业端重大利空）。
- **后续研究**：如果引入波动更大的 symbol（個股、行业 ETF）或更长周期（拉到 2000+ bars 覆盖 2022-2024 大掌），chandelier 可能更有价值，再重评。

## 对 M3 KPI 的启示

- MDD 不是竟争点（v3 baseline 已 1.51%，在 M3 目标 ≤1.80% 内）。
- Sharpe 1.216 的提升需要从收益端而非风险端抓。下一步转向 **Phase 2.2 参数扫描**（ladder_pct × atr_mult × trade_shares），目标提升 平均收益 / 波动性 比。
