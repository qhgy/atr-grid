# Checkpoint 2026-04-24 17:15 · SH515880 vs 纳指 40 日共振检验

## 现在在哪里
- 项目 `atr-grid-repo`，分支 `dev`，HEAD `bf314c5`（工作树干净）
- Phase 4 Trend-Hybrid MVP 已收口；本轮是一次**体外诊断**，未动策略代码
- 测试 172/172 绿（未重跑，未改源码）

## 本轮干了什么
- 写 `D:\000trae\A股数据\aaa\pysnowball\compare_515880_nasdaq.py`（体外脚本，不入 git）
- 两组数据各 40 根日线，均取自 Yahoo Finance 网页（本机直连被防火墙拦，走 Notion 侧 `web.loadPage` 拉 HTML 表格）
  - SH515880.SS：2026-02-26 → 2026-04-24
  - ^IXIC     ：2026-02-26 → 2026-04-23
- 同日对齐 + 隔夜对齐（A 股 T × IXIC T-1）两种口径分别统计

## 关键结论 / 数字

### 方向一致率
| 对齐 | 一致 | Pearson r | 同涨 | 同跌 | 背离 |
| --- | --- | --- | --- | --- | --- |
| 同日（T × T） | 23/38 = 60.5% | +0.182 | 13 | 10 | 14 |
| 隔夜（T × T-1） | 24/39 = 61.5% | +0.227 | 14 | 10 | 14 |

### 波动 & 均值（40 根）
| 指标 | SH515880 | IXIC |
| --- | --- | --- |
| 日均涨跌 | +0.55% | +0.18% |
| 日波动（pstdev） | 2.74% | 1.38% |
| 区间累计 | +20.4%（1.112→1.339） | +6.8%（22878→24439） |

**ETF 是纳指的 2× β 放大器，区间累计 3× 涨幅** —— 多出来的那部分是 AI/光模块自己的叙事溢价，不是共振带来的。

### 近 10 个 A 股交易日（隔夜对齐）
- 方向一致 6/10 = 60%，和整体一致
- 关键背离：**4/22 ETF +5.61% vs IXIC 前夜 -0.59%**（A 股独立暴涨）；**4/24 ETF -4.08% vs IXIC 前夜 -0.89%**（方向对但幅度 5×）
- 判定：最近这波**主驱动力在 A 股自身**，不是美股带的

### 结论陈述
- **弱共振**：方向 60%（仅略高于抛硬币 50%），r≈0.2，N=40 样本下 p≈0.15，**统计不显著**
- 想得到"共振"级别的证据需要：方向 > 75% 或 r > 0.5
- 95% 置信区间下真实一致率大概在 [45%, 75%]，**"纯随机"这个假设没被拒绝**

## 对策略的启示（只记录，不改代码）
- **D23 修正**：纳指**不适合**作为 regime 主信号。可做为**弱辅助**（纳指连跌 + 515880 高位分位 → 轻微加权 trim），权重不超过 ADX/ATR
- hybrid 的设计对独立波动是稳的：4/22 +5.61% 仍有仓位参与；4/24 -4.08% 有 cash_floor 20% 兜底
- paper_daily 可选增强：展示当日前夜 IXIC 涨跌 + 近 5 日方向一致率，纯信息展示，不接决策

## 待办（不急）
- [ ] Phase 5：hybrid 接入 `paper.simulate_day` 和 `backtest.run_backtest`
- [ ] SYSTEM.md 补 Phase 4 章节
- [ ] M1_BASELINE.md 加 "AI 牛市 beta 污染" 警告
- [ ] NEXT_PLAN.md 同步
- [ ] （可选）paper_daily 展示纳指前夜

## 调度用指针
- 体外脚本：`D:\000trae\A股数据\aaa\pysnowball\compare_515880_nasdaq.py`（40 根数据 inline，离线可复跑）
- 下一个 checkpoint 触发点：Phase 5 启动或再出现 `<transcript-segment>`

## 本轮踩的坑（写给未来的我）
- **E21** 本机 Python urllib / requests / curl.exe / pwsh IWR 全挂（SSL 中间件），解决：走 Notion `web.loadPage`
- **E22** 雪球 cookie 过期 `error_code 400016`，`fetch_515880.py` 里的 cookies 需要更新
- **E23** Yahoo JSON 接口（query2）单行被 Notion loadPage text 字段截断在 ~1500 字符，解决：改拉 HTML 历史页（多行表格）
- **E24** stooq.com 触发反爬（返回 obfuscated prompt injection），弃用
- **E25** PowerShell 嵌套多层 `python -c "...\"...\""` 引号地狱，解决：独立 .py 文件

—— 本 checkpoint 不做 git commit（检验属体外，主 repo 代码无变更）
