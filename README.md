# atr-grid

ETF ATR 网格策略引擎 · 自动化 Dashboard + 微信推送

📊 **Dashboard**：https://qhgy.github.io/atr-grid/

## 快速开始

克隆后，在项目根目录执行：

```bash
# 设置雪球 Cookie（本地运行必须）
export XUEQIU_COOKIE_FILE=/path/to/xueqiu.com_cookies.txt
# Windows PowerShell:
# $env:XUEQIU_COOKIE_FILE = "D:\your_path\xueqiu.com_cookies.txt"

# 多 ETF 汇总（主用）
uv run python -m atr_grid multi SH515880 SH513500 SZ159915

# 单标的查看
uv run python -m atr_grid plan SH515880 --no-save

# 历史回放
uv run python -m atr_grid replay SH515880 --lookback 30
```

> 注：需先安装 [uv](https://docs.astral.sh/uv/) 并执行 `uv sync`

## 当前监控标的

| 代码 | 名称 |
|------|------|
| SH515880 | 红利ETF |
| SH513500 | 标普500ETF |
| SZ159915 | 创业板ETF |

## 模块说明

| 模块 | 说明 |
|------|------|
| `atr_grid/config.py` | 策略参数集中配置 |
| `atr_grid/engine.py` | 核心计划生成 + 回放 |
| `atr_grid/regime.py` | 市场状态分类 |
| `atr_grid/report.py` | JSON/Markdown/HTML 报告 |
| `core/` | 数据获取与雪球 session |

## GitHub Actions

每日北京时间 09:00 自动更新 Dashboard，临近档位推送微信（方糖 App）。

所需 Secrets：`XUEQIUTOKEN`（雪球 Cookie）、`SERVERCHAN_KEY`（Server酱推送 Key）

手动触发：

```bash
gh workflow run update-dashboard.yml --repo qhgy/atr-grid
```
