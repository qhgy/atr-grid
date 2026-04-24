# Checkpoint 2026-04-24 18:45 · Phase 5 Trend-Hybrid 全链贯通

> 主动蒸馏产物，规范见 `D:\000trae\Jesus\notion\atr-grid-design\CONTEXT_DISTILL.md`。

## 现在在哪里
- 项目：`D:\000trae\atr-grid-repo`  ·  分支：`dev`
- HEAD commit：`00360e2`（Phase 5.1 + 5.2 源码 + 测试）。本文档 + `scripts/phase5_compare.py` + `scripts/paper_daily.py` 的地板预警显示会在后续一条 `docs+tools` commit 里补交
- 工作树：干净（除 `docs/CHECKPOINT_2026-04-24_nasdaq.md` / `output/atr_grid.html` 历史遗留）
- Phase：Phase 5 **hybrid 接入交易主循环**，180/180 绿
  - 5.1 底仓锁定：`simulate_day` 和 `run_backtest` 不再卖穿底仓
  - 5.2 现金地板：`simulate_day` 买单前走 `cash_floor_guard`，不够直接阻断

## 本轮干了什么

### 核心 commit `00360e2` feat(hybrid 接入)
- `atr_grid/paper.py`：`PaperState.base_shares` 新字段；卖单条件 `shares - trade_shares >= state.base_shares`；买单支发动 `hybrid.cash_floor_guard`，approved < intended 直接 break 加 `cash_floor_block` 事件
- `atr_grid/backtest.py`：hybrid 开启时以 `initial_shares` 为底仓锁；每日 loop 算 `cash_floor_value + emergency_unlocked` 传 simulate\_day
- `tests/atr_grid/test_paper_simulate_day.py`：+6 测试（底仓触底允许 / 跨越阻断 / base=0 等价 / 地板拦截 / 应急解锁 / 默认参数不动）
- `tests/atr_grid/test_backtest.py`：+2 测试（trend\_hybrid 单边上涨锁底仓 / 非 hybrid 震荡不动）
- 全量测试：**180/180** 绿（172 + 5 个 5.1 + 3 个 5.2）

### 后续一条 `docs+tools` commit 带进来
- `scripts/phase5_compare.py`：三 profile（stable / balanced / trend\_hybrid）在 2026-03-01 → 04-23 同起点对比
- `scripts/paper_daily.py`：对主买档做 `cash_floor_guard` 预检，日报多一行“地板预警”（不改 plan，只往 payload 多写 `cash_floor_check` 字段）
- 本文件

## 关键结论 / 数字（带警示）

### B 扩参：phase5\_compare 三 profile 对比（2026-03-01 → 04-23、41 交易日）

| profile       | 终权益     | 策略%  | 持有%  | 超额%     | 交易 | 回合 | MDD%  | 终股 |
|---------------|------------|--------|--------|-----------|------|------|-------|------|
| stable        | 21478.30   | +7.54  | +8.07  | **-0.53** | 10   | 2    | 3.22  | 6300 |
| balanced      | 21450.80   | +7.41  | +8.07  | **-0.66** | 10   | 2    | 3.19  | 5900 |
| trend\_hybrid | **21639.70** | +8.35 | +8.07 | **+0.28** | 6    | 3    | 3.44  | **7100** |

- 对 Phase 4 replay 的长进：超额 **+0.18% → +0.28%**，底仓 **6200 → 7100**（朗好不动）
- 交易数 **10 → 6**：hybrid 锁住底仓后，stable/balanced 过度的 4 单卖出被直接阻断
- 回合数 2 → 3：买→卖→买→卖 更完整，而非 stable 里的 “买→穿底→被迫持股” 夵里

### 重要警示（跟 Phase 4 一致）
- **样本小**：41 根 K 线 × 3 回合 还是统计意义上的噪声。不要拿这个 +0.28% 去外推
- **单边上涨行情**：网格策略在这种场景自然跑输纯持有，trend\_hybrid 能轻微赢一下本身已很好
- **现金地板和应急补仓这段什有走颜色**：该行情没有大跳水和资金紧张，两个行为传洞在本回测里未放炮；要跅它们要等下跌标的样本

### C：paper\_daily.py 上线每日显示
命令：`uv run python scripts/paper_daily.py --symbol SH515880 --profile trend_hybrid --total-equity 20000 --shares 7100`

