"""Unit tests for atr_grid.data helpers."""

from __future__ import annotations

import math

from core.market_data import get_current_price
from atr_grid.data import _to_float, normalize_symbol


class TestNormalizeSymbol:
    def test_already_prefixed_sh(self):
        assert normalize_symbol("SH515880") == "SH515880"

    def test_already_prefixed_sz(self):
        assert normalize_symbol("sz002837") == "SZ002837"

    def test_pure_digits_sh_auto_prefixed(self):
        assert normalize_symbol("515880") == "SH515880"
        assert normalize_symbol("600000") == "SH600000"
        assert normalize_symbol("900001") == "SH900001"

    def test_pure_digits_sz_auto_prefixed(self):
        assert normalize_symbol("002837") == "SZ002837"
        assert normalize_symbol("000001") == "SZ000001"
        assert normalize_symbol("300102") == "SZ300102"

    def test_strips_whitespace_and_uppercases(self):
        assert normalize_symbol("  sh515880  ") == "SH515880"

    def test_non_six_digits_passes_through(self):
        # unusual input — we return as-is (upper-stripped) rather than guess
        assert normalize_symbol("12345") == "12345"
        assert normalize_symbol("HK00700") == "HK00700"


class TestToFloat:
    def test_valid_number_string(self):
        assert _to_float("1.5") == 1.5

    def test_valid_number(self):
        assert _to_float(2) == 2.0

    def test_none_returns_nan(self):
        assert math.isnan(_to_float(None))

    def test_invalid_string_returns_nan(self):
        assert math.isnan(_to_float("abc"))

    def test_infinity_returns_nan(self):
        assert math.isnan(_to_float(float("inf")))

    def test_nan_stays_nan(self):
        assert math.isnan(_to_float(float("nan")))


class TestGetCurrentPrice:
    def test_quote_fetch_failure_returns_none(self):
        def broken_fetcher(_symbol: str):
            raise ModuleNotFoundError("requests")

        assert get_current_price("SH515880", quote_fetcher=broken_fetcher) is None
