"""Shared market data access for the active workflow."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from core.paths import ensure_pysnowball_path, project_path, resolve_project_path
from core.xueqiu_session import ensure_xueqiu_token_loaded

QuoteFetcher = Callable[[str], dict[str, Any] | None]
KlineFetcher = Callable[[str, str, int], dict[str, Any] | None]
AkshareEtfFetcher = Callable[[str, str, str], Any]


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
) -> float | None:
    """Return the current price for a symbol if available."""
    quote = get_realtime_quote(symbol, quote_fetcher=quote_fetcher)
    if not quote:
        return None
    current = quote.get("current")
    try:
        return float(current)
    except (TypeError, ValueError):
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


def get_kline_data(
    symbol: str,
    *,
    count: int = 100,
    period: str = "day",
    kline_fetcher: KlineFetcher | None = None,
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


def _safe_number(value: Any) -> float | None:
    try:
        if value in ("", None):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
