"""HTML renderer for the multi-symbol ATR Grid dashboard."""

from __future__ import annotations

from html import escape
from typing import Any


def render_multi_dashboard(
    plans: list[Any],
    *,
    paper_states: dict[str, dict | None],
    near_level: dict[str, bool],
    now_str: str,
    today: str,
    snapshot_dates: list[str] | None = None,
    snapshot_prefix: str = "snapshots/",
) -> str:
    """Render a self-contained operational dashboard for multiple symbols."""
    snapshot_dates = snapshot_dates or []
    alert_count = sum(1 for plan in plans if near_level.get(plan.symbol, False))
    grid_count = sum(1 for plan in plans if getattr(plan, "grid_enabled", False))
    weak_count = sum(1 for plan in plans if getattr(plan, "regime", "") == "trend_down")

    summary_rows = "\n".join(
        _summary_row(plan, near=near_level.get(plan.symbol, False))
        for plan in plans
    )
    instruments = "\n".join(
        _instrument_card(
            plan,
            paper_state=paper_states.get(plan.symbol),
            near=near_level.get(plan.symbol, False),
        )
        for plan in plans
    )
    snapshot_nav = _snapshot_nav(snapshot_dates, today=today, prefix=snapshot_prefix)

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ATR Grid Dev Dashboard</title>
<style>
  :root {{
    --bg: #f4f5f7;
    --surface: #ffffff;
    --ink: #161616;
    --muted: #6b6f76;
    --line: #d8dbe0;
    --line-strong: #b8bec7;
    --green: #0f8a5f;
    --red: #c2413a;
    --amber: #b7791f;
    --blue: #2457c5;
    --cyan: #087f8c;
    --black: #0b0d10;
  }}
  * {{ box-sizing: border-box; }}
  html {{ scroll-behavior: smooth; }}
  body {{
    margin: 0;
    background:
      linear-gradient(90deg, rgba(11,13,16,.04) 1px, transparent 1px),
      linear-gradient(180deg, rgba(11,13,16,.035) 1px, transparent 1px),
      var(--bg);
    background-size: 28px 28px;
    color: var(--ink);
    font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif;
    font-size: 15px;
    line-height: 1.45;
  }}
  a {{ color: inherit; }}
  .shell {{ width: min(1220px, calc(100vw - 28px)); margin: 0 auto; padding: 18px 0 34px; }}
  .topbar {{
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 16px;
    align-items: start;
    padding: 18px 0 16px;
    border-bottom: 2px solid var(--black);
  }}
  h1 {{ margin: 0; font-size: 28px; line-height: 1.1; font-weight: 900; }}
  .subtitle {{ margin-top: 6px; color: var(--muted); font-size: 13px; }}
  .branch {{
    display: inline-flex;
    align-items: center;
    gap: 8px;
    padding: 6px 10px;
    border: 1px solid var(--black);
    border-radius: 999px;
    background: var(--black);
    color: #fff;
    font-size: 12px;
    font-weight: 800;
  }}
  .dot {{ width: 7px; height: 7px; border-radius: 50%; background: var(--green); display: inline-block; }}
  .meta-line {{ display: flex; gap: 10px; flex-wrap: wrap; justify-content: flex-end; color: var(--muted); font-size: 12px; }}
  .metrics {{
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 10px;
    margin: 16px 0;
  }}
  .metric {{
    min-height: 78px;
    padding: 12px 14px;
    background: var(--surface);
    border: 1px solid var(--line-strong);
    border-radius: 8px;
    box-shadow: 0 1px 0 rgba(0,0,0,.05);
  }}
  .metric-label {{ color: var(--muted); font-size: 12px; }}
  .metric-value {{ margin-top: 6px; font-size: 26px; font-weight: 900; line-height: 1; }}
  .metric-note {{ margin-top: 8px; color: var(--muted); font-size: 12px; }}
  .toolbar {{
    display: flex;
    justify-content: space-between;
    gap: 12px;
    align-items: center;
    margin: 16px 0;
  }}
  .snapshot {{
    display: inline-flex;
    align-items: center;
    gap: 8px;
    color: var(--muted);
    font-size: 12px;
  }}
  select {{
    height: 32px;
    border: 1px solid var(--line-strong);
    border-radius: 6px;
    background: #fff;
    color: var(--ink);
    padding: 0 8px;
    font: inherit;
    font-size: 12px;
  }}
  .table-wrap {{
    overflow-x: auto;
    background: var(--surface);
    border: 1px solid var(--line-strong);
    border-radius: 8px;
  }}
  table {{ width: 100%; border-collapse: collapse; min-width: 760px; }}
  th, td {{ padding: 10px 12px; text-align: left; border-bottom: 1px solid var(--line); vertical-align: middle; }}
  th {{ color: var(--muted); font-size: 12px; font-weight: 800; background: #f9fafb; }}
  tr:last-child td {{ border-bottom: 0; }}
  tbody tr {{ cursor: pointer; }}
  tbody tr:hover {{ background: #f0f7f6; }}
  .symbol {{ font-weight: 900; color: var(--black); }}
  .price {{ font-variant-numeric: tabular-nums; font-weight: 800; }}
  .change-up {{ color: var(--green); }}
  .change-down {{ color: var(--red); }}
  .chip {{
    display: inline-flex;
    align-items: center;
    min-height: 24px;
    padding: 3px 8px;
    border-radius: 999px;
    border: 1px solid var(--line-strong);
    background: #fff;
    color: var(--ink);
    font-size: 12px;
    font-weight: 800;
    white-space: nowrap;
  }}
  .chip.green {{ border-color: rgba(15,138,95,.35); color: var(--green); background: #eef9f4; }}
  .chip.red {{ border-color: rgba(194,65,58,.35); color: var(--red); background: #fff1ef; }}
  .chip.amber {{ border-color: rgba(183,121,31,.35); color: var(--amber); background: #fff7e8; }}
  .chip.blue {{ border-color: rgba(36,87,197,.35); color: var(--blue); background: #eef4ff; }}
  .section-title {{ margin: 24px 0 10px; font-size: 13px; color: var(--muted); font-weight: 900; }}
  .instrument {{
    scroll-margin-top: 14px;
    margin-top: 14px;
    background: var(--surface);
    border: 1px solid var(--line-strong);
    border-radius: 8px;
    box-shadow: 0 1px 0 rgba(0,0,0,.05);
  }}
  .instrument-head {{
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 14px;
    padding: 16px;
    border-bottom: 1px solid var(--line);
  }}
  .instrument-title {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }}
  .instrument-title h2 {{ margin: 0; font-size: 22px; line-height: 1.1; }}
  .headline {{ margin-top: 8px; max-width: 760px; color: #30343a; font-weight: 700; }}
  .quote {{ text-align: right; }}
  .quote-price {{ font-size: 30px; font-weight: 900; line-height: 1; font-variant-numeric: tabular-nums; }}
  .quote-sub {{ margin-top: 6px; color: var(--muted); font-size: 12px; }}
  .instrument-body {{
    display: grid;
    grid-template-columns: minmax(0, 1.1fr) minmax(320px, .9fr);
    gap: 0;
  }}
  .panel {{ padding: 16px; border-right: 1px solid var(--line); }}
  .panel:last-child {{ border-right: 0; }}
  .panel-title {{ margin-bottom: 10px; font-size: 12px; color: var(--muted); font-weight: 900; }}
  .action-grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; margin-bottom: 14px; }}
  .action-cell {{ border: 1px solid var(--line); border-radius: 6px; padding: 10px; min-height: 72px; }}
  .action-cell strong {{ display: block; margin-top: 4px; font-size: 22px; line-height: 1.1; font-variant-numeric: tabular-nums; }}
  .steps {{ margin: 0; padding-left: 18px; color: #30343a; }}
  .steps li + li {{ margin-top: 6px; }}
  .band {{ position: relative; height: 22px; border-radius: 999px; background: linear-gradient(90deg, #fff1ef, #fff7e8 35%, #eef9f4 65%, #eef4ff); border: 1px solid var(--line-strong); margin: 10px 0 8px; }}
  .band-marker {{ position: absolute; top: -5px; width: 3px; height: 30px; border-radius: 3px; background: var(--black); }}
  .band-labels {{ display: flex; justify-content: space-between; color: var(--muted); font-size: 11px; }}
  .mini-grid {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; margin-top: 14px; }}
  .mini {{ border-top: 2px solid var(--line-strong); padding-top: 8px; min-height: 54px; }}
  .mini span {{ display: block; color: var(--muted); font-size: 11px; }}
  .mini strong {{ display: block; margin-top: 4px; font-size: 15px; font-variant-numeric: tabular-nums; }}
  .ladder {{ margin-top: 14px; display: grid; gap: 8px; }}
  .rung {{ display: grid; grid-template-columns: 50px 1fr 1fr auto; gap: 8px; align-items: center; min-height: 38px; border: 1px solid var(--line); border-radius: 6px; padding: 8px; }}
  .rung.current {{ border-color: rgba(183,121,31,.55); background: #fffaf0; }}
  .rung.passed {{ border-color: rgba(15,138,95,.35); background: #f3fbf7; }}
  .rung-label {{ color: var(--muted); font-size: 12px; font-weight: 800; }}
  .warn-list {{ margin: 12px 0 0; padding-left: 18px; color: var(--red); }}
  .footer {{ margin: 22px 0 0; color: var(--muted); font-size: 12px; text-align: center; }}
  @media (max-width: 860px) {{
    .topbar, .instrument-head, .instrument-body {{ grid-template-columns: 1fr; }}
    .quote, .meta-line {{ text-align: left; justify-content: flex-start; }}
    .metrics {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    .panel {{ border-right: 0; border-bottom: 1px solid var(--line); }}
    .panel:last-child {{ border-bottom: 0; }}
  }}
  @media (max-width: 560px) {{
    .shell {{ width: min(100vw - 18px, 1220px); padding-top: 8px; }}
    h1 {{ font-size: 23px; }}
    .metrics, .action-grid, .mini-grid {{ grid-template-columns: 1fr; }}
    .toolbar {{ align-items: flex-start; flex-direction: column; }}
    .rung {{ grid-template-columns: 42px 1fr; }}
    .rung-status {{ grid-column: 2; }}
  }}
</style>
</head>
<body>
<main class="shell">
  <header class="topbar">
    <div>
      <span class="branch"><span class="dot"></span> DEV BRANCH</span>
      <h1>ATR Grid Control Board</h1>
      <div class="subtitle">多标的网格决策盘 · 只显示可执行信号、风险边界和纸面账本状态</div>
    </div>
    <div class="meta-line">
      <span>生成 {escape(now_str)}</span>
      <span>数据日 {escape(today)}</span>
    </div>
  </header>

  <section class="metrics" aria-label="dashboard metrics">
    <div class="metric"><div class="metric-label">监控标的</div><div class="metric-value">{len(plans)}</div><div class="metric-note">当前分支配置</div></div>
    <div class="metric"><div class="metric-label">临近档位</div><div class="metric-value">{alert_count}</div><div class="metric-note">触发 Server 酱阈值</div></div>
    <div class="metric"><div class="metric-label">网格启用</div><div class="metric-value">{grid_count}</div><div class="metric-note">可执行区间交易</div></div>
    <div class="metric"><div class="metric-label">弱势观望</div><div class="metric-value">{weak_count}</div><div class="metric-note">trend_down 计数</div></div>
  </section>

  <div class="toolbar">
    <div class="section-title">汇总</div>
    {snapshot_nav}
  </div>

  <section class="table-wrap" aria-label="symbol summary">
    <table>
      <thead>
        <tr>
          <th>代码</th>
          <th>价格</th>
          <th>状态</th>
          <th>主卖</th>
          <th>主买</th>
          <th>动作</th>
          <th>交易日</th>
        </tr>
      </thead>
      <tbody>
        {summary_rows}
      </tbody>
    </table>
  </section>

  <div class="section-title">标的详情</div>
  {instruments}

  <div class="footer">ATR Grid · generated on dev · {escape(now_str)}</div>
</main>
</body>
</html>"""
    return _strip_trailing_spaces(html)


def _summary_row(plan: Any, *, near: bool) -> str:
    symbol = escape(plan.symbol)
    change = _pct_change(plan.current_price, plan.last_close)
    change_class = "change-up" if (change or 0) >= 0 else "change-down"
    change_text = _pct_text(change)
    sell = _price(plan.primary_sell, plan.price_precision)
    buy = _price(plan.primary_buy, plan.price_precision)
    chips = _chips(plan, near=near)
    action = escape(_compact(plan.headline_action, 34))
    return (
        f'<tr onclick="document.getElementById(\'sec-{symbol}\').scrollIntoView({{behavior:\'smooth\'}})">'
        f'<td><span class="symbol">{symbol}</span></td>'
        f'<td><span class="price">{_price(plan.current_price, plan.price_precision)}</span> '
        f'<span class="{change_class}">{change_text}</span></td>'
        f'<td>{chips}</td>'
        f'<td>{sell}</td>'
        f'<td>{buy}</td>'
        f'<td>{action}</td>'
        f'<td>{escape(str(plan.last_trade_date))}</td>'
        f'</tr>'
    )


def _instrument_card(plan: Any, *, paper_state: dict | None, near: bool) -> str:
    symbol = escape(plan.symbol)
    change = _pct_change(plan.current_price, plan.last_close)
    change_class = "change-up" if (change or 0) >= 0 else "change-down"
    steps = "".join(f"<li>{escape(step)}</li>" for step in plan.action_steps)
    warnings = _warnings(plan)
    paper = _paper_block(paper_state, plan)
    band = _price_band(plan)
    ladder = _ladder(plan)
    snap = plan.snapshot
    return f"""
  <article class="instrument" id="sec-{symbol}">
    <div class="instrument-head">
      <div>
        <div class="instrument-title">
          <h2>{symbol}</h2>
          {_chips(plan, near=near)}
        </div>
        <div class="headline">{escape(plan.headline_action)}</div>
      </div>
      <div class="quote">
        <div class="quote-price">{_price(plan.current_price, plan.price_precision)}</div>
        <div class="quote-sub"><span class="{change_class}">{_pct_text(change)}</span> · {escape(str(plan.data_source))} · {escape(str(plan.last_trade_date))}</div>
      </div>
    </div>
    <div class="instrument-body">
      <section class="panel">
        <div class="panel-title">执行面板</div>
        <div class="action-grid">
          <div class="action-cell"><span>主卖点</span><strong class="change-down">{_price(plan.primary_sell, plan.price_precision)}</strong></div>
          <div class="action-cell"><span>主买点</span><strong class="change-up">{_price(plan.primary_buy, plan.price_precision)}</strong></div>
        </div>
        <ol class="steps">{steps}</ol>
        {warnings}
        {ladder}
      </section>
      <section class="panel">
        <div class="panel-title">价格结构</div>
        {band}
        <div class="mini-grid">
          <div class="mini"><span>MA20</span><strong>{_price(getattr(snap, "ma20", None), plan.price_precision)}</strong></div>
          <div class="mini"><span>MA60</span><strong>{_price(getattr(snap, "ma60", None), plan.price_precision)}</strong></div>
          <div class="mini"><span>ATR14</span><strong>{_price(getattr(snap, "atr14", None), plan.price_precision)}</strong></div>
          <div class="mini"><span>下沿失效</span><strong class="change-down">{_price(plan.lower_invalidation, plan.price_precision)}</strong></div>
          <div class="mini"><span>上沿突破</span><strong class="change-up">{_price(plan.upper_breakout, plan.price_precision)}</strong></div>
          <div class="mini"><span>模板</span><strong>{plan.reference_position_shares} / {plan.reference_tranche_shares}</strong></div>
        </div>
        {paper}
      </section>
    </div>
  </article>"""


def _chips(plan: Any, *, near: bool) -> str:
    chips = [
        f'<span class="chip {_regime_class(plan.regime)}">{escape(_regime_label(plan.regime))}</span>',
        f'<span class="chip {_mode_class(plan.mode)}">{escape(_mode_label(plan.mode))}</span>',
    ]
    if near:
        chips.append('<span class="chip red">临近档位</span>')
    if not getattr(plan, "grid_enabled", False):
        chips.append('<span class="chip amber">网格暂停</span>')
    return "".join(chips)


def _snapshot_nav(dates: list[str], *, today: str, prefix: str) -> str:
    if not dates:
        return f'<div class="snapshot">今日 {escape(today)}</div>'
    options = "".join(f'<option value="{escape(d)}">{escape(d)}</option>' for d in dates)
    return f"""
    <label class="snapshot">
      历史快照
      <select onchange="if(this.value) window.location.href='{escape(prefix)}'+this.value+'.html'">
        <option value="">选择日期</option>
        {options}
      </select>
    </label>"""


def _price_band(plan: Any) -> str:
    snap = plan.snapshot
    lower = getattr(snap, "bb_lower", None)
    upper = getattr(snap, "bb_upper", None)
    current = plan.current_price
    if lower is None or upper is None or upper <= lower:
        return '<div class="band-labels"><span>布林带数据不足</span></div>'
    pos = (current - lower) / (upper - lower) * 100
    pos = max(0.0, min(100.0, pos))
    return f"""
        <div class="band" aria-label="price position in Bollinger band">
          <div class="band-marker" style="left:calc({pos:.2f}% - 1px)"></div>
        </div>
        <div class="band-labels">
          <span>下轨 {_price(lower, plan.price_precision)}</span>
          <span>现价 {_price(current, plan.price_precision)}</span>
          <span>上轨 {_price(upper, plan.price_precision)}</span>
        </div>"""


def _ladder(plan: Any) -> str:
    sell_ladder = list(plan.reference_sell_ladder or [])
    rebuy_ladder = list(plan.reference_rebuy_ladder or [])
    if not sell_ladder:
        return ""
    current = plan.current_price
    first_active = next((i for i, price in enumerate(sell_ladder) if current < price), len(sell_ladder))
    rows = []
    for idx, sell in enumerate(sell_ladder):
        rebuy = rebuy_ladder[idx] if idx < len(rebuy_ladder) else None
        klass = "passed" if idx < first_active else "current" if idx == first_active else ""
        status = "已过" if idx < first_active else "当前" if idx == first_active else "等待"
        rows.append(
            f'<div class="rung {klass}">'
            f'<div class="rung-label">L{idx + 1}</div>'
            f'<div><span>卖出</span><strong>{_price(sell, plan.price_precision)}</strong></div>'
            f'<div><span>接回</span><strong>{_price(rebuy, plan.price_precision)}</strong></div>'
            f'<div class="rung-status"><span class="chip">{status}</span></div>'
            f'</div>'
        )
    return '<div class="panel-title" style="margin-top:16px">卖出梯队</div><div class="ladder">' + "".join(rows) + "</div>"


def _paper_block(state: dict | None, plan: Any) -> str:
    if not state:
        return ""
    shares = int(state.get("shares", 0) or 0)
    cash = float(state.get("cash", 0.0) or 0.0)
    trades = int(state.get("trades_count", 0) or 0)
    profile = escape(str(state.get("profile", "stable")))
    equity = shares * float(plan.current_price) + cash
    return f"""
        <div class="panel-title" style="margin-top:16px">纸面账本</div>
        <div class="mini-grid">
          <div class="mini"><span>profile</span><strong>{profile}</strong></div>
          <div class="mini"><span>持仓</span><strong>{shares}</strong></div>
          <div class="mini"><span>现金</span><strong>¥{cash:.0f}</strong></div>
          <div class="mini"><span>净值</span><strong>¥{equity:.0f}</strong></div>
          <div class="mini"><span>交易</span><strong>{trades}</strong></div>
        </div>"""


def _warnings(plan: Any) -> str:
    warnings = list(getattr(plan, "warnings", []) or [])
    if not warnings:
        return ""
    items = "".join(f"<li>{escape(str(item))}</li>" for item in warnings)
    return f'<ul class="warn-list">{items}</ul>'


def _price(value: Any, precision: int = 3) -> str:
    if value is None:
        return "N/A"
    try:
        return f"¥{float(value):.{precision}f}"
    except (TypeError, ValueError):
        return "N/A"


def _pct_change(value: Any, base: Any) -> float | None:
    try:
        value_f = float(value)
        base_f = float(base)
    except (TypeError, ValueError):
        return None
    if base_f == 0:
        return None
    return (value_f - base_f) / base_f * 100


def _pct_text(value: float | None) -> str:
    return f"{value:+.2f}%" if value is not None else ""


def _compact(text: str, limit: int) -> str:
    text = str(text)
    return text if len(text) <= limit else text[:limit] + "..."


def _regime_label(regime: str) -> str:
    return {
        "trend_up": "多头趋势",
        "trend_down": "弱势观望",
        "range": "震荡区间",
        "disabled": "数据停用",
    }.get(regime, regime or "unknown")


def _mode_label(mode: str) -> str:
    return {
        "trend_trim": "锁利",
        "range_grid": "网格",
        "trend_avoid": "观望",
        "disabled": "暂停",
    }.get(mode, mode or "unknown")


def _regime_class(regime: str) -> str:
    return {
        "trend_up": "green",
        "range": "blue",
        "trend_down": "red",
        "disabled": "amber",
    }.get(regime, "")


def _mode_class(mode: str) -> str:
    return {
        "trend_trim": "amber",
        "range_grid": "blue",
        "trend_avoid": "red",
        "disabled": "amber",
    }.get(mode, "")


def _strip_trailing_spaces(html: str) -> str:
    return "\n".join(line.rstrip() for line in html.splitlines()) + "\n"
