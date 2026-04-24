"""东方财富 ETF / 基金元数据（dfcf 公开接口，无需 cookie）。

参考 C:\\Users\\qhgy\\.agents\\skills\\dfcf-public-api SKILL.md 的最佳实践：
- fundgz.1234567.com.cn/js/{code}.js 拿实时估值 + 实时净值
- fundmobapi.eastmoney.com/FundMNewApi/FundMNNBasicInformation 拿规模 / 类型 / 经理 / 成立日

所有接口无需 cookie，失败时返回 None / 空 dict，不抛出异常（避免阻断主流程）。
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any, Callable

_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

HttpOpener = Callable[..., Any]


def strip_exchange_prefix(symbol: str) -> str:
    """SH515880 -> 515880；515880 -> 515880。"""
    s = symbol.strip().upper()
    if s.startswith(("SH", "SZ")):
        return s[2:]
    return s


def _http_get_text(url: str, *, timeout: float, opener: HttpOpener | None) -> str | None:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": _USER_AGENT, "Referer": "https://fund.eastmoney.com/"},
    )
    opener_fn = opener or urllib.request.urlopen
    try:
        with opener_fn(req, timeout=timeout) as resp:  # type: ignore[call-arg]
            raw = resp.read()
    except (urllib.error.URLError, OSError, ValueError):
        return None
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="ignore")
    return str(raw)


def fetch_realtime_estimate(
    fund_code: str,
    *,
    timeout: float = 5.0,
    opener: HttpOpener | None = None,
) -> dict[str, Any] | None:
    """拉 fundgz.1234567.com.cn 实时估值（JSONP），解出 dict。

    返回例：{"fundcode":"515880","name":"科创50ETF","jzrq":"2026-04-23",
             "dwjz":"3.069","gsz":"3.075","gszzl":"0.20","gztime":"2026-04-24 10:30"}
    """
    code = strip_exchange_prefix(fund_code)
    url = f"http://fundgz.1234567.com.cn/js/{code}.js"
    text = _http_get_text(url, timeout=timeout, opener=opener)
    if not text:
        return None
    match = re.search(r"jsonpgz\((\{.*?\})\)", text, flags=re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def fetch_basic_info(
    fund_code: str,
    *,
    timeout: float = 5.0,
    opener: HttpOpener | None = None,
) -> dict[str, Any] | None:
    """拉 fundmobapi.eastmoney.com 的基金基础资料。

    字段参考（不同基金可能有增减）：
    - FCODE, SHORTNAME, JJFULLNAME, FTYPE, INVESTMENTTYPE, RISKLEVEL
    - FSRQ (最新净值日期), DWJZ (单位净值), LJJZ (累计净值)
    - ESTABDATE (成立日), ENDNAV (资产净值亿元), MANAGER / JJJL (基金经理)
    - BENCH (业绩基准), INDEXCODE / INDEXNAME (跟踪指数)
    """
    code = strip_exchange_prefix(fund_code)
    url = (
        "https://fundmobapi.eastmoney.com/FundMNewApi/FundMNNBasicInformation"
        f"?FCODE={code}&deviceid=Wap&plat=Wap&product=EFund&version=2.0.0"
    )
    text = _http_get_text(url, timeout=timeout, opener=opener)
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    datas = payload.get("Datas") if isinstance(payload, dict) else None
    if isinstance(datas, dict):
        return datas
    return None


def _to_float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch_fund_meta(
    fund_code: str,
    *,
    timeout: float = 5.0,
    opener: HttpOpener | None = None,
) -> dict[str, Any]:
    """整合实时估值 + 基础资料，返回统一的浅层 dict。任何源失败都不会抛异常。"""
    code = strip_exchange_prefix(fund_code)
    estimate = fetch_realtime_estimate(code, timeout=timeout, opener=opener) or {}
    info = fetch_basic_info(code, timeout=timeout, opener=opener) or {}

    return {
        "code": code,
        "name": estimate.get("name") or info.get("SHORTNAME") or info.get("JJFULLNAME"),
        "full_name": info.get("JJFULLNAME"),
        "fund_type": info.get("FTYPE") or info.get("INVESTMENTTYPE"),
        "risk_level": info.get("RISKLEVEL"),
        "latest_nav": _to_float(estimate.get("dwjz")) or _to_float(info.get("DWJZ")),
        "latest_nav_date": estimate.get("jzrq") or info.get("FSRQ"),
        "accum_nav": _to_float(info.get("LJJZ")),
        "estimate_price": _to_float(estimate.get("gsz")),
        "estimate_percent": _to_float(estimate.get("gszzl")),
        "estimate_time": estimate.get("gztime"),
        "inception_date": info.get("ESTABDATE"),
        "size_billion": _to_float(info.get("ENDNAV")),
        "manager": info.get("MANAGER") or info.get("JJJL"),
        "benchmark": info.get("BENCH"),
        "tracking_index_code": info.get("INDEXCODE"),
        "tracking_index_name": info.get("INDEXNAME"),
        "_estimate_raw": estimate or None,
        "_info_raw": info or None,
    }
