"""Report rendering helpers for the ETF ATR grid MVP."""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path

from core.paths import project_path

from .engine import GridPlan, plan_to_dict

CSV_HEADER_MAP = {
    "row_type": "记录类型",
    "symbol": "代码",
    "data_source": "数据来源",
    "regime": "市场状态",
    "mode": "当前模式",
    "strategy_name": "策略名称",
    "current_price": "当前价",
    "last_trade_date": "最后交易日",
    "tranche_index": "档位",
    "tranche_shares": "每档股数",
    "sell_price": "卖出价",
    "sell_upside_pct": "较现价上涨幅度%",
    "sell_upside_pct_abs": "较现价上涨幅度绝对值%",
    "rebuy_price": "接回价",
    "rebuy_vs_current_pct": "较现价回落幅度%",
    "rebuy_vs_current_pct_abs": "较现价回落幅度绝对值%",
    "rebuy_from_sell_pct": "较卖点回落幅度%",
    "rebuy_from_sell_pct_abs": "较卖点回落幅度绝对值%",
    "trend_allowed": "当前是否允许执行",
    "trend_limit_shares": "当前最多建议卖出股数",
    "lower_invalidation": "失效下沿",
    "upper_breakout": "上沿突破",
    "note": "说明",
}


def write_json_report(plan: GridPlan, target: str | Path) -> Path:
    """Persist a JSON plan report."""
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan_to_dict(plan), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_markdown_report(plan: GridPlan, target: str | Path) -> Path:
    """Persist a Markdown plan report."""
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(plan), encoding="utf-8")
    return path


def write_csv_report(plan: GridPlan, target: str | Path) -> Path:
    """Persist a CSV plan report with price ladders and percentage deltas."""
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = render_csv_rows(plan)
    if not rows:
        rows = [build_summary_row(plan)]
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([CSV_HEADER_MAP.get(key, key) for key in fieldnames])
        for row in rows:
            translated = _translate_csv_row(row)
            writer.writerow([translated.get(key, "") for key in fieldnames])
    return path


def render_markdown(plan: GridPlan) -> str:
    """Render a human-readable Markdown report."""
    snapshot = plan.snapshot
    warnings = "\n".join(f"- {item}" for item in plan.warnings) if plan.warnings else "- 无"

    lines = [
        f"# {plan.symbol} ETF ATR 网格计划",
        "",
        "## 摘要",
        "",
        f"- 当前价：¥{plan.current_price:.3f}",
        f"- 市场状态：`{_translate_regime(plan.regime)}`",
        f"- 网格启用：`{'是' if plan.grid_enabled else '否'}`",
        f"- 当前模式：`{_translate_mode(plan.mode)}`",
        f"- 策略名称：`{plan.strategy_name}`",
        f"- 现在该做什么：{plan.headline_action}",
        f"- 结论：{plan.reason}",
        "",
        "## 操作卡片",
        "",
        f"- 策略名称：{plan.strategy_name}",
        f"- 当前动作：{plan.headline_action}",
        f"- 机动仓规模：{plan.tactical_shares if plan.tactical_shares else '无'}",
        *[f"- {step}" for step in plan.action_steps],
        "",
        "## 标准 1000 股卖出模板",
        "",
        f"- 模板仓位：{plan.reference_position_shares} 股",
        f"- 每档卖出：{plan.reference_tranche_shares} 股",
        f"- 机械卖出网格：{fmt_levels(plan.reference_sell_ladder)}",
        f"- 机械接回网格：{fmt_levels(plan.reference_rebuy_ladder)}",
        "",
        "| 档位 | 卖出价 | 较现价上涨幅度 | 接回价 | 较卖点回落幅度 | 当前是否允许执行 |",
        "|------|--------|----------------|--------|----------------|------------------|",
        *[
            _render_ladder_line(plan, index)
            for index in range(min(len(plan.reference_sell_ladder), len(plan.reference_rebuy_ladder)))
        ],
        "",
        "## 趋势修正版",
        "",
        f"- 当前最多建议卖出：{plan.trend_sell_limit_shares} 股",
        f"- 当前最多建议执行：第 1 到第 {plan.trend_sell_limit_tranches} 档" if plan.trend_sell_limit_tranches else "- 当前最多建议执行：先不按模板卖出",
        f"- 修正说明：{plan.trend_adjustment_note}",
        "",
        "## 数据来源与时效",
        "",
        f"- 数据来源：`{_translate_data_source(plan.data_source)}`",
        f"- 最后交易日：`{plan.last_trade_date}`",
        f"- 最近收盘：¥{plan.last_close:.3f}",
        f"- 告警：\n{warnings}",
        "",
        "## 市场状态判断",
        "",
        f"- ATR14：¥{_fmt(snapshot.atr14, 3)}",
        f"- Boll 上轨 / 中轨 / 下轨：¥{_fmt(snapshot.bb_upper, 3)} / ¥{_fmt(snapshot.bb_middle, 3)} / ¥{_fmt(snapshot.bb_lower, 3)}",
        f"- MA20 / MA60：¥{_fmt(snapshot.ma20, 3)} / ¥{_fmt(snapshot.ma60, 3)}",
        f"- 说明：{plan.reason}",
        "",
        "## 网格参数与买卖点",
        "",
        f"- 中枢：{_fmt_price(plan.center)}",
        f"- 步长：{_fmt_price(plan.step)}",
        f"- 主买点：{_fmt_price(plan.primary_buy)}",
        f"- 主卖点：{_fmt_price(plan.primary_sell)}",
        f"- 建议减仓股数：{plan.trim_shares if plan.trim_shares else '无'}",
        f"- 建议接回价：{_fmt_price(plan.rebuy_price)}",
        f"- 买入档：{fmt_levels(plan.buy_levels)}",
        f"- 卖出档：{fmt_levels(plan.sell_levels)}",
        "",
        "## 风险边界与失效条件",
        "",
        f"- 下沿失效：{_fmt_price(plan.lower_invalidation)}",
        f"- 上沿突破：{_fmt_price(plan.upper_breakout)}",
        "- 解释：跌破下沿失效线代表均值回归预期被破坏；突破上沿突破线代表单边趋势可能形成，MVP 不再继续双向网格。",
        "",
    ]
    return "\n".join(lines)


