"""Unit tests for pure helpers in atr_grid.engine."""

from __future__ import annotations

import pandas as pd
import pytest

from atr_grid.config import GridConfig
from atr_grid.engine import (
    _build_reference_ladder,
    _build_grid_diagnostics,
    _effective_step,
    _generate_buy_levels,
    _generate_sell_levels,
    _suggest_tactical_shares,
    _suggest_trim_shares,
    quantize_price,
)


class TestQuantizePrice:
    def test_half_up_rounds_up_at_midpoint(self):
        assert quantize_price(1.2345, 3) == 1.235
        assert quantize_price(1.2355, 3) == 1.236

    def test_zero_precision(self):
        assert quantize_price(12.6, 0) == 13.0
        assert quantize_price(12.4, 0) == 12.0

    def test_negative_precision_not_supported_but_handled(self):
        # Decimal scaleb(-0) == Decimal(1), so precision=0 still works; guard test for 3 precision
        assert quantize_price(0.0005, 3) == 0.001

    def test_returns_float_type(self):
        value = quantize_price(1.0, 3)
        assert isinstance(value, float)


class TestEffectiveStep:
    def test_atr_within_bounds_returns_atr(self):
        # band width = 10, min_step = 10/8 = 1.25, max_step = 10/3 ≈ 3.33
        # atr = 2 is within, should return 2
        cfg = GridConfig()
        assert _effective_step(atr14=2.0, lower=10.0, upper=20.0, precision=3, cfg=cfg) == 2.0

    def test_atr_below_min_clamped_to_min(self):
        # band width = 10, min_step = 1.25
        cfg = GridConfig()
        step = _effective_step(atr14=0.5, lower=10.0, upper=20.0, precision=3, cfg=cfg)
        assert step == 1.25

    def test_atr_above_max_clamped_to_max(self):
        # band width = 10, max_step = 10/3 ≈ 3.333
        cfg = GridConfig()
        step = _effective_step(atr14=5.0, lower=10.0, upper=20.0, precision=3, cfg=cfg)
        assert step == pytest.approx(3.333, abs=0.001)

    def test_respects_custom_config_fractions(self):
        cfg = GridConfig(step_min_fraction=0.1, step_max_fraction=0.5)
        # band 10, min=1, max=5, atr=3 → 3
        assert _effective_step(atr14=3.0, lower=10.0, upper=20.0, precision=3, cfg=cfg) == 3.0


class TestGridDiagnostics:
    def test_flags_fast_atr_rise_and_explains_step_change(self):
        frame = pd.DataFrame(
            {
                "close": [14.0] * 6,
                "atr14": [0.8, 0.9, 1.0, 1.0, 1.0, 1.2],
                "bb_lower": [10.0] * 6,
                "bb_middle": [14.0] * 6,
                "bb_upper": [18.0] * 6,
                "ma20": [14.0] * 6,
                "ma60": [13.5] * 6,
            }
        )

        diagnostics = _build_grid_diagnostics(frame, precision=3)

        assert diagnostics.atr_change_3d_pct == 20.0
        assert diagnostics.atr_change_5d_pct == 50.0
        assert diagnostics.previous_step == 1.0
        assert diagnostics.step_change_pct == 20.0
        assert "波动明显抬升" in diagnostics.volatility_note
        assert "网格间距从 ¥1.000 调到 ¥1.200" in diagnostics.spacing_note


class TestGenerateBuyLevels:
    def test_three_levels_within_lower_bound(self):
        levels = _generate_buy_levels(center=100.0, step=2.0, lower=90.0, precision=3)
        assert levels == [98.0, 96.0, 94.0]

    def test_level_below_lower_is_dropped(self):
        # center=100, step=5, levels would be 95/90/85, but lower=92 drops the last two
        levels = _generate_buy_levels(center=100.0, step=5.0, lower=92.0, precision=3)
        assert levels == [95.0]

    def test_all_below_lower_returns_empty(self):
        levels = _generate_buy_levels(center=100.0, step=10.0, lower=99.0, precision=3)
        assert levels == []

    def test_respects_custom_grid_level_count(self):
        cfg = GridConfig(grid_level_count=5)
        levels = _generate_buy_levels(center=100.0, step=1.0, lower=90.0, precision=3, cfg=cfg)
        assert len(levels) == 5


class TestGenerateSellLevels:
    def test_three_levels_within_upper_bound(self):
        levels = _generate_sell_levels(center=100.0, step=2.0, upper=110.0, precision=3)
        assert levels == [102.0, 104.0, 106.0]

    def test_level_above_upper_is_dropped(self):
        levels = _generate_sell_levels(center=100.0, step=5.0, upper=108.0, precision=3)
        assert levels == [105.0]

    def test_all_above_upper_returns_empty(self):
        levels = _generate_sell_levels(center=100.0, step=10.0, upper=101.0, precision=3)
        assert levels == []


class TestSuggestTrimShares:
    def test_rounds_down_to_lot_size(self):
        # 1000 * 0.10 = 100, already aligned
        assert _suggest_trim_shares(1000) == 100

    def test_250_shares_trims_to_zero_not_wrong_lot(self):
        # 250 * 0.10 = 25, // 100 = 0, * 100 = 0
        assert _suggest_trim_shares(250) == 0

    def test_zero_shares(self):
        assert _suggest_trim_shares(0) == 0

    def test_negative_shares_returns_zero(self):
        assert _suggest_trim_shares(-100) == 0

    def test_respects_custom_trim_ratio(self):
        cfg = GridConfig(trim_ratio=0.2)
        assert _suggest_trim_shares(1000, cfg=cfg) == 200


class TestSuggestTacticalShares:
    def test_rounds_down_to_lot_size(self):
        # 1000 * 0.20 = 200, aligned
        assert _suggest_tactical_shares(1000) == 200

    def test_below_one_lot_returns_zero(self):
        assert _suggest_tactical_shares(400) == 0

    def test_zero_shares(self):
        assert _suggest_tactical_shares(0) == 0


class TestBuildReferenceLadder:
    def test_produces_ladder_with_default_three_tranches(self):
        sell, rebuy = _build_reference_ladder(anchor_sell=100.0, atr14=2.0, precision=3)
        assert len(sell) == 3
        assert len(rebuy) == 3
        # First sell == anchor, rebuy = anchor - atr
        assert sell[0] == 100.0
        assert rebuy[0] == 98.0
        # Each subsequent sell goes up by atr
        assert sell[1] == 102.0
        assert sell[2] == 104.0

    def test_rebuy_floor_never_negative(self):
        # atr huge → rebuy would be negative without floor
        sell, rebuy = _build_reference_ladder(anchor_sell=1.0, atr14=100.0, precision=3)
        for price in rebuy:
            assert price > 0
