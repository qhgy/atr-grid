"""atr_grid.fund_eastmoney 的单元测试（全部 mock HTTP）。"""

from __future__ import annotations

import io
import json
from contextlib import contextmanager
from typing import Any

from atr_grid.fund_eastmoney import (
    fetch_basic_info,
    fetch_fund_meta,
    fetch_realtime_estimate,
    strip_exchange_prefix,
)


class _FakeResp:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _make_opener(mapping: dict[str, bytes]):
    def opener(req, *args, **kwargs):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        for key, value in mapping.items():
            if key in url:
                return _FakeResp(value)
        raise AssertionError(f"未 mock 的 URL: {url}")

    return opener


def test_strip_exchange_prefix():
    assert strip_exchange_prefix("SH515880") == "515880"
    assert strip_exchange_prefix("sz000001") == "000001"
    assert strip_exchange_prefix("515880") == "515880"
    assert strip_exchange_prefix(" sh515880 ") == "515880"


def test_fetch_realtime_estimate_parses_jsonp():
    jsonp = (
        'jsonpgz({"fundcode":"515880","name":"科创50ETF","jzrq":"2026-04-23",'
        '"dwjz":"3.069","gsz":"3.075","gszzl":"0.20","gztime":"2026-04-24 10:30"});'
    )
    opener = _make_opener({"fundgz.1234567.com.cn": jsonp.encode("utf-8")})
    result = fetch_realtime_estimate("SH515880", opener=opener)
    assert result is not None
    assert result["fundcode"] == "515880"
    assert result["name"] == "科创50ETF"
    assert result["dwjz"] == "3.069"


def test_fetch_realtime_estimate_no_match_returns_none():
    opener = _make_opener({"fundgz.1234567.com.cn": b"not a jsonp"})
    assert fetch_realtime_estimate("515880", opener=opener) is None


def test_fetch_realtime_estimate_urlerror_returns_none():
    def opener(req, *args, **kwargs):
        raise OSError("network down")

    assert fetch_realtime_estimate("515880", opener=opener) is None


def test_fetch_basic_info_parses_json():
    payload = json.dumps({
        "Datas": {
            "FCODE": "515880",
            "SHORTNAME": "博时上证科创板50ETF",
            "JJFULLNAME": "博时上证科创板50成份交易型开放式指数证券投资基金",
            "FTYPE": "股票型",
            "ESTABDATE": "2020-09-22",
            "ENDNAV": "36.82",
            "MANAGER": "张维",
            "INDEXCODE": "000688",
            "INDEXNAME": "科创50",
        }
    }).encode("utf-8")
    opener = _make_opener({"fundmobapi.eastmoney.com": payload})
    result = fetch_basic_info("SH515880", opener=opener)
    assert result is not None
    assert result["FCODE"] == "515880"
    assert result["INDEXNAME"] == "科创50"


def test_fetch_basic_info_bad_json_returns_none():
    opener = _make_opener({"fundmobapi.eastmoney.com": b"not json"})
    assert fetch_basic_info("515880", opener=opener) is None


def test_fetch_fund_meta_merges_sources():
    jsonp = (
        'jsonpgz({"fundcode":"515880","name":"科创50ETF","jzrq":"2026-04-23",'
        '"dwjz":"3.069","gsz":"3.075","gszzl":"0.20","gztime":"2026-04-24 10:30"});'
    )
    info = json.dumps({
        "Datas": {
            "SHORTNAME": "博时上证科创板50ETF",
            "ENDNAV": "36.82",
            "MANAGER": "张维",
            "INDEXNAME": "科创50",
        }
    }).encode("utf-8")
    opener = _make_opener({
        "fundgz.1234567.com.cn": jsonp.encode("utf-8"),
        "fundmobapi.eastmoney.com": info,
    })
    meta = fetch_fund_meta("SH515880", opener=opener)
    assert meta["code"] == "515880"
    assert meta["name"] == "科创50ETF"  # 估值源的 name 优先
    assert meta["latest_nav"] == 3.069
    assert meta["latest_nav_date"] == "2026-04-23"
    assert meta["estimate_price"] == 3.075
    assert meta["estimate_percent"] == 0.20
    assert meta["size_billion"] == 36.82
    assert meta["manager"] == "张维"
    assert meta["tracking_index_name"] == "科创50"


def test_fetch_fund_meta_all_sources_fail_returns_none_fields():
    def opener(req, *args, **kwargs):
        raise OSError("no network")

    meta = fetch_fund_meta("515880", opener=opener)
    assert meta["code"] == "515880"
    assert meta["name"] is None
    assert meta["latest_nav"] is None
    assert meta["estimate_price"] is None
