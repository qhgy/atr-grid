"""完整 cookies 雪球 K 线 fetcher。

直接调用 stock.xueqiu.com/v5/stock/chart/kline.json，携带 xq_a_token + xq_r_token + xqat + u 等
完整 cookies（pip 版 pysnowball 只带 XUEQIUTOKEN 单 token，K 线接口经常返空）。

Cookies 解析优先级：
1. 环境变量 ATRGRID_SNOWBALL_COOKIES 指向的文件
2. 环境变量 PYSNOWBALL_LOCAL_DIR/dca_dashboard/cookies.txt
3. 默认 D:\\000trae\\A股数据\\aaa\\pysnowball\\dca_dashboard\\cookies.txt
   （即 xq_tools.cookie_loader 的默认路径）
返回值类型与 pysnowball.kline 排齐：
dict 列表，每条包含 timestamp/volume/open/high/low/close/chg/percent/turnoverrate/amount 等字段。
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable

import requests

DEFAULT_PYSNOWBALL_LOCAL = Path(r"D:\000trae\A股数据\aaa\pysnowball")
DEFAULT_COOKIES_RELPATH = Path("dca_dashboard") / "cookies.txt"

KLINE_URL = "https://stock.xueqiu.com/v5/stock/chart/kline.json"

HttpGetter = Callable[..., requests.Response]


class SnowballCookieError(RuntimeError):
    """没有可用的雪球 cookies 或文件为空。"""


class SnowballFetchError(RuntimeError):
    """雪球接口返回空或 HTTP 异常。"""


def resolve_cookies_path(
    *,
    explicit_path: str | os.PathLike[str] | None = None,
) -> Path | None:
    """按优先级查找 cookies.txt 文件路径，找不到返回 None。"""
    if explicit_path:
        p = Path(explicit_path)
        return p if p.exists() else None

    env_explicit = os.environ.get("ATRGRID_SNOWBALL_COOKIES")
    if env_explicit:
        p = Path(env_explicit)
        if p.exists():
            return p

    env_local_dir = os.environ.get("PYSNOWBALL_LOCAL_DIR")
    if env_local_dir:
        p = Path(env_local_dir) / DEFAULT_COOKIES_RELPATH
        if p.exists():
            return p

    default_path = DEFAULT_PYSNOWBALL_LOCAL / DEFAULT_COOKIES_RELPATH
    if default_path.exists():
        return default_path
    return None


def load_cookies_string(
    *,
    explicit_path: str | os.PathLike[str] | None = None,
) -> str:
    """读取 cookies.txt 拼接成单行 Cookie 头格式。"""
    path = resolve_cookies_path(explicit_path=explicit_path)
    if path is None:
        raise SnowballCookieError(
            "未找到雪球 cookies。请将完整 cookies 存入 "
            r"D:\000trae\A股数据\aaa\pysnowball\dca_dashboard\cookies.txt"
            "，或设置环境变量 ATRGRID_SNOWBALL_COOKIES 指向对应文件。"
        )
    lines: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if stripped and not stripped.startswith("#"):
            lines.append(stripped)
    if not lines:
        raise SnowballCookieError(f"cookies 文件内容为空: {path}")
    return "; ".join(lines)


def build_headers(cookies: str) -> dict[str, str]:
    """构造雪球 K 线接口的请求头。"""
    return {
        "Cookie": cookies,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://xueqiu.com/",
        "Origin": "https://xueqiu.com",
    }


def fetch_kline_rows(
    symbol: str,
    *,
    period: str = "day",
    count: int = 300,
    timeout: float = 10.0,
    cookies: str | None = None,
    http_getter: HttpGetter | None = None,
) -> list[dict[str, Any]]:
    """拉完整 cookies 雪球 K 线，返回带列名的 dict 列表。

    Args:
        symbol: SH515880 / SZ000001 格式。
        period: day / week / month / 60m / 30m / 15m / 5m / 1m。
        count: 要拉的根数。
        cookies: 手动传入的 cookies 字符串，不传就从文件加载。
        http_getter: 测试注入点；默认用 requests.get。
    """
    cookies_str = cookies if cookies is not None else load_cookies_string()
    headers = build_headers(cookies_str)
    params = {
        "symbol": symbol,
        "begin": int(time.time() * 1000),
        "period": period,
        "type": "before",
        "count": -abs(count),
        "indicator": "kline",
    }
    getter = http_getter or requests.get
    resp = getter(KLINE_URL, params=params, headers=headers, timeout=timeout)
    if resp.status_code != 200:
        raise SnowballFetchError(
            f"雪球 K 线接口 HTTP {resp.status_code}: {resp.text[:200]}"
        )
    payload = resp.json()
    data = (payload or {}).get("data") or {}
    columns: list[str] = data.get("column") or []
    items: list[list[Any]] = data.get("item") or []
    if not columns or not items:
        raise SnowballFetchError(
            f"雪球 K 线返回空（cookies 可能已过期）：symbol={symbol} payload={payload}"
        )
    return [dict(zip(columns, item)) for item in items]
