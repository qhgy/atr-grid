"""Unit tests for atr_grid.report pure helpers."""

from __future__ import annotations

from atr_grid.report import _pct_abs_change, _pct_change, fmt_levels


class TestPctChange:
    def test_positive_change(self):
        assert _pct_change(110.0, 100.0) == 10.0

    def test_negative_change(self):
        assert _pct_change(90.0, 100.0) == -10.0

    def test_none_target_returns_none(self):
        assert _pct_change(None, 100.0) is None

    def test_none_base_returns_none(self):
        assert _pct_change(100.0, None) is None

    def test_zero_base_returns_none(self):
        # Zero base should not cause a ZeroDivisionError
        assert _pct_change(100.0, 0.0) is None

    def test_rounds_to_two_decimals(self):
        assert _pct_change(100.123, 100.0) == 0.12


class TestPctAbsChange:
    def test_negative_change_becomes_abs(self):
        assert _pct_abs_change(90.0, 100.0) == 10.0

    def test_positive_change_stays_positive(self):
        assert _pct_abs_change(110.0, 100.0) == 10.0

    def test_none_returns_none(self):
        assert _pct_abs_change(None, 100.0) is None
        assert _pct_abs_change(100.0, None) is None


class TestFmtLevels:
    def test_empty_returns_placeholder(self):
        assert fmt_levels([]) == "无"

    def test_single_level(self):
        assert fmt_levels([1.234]) == "¥1.234"

    def test_multiple_levels_joined(self):
        assert fmt_levels([1.0, 2.0, 3.0]) == "¥1.000 / ¥2.000 / ¥3.000"
