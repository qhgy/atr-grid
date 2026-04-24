"""atr_grid.data_snowball 的单元测试（全部 mock HTTP）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from atr_grid import data_snowball
from atr_grid.data_snowball import (
    SnowballCookieError,
    SnowballFetchError,
    build_headers,
    fetch_kline_rows,
    load_cookies_string,
    resolve_cookies_path,
)


class _FakeResp:
    def __init__(self, *, status_code: int = 200, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> dict:
        assert self._payload is not None, "_FakeResp 没有 payload不能调 json()"
        return self._payload


def _write_cookies(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "cookies.txt"
    p.write_text(content, encoding="utf-8")
    return p


def test_resolve_cookies_path_explicit(tmp_path, monkeypatch):
    p = _write_cookies(tmp_path, "xq_a_token=abc")
    monkeypatch.delenv("ATRGRID_SNOWBALL_COOKIES", raising=False)
    monkeypatch.delenv("PYSNOWBALL_LOCAL_DIR", raising=False)
    assert resolve_cookies_path(explicit_path=p) == p


def test_resolve_cookies_path_env_explicit(tmp_path, monkeypatch):
    p = _write_cookies(tmp_path, "xq_a_token=abc")
    monkeypatch.setenv("ATRGRID_SNOWBALL_COOKIES", str(p))
    monkeypatch.delenv("PYSNOWBALL_LOCAL_DIR", raising=False)
    monkeypatch.setattr(data_snowball, "DEFAULT_PYSNOWBALL_LOCAL", tmp_path / "_no_such")
    assert resolve_cookies_path() == p


def test_resolve_cookies_path_env_local_dir(tmp_path, monkeypatch):
    # PYSNOWBALL_LOCAL_DIR 下构造 dca_dashboard/cookies.txt
    local_dir = tmp_path / "pysnowball"
    (local_dir / "dca_dashboard").mkdir(parents=True)
    cookies = local_dir / "dca_dashboard" / "cookies.txt"
    cookies.write_text("xq_a_token=abc", encoding="utf-8")
    monkeypatch.delenv("ATRGRID_SNOWBALL_COOKIES", raising=False)
    monkeypatch.setenv("PYSNOWBALL_LOCAL_DIR", str(local_dir))
    monkeypatch.setattr(data_snowball, "DEFAULT_PYSNOWBALL_LOCAL", tmp_path / "_no_such")
    assert resolve_cookies_path() == cookies


def test_resolve_cookies_path_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("ATRGRID_SNOWBALL_COOKIES", raising=False)
    monkeypatch.delenv("PYSNOWBALL_LOCAL_DIR", raising=False)
    monkeypatch.setattr(data_snowball, "DEFAULT_PYSNOWBALL_LOCAL", tmp_path / "_no_such")
    assert resolve_cookies_path() is None


def test_load_cookies_string_skips_comments_and_blanks(tmp_path, monkeypatch):
    content = "# comment\nxq_a_token=abc\n\nxq_r_token=def\n"
    p = _write_cookies(tmp_path, content)
    monkeypatch.setenv("ATRGRID_SNOWBALL_COOKIES", str(p))
    monkeypatch.setattr(data_snowball, "DEFAULT_PYSNOWBALL_LOCAL", tmp_path / "_no_such")
    assert load_cookies_string() == "xq_a_token=abc; xq_r_token=def"


def test_load_cookies_string_empty_raises(tmp_path, monkeypatch):
    p = _write_cookies(tmp_path, "# only comments\n\n")
    monkeypatch.setenv("ATRGRID_SNOWBALL_COOKIES", str(p))
    with pytest.raises(SnowballCookieError):
        load_cookies_string()


def test_load_cookies_string_missing_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("ATRGRID_SNOWBALL_COOKIES", raising=False)
    monkeypatch.delenv("PYSNOWBALL_LOCAL_DIR", raising=False)
    monkeypatch.setattr(data_snowball, "DEFAULT_PYSNOWBALL_LOCAL", tmp_path / "_no_such")
    with pytest.raises(SnowballCookieError):
        load_cookies_string()


def test_build_headers_contains_cookie_and_ua():
    h = build_headers("xq_a_token=abc")
    assert h["Cookie"] == "xq_a_token=abc"
    assert "Mozilla" in h["User-Agent"]
    assert h["Referer"].startswith("https://xueqiu.com")
    assert h["Origin"] == "https://xueqiu.com"


def test_fetch_kline_rows_parses_columns_item():
    payload = {
        "data": {
            "column": ["timestamp", "open", "high", "low", "close", "volume"],
            "item": [
                [1727366400000, 3.0, 3.1, 2.9, 3.05, 1000],
                [1727452800000, 3.05, 3.2, 3.0, 3.15, 1200],
            ],
        }
    }
    captured = {}

    def fake_get(url, *, params, headers, timeout):
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers
        return _FakeResp(status_code=200, payload=payload)

    rows = fetch_kline_rows(
        "SH515880",
        count=300,
        cookies="xq_a_token=abc",
        http_getter=fake_get,
    )
    assert len(rows) == 2
    assert rows[0]["close"] == 3.05
    assert rows[1]["open"] == 3.05
    assert captured["url"].endswith("/kline.json")
    assert captured["params"]["symbol"] == "SH515880"
    assert captured["params"]["count"] == -300
    assert captured["params"]["period"] == "day"
    assert captured["params"]["type"] == "before"
    assert captured["params"]["indicator"] == "kline"
    assert captured["headers"]["Cookie"] == "xq_a_token=abc"


def test_fetch_kline_rows_non_200_raises():
    def fake_get(url, *, params, headers, timeout):
        return _FakeResp(status_code=403, text="forbidden")

    with pytest.raises(SnowballFetchError):
        fetch_kline_rows("SH515880", cookies="x=y", http_getter=fake_get)


def test_fetch_kline_rows_empty_data_raises():
    def fake_get(url, *, params, headers, timeout):
        return _FakeResp(status_code=200, payload={"data": {"column": [], "item": []}})

    with pytest.raises(SnowballFetchError):
        fetch_kline_rows("SH515880", cookies="x=y", http_getter=fake_get)