def default_report_paths(plan: GridPlan) -> tuple[Path, Path]:
    """Return default JSON and Markdown output paths under the project report directory."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = project_path("output", "atr_grid_reports")
    base_name = f"{plan.symbol}_atr_grid_{timestamp}"
    return report_dir / f"{base_name}.json", report_dir / f"{base_name}.md"


def default_csv_report_path(plan: GridPlan) -> Path:
    """Return the default CSV output path under the project report directory."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = project_path("output", "atr_grid_reports")
    base_name = f"{plan.symbol}_atr_grid_{timestamp}"
    return report_dir / f"{base_name}.csv"


def _base_csv_row(plan: GridPlan) -> dict[str, str | int | float]:
    """Common CSV fields shared by every row type."""
    return {
        "row_type": "",
        "symbol": plan.symbol,
        "data_source": plan.data_source,
        "regime": plan.regime,
        "mode": plan.mode,
        "strategy_name": plan.strategy_name,
        "current_price": round(plan.current_price, 3),
        "last_trade_date": plan.last_trade_date,
        "tranche_index": "",
        "tranche_shares": plan.reference_tranche_shares,
        "sell_price": "",
        "sell_upside_pct": "",
        "sell_upside_pct_abs": "",
        "rebuy_price": "",
        "rebuy_vs_current_pct": "",
        "rebuy_vs_current_pct_abs": "",
        "rebuy_from_sell_pct": "",
        "rebuy_from_sell_pct_abs": "",
        "trend_allowed": "",
        "trend_limit_shares": plan.trend_sell_limit_shares,
        "lower_invalidation": round(plan.lower_invalidation, 3) if plan.lower_invalidation is not None else "",
        "upper_breakout": round(plan.upper_breakout, 3) if plan.upper_breakout is not None else "",
        "note": "",
    }


