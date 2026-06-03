"""Unit tests for Xueqiu cookie parsing."""

from __future__ import annotations

from core.xueqiu_session import parse_cookie_text


class TestParseCookieText:
    def test_raw_cookie_header_passes_through(self):
        raw = "xq_a_token=abc; u=123"
        assert parse_cookie_text(raw) == raw

    def test_cookie_editor_json_export(self):
        raw = """
[
  {"domain": ".xueqiu.com", "name": "xq_a_token", "value": "abc"},
  {"domain": ".xueqiu.com", "name": "u", "value": "123"},
  {"domain": ".example.com", "name": "ignored", "value": "nope"}
]
"""
        assert parse_cookie_text(raw) == "xq_a_token=abc; u=123"
