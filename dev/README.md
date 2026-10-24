# dev：515880 交易系统（胜率赔率最大化）

与 `atr_grid` 包相互独立的全新项目，只复用 `core/market_data.py` 行情链路。
设计原则：**证据优先**——任何规则先过带成本回测，输出胜率/赔率/期望，与买入持有对照。

## 用法

```powershell
uv run python -m dev backtest            # 全历史带成本回测 + 绩效报告（dev/reports/）
uv run python -m dev backtest --offline  # 仅用 dev/cache 离线快照（可复现）
uv run python -m dev signal              # 每日信号：当前状态 + 明日操作单 + 原因链
uv run python -m dev scan                # walk-forward 参数检验（训练/验证切分）
uv run pytest tests/dev -q               # 单元测试
```

## 架构

| 模块 | 职责 |
|---|---|
| `config.py` | 全部参数（frozen dataclass，单一事实来源） |
| `datafeed.py` | 行情：雪球(前复权)→akshare(强制qfq)→dev/cache 离线快照；数据校验 |
| `indicators.py` | 纯函数指标：MA/ATR/已实现波动率等 |
| `strategy/states.py` | 显式状态机：IDLE→TRIMMED→(接回/弃轮/冻结)→IDLE |
| `strategy/rules.py` | 量化规则：指数过滤、波动率目标、现金地板、应急通道 |
| `strategy/engine.py` | 每日决策：三层资金 → 次日订单 + 中文原因链（无前视） |
| `backtest/broker.py` | T+1、整手、佣金(万1+下限)、滑点、一字板不成交 |
| `backtest/runner.py` | 事件驱动回测：t 收盘决策 → t+1 撮合 |
| `backtest/metrics.py` | 胜率/赔率/期望 + CAGR/MaxDD/Sharpe/Calmar/换手 |
| `backtest/walkforward.py` | 训练段粗网格调参，验证段（样本外）评分 |

## 策略（三层资金 + 状态机）

1. **底仓（趋势层）**：收盘连续 N 日站上/跌破 MA200 确认趋势开关（Faber 2007）；
   仓位 = base_ratio × 波动率系数（Moreira-Muir 2017 改良版：与**自身近一年波动中位数**
   比较而非固定阈值——成分股换血、波动水位整体抬升时不误伤），分批靠拢。
2. **机动仓（反转层）**：上行趋势中高卖一档（收盘+k×ATR），回落接回（卖价−k×ATR）；
   向上脱离卖价+1×ATR → 弃轮不追高；接回后续跌超 1×ATR → 冻结买入，站回 MA20 解冻。
3. **指数过滤**：深成指+创业板均低于 MA20 且 5 日收益为负 → 只许接回，不开新仓。
4. **现金地板**：买单不得击穿 20%；20 日回撤≥10% 时解锁一半。

## 双机制检验协议（应对成分股换血）

515880 成分股在 AI 大潮中整体换血，2024 年前后统计上是两个资产
（年化波动 30%→42%，对创业板 beta 0.72→0.98）。因此 `scan` 采用双机制协议：

- **旧机制段（~2023）只验生存**：参数必须满足 MaxDD ≤ 买入持有 × 70%，不用于预测收益；
- **新机制段（2024+）选参数**，并留出最近 120 个交易日做样本外检验；
- 样本外大幅劣化 = 过拟合，回退默认参数。

## 纪律

- 参数改动必须跑 `scan`，样本外大幅劣化即回退；
- 报告中的轮次胜率不含弃轮成本，**以组合层 CAGR/MaxDD/Sharpe 为最终裁判**；
- 不自动下单，信号仅供人工确认。
