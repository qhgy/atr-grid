"""Shared market data access for the active workflow."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from core.paths import ensure_pysnowball_path, project_path, resolve_project_path
from core.xueqiu_session import ensure_xueqiu_token_loaded

QuoteFetcher = Callable[[str], dict[str, Any] | None]
KlineFetcher = Callable[[str, str, int], dict[str, Any] | None]
AkshareEtfFetcher = Callable[[str, str, str], Any]
SinaKlineFetcher = Callable[[str, int], Any]
SinaQuoteFetcher = Callable[[str], Any]


def _default_quote_fetcher(symbol: str) -> dict[str, Any] | None:
    ensure_pysnowball_path()
    ensure_xueqiu_token_loaded()
    from pysnowball import quotec

    return quotec(symbol)


def _default_kline_fetcher(symbol: str, period: str, count: int) -> dict[str, Any] | None:
    ensure_pysnowball_path()
    ensure_xueqiu_token_loaded()
    from pysnowball import kline

    return kline(symbol, period=period, count=count)


def _default_sina_kline_fetcher(symbol: str, count: int) -> str:
    import requests

    url = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
    response = requests.get(
        url,
        params={"symbol": _to_sina_symbol(symbol), "scale": 240, "ma": "no", "datalen": count},
        headers={
            "Referer": "https://finance.sina.com.cn/",
            "User-Agent": "Mozilla/5.0",
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.text


def _default_sina_quote_fetcher(symbol: str) -> str:
    import requests

    response = requests.get(
        f"https://hq.sinajs.cn/list={_to_sina_symbol(symbol)}",
        headers={
            "Referer": "https://finance.sina.com.cn/",
            "User-Agent": "Mozilla/5.0",
        },
        timeout=8,
    )
    response.raise_for_status()
    return response.text


def get_realtime_quote(
    symbol: str,
    *,
    quote_fetcher: QuoteFetcher | None = None,
) -> dict[str, Any] | None:
    """Fetch and normalize the first realtime quote entry."""
    fetcher = quote_fetcher or _default_quote_fetcher
    try:
        result = fetcher(symbol)
    except Exception:
        return None
    if not result or "data" not in result:
        return None
    data = result["data"]
    if isinstance(data, list) and data:
        return data[0]
    return None


def get_current_price(
    symbol: str,
    *,
    quote_fetcher: QuoteFetcher | None = None,
    sina_quote_fetcher: SinaQuoteFetcher | None = None,
) -> float | None:
    """Return the current price for a symbol if available."""
    quote = get_realtime_quote(symbol, quote_fetcher=quote_fetcher)
    if quote:
        current = quote.get("current")
        try:
            return float(current)
        except (TypeError, ValueError):
            pass

    # Keep injected quote fetchers deterministic in tests/callers unless a Sina fetcher is supplied.
    if quote_fetcher is not None and sina_quote_fetcher is None:
        return None
    fetcher = sina_quote_fetcher or _default_sina_quote_fetcher
    try:
        return normalize_sina_quote_price(fetcher(symbol))
    except Exception:
        return None


def _local_kline_path(symbol: str, count: int = 100, base_dir: str | Path | None = None) -> Path:
    root = resolve_project_path(base_dir) if base_dir else project_path()
    return root / f"{symbol}_{count}日K线.json"


def load_local_kline(symbol: str, count: int = 100, *, base_dir: str | Path | None = None) -> list[dict[str, Any]] | None:
    """Load locally cached kline arrays and normalize them to dict rows."""
    path = _local_kline_path(symbol, count=count, base_dir=base_dir)
    if not path.exists():
        fallback_path = _local_kline_path(symbol, count=100, base_dir=base_dir)
        path = fallback_path if fallback_path.exists() else path
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as handle:
        raw_rows = json.load(handle)
    return normalize_local_kline_rows(raw_rows)


def normalize_local_kline_rows(raw_rows: list[list[Any]]) -> list[dict[str, Any]]:
    """Convert local JSON kline arrays into named dict rows."""
    normalized: list[dict[str, Any]] = []
    for row in raw_rows:
        normalized.append(
            {
                "timestamp": row[0],
                "volume": row[1],
                "open": row[2],
                "high": row[3],
                "low": row[4],
                "close": row[5],
                "chg": row[6] if len(row) > 6 else None,
                "percent": row[7] if len(row) > 7 else None,
                "turnoverrate": row[8] if len(row) > 8 else None,
                "amount": row[9] if len(row) > 9 else None,
                "volume_post": row[10] if len(row) > 10 else None,
                "amount_post": row[11] if len(row) > 11 else None,
            }
        )
    return normalized


def normalize_api_kline_rows(result: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Convert pysnowball kline response into named dict rows."""
    if not result or "data" not in result:
        return None
    data = result["data"]
    if "item" not in data or "column" not in data:
        return None
    columns = data["column"]
    return [dict(zip(columns, item)) for item in data["item"]]


def _default_akshare_etf_fetcher(symbol: str, start_date: str, end_date: str):
    import akshare as ak

    return ak.fund_etf_hist_em(symbol=symbol, period="daily", start_date=start_date, end_date=end_date)


