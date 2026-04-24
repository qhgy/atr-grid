"""Unit tests for the multi-symbol dashboard renderer."""

from __future__ import annotations

from types import SimpleNamespace

from atr_grid.dashboard import render_multi_dashboard


def test_render_multi_dashboard_contains_operational_sections():
    plan = _fake_plan()

    html = render_multi_dashboard(
        [plan],
        paper_states={"TEST": {"profile": "stable", "shares": 300, "cash": 1200, "trades_count": 4}},
        near_level={"TEST": True},
        now_str="2026-04-24 21:30",
        today="2026-04-24",
        snapshot_dates=["2026-04-23"],
        snapshot_prefix="snapshots/",
    )

    assert "ATR Grid Control Board" in html
    assert "TEST" in html
    assert "执行面板" in html
    assert "价格结构" in html
    assert "纸面账本" in html
    assert "临近档位" in html
    assert "window.location.href='snapshots/'+this.value+'.html'" in html


def test_render_multi_dashboard_escapes_dynamic_text():
    plan = _fake_plan(
        headline_action="<script>alert(1)</script>",
        action_steps=["买入 <100 份>"],
        warnings=["风险 <扩大>"],
    )

    html = render_multi_dashboard(
        [plan],
        paper_states={},
        near_level={},
        now_str="2026-04-24 21:30",
        today="2026-04-24",
    )

    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "买入 &lt;100 份&gt;" in html
    assert "风险 &lt;扩大&gt;" in html


def _fake_plan(**overrides):
    fields = {
        "symbol": "TEST",
        "instrument_type": "ETF",
        "data_source": "unit",
        "current_price": 1.234,
        "last_close": 1.200,
        "last_trade_date": "2026-04-24",
        "price_precision": 3,
        "snapshot": SimpleNamespace(bb_lower=1.000, bb_upper=1.500, ma20=1.210, ma60=1.180, atr14=0.030),
        "strategy_name": "ATR Grid",
        "headline_action": "震荡区间内按主买卖点执行",
        "tactical_shares": 100,
        "action_steps": ["到主买点买入 100 份", "到主卖点卖出 100 份"],
        "reference_position_shares": 1000,
        "reference_tranche_shares": 100,
        "reference_sell_ladder": [1.300, 1.400],
        "reference_rebuy_ladder": [1.200, 1.250],
        "trend_sell_limit_tranches": 1,
        "trend_sell_limit_shares": 100,
        "trend_adjustment_note": "",
        "mode": "range_grid",
        "regime": "range",
        "grid_enabled": True,
        "reason": "",
        "center": 1.200,
        "step": 0.030,
        "primary_buy": 1.180,
        "primary_sell": 1.300,
        "prealert_buy": 1.190,
        "prealert_sell": 1.290,
        "buy_levels": [1.180],
        "sell_levels": [1.300],
        "lower_invalidation": 0.950,
        "upper_breakout": 1.550,
        "trim_shares": 0,
        "rebuy_price": 1.200,
        "shares": 1000,
        "warnings": ["注意风险"],
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)
