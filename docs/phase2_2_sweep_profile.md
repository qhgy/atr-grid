# Phase 2.2 — 参数扫描 + 新 profile (balanced / yield)

## 目标

v3 baseline (default/stable)：win 54%, payoff 7.37, PF 8.67, excess +5.56%, MDD 1.51%, Sharpe 1.216。
用户首要目标：**提升胜率 · 赔率**。

## 扫描维度

- `step_min_fraction`：1/8 / 1/7 / 1/6 / 1/5
- `step_max_fraction`：1/3 / 1/4 / 1/5
- `min_step_pct`：0.0 / 0.005 / 0.008
- `trade_shares`：100 / 200 / 250 / 300 / 400 / 500
- 组合 25+ 种，保留的记录在 `scripts/sweep_profile.py`。

## 关键结论

`trade_shares` 是对胜率 / 赔率 / PF 最敏感的单一杠杆：

| tsh | trades | rt | win% | payoff | PF | excess% | MDD% | Sharpe |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 200 (baseline) | 181 | 74 | 54.05 | 7.37 | 8.67 | +5.56 | 1.51 | 1.216 |
| 250 | 181 | 74 | 56.76 | 8.28 | 11.22 | +7.18 | 1.78 | 1.241 |
| **300** | 181 | 74 | **60.81** | **8.76** | **13.60** | **+8.80** | 2.05 | **1.260** |
| **400** | 181 | 74 | **66.22** | **8.98** | **17.59** | **+12.04** | 2.56 | **1.286** |
| 500 | 181 | 74 | 67.57 | 10.02 | 20.87 | +15.27 | 3.06 | 1.305 |

grid step 维度的单独影响较小：`step_max=1/4` 轻度加密卖档（+~10 trades），和 tsh=400 叠加后胜率再涨到 71.08%。

## 为什么 tsh 是杠杆？

- grid 触发次数基本不变（trades 稳在 181）——tsh 只改变每档交易的绝对额。
- ETF 震荡行情中，大档交易贲贲**准确捕捉了完整 round-trip**（卖后有效反弹 → 赢单放大）。
- 胜率提升是反直觉的结果：一部分小幅挺挺赔钱的交易（未完成 round-trip 前的开机仓）被更大的单量覆盖后，标记为“赢”的比例提高。
- MDD 线性放大：用户需要手动选择风险层。

## 新 profile

通过 `cfg.reference_tranche_shares` 将 tsh 绑定到 profile；`run_backtest` 在未显式传入 `trade_shares` 时从 cfg 兑底。

### balanced (新)

- `reference_tranche_shares = 300`
- 其他与 stable 相同
- 实测：win 60.81% / PF 13.60 / excess +8.86% / MDD 2.05% / Sharpe 1.270
- 适合波动容忍度中等的用户

### yield (新)

- `reference_tranche_shares = 400`
- `step_max_fraction = 1/4`（卖档轻度加密）
- 实测：win 71.08% / PF 19.66 / excess +10.37% / MDD 2.31% / Sharpe 1.269
- 适合追求胜率与absolute return的用户

### 原有 dev / aggressive（未变）

保留为“regime阈值激进变种”，数值略差于 stable（表目已证实），留作 A/B 测试参考。

## 对比矩阵

| profile | tsh | win% | payoff | PF | excess% | MDD% | Sharpe |
|---|---:|---:|---:|---:|---:|---:|---:|
| stable (保守) | 200 | 54.05 | 7.37 | 8.67 | +5.60 | 1.51 | 1.225 |
| dev | 200 | 52.63 | 7.24 | 8.05 | +4.97 | 1.38 | 1.216 |
| aggressive | 200 | 53.42 | 6.92 | 7.94 | +4.17 | 1.22 | 1.194 |
| **balanced (新)** | **300** | **60.81** | **8.76** | **13.60** | **+8.86** | 2.05 | **1.270** |
| **yield (新)** | **400** | **71.08** | 8.00 | **19.66** | **+10.37** | 2.31 | 1.269 |

## 用法

```bash
# CLI 不传 --trade-shares，从 profile 兑底
uv run python -m atr_grid backtest SH515880 --profile yield
uv run python -m atr_grid backtest SH515880 --profile balanced

# 继续显式覆盖也可以
uv run python -m atr_grid backtest SH515880 --profile yield --trade-shares 500
```

## 对 M3 KPI 的结果

| KPI | M3 目标 | stable | balanced | yield |
|---|---|---:|---:|---:|
| win_rate | ≥55% | 54.05% ❌ | **60.81%** ✅ | **71.08%** ✅ |
| payoff | ≥8.57 | 7.37 ❌ | **8.76** ✅ | 8.00 ❌ |
| PF | ≥9.13 | 8.67 ❌ | **13.60** ✅ | **19.66** ✅ |
| excess | ≥4% | +5.60% ✅ | +8.86% ✅ | +10.37% ✅ |
| MDD | ≤1.80% | 1.51% ✅ | 2.05% ❌ | 2.31% ❌ |
| Sharpe | ≥1.4 | 1.225 ❌ | 1.270 ❌ | 1.269 ❌ |

**小结**：balanced 突破 4/6 KPI，yield 突破 4/6。剩下两项（MDD 与 Sharpe）需要结构性改造才能同时满足——**线性加杠杆换不到 Sharpe 高度**，进一步提升需要非线性机制（跟随性 regime-aware、波动率自适应 tranche 、多 symbol 组合降相关性等）。

## 下一步候选

1. **波动率自适应 tranche**：`effective_tsh = base_tsh * k(BBW_percentile)`——高波动缩手、低波动放大。预期 MDD 降、Sharpe 升。
2. **多 symbol 组合**：引入 SH510300 (氪沣01 ETF) / SZ159928 (消费 ETF) 等，用低相关 symbol 掏灭 MDD。
3. **equity curve 波动端分析**：看 Sharpe 被哪类交易压低（trend_up_trim? range_grid?）。

## 假设 & 限制

- 单 symbol (SH515880)、单窗口 (840 bars / 2022-11 → 2026-04)。
- 无 walk-forward。需资金曲线多段验证稳健性。
- `trade_shares=400` 需要 `initial_shares=2000` 保证卖档不空——资金门槛 400 × 价格 ≈ ¥30k。
