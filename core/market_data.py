"""Shared market data access for the active workflow."""

from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from core.paths import ensure_pysnowball_path, project_path, resolve_project_path
from core.xueqiu_session import ensure_xueqiu_token_loaded

QuoteFetcher = Callable[[str], dict[str, Any] | None]
KlineFetcher = Callable[[str, str, int], dict[str, Any] | None]
AkshareEtfFetcher = Callable[[str, str, str], Any]
TencentKlineFetcher = Callable[[str, int], dict[str, Any] | None]


@dataclass(slots=True)
class TencentQuote:
    """Normalized quote returned by Tencent's public qt.gtimg.cn endpoint."""

    symbol: str
    name: str
    current: float | None
    last_close: float | None
    open: float | None
    high: float | None
    low: float | None
    chg: float | None
    percent: float | None
    volume: float | None
    amount: float | None
    timestamp: str | None
    instrument_type: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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


def get_tencent_quotes(symbols: list[str] | tuple[str, ...]) -> dict[str, TencentQuote]:
    """Fetch realtime quotes from Tencent without cookies.

    The endpoint is useful as a lightweight intraday source. It is treated as
    advisory/fallback data because field positions are undocumented.
    """
    normalized = [_tencent_symbol(symbol) for symbol in symbols if symbol]
    if not normalized:
        return {}

    url = "https://qt.gtimg.cn/q=" + ",".join(normalized)
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}),
            timeout=8,
        ) as resp:
            text = resp.read().decode("gbk", errors="ignore")
    except Exception:
        return {}

    quotes: dict[str, TencentQuote] = {}
    for raw_symbol, payload in re.findall(r'v_([a-z]{2}\d{6})="([^"]*)"', text):
        quote = parse_tencent_quote(raw_symbol, payload)
        if quote:
            quotes[quote.symbol] = quote
    return quotes


def get_tencent_quote(symbol: str) -> TencentQuote | None:
    """Fetch a single Tencent realtime quote."""
    return get_tencent_quotes([symbol]).get(_normalize_prefixed_symbol(symbol))


def _default_tencent_kline_fetcher(symbol: str, count: int) -> dict[str, Any] | None:
    tencent_symbol = _tencent_symbol(symbol)
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={tencent_symbol},day,,,{count},qfq"
    with urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}),
        timeout=8,
    ) as resp:
        return json.loads(resp.read().decode("utf-8", errors="ignore"))


def get_tencent_kline_rows(
    symbol: str,
    *,
    count: int = 100,
    kline_fetcher: TencentKlineFetcher | None = None,
) -> list[dict[str, Any]] | None:
    """Fetch Tencent daily kline rows without cookies."""
    fetcher = kline_fetcher or _default_tencent_kline_fetcher
    try:
        result = fetcher(symbol, count)
    except Exception:
        return None
    return normalize_tencent_kline_rows(result, symbol, count=count)


def parse_tencent_quote(raw_symbol: str, payload: str) -> TencentQuote | None:
    """Parse one Tencent full quote payload into a TencentQuote."""
    parts = payload.split("~")
    if len(parts) < 35:
        return None
    symbol = _normalize_prefixed_symbol(raw_symbol)
    amount = _safe_number(parts[35].split("/")[-1] if len(parts) > 35 and "/" in parts[35] else None)
    if amount is None:
        # Tencent also exposes amount in ten-thousand yuan at index 37.
        amount_10k = _safe_number(_field(parts, 37))
        amount = amount_10k * 10_000 if amount_10k is not None else None
    return TencentQuote(
        symbol=symbol,
        name=_field(parts, 1) or "",
        current=_safe_number(_field(parts, 3)),
        last_close=_safe_number(_field(parts, 4)),
        open=_safe_number(_field(parts, 5)),
        high=_safe_number(_field(parts, 33)),
        low=_safe_number(_field(parts, 34)),
        chg=_safe_number(_field(parts, 31)),
        percent=_safe_number(_field(parts, 32)),
        volume=_safe_number(_field(parts, 36)),
        amount=amount,
        timestamp=_field(parts, 30),
        instrument_type=(_field(parts, 61) or "").strip() or None,
    )


def normalize_tencent_kline_rows(
    result: dict[str, Any] | None,
    symbol: str,
    *,
    count: int = 100,
) -> list[dict[str, Any]] | None:
    """Convert Tencent fqkline daily rows into named dict rows."""
    if not result or result.get("code") != 0:
        return None
    data = result.get("data")
    if not isinstance(data, dict):
        return None
    block = data.get(_tencent_symbol(symbol))
    if not isinstance(block, dict):
        return None
    raw_rows = block.get("qfqday") or block.get("day")
    if not isinstance(raw_rows, list):
        return None

    normalized: list[dict[str, Any]] = []
    for row in raw_rows:
        if not isinstance(row, list) or len(row) < 6:
            continue
        try:
            timestamp = int(datetime.strptime(str(row[0]), "%Y-%m-%d").timestamp() * 1000)
        except ValueError:
            continue
        normalized.append(
            {
                "timestamp": timestamp,
                "volume": _safe_number(row[5]),
                "open": _safe_number(row[1]),
                "high": _safe_number(row[3]),
                "low": _safe_number(row[4]),
                "close": _safe_number(row[2]),
                "chg": None,
                "percent": None,
                "turnoverrate": None,
                "amount": None,
                "volume_post": None,
                "amount_post": None,
            }
        )
    if not normalized:
        return None
    return normalized[-count:] if len(normalized) > count else normalized


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
    tencent_kline_fetcher: TencentKlineFetcher | None = None,
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

    if period == "day":
        tencent_rows = get_tencent_kline_rows(symbol, count=count, kline_fetcher=tencent_kline_fetcher)
        if tencent_rows:
            return tencent_rows, "tencent"

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


def _normalize_prefixed_symbol(symbol: str) -> str:
    normalized = str(symbol).strip().upper()
    if normalized.startswith(("SH", "SZ")) and len(normalized) >= 8:
        return normalized[:8]
    if normalized.startswith(("SH", "SZ")):
        return normalized
    lowered = str(symbol).strip().lower()
    if lowered.startswith(("sh", "sz")) and len(lowered) >= 8:
        return lowered[:2].upper() + lowered[2:8]
    raw = _strip_exchange_prefix(normalized)
    if len(raw) == 6 and raw.isdigit():
        prefix = "SH" if raw.startswith(("5", "6", "9")) else "SZ"
        return f"{prefix}{raw}"
    return normalized


def _tencent_symbol(symbol: str) -> str:
    normalized = _normalize_prefixed_symbol(symbol)
    if normalized.startswith("SH"):
        return "sh" + normalized[2:]
    if normalized.startswith("SZ"):
        return "sz" + normalized[2:]
    return str(symbol).strip().lower()


def _field(parts: list[str], index: int) -> str | None:
    if index >= len(parts):
        return None
    value = parts[index]
    return value if value != "" else None


def _safe_number(value: Any) -> float | None:
    try:
        if value in ("", None):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
