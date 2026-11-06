# ATR 网格 dev 版本路线图 · 2026-04-24

> 终极目标：**提升胜率（winrate）与赔率（payoff ratio）**，并且每一步改动都要能被回测量化证伪。
>
> 参考设计文档：`D:/0000000/worklogs/codex/系统设计-2026-04-24-ATR网格-AI友好架构说明.md`

---

## 0. 当前状态速览

- 分支：`dev`（从 `main` 切出，已把 WIP 的"预警带 + 2000/200 演练口径"作为 baseline 提交）。
- 代码基线：MA20/MA60 + BOLL(20,2) + ATR14 → regime 三态 → range 建网、trend_up 减机动仓、trend_down 观望。
- `paper` 模拟单日最多一卖一买、支持 stop_price 冻结。
- `replay` 只统计买卖档命中次数，**不统计 P&L/胜率/赔率**（这是最大的盲点）。

> 换句话说：系统能给出一份"像样的操作模板"，但**还不会证明自己胜率赔率有提升**。dev 的首要任务是把"可衡量"这件事做完。

---

## 1. Review · 当前可改进点（按优先级）

### P0. 没有 P&L-aware 回测 → 胜率赔率无法度量
- `engine.replay_symbol()` 只数 `buy_hits/sell_hits/invalidations/breakouts`。
- 没有 trade pairing（进/出配对）、没有现金账户、没有日度净值。
- → 所有"参数更好"的断言都是拍脑袋。
- **改法**：把 `paper._simulate_fills` 拆成纯函数 `simulate_day(portfolio, plan, prev, high, low, close)`，然后给 `replay` 串起来，输出 `{trades, wins, losses, avg_win, avg_loss, payoff_ratio, win_rate, sharpe, max_dd, total_return}`。

### P1. regime 判定偏粗，假突破/弱趋势被当成震荡
- 只用 MA20/MA60 结构 + MA20 slope_ratio（5 根 /ATR）判定。
- 缺少**趋势强度**、**波动收敛**、**位置**三个维度的加护：
  1. ADX14（或最近 20 日收盘线性回归 R²）→ 过滤伪趋势。
  2. BBW（布林带宽度分位）→ 极度收敛期 step 会被强制到 BW/8，等价于让网格挤到极窄，假突破一来就全踩。
  3. 当前价到 `ma60` 的 z-score → 极端位置下 range 策略不可靠。
- **期望收益**：更少的"半山腰买入 → 第二天跌破失效位"→ 直接提升胜率。

### P2. 赔率设计几乎对称（1:1），缺乏"远档多买 / 强势追涨"
- range 计划 `buy_levels = center − k·step`、`sell_levels = center + k·step`，k=1..3，**等步等量**。
- trend_up 的 `rebuy = sell_trigger − ATR`，机动仓卖出后只在 1*ATR 下方等一次。
- reference sell ladder 是等步长 `sell_i = anchor + i·ATR`，对应 rebuy 等步长。
- **改法（保持"先卖后买、单日一动"纪律下的非对称化）**：
  1. 远档加码：`buy_i = center − (1, 1.2, 1.5)·step`，shares 权重 `(1, 1.5, 2)`；靠近下轨（i=3）配更大仓位。
  2. sell ladder 扩步：`sell_i = anchor + (1, 1.5, 2.2)·ATR`，rebuy 配 `(1.0, 1.2, 1.5)·ATR`。
  3. trend_up 的 rebuy 分两档：50% @ `sell-1.0·ATR`，50% @ `sell-1.8·ATR`。
- **期望收益**：单次赔率从 1:1 拉到 1.3:1～1.6:1。