输出新增一行：`地板预警：✓ 主买档可过（打算花￥419，现金估￥10493）`

- 位置刻度 78.3 / mid\_high 档位 / swing 乘数 0.33 / 资金分配 ¥8000 + ¥2640 + ¥4000
- 预警器逻辑：`cash_before ≈ total_equity − shares × current_price`；拟买额 = `primary_buy × reference_tranche_shares × 1.003`；调 `hybrid.cash_floor_guard` 看 approved 是否 >= intended。不改 engine 计算出的 plan

## 下一步选项（由用户选，我不自己决定）

1. **观察 2-3 周**：每日跑 `paper_daily` 看决策质量（包括新的地板预警行），不改代码
2. **换下跌标的回测**：找个 2026 Q1 下跌的 ETF 跑同样 40 天，验证 hybrid 在主战场的表现 —— 现在是真的有 hybrid 接入，下跌行情里应该能见到现金地板和应急补仓走颜色
3. **Server 酱接入**：把 paper\_daily 的 md 输出推到微信，实现无人值守
4. **参数微调**：比如改 `reference_tranche_shares` / `cash_floor_ratio`，再跑 phase5\_compare 看灵敏度

没有默认选。当前最有信息密度的是选项 2：只有下跌行情能把 hybrid 的三条感应线（底仓 / 地板 / 应急）全点亮。

## 调度用指针（给下一个会话的我）

### 要读的文件（3 个）
1. `D:\000trae\Jesus\notion\atr-grid-design\CONTEXT_DISTILL.md`  — 蒸馏协议
2. `docs/CHECKPOINT_2026-04-24_phase5.md`  — 本件
3. `docs/CHECKPOINT_2026-04-24_phase4.md`  — 前一件，知道 Phase 4 的上下文

### 要跑的命令
```
git log --oneline -10
uv run pytest -q                         # 期待 180 passed
uv run python scripts/phase5_compare.py  # hybrid 对比数字稳定就不用重跑
```

### 要避免的坑（指针，详见 error register）
- **E19**：`apply_patch` 用绝对路径
- **E26**：`run_command` 参数是 `timeout`（int 秒），不是 `timeout_seconds`
- **E27**：MCP 无 `search_in_file`；`search` 的 `roots`/`context` 不是合法参数 → 用 `read_text` 绕过
- **E28**：Notion 偶发瞬时 `User does not have edit access to record` → 重试即 OK
- **E-U3**：ATR / ADX 必须保留，不要提议 RSI / 乖离率
- **E-U4**：不再做大样本回测；只跑用户明确申请的短窗口
- **E-U5**：MVP + 参数可传 + 模块化
- **E-U1**：SH515880 是通信 / AI 硬件 ETF（追踪 931160），不是红利

### Phase 5 新 API 速查
- `atr_grid.paper.PaperState.base_shares: int = 0` — 卖单下限
- `simulate_day(state, plan, *, trade_shares=DEFAULT_TRADE_SHARES, cash_floor=0.0, total_equity=0.0, cfg=None, emergency_unlocked=False)` — 新 4 个 kwargs 全默认兼容旧调用
- `run_backtest`：hybrid 开时自动锁 `initial_shares` 为底仓；每日传现金地板 + 应急状态给 simulate\_day

### profile 当前参数（未变）
6 profiles：`stable / dev / aggressive / balanced / yield / trend_hybrid`。只 `trend_hybrid` 默认 `trend_hybrid_enabled=True`，其他都 False。

## 本 Checkpoint 的口味

用户今晚从 **验收 Phase 4 数据 → 吐槽超额只有 +0.18% 试用偶发 → 立刻提出 Phase 5（底仓 + 地板接入 simulate\_day）→ ABC 三件齐头** 走一轮。

关键信号：用户一直在把 “bug / 未接入的功能” 和 “可用的实盘特性” 区开。Phase 4 的 +0.18% 他第一时间看出来 “这个数字的主要贡献是 hybrid 没真接入”，然后要求 Phase 5 把它补上。这种 **不受好看数字骗** 的习惯是本轮能干完的前提。

另外他明确要求 “通俗易懂 + 比喻举例”，后续会话用“煎饼摊 / 小卖部 / 压箱底货 / 保命钱”这种口吻。
