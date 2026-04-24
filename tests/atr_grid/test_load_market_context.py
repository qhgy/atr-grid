"""load_market_context 多数据源优先级 / fund_meta 行为测试（mock HTTP）。"""

from __future__ import annotations

import time

import pytest

from atr_grid import data as atr_data
from atr_grid.config import DEFAULT_CONFIG


def _make_rows(n: int = 80) -> list[dict]:
    base_ts = 1_700_000_000_000
    day_ms = 86_400_000
    rows = []
    for i in range(n):
        price = 3.0 + i * 0.01
        rows.append({
            "timestamp": base_ts + i * day_ms,
            "open": price,
            "high": price + 0.05,
            "low": price - 0.05,
            "close": price + 0.02,
            "volume": 1_000_000 + i,
        })
    return rows


def test_load_market_context_uses_snowball_full_first(monkeypatch):
    """snowball-full 成功时不应调 get_kline_data。"""
    called = {"snowball": 0, "legacy": 0, "fund": 0}

    def fake_snowball(symbol, *, count=300):
        called["snowball"] += 1
        assert symbol == "SH515880"
        return _make_rows(80)

    def fake_legacy(symbol, *, count=100):
        called["legacy"] += 1
        return [], "local"

    def fake_current_price(symbol):
        return 3.50

    def fake_fund_meta(symbol):
        called["fund"] += 1
        return {"code": "515880", "name": "科创50ETF", "latest_nav": 3.069}

    monkeypatch.setattr(atr_data, "_snowball_fetch_kline_rows", fake_snowball)
    monkeypatch.setattr(atr_data, "get_kline_data", fake_legacy)
    monkeypatch.setattr(atr_data, "get_current_price", fake_current_price)
    monkeypatch.setattr(atr_data, "_fetch_fund_meta", fake_fund_meta)
    monkeypatch.delenv("ATRGRID_DISABLE_SNOWBALL_FULL", raising=False)

    ctx = atr_data.load_market_context("SH515880", shares=2000, kline_count=80)
    assert ctx.symbol == "SH515880"
    assert ctx.data_source == "snowball-full"
    assert ctx.current_price == pytest.approx(3.50)
    assert ctx.fund_meta is not None
    assert ctx.fund_meta["name"] == "科创50ETF"
    assert called["snowball"] == 1
    assert called["legacy"] == 0
    assert called["fund"] == 1


def test_load_market_context_falls_back_on_cookie_error(monkeypatch):
    """cookies 缺失时自动回退到旧链路并附 warning。"""
    def fake_snowball(symbol, *, count=300):
        raise atr_data.SnowballCookieError("no cookies")

    def fake_legacy(symbol, *, count=100):
        return _make_rows(80), "api"

    def fake_current_price(symbol):
        return None

    monkeypatch.setattr(atr_data, "_snowball_fetch_kline_rows", fake_snowball)
    monkeypatch.setattr(atr_data, "get_kline_data", fake_legacy)
    monkeypatch.setattr(atr_data, "get_current_price", fake_current_price)
    monkeypatch.setattr(atr_data, "_fetch_fund_meta", lambda s: {"code": "515880"})
    monkeypatch.delenv("ATRGRID_DISABLE_SNOWBALL_FULL", raising=False)

    ctx = atr_data.load_market_context("515880", shares=2000, kline_count=80)
    assert ctx.symbol == "SH515880"
    assert ctx.data_source == "api"
    assert "snowball_full_cookies_missing" in ctx.warnings
    assert "current_price_fallback_to_last_close" in ctx.warnings


def test_load_market_context_disable_flag_skips_snowball(monkeypatch):
    """ATRGRID_DISABLE_SNOWBALL_FULL=1 时跳过 snowball-full。"""
    snowball_called = {"n": 0}

    def fake_snowball(symbol, *, count=300):
        snowball_called["n"] += 1
        return _make_rows(80)

    def fake_legacy(symbol, *, count=100):
        return _make_rows(80), "api"

    monkeypatch.setattr(atr_data, "_snowball_fetch_kline_rows", fake_snowball)
    monkeypatch.setattr(atr_data, "get_kline_data", fake_legacy)
    monkeypatch.setattr(atr_data, "get_current_price", lambda s: 3.0)
    monkeypatch.setattr(atr_data, "_fetch_fund_meta", lambda s: None)
    monkeypatch.setenv("ATRGRID_DISABLE_SNOWBALL_FULL", "1")

    ctx = atr_data.load_market_context("SH515880", shares=2000, kline_count=80)
    assert ctx.data_source == "api"
    assert snowball_called["n"] == 0


def test_load_market_context_no_fund_meta_when_disabled(monkeypatch):
    """include_fund_meta=False 时不拉基金元数据。"""
    monkeypatch.setattr(atr_data, "_snowball_fetch_kline_rows", lambda s, *, count=300: _make_rows(80))
    monkeypatch.setattr(atr_data, "get_current_price", lambda s: 3.0)

    def should_not_call(*args, **kwargs):
        raise AssertionError("include_fund_meta=False 时不应拉基金元数据")

    monkeypatch.setattr(atr_data, "_fetch_fund_meta", should_not_call)
    monkeypatch.delenv("ATRGRID_DISABLE_SNOWBALL_FULL", raising=False)

    ctx = atr_data.load_market_context(
        "SH515880", shares=2000, kline_count=80, include_fund_meta=False
    )
    assert ctx.fund_meta is None