### P3. 成本线止损逻辑只在 `paper` 层，引擎输出没有传达
- `GridPlan.lower_invalidation = lower − ATR`，但真实"保本止损价"没有算过。
- `paper.Portfolio.stop_price` 是用户手动输入，实盘参考性弱。
- **改法**：`config.GridConfig` 增加 `cost_stop_atr_multiple`（例如 2.0），当持仓有成本价时 engine 返回 `cost_stop_price = cost − 2·ATR` 与 `lower_invalidation` 二者取较高（保本优先）。

### P4. 通知与预警的阈值硬编码、绝对口径不适配多标的
- `report._NOTIFY_THRESHOLD_PCT = 1.5`（模块级常量）。
- `prealert_abs_buffer = 0.005 元` 是绝对值，适合 1～2 元价位的 ETF，贵标的会偏紧。
- **改法**：提到 `GridConfig`：
  - `notify_threshold_pct`
  - `prealert_buffer_pct`（相对 primary 价位），与 `prealert_abs_buffer` 二选大
  - 提供 `GridConfig.for_profile("stable"|"dev"|"aggressive")` 工厂

### P5. `_effective_step` 在低波动期强制 BW/8，实际是"被压到噪声里"
- `clamp(ATR, BW/8, BW/3)`：BBW 越窄，最低 step 越小，但此时真实噪声相对变大，抢 0.5% 的步长容易触发无效交易。
- **改法**：加一个百分比保底 `step = max(ATR, BW/8, min_step_pct · price)`，默认 `min_step_pct=0.8%`。

### P6. paper 结算的几个盲区
- `_simulate_fills` 用 `prev_close → today_close` 判定穿越，等价于"今天的收盘穿越，今天收盘成交"。**无法模拟盘中瞬穿**，也就统计不到"预警买在 primary 附近多少 bp"。
- 建议：接入 `next_day.high/low` 做更保守的成交区间穿越判定（跟 `replay_symbol` 一致），并区分"真实触发价 vs 模拟成交价"。
- 单日最多一买一卖：在强趋势天会把机会吃掉。改成 `max_fills_per_day` 配置化（默认 1，dev 可设 2）。

### P7. 数据源鲁棒性
- `core/market_data.py` 雪球优先 + 本地缓存兜底；但没有"K 线跳日"或"停牌"检测。
- 若上一日是停牌/复牌，MA/ATR 会失真，建议加一个 `validate_kline(rows)` 函数：检查最近 N 日是否有重复时间戳、非交易日。

### P8. 测试覆盖空白
- `tests/atr_grid/` 覆盖了 regime/engine pure/engine assemble/report/data，没有对 `paper._simulate_fills` 的单日多场景参数化测试（例如：同日穿越上下两档、跌破失效位、盈利冻结释放）。
- 如果后续改动 paper 结算逻辑，没有这些 fixture 会很危险。

---

## 2. dev 路线图（分 3 个阶段）

### Phase 1 · 让改动可衡量（本周）
> 没有这个 phase，所有后面的优化都是盲调。

| # | 工作 | 文件 | 产出 |
|---|------|------|------|
| 1.1 | 把魔法数提到 config，预警支持百分比/绝对值取大 | `atr_grid/config.py`, `atr_grid/engine.py`, `atr_grid/report.py` | `notify_threshold_pct`, `prealert_buffer_pct`; `for_profile()` 工厂 |
| 1.2 | 抽取 `simulate_day` 纯函数 | `atr_grid/paper.py` | 单元可测的一日结算 |
| 1.3 | 新增 `atr_grid/backtest.py` | 新文件 | `run_backtest(symbol, cfg, lookback, init_shares, init_cash)` 输出完整指标 |
| 1.4 | CLI 加 `backtest` 子命令 + `--profile` 开关 | `atr_grid/cli.py` | `uv run python -m atr_grid backtest SH515880 --lookback 180 --profile dev` |
| 1.5 | paper `_simulate_fills` 加 `max_fills_per_day` | `atr_grid/paper.py` + tests | 可配置化 |

**验收**：能跑出三只 ETF 近 180 日的 `stable vs dev` 胜率/赔率/Sharpe 对比表。

