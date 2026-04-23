# atr-grid

ETF ATR 网格策略引擎 · SH515880 专用

## 快速开始

\\\ash
uv run python -m atr_grid plan SH515880
uv run python -m atr_grid replay SH515880 --lookback 30
\\\

## 模块说明

| 模块 | 说明 |
|------|------|
| \tr_grid/config.py\ | 策略参数集中配置 |
| \tr_grid/engine.py\ | 核心计划生成 + 回放 |
| \tr_grid/regime.py\ | 市场状态分类 |
| \tr_grid/report.py\ | JSON/Markdown/HTML 报告 |
| \core/\ | 数据获取与雪球 session |