def render_csv_rows(plan: GridPlan) -> list[dict[str, str | int | float]]:
    """Render structured CSV rows for ladders and trend adjustments."""
    rows: list[dict[str, str | int | float]] = [build_summary_row(plan)]
    for index, sell_price in enumerate(plan.reference_sell_ladder, start=1):
        rebuy_price = plan.reference_rebuy_ladder[index - 1] if len(plan.reference_rebuy_ladder) >= index else None
        row = _base_csv_row(plan)
        row.update(
            row_type="standard_ladder",
            tranche_index=index,
            sell_price=round(sell_price, 3),
            sell_upside_pct=_pct_or_blank(_pct_change(sell_price, plan.current_price)),
            sell_upside_pct_abs=_pct_or_blank(_pct_abs_change(sell_price, plan.current_price)),
            rebuy_price=round(rebuy_price, 3) if rebuy_price is not None else "",
            rebuy_vs_current_pct=_pct_or_blank(_pct_change(rebuy_price, plan.current_price)),
            rebuy_vs_current_pct_abs=_pct_or_blank(_pct_abs_change(rebuy_price, plan.current_price)),
            rebuy_from_sell_pct=_pct_or_blank(_pct_change(rebuy_price, sell_price)),
            rebuy_from_sell_pct_abs=_pct_or_blank(_pct_abs_change(rebuy_price, sell_price)),
            trend_allowed="yes" if index <= plan.trend_sell_limit_tranches else "no",
            note=f"标准 {plan.reference_position_shares} 股模板，第 {index} 档卖出后等待回落接回",
        )
        rows.append(row)

    trend_row = _base_csv_row(plan)
    trend_row.update(
        row_type="trend_adjustment",
        sell_price=round(plan.primary_sell, 3) if plan.primary_sell is not None else "",
        sell_upside_pct=_pct_or_blank(_pct_change(plan.primary_sell, plan.current_price)),
        sell_upside_pct_abs=_pct_or_blank(_pct_abs_change(plan.primary_sell, plan.current_price)),
        rebuy_price=round(plan.rebuy_price, 3) if plan.rebuy_price is not None else "",
        rebuy_vs_current_pct=_pct_or_blank(_pct_change(plan.rebuy_price, plan.current_price)),
        rebuy_vs_current_pct_abs=_pct_or_blank(_pct_abs_change(plan.rebuy_price, plan.current_price)),
        rebuy_from_sell_pct=_pct_or_blank(_pct_change(plan.rebuy_price, plan.primary_sell)),
        rebuy_from_sell_pct_abs=_pct_or_blank(_pct_abs_change(plan.rebuy_price, plan.primary_sell)),
        trend_allowed="yes",
        note=plan.trend_adjustment_note,
    )
    rows.append(trend_row)
    return rows


def build_summary_row(plan: GridPlan) -> dict[str, str | int | float]:
    """Build the summary row for the CSV export."""
    row = _base_csv_row(plan)
    row.update(
        row_type="summary",
        sell_price=round(plan.primary_sell, 3) if plan.primary_sell is not None else "",
        sell_upside_pct=_pct_or_blank(_pct_change(plan.primary_sell, plan.current_price)),
        sell_upside_pct_abs=_pct_or_blank(_pct_abs_change(plan.primary_sell, plan.current_price)),
        rebuy_price=round(plan.primary_buy, 3) if plan.primary_buy is not None else "",
        rebuy_vs_current_pct=_pct_or_blank(_pct_change(plan.primary_buy, plan.current_price)),
        rebuy_vs_current_pct_abs=_pct_or_blank(_pct_abs_change(plan.primary_buy, plan.current_price)),
        rebuy_from_sell_pct=_pct_or_blank(_pct_change(plan.primary_buy, plan.primary_sell)),
        rebuy_from_sell_pct_abs=_pct_or_blank(_pct_abs_change(plan.primary_buy, plan.primary_sell)),
        trend_allowed="yes" if plan.trend_sell_limit_tranches else "no",
        note=plan.headline_action,
    )
    return row


def _fmt(value: float | None, precision: int) -> str:
    if value is None:
        return "N/A"
    return f"{value:.{precision}f}"