### Phase 2 · 提升胜率（两周）
> 核心思路：**让网格只在真正震荡里开火**。

| # | 工作 | 期望 |
|---|------|------|
| 2.1 | regime 加入 ADX14 过滤（ADX>25 视为趋势，禁用 range 网格） | winrate +3~5% |
| 2.2 | BBW 分位过滤：当 BBW 处于 60 日 10% 分位以下，视为"极端收敛"，降低单次仓位到 50% 或改用等待 | 减少假突破后连锁止损 |
| 2.3 | `_effective_step` 加入百分比保底 `min_step_pct` | 减少噪声交易 |
| 2.4 | range 计划的 `primary_buy` 过滤：若离 `lower_invalidation` < 0.5·ATR，直接不挂当前档（等更低支撑聚集） | 减少半山腰接刀 |
| 2.5 | 对 Phase 1 baseline 跑 backtest 报告，验证提升 | 可复现结果 |

**验收**：在 SH515880/SH513500/SZ159915 近 1 年回测上，winrate ≥ baseline + 3%，无显著 Sharpe 退化。

### Phase 3 · 提升赔率（两周）
> 核心思路：**做对的时候，让赚的更多；做错的时候，更快止损**。

| # | 工作 | 期望 |
|---|------|------|
| 3.1 | range 计划非对称步长 + 非对称仓位（下档权重高） | 单次赔率 1.3+ |
| 3.2 | trend_up `rebuy` 双档（1.0·ATR / 1.8·ATR, 各 50%） | 强势回接不空手，回得深再加码 |
| 3.3 | 引入 `chandelier_exit` 作为趋势尾部止盈（close < max(high, N) − k·ATR 清机动仓） | 锁大单赔率 |
| 3.4 | 引入 `cost_stop_price` 输出到 plan（engine 层） | 与 paper 解耦，仪表盘可见 |
| 3.5 | 突破加仓（可选）：放量突破 `upper_breakout`，把机动仓回补为底仓 | 吃住右侧 |

**验收**：payoff_ratio ≥ baseline + 0.3，max_dd 不超过 baseline 的 110%。

---

## 3. Dev profile 初步参数（Phase 1 默认）

| 参数 | stable（main） | dev |
|------|---------------|-----|
| `regime_ma_lookback` | 5 | 7 |
| `regime_slope_threshold` | 0.25 | 0.35 |
| `step_min_fraction` | 1/8 | 1/6 |
| `step_max_fraction` | 1/3 | 1/3 |
| `grid_level_count` | 3 | 3 |
| `prealert_abs_buffer` | 0.005 | 0.005 |
| `prealert_buffer_pct`（新） | 0 | 0.003 |
| `notify_threshold_pct`（新） | 1.5 | 1.0 |
| `min_step_pct`（新） | 0 | 0.008 |

> 这些默认值是"有依据但还没 A/B 验证"的起点，Phase 1 backtest 跑完会回来修正。

---

## 4. 分支协作约定

- `main`：上线版，只接受经过 Phase 1 backtest 验证的 PR。
- `dev`：日常迭代，允许 WIP commit；改动必须带测试。
- 每个 Phase 结束在 `dev` 上打 tag：`dev-phase-1`、`dev-phase-2`，便于回滚对照。
- 每次实验参数用 `GridConfig.for_profile("xxx")` 命名注册，不要散落在 CLI 脚本里。

---

## 5. 交付清单（本次 dev 基线之后）

- [x] 切分 `dev` 分支 & 迁移已有 WIP
- [x] 写此路线图（`docs/DEV_ROADMAP_2026-04-24.md`）
- [ ] Phase 1.1：config 参数化 + `for_profile()` 工厂 + 测试（**下一次提交**）
- [ ] Phase 1.2~1.4：backtest 模块（本周内）
- [ ] Phase 2/3 逐项按 backtest 结果推进
