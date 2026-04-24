# Phase 4 · Trend-Hybrid 分层资金模块（MVP）

## 一、策略哲学

以 **SH515880（国泰中证全指通信设备 ETF）** 为唯一标的，其本质是 AI
算力硬件（光模块 CPO 权重 > 40%）的 beta 代理。该资产存在三个特征：

1. **长期方向向上**——AI 容量需求持续，趋势确定性高。
2. **高波动**——单年 MDD 可达 25%+，不是纯震荡标的。
3. **人性困局**——散户「涨了舍不得卖 + 跌了不敢买」，最后坐过山车回起点。

因此纯均值回归型网格（Phase 1-3 基线）存在结构性缺陷：在单边行情中会被
被动减仓或空仓追不上。Phase 4 的任务：**在原网格之上加两道护栏**——一
道「务必吃到趋势」（底仓）、一道「务必在高位减仓」（位置分档）——加上
一个底线（现金地板）。

## 二、四层资金结构

```
总资产 (total_equity)
 ├─ 底仓层 (base_budget)              = equity * base_position_ratio
 │    └─ 一次建仓后不交易，吞趋势 beta
 ├─ 网格层预算 (swing_budget)         = swing_pool * band.swing_ratio
 │    └─ swing_pool = equity - base - floor
 │    └─ ATR 网格，“位置刻度”动态缩放可用资金
 └─ 现金地板 (cash_floor)             = equity * cash_floor_ratio
      └─ 硬下限；仅大跌时的「应急通道」中可动用一部分
```

### 位置分档（position band）

根据“最近 position_window 根 K 线中今日所在的分位刻度 0-100”分 4 档：

| 档名     | 范围         | swing_ratio | 行为                 |
| -------- | ------------ | ----------- | -------------------- |
| low      | [0, 30)      | 1.00        | 网格资金满用 |
| mid_low  | [30, 70)     | 0.67        | 正常盖           |
| mid_high | [70, 85)     | 0.33        | 大幅缩同       |
| high     | [85, 100]    | 0.00        | **只卖不买**   |

### 应急补仓通道

过去 `emergency_refill_lookback`（默认 20）日最高价到今日收盘跌幅 ≥
`emergency_refill_drop_pct`（默认 10%）时触发：允许动用现金地板的
`emergency_refill_use_ratio`（默认 50%）去补仓，仍留硬底 10%。

## 三、模块结构

```
atr_grid/
├─ hybrid.py          ← 新增，纯函数，本 Phase 核心
├─ config.py          ← 扩展了 14 个 hybrid 参数，新增 trend_hybrid profile
├─ engine.py          ← 本轮不动（下一步接线）
├─ paper.py           ← 本轮不动（下一步接线）
└─ ...

tests/atr_grid/
└─ test_hybrid.py     ← 29 用例，覆盖位置刻度 / 分档 / 配额 / guard / 应急
```

### hybrid.py 公开 API

| 函数 / 类                    | 作用                                                       |
| --------------------------- | ---------------------------------------------------------- |
| `PositionBand`              | 单档定义：名称 / 范围 / swing_ratio / only_sell |
| `CapitalAllocation`         | 资金分配结果：base / swing / floor / only_sell         |
| `CashFloorDecision`         | `cash_floor_guard` 的返回结构                         |
| `position_percentile`       | 计算 0-100 分位                                       |
| `default_bands_from_config` | 从 cfg 构造 4 档默认分档                       |
| `resolve_band`              | 查找 percentile 所属档位                            |
| `compute_capital_allocation`| 输入 equity+percentile → 四层预算                 |
| `cash_floor_guard`          | 下单前检查 + 部分放行 + 应急解锁           |
| `should_emergency_refill`   | 是否进入应急补仓通道                            |

## 四、参数（全可调，无硬编码）

新增在 `GridConfig` 中：

| 参数                             | 默认   | 作用                                       |
| ------------------------------- | ------ | ------------------------------------------ |
| `trend_hybrid_enabled`          | False  | 总开关                                 |
| `base_position_ratio`           | 0.0    | 底仓比                                 |
| `cash_floor_ratio`              | 0.0    | 现金地板比                         |
| `position_window`               | 60     | 分位窗口                               |
| `position_band_low/mid/high`    | 30/70/85 | 4 档边界                               |
| `position_alloc_low/...` x4     | 1/.67/.33/0 | 每档 swing 乘数                   |
| `emergency_refill_drop_pct`     | 0.10   | 应急触发跌幅                         |
| `emergency_refill_lookback`     | 20     | 应急判定窗口                         |
| `emergency_refill_use_ratio`    | 0.5    | 地板可解锁比例                     |

`trend_hybrid` profile 预烤值：base 40% / floor 20% / 窗口 60 / 分档默认 + 卖 1.2x /
买 0.9x。用户在 CLI 或代码线上可用 `for_profile("trend_hybrid", …=…)` 单点覆盖。

## 五、与 Phase 1-3 的耦合

本 Phase **零侵入**：

- `hybrid.py` 是独立模块，未被 engine / paper / cli import。
- `config.py` 新字段默认值为 0 / False，日对 stable / dev / balanced / yield 等原有
  profile 输出完全相同（`test_existing_profiles_have_hybrid_disabled_by_default`
  明确锁住）。
- 165 个测试全绿；这是“空线上玩”的最大保障。

## 六、下一步（不在本 Phase 范围）

1. **engine 接线**：`build_plan_from_context` 内调用 `position_percentile` +
   `compute_capital_allocation`，把 `alloc.swing_budget` 交给原有 ladder
   生成器；`alloc.only_sell=True` 时屏蔽买单层；`cash_floor_guard`
   精准覆盖买入金额。
2. **paper 接线**：`simulate_day` 每日开盘前调用 `should_emergency_refill`
   决定 `emergency_unlocked` 标志；底仓一次性建仓放在 PaperState 初始化。
3. **CLI**：`atr_grid paper --profile trend_hybrid --since YYYY-MM-DD`
   每日拉雪球日 K + 刷新 paper state。
4. **Server 酱推送**（用户已经配好）：`paper_daily` 完成后按格式发送
   今日动作、持仓 / 现金 / 位置刻度。

## 七、清单

- [x] `atr_grid/hybrid.py` 新建
- [x] `atr_grid/config.py` 扩展 14 个参数 + `trend_hybrid` profile
- [x] `tests/atr_grid/test_hybrid.py` 29 用例
- [x] 全量测试 165 绿
- [x] 本设计文档
- [ ] engine 接线（下一轮）
- [ ] paper_daily 脚本（下一轮）
- [ ] Server 酱推送（下下轮）