def _fmt_price(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"¥{value:.3f}"


def fmt_levels(levels: list[float]) -> str:
    """Format a list of price levels as a human-readable string."""
    if not levels:
        return "无"
    return " / ".join(f"¥{level:.3f}" for level in levels)


def _render_ladder_line(plan: GridPlan, index: int) -> str:
    sell_price = plan.reference_sell_ladder[index]
    rebuy_price = plan.reference_rebuy_ladder[index]
    rise_pct = _pct_change(sell_price, plan.current_price)
    fall_pct = _pct_abs_change(rebuy_price, sell_price)
    rise_text = f"{rise_pct:+.2f}%" if rise_pct is not None else "N/A"
    fall_text = f"{fall_pct:.2f}%" if fall_pct is not None else "N/A"
    trend_allowed = "是" if index < plan.trend_sell_limit_tranches else "否"
    return f"| 第{index + 1}档 | ¥{sell_price:.3f} | {rise_text} | ¥{rebuy_price:.3f} | {fall_text} | {trend_allowed} |"


def _pct_change(target: float | None, base: float | None) -> float | None:
    if target is None or base in (None, 0):
        return None
    return round((target - base) / base * 100, 2)


def _pct_abs_change(target: float | None, base: float | None) -> float | None:
    if target is None or base in (None, 0):
        return None
    return round(abs((target - base) / base * 100), 2)


def _pct_or_blank(value: float | None) -> float | str:
    """Format a percentage value for CSV output: None becomes empty string."""
    return value if value is not None else ""


def _translate_csv_row(row: dict[str, str | int | float]) -> dict[str, str | int | float]:
    translated = dict(row)
    translated["row_type"] = _translate_row_type(str(row.get("row_type", "")))
    translated["data_source"] = _translate_data_source(str(row.get("data_source", "")))
    translated["regime"] = _translate_regime(str(row.get("regime", "")))
    translated["mode"] = _translate_mode(str(row.get("mode", "")))
    translated["trend_allowed"] = "是" if str(row.get("trend_allowed", "")).lower() == "yes" else "否"
    return translated


def _translate_row_type(value: str) -> str:
    mapping = {
        "summary": "摘要",
        "standard_ladder": "标准模板",
        "trend_adjustment": "趋势修正",
    }
    return mapping.get(value, value)


def _translate_regime(value: str) -> str:
    mapping = {
        "range": "震荡区间",
        "trend_up": "多头趋势",
        "trend_down": "空头趋势",
        "disabled": "暂不可用",
    }
    return mapping.get(value, value)


def _translate_mode(value: str) -> str:
    mapping = {
        "range_grid": "区间网格",
        "trend_trim": "上涨中先卖一小部分",
        "trend_avoid": "下跌趋势先观望",
        "disabled": "暂不可用",
        "trend_up": "多头趋势",
        "trend_down": "空头趋势",
    }
    return mapping.get(value, value)


def _translate_data_source(value: str) -> str:
    mapping = {
        "api": "雪球 API",
        "akshare": "AKShare",
        "local": "本地缓存",
        "replay": "回放数据",
        "missing": "缺失",
    }
    return mapping.get(value, value)


# ---------------------------------------------------------------------------
# HTML dashboard
# ---------------------------------------------------------------------------

def default_html_report_path(plan: GridPlan) -> Path:
    """Return the fixed (always-overwritten) HTML dashboard path."""
    return project_path("output", "atr_grid.html")


def write_html_report(plan: GridPlan, target: str | Path | None = None) -> Path:
    """Write the HTML dashboard for *plan*, loading paper state if available."""
    path = Path(target) if target is not None else default_html_report_path(plan)
    path.parent.mkdir(parents=True, exist_ok=True)
    paper_state = _load_paper_state(plan.symbol)
    path.write_text(render_html(plan, paper_state=paper_state), encoding="utf-8")
    return path


def _load_paper_state(symbol: str) -> dict | None:
    state_path = project_path("output", "paper_logs", f"{symbol}_state.json")
    if not state_path.exists():
        return None
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def render_html(plan: GridPlan, *, paper_state: dict | None = None) -> str:
    """Render a self-contained HTML trading dashboard for *plan*."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    snap = plan.snapshot

    # Price change vs last close
    price_pct = _pct_change(plan.current_price, plan.last_close)
    price_pct_str = f"{price_pct:+.2f}%" if price_pct is not None else ""
    price_color = "#22c55e" if (price_pct or 0) >= 0 else "#f87171"

    regime_label = _translate_regime(plan.regime)
    mode_label = _translate_mode(plan.mode)

    regime_badge = _html_badge(regime_label, "#1f2937", "#6b7280")
    mode_badge = _html_badge_mode(mode_label, plan.mode)

    # Action card
    action_steps_html = "".join(
        f'<p style="margin:8px 0;color:#d1d5db;line-height:1.6">{s}</p>'
        for s in plan.action_steps
    )
    action_numbers_html = _html_action_numbers(plan)

    # Sell ladder table
    ladder_rows_html = _html_ladder_rows(plan)

    # Indicators
    indicators_html = _html_indicators(snap)

    # Risk boundaries
    risk_html = _html_risk(plan)

    # Warnings
    warnings_html = ""
    if plan.warnings:
        items = "".join(f'<li style="margin:4px 0">{w}</li>' for w in plan.warnings)
        warnings_html = f'''
<div class="card" style="border-color:#f87171aa">
  <div style="color:#f87171;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px">⚠️ 告警</div>
  <ul style="color:#fca5a5;padding-left:20px;font-size:14px">{items}</ul>
</div>'''

    # Paper portfolio section
    paper_html = _html_paper(paper_state, plan) if paper_state else ""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{plan.symbol} ATR 网格</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #0f172a; color: #f1f5f9; font-family: -apple-system, "PingFang SC", sans-serif; padding: 16px; }}
  .card {{ background: #1e293b; border-radius: 14px; padding: 20px; margin-bottom: 16px; border: 1px solid #334155; }}
  .label {{ color: #64748b; font-size: 12px; text-transform: uppercase; letter-spacing: .5px; margin-bottom: 4px; }}
  table {{ width: 100%; border-collapse: collapse; }}
  tr + tr {{ border-top: 1px solid #1f2937; }}
  .meta {{ color: #475569; font-size: 12px; }}
  @media (max-width: 480px) {{ .two-col {{ flex-direction: column; }} }}
</style>
</head>
<body>

<!-- Header -->
<div class="card">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px">
    <div>
      <div style="font-size:13px;color:#64748b;margin-bottom:4px">{plan.symbol}</div>
      <div style="font-size:30px;font-weight:800;color:#f8fafc;letter-spacing:-1px">
        ¥{plan.current_price:.3f}
        <span style="color:{price_color};font-size:14px;margin-left:8px">{price_pct_str}</span>
      </div>
      <div style="margin-top:8px;display:flex;gap:8px;flex-wrap:wrap">
        {regime_badge}
        {mode_badge}
      </div>
    </div>
    <div style="text-align:right">
      <div class="meta">最后交易日</div>
      <div style="color:#94a3b8;font-size:14px">{plan.last_trade_date}</div>
      <div class="meta" style="margin-top:4px">更新于 {now_str}</div>
      <div class="meta">来源: {plan.data_source}</div>
    </div>
  </div>
</div>

<!-- Action Card -->
<div class="card" style="border-color:#f97316aa">
  <div style="color:#fb923c;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;margin-bottom:10px">📋 下一步操作</div>
  <div style="font-size:17px;color:#fef3c7;font-weight:600;line-height:1.5">{plan.headline_action}</div>
  {action_numbers_html}
  <div style="margin-top:16px;padding-top:16px;border-top:1px solid #374151">
    {action_steps_html}
  </div>
</div>

<!-- Sell Ladder -->
<div class="card">
  <div style="color:#94a3b8;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;margin-bottom:12px">
    📊 卖出梯队（参考 {plan.reference_position_shares} 股模板 · 每档 {plan.reference_tranche_shares} 股）
  </div>
  <table>
    <thead>
      <tr style="background:#111827">
        <th style="padding:8px 14px;color:#6b7280;font-weight:500;text-align:left;font-size:12px">档位</th>
        <th style="padding:8px 14px;color:#6b7280;font-weight:500;text-align:left;font-size:12px">卖出价</th>
        <th style="padding:8px 14px;color:#6b7280;font-weight:500;text-align:left;font-size:12px">较现价</th>
        <th style="padding:8px 14px;color:#6b7280;font-weight:500;text-align:left;font-size:12px">接回价</th>
        <th style="padding:8px 14px;color:#6b7280;font-weight:500;text-align:left;font-size:12px">状态</th>
      </tr>
    </thead>
    <tbody>
      {ladder_rows_html}
    </tbody>
  </table>
</div>

<!-- Indicators + Risk -->
<div class="two-col" style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
  {indicators_html}
  {risk_html}
</div>

{paper_html}
{warnings_html}

<!-- Footer -->
<div style="text-align:center;margin-top:20px;color:#374151;font-size:12px">
  ATR Grid MVP · {plan.symbol} · {now_str}
</div>

</body>
</html>"""


def _html_badge(label: str, bg: str, fg: str) -> str:
    return (
        f'<span style="background:{bg};color:{fg};padding:3px 10px;border-radius:12px;'
        f'font-size:13px;font-weight:600;border:1px solid {fg}40">{label}</span>'
    )


def _html_badge_mode(label: str, mode: str) -> str:
    if mode == "trend_trim":
        return _html_badge(label, "#431407", "#fb923c")
    if mode == "range_grid":
        return _html_badge(label, "#0c1a40", "#60a5fa")
    if mode == "trend_avoid":
        return _html_badge(label, "#1c1917", "#a8a29e")
    return _html_badge(label, "#1f2937", "#6b7280")


def _html_action_numbers(plan: GridPlan) -> str:
    sell_price = plan.primary_sell
    rebuy_price = plan.primary_buy
    if sell_price is None and rebuy_price is None:
        return ""
    parts = []
    if sell_price is not None:
        pct = _pct_change(sell_price, plan.current_price)
        pct_str = f"{pct:+.2f}%" if pct is not None else ""
        parts.append(
            f'<div style="background:#1c2a3a;border-radius:10px;padding:14px 20px;min-width:120px">'
            f'<div style="color:#60a5fa;font-size:12px;margin-bottom:4px">建议卖出</div>'
            f'<div style="color:#fb923c;font-size:22px;font-weight:700">{plan.trend_sell_limit_shares} 股</div>'
            f'<div style="color:#9ca3af;font-size:12px">在 ¥{sell_price:.3f} {pct_str}</div>'
            f'</div>'
        )
    if rebuy_price is not None:
        parts.append(
            f'<div style="background:#1c2a3a;border-radius:10px;padding:14px 20px;min-width:120px">'
            f'<div style="color:#60a5fa;font-size:12px;margin-bottom:4px">接回价位</div>'
            f'<div style="color:#22c55e;font-size:22px;font-weight:700">¥{rebuy_price:.3f}</div>'
            f'<div style="color:#9ca3af;font-size:12px">回落后接</div>'
            f'</div>'
        )
    return (
        f'<div style="display:flex;gap:20px;margin-top:16px;flex-wrap:wrap">'
        + "".join(parts)
        + "</div>"
    )


def _html_ladder_rows(plan: GridPlan) -> str:
    rows = []
    sell_ladder = plan.reference_sell_ladder
    rebuy_ladder = plan.reference_rebuy_ladder
    # Find first rung that hasn't been passed yet
    current = plan.current_price
    first_active = next(
        (i for i, sp in enumerate(sell_ladder) if current < sp),
        len(sell_ladder),
    )
    for i, sell_price in enumerate(sell_ladder):
        rebuy = rebuy_ladder[i] if i < len(rebuy_ladder) else None
        pct = _pct_change(sell_price, current)
        pct_str = f"{pct:+.2f}%" if pct is not None else ""
        pct_color = "#22c55e" if (pct or 0) >= 0 else "#f87171"
        rebuy_str = f"¥{rebuy:.3f}" if rebuy is not None else "—"

        if i < first_active:
            bg = "#0a1a0f"
            border = "#22c55e"
            status_html = '<span style="color:#22c55e;font-weight:700;font-size:12px">✅ 已过</span>'
            pct_color = "#6b7280"
            pct_str = "已过"
        elif i == first_active:
            bg = "#431407"
            border = "#f97316"
            status_html = '<span style="color:#fb923c;font-weight:700;font-size:12px">▶ 当前</span>'
        else:
            bg = "#111827"
            border = "transparent"
            status_html = '<span style="color:#374151;font-size:12px">等待</span>'

        rows.append(
            f'<tr style="background:{bg};border-left:3px solid {border}">'
            f'<td style="padding:10px 14px;color:#9ca3af">第{i + 1}档</td>'
            f'<td style="padding:10px 14px;color:#f3f4f6;font-weight:600;font-size:16px">¥{sell_price:.3f}</td>'
            f'<td style="padding:10px 14px;color:{pct_color}">{pct_str}</td>'
            f'<td style="padding:10px 14px;color:#60a5fa">{rebuy_str}</td>'
            f'<td style="padding:10px 14px">{status_html}</td>'
            f'</tr>'
        )
    return "\n".join(rows)


def _html_indicators(snap) -> str:
    rows = [
        ("MA20", snap.ma20, 3),
        ("MA60", snap.ma60, 3),
        ("ATR14", snap.atr14, 3),
        ("BB上轨", snap.bb_upper, 3),
        ("BB中轨", snap.bb_middle, 3),
        ("BB下轨", snap.bb_lower, 3),
    ]
    cells = "".join(
        f'<div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #1e293b">'
        f'<span style="color:#64748b;font-size:13px">{label}</span>'
        f'<span style="color:#e2e8f0;font-size:13px;font-weight:600">¥{_fmt(val, prec)}</span>'
        f'</div>'
        for label, val, prec in rows
    )
    return (
        '<div class="card">'
        '<div style="color:#94a3b8;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;margin-bottom:12px">📈 技术指标</div>'
        + cells
        + "</div>"
    )


def _html_risk(plan: GridPlan) -> str:
    low = f"¥{plan.lower_invalidation:.3f}" if plan.lower_invalidation else "N/A"
    high = f"¥{plan.upper_breakout:.3f}" if plan.upper_breakout else "N/A"
    return f'''<div class="card">
  <div style="color:#94a3b8;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;margin-bottom:12px">⚠️ 风险边界</div>
  <div style="margin-bottom:16px;padding:14px;background:#450a0a;border-radius:10px;border:1px solid #7f1d1d">
    <div style="color:#f87171;font-size:12px;margin-bottom:4px">下沿失效（止损线）</div>
    <div style="color:#fca5a5;font-size:24px;font-weight:700">{low}</div>
  </div>
  <div style="padding:14px;background:#14532d;border-radius:10px;border:1px solid #166534">
    <div style="color:#4ade80;font-size:12px;margin-bottom:4px">上沿突破（趋势确认）</div>
    <div style="color:#86efac;font-size:24px;font-weight:700">{high}</div>
  </div>
</div>'''


def _html_paper(state: dict, plan: GridPlan) -> str:
    shares = state.get("shares", 0)
    cash = state.get("cash", 0.0)
    initial_shares = state.get("initial_shares", 0)
    initial_price = state.get("initial_price", 0.0)
    price = plan.current_price
    equity = shares * price + cash
    initial_equity = initial_shares * initial_price
    float_pnl = equity - initial_equity
    float_pnl_color = "#22c55e" if float_pnl >= 0 else "#f87171"
    float_pnl_str = f"{'+' if float_pnl >= 0 else ''}¥{float_pnl:.2f}"
    trades = state.get("trades_count", 0)
    return f'''<div class="card">
  <div style="color:#94a3b8;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;margin-bottom:12px">📂 模拟持仓（Paper）</div>
  <div style="display:flex;gap:12px;flex-wrap:wrap">
    <div style="background:#1c2a3a;border-radius:8px;padding:12px 16px;flex:1;min-width:100px">
      <div style="color:#64748b;font-size:11px;margin-bottom:2px">持仓</div>
      <div style="color:#e2e8f0;font-size:18px;font-weight:700">{shares} 股</div>
    </div>
    <div style="background:#1c2a3a;border-radius:8px;padding:12px 16px;flex:1;min-width:100px">
      <div style="color:#64748b;font-size:11px;margin-bottom:2px">现金</div>
      <div style="color:#e2e8f0;font-size:18px;font-weight:700">¥{cash:.0f}</div>
    </div>
    <div style="background:#1c2a3a;border-radius:8px;padding:12px 16px;flex:1;min-width:100px">
      <div style="color:#64748b;font-size:11px;margin-bottom:2px">净值</div>
      <div style="color:#e2e8f0;font-size:18px;font-weight:700">¥{equity:.0f}</div>
    </div>
    <div style="background:#1c2a3a;border-radius:8px;padding:12px 16px;flex:1;min-width:100px">
      <div style="color:#64748b;font-size:11px;margin-bottom:2px">浮盈</div>
      <div style="color:{float_pnl_color};font-size:18px;font-weight:700">{float_pnl_str}</div>
    </div>
    <div style="background:#1c2a3a;border-radius:8px;padding:12px 16px;flex:1;min-width:100px">
      <div style="color:#64748b;font-size:11px;margin-bottom:2px">交易次数</div>
      <div style="color:#e2e8f0;font-size:18px;font-weight:700">{trades}</div>
    </div>
  </div>
</div>'''
