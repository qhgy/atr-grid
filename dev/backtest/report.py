"""Markdown 回测报告。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..config import DEFAULT_CONFIG, StrategyConfig
from .metrics import MetricsBundle, compute_metrics
from .runner import BacktestResult

REPORT_DIR = Path(__file__).resolve().parent.parent / "reports"


def render_report(
    result: BacktestResult,
    cfg: StrategyConfig = DEFAULT_CONFIG,
    *,
    metrics: MetricsBundle | None = None,
) -> str:
    m = metrics or compute_metrics(result, cfg)
    s, b, t = m.strategy, m.benchmark, m.trips

    lines: list[str] = []
    lines.append(f"# 回测报告：{result.symbol}")
    lines.append("")
    lines.append(f"区间：{result.start} ~ {result.end}　初始资金：¥{cfg.initial_capital:,.0f}")
    lines.append("")
    lines.append("## 组合绩效（vs 买入持有）")
    lines.append("")
    lines.append("| 指标 | 策略 | 买入持有 |")
    lines.append("|---|---:|---:|")
    lines.append(f"| 期末净值 | ¥{s.final_value:,.0f} | ¥{b.final_value:,.0f} |")
    lines.append(f"| 总收益 | {s.total_return:+.1%} | {b.total_return:+.1%} |")
    lines.append(f"| 年化收益 CAGR | {s.cagr:+.1%} | {b.cagr:+.1%} |")
    lines.append(f"| 最大回撤 | {s.max_drawdown:.1%} | {b.max_drawdown:.1%} |")
    lines.append(f"| 夏普比率 | {s.sharpe:.2f} | {b.sharpe:.2f} |")
    lines.append(f"| Calmar | {_fmt_calmar(s.calmar)} | {_fmt_calmar(b.calmar)} |")
    lines.append("")
    lines.append("## 机动仓轮次（胜率 × 赔率 = 期望）")
    lines.append("")
    if t.count:
        lines.append(f"- 完成轮次：{t.count}（胜 {t.wins}）　弃轮（冻结放弃接回）：{t.abandoned}")
        lines.append(f"- **胜率：{t.win_rate:.1%}**")
        lines.append(f"- 平均盈利 ¥{t.avg_win:,.0f} / 平均亏损 ¥{t.avg_loss:,.0f} → **赔率 {_fmt_payoff(t.payoff)}**")
        lines.append(f"- **每轮期望：¥{t.expectancy:,.0f}**　轮次累计盈亏：¥{t.total_pnl:,.0f}")
    else:
        lines.append(f"- 区间内无完成轮次（弃轮 {t.abandoned}）")
    lines.append("")
    lines.append("## 成本与换手")
    lines.append("")
    lines.append(f"- 成交笔数：{m.trade_count}　总费用：¥{m.total_fees:,.2f}")
    lines.append(f"- 年化换手（双边/平均净值）：{m.turnover_annual:.1f}x")
    lines.append("")
    lines.append("## 期末状态")
    lines.append("")
    p = result.final_portfolio
    lines.append(
        f"- 底仓 {p.base_shares} 股　机动仓 {p.tactical_shares} 股　现金 ¥{p.cash:,.0f}"
    )
    lines.append(
        f"- 状态机：{result.final_state.tactical.value}　趋势：{'上行确认' if result.final_state.trend_on else '未确认/转弱'}"
    )
    if result.warnings:
        lines.append("")
        lines.append("## 数据警告")
        lines.append("")
        for w in result.warnings:
            lines.append(f"- {w}")
    lines.append("")
    lines.append(f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")
    return "\n".join(lines)


def save_report(content: str, symbol: str, *, suffix: str = "") -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    name = f"backtest_{symbol}{('_' + suffix) if suffix else ''}_{datetime.now().strftime('%Y%m%d')}.md"
    path = REPORT_DIR / name
    path.write_text(content, encoding="utf-8")
    return path


def _fmt_calmar(value: float) -> str:
    return "∞" if value == float("inf") else f"{value:.2f}"


def _fmt_payoff(value: float) -> str:
    return "∞" if value == float("inf") else f"{value:.2f}"
