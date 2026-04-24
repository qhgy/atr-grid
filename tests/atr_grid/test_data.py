"""Unit tests for atr_grid.data helpers."""

from __future__ import annotations

import math

from core.market_data import get_current_price, get_kline_data, normalize_sina_kline_rows
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

    def test_quote_falls_back_to_sina_when_supplied(self):
        def broken_fetcher(_symbol: str):
            return None

        def fake_sina_quote(_symbol: str):
            return 'var hq_str_sh515880="通信ETF,1.264,1.264,1.309,1.320,1.250";'

        assert get_current_price(
            "SH515880",
            quote_fetcher=broken_fetcher,
            sina_quote_fetcher=fake_sina_quote,
        ) == 1.309


class TestSinaKlineFallback:
    def test_normalize_sina_legacy_rows(self):
        raw = '[{day:"2026-04-23",open:"1.10",high:"1.20",low:"1.00",close:"1.15",volume:"123"}]'

        rows = normalize_sina_kline_rows(raw)

        assert rows is not None
        assert len(rows) == 1
        assert rows[0]["open"] == 1.10
        assert rows[0]["close"] == 1.15
        assert rows[0]["volume"] == 123.0
        assert rows[0]["timestamp"] > 0

    def test_get_kline_data_uses_sina_after_api_failure(self):
        def broken_kline(_symbol: str, _period: str, _count: int):
            raise RuntimeError("snowball unavailable")

        def fake_sina(_symbol: str, _count: int):
            return [
                {"day": "2026-04-22", "open": "1.00", "high": "1.10", "low": "0.95", "close": "1.05", "volume": "100"},
                {"day": "2026-04-23", "open": "1.05", "high": "1.20", "low": "1.01", "close": "1.15", "volume": "120"},
                {"day": "2026-04-24", "open": "1.15", "high": "1.30", "low": "1.10", "close": "1.25", "volume": "140"},
            ]

        rows, source = get_kline_data(
            "SH515880",
            count=2,
            kline_fetcher=broken_kline,
            sina_fetcher=fake_sina,
        )

        assert source == "sina"
        assert rows is not None
        assert len(rows) == 2
        assert rows[-1]["close"] == 1.25
