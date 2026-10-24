"""Unit tests for atr_grid.data helpers."""

from __future__ import annotations

import math

from core.market_data import get_current_price, get_kline_data, normalize_tencent_kline_rows, parse_tencent_quote
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


class TestTencentQuote:
    def test_parse_full_quote_payload(self):
        payload = (
            "1~通信ETF国泰~515880~1.754~1.729~1.701~28620706~14652688~13936102~"
            "1.754~13462~1.753~62555~1.752~29477~1.751~33752~1.750~42244~"
            "1.755~134887~1.756~101934~1.757~49730~1.758~31432~1.759~21701~~"
            "20260617145247~0.025~1.45~1.755~1.701~1.754/28620706/4958063562~"
            "28620706~495806~10.60~~~1.755~1.701~3.12~473.45~473.45~0.00~"
            "1.902~1.556~0.81~-158194~1.732~~~~~~495806.3562~0.0000~0~ ~ETF"
        )

        quote = parse_tencent_quote("sh515880", payload)

        assert quote is not None
        assert quote.symbol == "SH515880"
        assert quote.name == "通信ETF国泰"
        assert quote.current == 1.754
        assert quote.last_close == 1.729
        assert quote.open == 1.701
        assert quote.high == 1.755
        assert quote.low == 1.701
        assert quote.percent == 1.45
        assert quote.amount == 4_958_063_562
        assert quote.instrument_type == "ETF"


class TestTencentKline:
    def test_normalize_tencent_kline_rows(self):
        payload = {
            "code": 0,
            "msg": "",
            "data": {
                "sh515880": {
                    "qfqday": [
                        ["2026-06-16", "1.628", "1.730", "1.735", "1.614", "51708256.000"],
                        ["2026-06-17", "1.700", "1.760", "1.760", "1.700", "30267967"],
                    ]
                }
            },
        }

        rows = normalize_tencent_kline_rows(payload, "SH515880", count=1)

        assert rows is not None
        assert len(rows) == 1
        assert rows[0]["open"] == 1.7
        assert rows[0]["close"] == 1.76
        assert rows[0]["high"] == 1.76
        assert rows[0]["low"] == 1.7
        assert rows[0]["volume"] == 30_267_967

    def test_get_kline_data_falls_back_to_tencent(self):
        def broken_xueqiu(_symbol: str, _period: str, _count: int):
            raise ModuleNotFoundError("pysnowball")

        def fake_tencent(_symbol: str, _count: int):
            return {
                "code": 0,
                "data": {
                    "sh515880": {
                        "qfqday": [
                            ["2026-06-16", "1.628", "1.730", "1.735", "1.614", "51708256.000"],
                            ["2026-06-17", "1.700", "1.760", "1.760", "1.700", "30267967"],
                        ]
                    }
                },
            }

        rows, source = get_kline_data(
            "SH515880",
            count=2,
            kline_fetcher=broken_xueqiu,
            tencent_kline_fetcher=fake_tencent,
        )

        assert source == "tencent"
        assert rows is not None
        assert rows[-1]["close"] == 1.76
