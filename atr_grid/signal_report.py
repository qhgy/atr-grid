"""Render daily signal as static HTML page."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path

from core.paths import project_path

from .signal_engine import SignalResult

_BJT = timezone(timedelta(hours=8))


def render_signal_html(sig: SignalResult, snapshot_dates: list[str] | None = None) -> str:
    """Render a SignalResult into a standalone HTML page."""
    today = datetime.now(_BJT).strftime("%Y-%m-%d")

    # Snapshot picker
    snap_nav = ""
    if snapshot_dates:
        options = "".join(f'<option value="{d}">{d}</option>' for d in snapshot_dates[:30])
        snap_nav = f'''
<div class="card" style="display:flex;align-items:center;gap:12px;padding:12px 20px">
  <span style="color:#64748b;font-size:13px">历史快照：</span>
  <select onchange="window.location.href='snapshots/'+this.value+'.html'"
    style="background:#0f172a;color:#94a3b8;border:1px solid #334155;border-radius:6px;padding:4px 8px;font-size:13px">
    <option value="">— 选择日期 —</option>
    {options}
  </select>
  <span style="color:#374151;font-size:11px;margin-left:auto">今日：{today}</span>
</div>'''

    # RSI color
    rsi_color = "#22c55e" if sig.rsi_state.startswith("超卖") else "#f87171" if sig.rsi_state.startswith("超买") else "#94a3b8"
    rsi_val = f"{sig.rsi14:.1f}" if sig.rsi14 is not None else "N/A"

    # NVDA display
    if sig.nvda_ret is not None:
        nvda_color = "#f87171" if sig.nvda_ret < -3 else "#22c55e" if sig.nvda_ret > 0 else "#94a3b8"
        nvda_text = f"{sig.nvda_ret:+.2f}%"
    else:
        nvda_color = "#475569"
        nvda_text = "无数据/已关闭"

    # Risk action styling
    risk_border = "#334155"
    risk_bg = "#1e293b"
    if sig.risk_action == "全部清仓+冻结2天":
        risk_border = "#ef4444"
        risk_bg = "#450a0a"
    elif sig.risk_action == "减仓到60%":
        risk_border = "#f59e0b"
        risk_bg = "#431407"

    # Build order tables
    buy_rows = _build_order_rows(sig.buy_orders, sig.close, "buy")
    sell_rows = _build_order_rows(sig.sell_orders, sig.close, "sell")

    # Warnings
    warn_html = ""
    if sig.warnings:
        items = "".join(f'<div style="color:#fbbf24;font-size:13px;padding:4px 0">{w}</div>' for w in sig.warnings)
        warn_html = f'<div class="card" style="border-color:#f59e0b">{items}</div>'

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>515880 每日信号 · {sig.date}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #0f172a; color: #f1f5f9; font-family: -apple-system, "PingFang SC", sans-serif; padding: 16px; max-width: 700px; margin: 0 auto; }}
  .card {{ background: #1e293b; border-radius: 14px; padding: 20px; margin-bottom: 16px; border: 1px solid #334155; }}
  .label {{ color: #64748b; font-size: 12px; text-transform: uppercase; letter-spacing: .5px; margin-bottom: 4px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
  th {{ padding: 8px 12px; color: #6b7280; font-weight: 500; text-align: left; font-size: 12px; background: #111827; }}
  td {{ padding: 8px 12px; }}
  tr + tr {{ border-top: 1px solid #1f2937; }}
  .stat {{ background: #0f172a; border-radius: 10px; padding: 14px 18px; flex: 1; min-width: 100px; }}
  .stat-label {{ color: #64748b; font-size: 11px; margin-bottom: 2px; }}
  .stat-value {{ font-size: 20px; font-weight: 700; }}
</style>
</head>
<body>
{snap_nav}
{warn_html}

<div class="card">
  <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px">
    <div>
      <div style="font-size:13px;color:#64748b;margin-bottom:4px">SH515880 · MacroRsi14HardV2</div>
      <div style="font-size:28px;font-weight:800;color:#f8fafc;letter-spacing:-1px">¥{sig.close:.3f}</div>
    </div>
    <div style="text-align:right">
      <div style="color:#475569;font-size:12px">基准日期</div>
      <div style="color:#94a3b8;font-size:14px">{sig.date}</div>
      <div style="color:#475569;font-size:11px;margin-top:4px">生成于 {sig.generated_at}</div>
      <div style="color:#475569;font-size:11px">来源: {sig.data_source}</div>
    </div>
  </div>
</div>

<div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px">
  <div class="stat"><div class="stat-label">ATR14</div><div class="stat-value" style="color:#60a5fa">{sig.atr14:.4f}</div></div>
  <div class="stat"><div class="stat-label">RSI14</div><div class="stat-value" style="color:{rsi_color}">{rsi_val}</div><div style="color:{rsi_color};font-size:11px">{sig.rsi_state}</div></div>
  <div class="stat"><div class="stat-label">NVDA涨跌</div><div class="stat-value" style="color:{nvda_color}">{nvda_text}</div></div>
  <div class="stat"><div class="stat-label">网格间距</div><div class="stat-value" style="color:#94a3b8">{sig.grid_step:.4f}</div><div style="color:#475569;font-size:11px">{sig.grid_step / sig.close * 100:.2f}%</div></div>
</div>

<div class="card" style="border-color:{risk_border};background:{risk_bg}">
  <div style="color:#94a3b8;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;margin-bottom:8px">风控信号</div>
  <div style="font-size:20px;font-weight:700;color:{'#f87171' if sig.risk_action != '正常' else '#22c55e'}">{sig.risk_action}</div>
  {"<div style='color:#fbbf24;font-size:14px;margin-top:8px'>明天开盘全部卖出，之后冻结 2 天不操作</div>" if sig.risk_action == "全部清仓+冻结2天" else ""}
</div>'''

    if sig.risk_action != "全部清仓+冻结2天":
        html += f'''
<div class="card">
  <div style="color:#22c55e;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;margin-bottom:12px">买入挂单（低于收盘价）</div>
  <table>
    <thead><tr><th>档位</th><th>挂单价</th><th>数量</th><th>距收盘</th><th>备注</th></tr></thead>
    <tbody>{buy_rows}</tbody>
  </table>
</div>

<div class="card">
  <div style="color:#f87171;font-size:12px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;margin-bottom:12px">卖出挂单（高于收盘价）</div>
  <table>
    <thead><tr><th>档位</th><th>挂单价</th><th>数量</th><th>距收盘</th><th>备注</th></tr></thead>
    <tbody>{sell_rows}</tbody>
  </table>
</div>'''

    html += f'''
<div class="card" style="border-color:#334155">
  <div style="color:#64748b;font-size:12px;margin-bottom:8px">操作提醒</div>
  <div style="color:#94a3b8;font-size:13px;line-height:1.8">
    1. 以上价格基于 {sig.date} 收盘数据，信号用于次日挂单<br>
    2. 次日开盘前挂单，收盘后撤未成交单<br>
    3. 实际下单前确认持仓数量，卖出不超过持仓<br>
    4. ETF 最小交易单位 100 股
  </div>
</div>

<div style="text-align:center;margin-top:20px;color:#374151;font-size:11px">
  MacroRsi14HardV2 · SH515880 · {sig.generated_at}
</div>
</body>
</html>'''

    return html


def _build_order_rows(orders, close: float, side: str) -> str:
    rows = []
    for o in orders:
        pct = (o.price - close) / close * 100 if close > 0 else 0
        pct_color = "#22c55e" if pct < 0 else "#f87171"
        prefix = "B" if side == "buy" else "S"
        note_html = f'<span style="color:#fbbf24;font-size:12px">{o.note}</span>' if o.note else ""
        rows.append(
            f'<tr>'
            f'<td style="color:#9ca3af">{prefix}{o.level}</td>'
            f'<td style="color:#f3f4f6;font-weight:600">¥{o.price:.3f}</td>'
            f'<td style="color:#e2e8f0">{o.shares}</td>'
            f'<td style="color:{pct_color}">{pct:+.2f}%</td>'
            f'<td>{note_html}</td>'
            f'</tr>'
        )
    return "".join(rows)


def write_signal_html(sig: SignalResult) -> Path:
    """Write signal HTML to docs/signal/ and a dated snapshot."""
    today = datetime.now(_BJT).strftime("%Y-%m-%d")

    out_dir = project_path("output", "signal")
    out_dir.mkdir(parents=True, exist_ok=True)
    snap_dir = out_dir / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)

    existing_dates = sorted(
        [p.stem for p in snap_dir.glob("????-??-??.html") if p.stem != today],
        reverse=True,
    )

    html = render_signal_html(sig, snapshot_dates=existing_dates)
    main_path = out_dir / "index.html"
    main_path.write_text(html, encoding="utf-8")

    snap_html = html.replace("href='snapshots/", "href='")
    snap_path = snap_dir / f"{today}.html"
    snap_path.write_text(snap_html, encoding="utf-8")

    return main_path
