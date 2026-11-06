# Checkpoint 2026-04-24 16:40 · Phase 4 Trend-Hybrid 整体收口

> 主动蒸馏产物，规范见 `D:\000trae\Jesus\notion\atr-grid-design\CONTEXT_DISTILL.md`。

## 现在在哪里
- 项目：`D:\000trae\atr-grid-repo`  ·  分支：`dev`
- HEAD commit：`671589b`  ·  工作树：干净（除 `output/atr_grid.html` 历史遗留）
- Phase：Phase 4 Trend-Hybrid **MVP 3 层已落地**
  - 4.1 hybrid 模块（双独立）
  - 4.2 engine overlay 接线（可选）
  - 4.3 paper\_daily 每日决策脚本、以及 30+ 天事后诸葛亮回测
- 未做：hybrid 还没接到 simulate\_day / run\_backtest。

## 本轮干了什么
- `08c66d8`  feat(hybrid)：Phase 4 hybrid 模块（分层资金 + 现金地板 + 应急补仓）+ 29 用例
- `137a4e0`  feat(engine)：overlay 接线，新增 `build_plan_with_frame` / `apply_hybrid_overlay` / `generate_plan_with_hybrid`；主路径零改动；+7 用例
- `b36595a`  feat(scripts)：`scripts/paper_daily.py` 每日决策 CLI，text/md/json 三格式；不动账本
- `671589b`  feat(scripts)：`scripts/replay_last_30d.py` 事后诸葛亮回测脚本
- 全量测试：**172/172** 绿

## 关键结论 / 数字（带警示）

### 回测数据：2026-03-01 入场 2w，39 根 K 线（到 2026-04-23）
- 终权益：**¥21619.60**
- 策略 +8.10% vs 买入持有 +7.92%，超额 **+0.18%**（几乎打平）
- 最大回撤 3.44%  ·  持有回撤 ≈7.4%  → 波动相对削半
- 交易 9 笔，完整回合 **仅 3 个**：2 胜 1 负

### 重要警示
- **样本太小**：n=3 回合的胜率/PF/Sharpe 在统计上无意义，别当真
- **单边上涨行情**：策略在这种场景天然吃亏，+0.18% 已经很运气
- **底仓被侵蚀 900 股**：run\_backtest 不知道 hybrid 语义，把 7100 股底仓卖到 6200；实盘接入 hybrid 后本数值会上调一些，幅度未知
- **hybrid 定性效果未在回测中分离**：现金地板 / only\_sell / 应急补仓全部未起效

## 下一步选项（由用户选，我不自己决定）

1. **观察 2-3 周**：每天跑 `paper_daily` 看决策质量，不改代码
2. **换下跌标的回测**：找个 2026 Q1 下跌的 ETF跑同样 40 天，验证策略在主战场的表现
3. **hybrid 接入 simulate\_day**：让 `cash_floor_guard` / 底仓保护 / only\_sell 真的在每日模拟中生效，再回测一次
4. **Server 酱接入**：把 paper\_daily 的 md 输出推到微信，实现无人值守

没有默认选。中间势均力的是选项 2（换下跌标的）：既能验证策略质量，又不用写代码。

## 调度用指针（给下一个会话的我）

### 要读的文件（3 个）
1. `D:\000trae\Jesus\notion\atr-grid-design\CONTEXT_DISTILL.md`  — 蒸馏协议
2. `docs/CHECKPOINT_2026-04-24_phase4.md`  — 本件
3. `docs/phase4_trend_hybrid.md`  — Phase 4 设计

### 要跑的命令
```bash
git log --oneline -10
uv run pytest -q          # 期待 172 passed
```

### 要避免的坑（指针，详见 error register）
- **E19**：`apply_patch` 用**绝对路径**，`*** Update File:` 行不能写相对路径
- **E-U3**：ATR/ADX 必须保留，不要提议用 RSI/乖离率替换
- **E-U4**：不再做历史回测大样本跑综；只跑用户明确要求的短窗口事后诸葛亮
- **E-U5**：MVP + 参数可传 + 模块化；不要大而全
- **E-U1**：SH515880 是**通信/AI 硬件 ETF**（追踪 931160），不是红利

### Phase 4 新 API 速查
```python
# atr_grid/hybrid.py
from atr_grid import hybrid
hybrid.position_percentile(frame, window=60)          # -> float | None
hybrid.compute_capital_allocation(total_equity, pct, cfg)  # -> CapitalAllocation
hybrid.cash_floor_guard(cash, amount, total_equity, cfg, emergency_unlocked=False)
hybrid.should_emergency_refill(frame, cfg)

# atr_grid/engine.py  (新增的 3 个导出)
from atr_grid.engine import (
    build_plan_with_frame,        # (ctx, cfg) -> (plan, frame)
    apply_hybrid_overlay,         # (plan, frame, *, total_equity, cfg) -> (plan, alloc|None)
    generate_plan_with_hybrid,    # (symbol, *, total_equity, ...) -> (plan, alloc|None)
)
```

### profile 当前参数
all 6 profiles: `stable / dev / aggressive / balanced / yield / trend_hybrid`
——只有 `trend_hybrid` 缺省 `trend_hybrid_enabled=True`，其他都是 False，hybrid overlay 对它们是 no-op。

## 本 Checkpoint 的口味

用户今晚从 **审阅项目 → 接受 trend_hybrid 设计 → 决定 MVP 而非大全 → 则着回测拿数字 → 讨论上下文管理** 走一轮。
最后这个话题（主动蒸馏）是用户老锤中的工程师习惯：**别相信默认值，给自己安全边界**。留个本事据。
