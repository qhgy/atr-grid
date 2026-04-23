"""Unit tests for atr_grid.regime.classify_regime."""

from __future__ import annotations

import pandas as pd

from atr_grid.indicators import IndicatorSnapshot
from atr_grid.regime import classify_regime


def _snapshot(**overrides) -> IndicatorSnapshot:
    base = dict(close=10.0, atr14=0.5, bb_upper=11.0, bb_middle=10.0, bb_lower=9.0, ma20=10.0, ma60=9.5)
    base.update(overrides)
    return IndicatorSnapshot(**base)


def _frame(ma20_series: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"ma20": ma20_series})


class TestClassifyRegime:
    def test_empty_frame_returns_disabled(self):
        result = classify_regime(pd.DataFrame(), _snapshot())
        assert result.regime == "disabled"
        assert result.grid_enabled is False

    def test_missing_atr_returns_disabled(self):
        result = classify_regime(_frame([10.0] * 10), _snapshot(atr14=None))
        assert result.regime == "disabled"

    def test_zero_atr_returns_disabled(self):
        result = classify_regime(_frame([10.0] * 10), _snapshot(atr14=0.0))
        assert result.regime == "disabled"

    def test_insufficient_ma_window(self):
        # lookback defaults to 5; fewer points → disabled
        result = classify_regime(_frame([10.0, 10.1]), _snapshot())
        assert result.regime == "disabled"
        assert "MA20" in result.reason

    def test_trend_up_when_price_above_both_ma_and_slope_steep(self):
        # ma20 monotonically increasing, slope/atr ratio crosses threshold 0.25
        ma20 = [9.0, 9.5, 10.0, 10.5, 11.0]  # slope 2.0, atr 0.5 → ratio 4.0
        result = classify_regime(
            _frame(ma20),
            _snapshot(close=12.0, ma20=11.0, ma60=10.0, atr14=0.5),
        )
        assert result.regime == "trend_up"
        assert result.grid_enabled is False

    def test_trend_down_when_price_below_both_ma_and_slope_steep(self):
        ma20 = [11.0, 10.5, 10.0, 9.5, 9.0]
        result = classify_regime(
            _frame(ma20),
            _snapshot(close=8.0, ma20=9.0, ma60=10.0, atr14=0.5),
        )
        assert result.regime == "trend_down"
        assert result.grid_enabled is False

    def test_flat_ma_returns_range(self):
        ma20 = [10.0, 10.05, 10.0, 10.05, 10.0]  # non-monotonic
        result = classify_regime(_frame(ma20), _snapshot())
        assert result.regime == "range"
        assert result.grid_enabled is True

    def test_shallow_slope_below_threshold_returns_range(self):
        # monotonic but slope/atr ratio = 0.1/0.5 = 0.2 < 0.25
        ma20 = [10.0, 10.025, 10.05, 10.075, 10.1]
        result = classify_regime(
            _frame(ma20),
            _snapshot(close=12.0, ma20=11.0, ma60=10.0, atr14=0.5),
        )
        assert result.regime == "range"