def normalize_akshare_etf_rows(result: Any) -> list[dict[str, Any]] | None:
    """Convert akshare ETF history rows into named dict rows."""
    if result is None:
        return None
    try:
        records = result.to_dict("records")
    except Exception:
        return None
    if not records:
        return None

    normalized: list[dict[str, Any]] = []
    for record in records:
        trade_date = record.get("日期")
        if not trade_date:
            continue
        try:
            timestamp = int(datetime.strptime(str(trade_date), "%Y-%m-%d").timestamp() * 1000)
        except ValueError:
            try:
                timestamp = int(datetime.strptime(str(trade_date), "%Y/%m/%d").timestamp() * 1000)
            except ValueError:
                continue

        normalized.append(
            {
                "timestamp": timestamp,
                "volume": _safe_number(record.get("成交量")),
                "open": _safe_number(record.get("开盘")),
                "high": _safe_number(record.get("最高")),
                "low": _safe_number(record.get("最低")),
                "close": _safe_number(record.get("收盘")),
                "chg": _safe_number(record.get("涨跌额")),
                "percent": _safe_number(record.get("涨跌幅")),
                "turnoverrate": _safe_number(record.get("换手率")),
                "amount": _safe_number(record.get("成交额")),
                "volume_post": None,
                "amount_post": None,
            }
        )
    return normalized or None


def normalize_sina_kline_rows(result: Any) -> list[dict[str, Any]] | None:
    """Convert Sina daily K-line response rows into the internal kline shape."""
    if result is None:
        return None
    if isinstance(result, bytes):
        result = result.decode("utf-8", errors="ignore")
    if isinstance(result, str):
        text = result.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = _parse_sina_legacy_rows(text)
    else:
        parsed = result
    if not isinstance(parsed, list):
        return None

    normalized: list[dict[str, Any]] = []
    for row in parsed:
        if not isinstance(row, dict):
            continue
        trade_date = row.get("day") or row.get("date")
        if not trade_date:
            continue
        try:
            timestamp = int(datetime.strptime(str(trade_date), "%Y-%m-%d").timestamp() * 1000)
        except ValueError:
            continue
        normalized.append(
            {
                "timestamp": timestamp,
                "volume": _safe_number(row.get("volume")),
                "open": _safe_number(row.get("open")),
                "high": _safe_number(row.get("high")),
                "low": _safe_number(row.get("low")),
                "close": _safe_number(row.get("close")),
                "chg": None,
                "percent": None,
                "turnoverrate": None,
                "amount": _safe_number(row.get("amount")),
                "volume_post": None,
                "amount_post": None,
            }
        )
    normalized.sort(key=lambda item: item.get("timestamp") or 0)
    return normalized or None


def normalize_sina_quote_price(result: Any) -> float | None:
    """Extract current price from hq.sinajs.cn quote text."""
    if result is None:
        return None
    if isinstance(result, bytes):
        result = result.decode("gbk", errors="ignore")
    text = str(result)
    match = re.search(r'="([^"]*)"', text)
    payload = match.group(1) if match else text
    fields = payload.split(",")
    if len(fields) < 4:
        return None
    return _safe_number(fields[3])


def get_kline_data(
    symbol: str,
    *,
    count: int = 100,
    period: str = "day",
    kline_fetcher: KlineFetcher | None = None,
    sina_fetcher: SinaKlineFetcher | None = None,
    akshare_fetcher: AkshareEtfFetcher | None = None,
    base_dir: str | Path | None = None,
) -> tuple[list[dict[str, Any]] | None, str]:
    """Fetch kline data, falling back to local cached JSON when needed."""
    fetcher = kline_fetcher or _default_kline_fetcher
    try:
        result = fetcher(symbol, period, count)
        rows = normalize_api_kline_rows(result)
        if rows:
            return rows, "api"
    except Exception:
        pass

    if period == "day" and _looks_like_etf(symbol):
        fetch_sina = sina_fetcher or _default_sina_kline_fetcher
        try:
            sina_rows = normalize_sina_kline_rows(fetch_sina(symbol, count))
            if sina_rows:
                if len(sina_rows) > count:
                    return sina_rows[-count:], "sina"
                return sina_rows, "sina"
        except Exception:
            pass

    if period == "day" and _looks_like_etf(symbol):
        ak_fetcher = akshare_fetcher or _default_akshare_etf_fetcher
        try:
            raw_symbol = _strip_exchange_prefix(symbol)
            calendar_days = max(count * 3, 180)
            end_date = datetime.now().strftime("%Y%m%d")
            start_date = (datetime.now() - timedelta(days=calendar_days)).strftime("%Y%m%d")
            ak_rows = normalize_akshare_etf_rows(ak_fetcher(raw_symbol, start_date, end_date))
            if ak_rows:
                if len(ak_rows) > count:
                    return ak_rows[-count:], "akshare"
                return ak_rows, "akshare"
        except Exception:
            pass

    try:
        local_rows = load_local_kline(symbol, count=count, base_dir=base_dir)
    except Exception:
        local_rows = None
    if local_rows:
        if len(local_rows) > count:
            return local_rows[-count:], "local"
        return local_rows, "local"

    return None, "missing"


def _looks_like_etf(symbol: str) -> bool:
    raw_symbol = _strip_exchange_prefix(symbol)
    return len(raw_symbol) == 6 and raw_symbol[0] in {"1", "5"}


def _strip_exchange_prefix(symbol: str) -> str:
    normalized = str(symbol).upper()
    if normalized.startswith(("SH", "SZ")):
        return normalized[2:]
    return normalized


def _to_sina_symbol(symbol: str) -> str:
    normalized = str(symbol).strip().lower()
    if normalized.startswith(("sh", "sz")):
        return normalized
    raw = _strip_exchange_prefix(normalized)
    if raw.startswith(("5", "6", "9")):
        return f"sh{raw}"
    return f"sz{raw}"


def _parse_sina_legacy_rows(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for body in re.findall(r"\{([^{}]+)\}", text):
        row: dict[str, str] = {}
        for key, value in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*\"?([^,\"}]+)\"?", body):
            row[key] = value
        if row:
            rows.append(row)
    return rows


def _safe_number(value: Any) -> float | None:
    try:
        if value in ("", None):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
