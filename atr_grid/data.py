"""Data loading helpers for the ETF ATR grid MVP."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any

from core.market_data import get_current_price, get_kline_data

from .config import DEFAULT_CONFIG, GridConfig

_SHANGHAI_TZ = timezone(timedelta(hours=8))


@dataclass(slots=True)
class MarketContext:
    """Normalized market snapshot for downstream plan generation."""

    symbol: str
    instrument_type: str
    price_precision: int
    shares: int
    rows: list[dict[str, Any]]
    data_source: str
    current_price: float
    last_close: float
    last_trade_date: str
    warnings: list[str] = field(default_factory=list)


def normalize_symbol(symbol: str) -> str:
    """Normalize a symbol to the SH/SZ prefixed form."""
    normalized = symbol.strip().upper()
    if normalized.startswith(("SH", "SZ")):
        return normalized
    if normalized.isdigit() and len(normalized) == 6:
        if normalized.startswith(("5", "6", "9")):
            return f"SH{normalized}"
        return f"SZ{normalized}"
    return normalized


def load_market_context(
    symbol: str,
    *,
    shares: int = 200,
    kline_count: int = 120,
    cfg: GridConfig = DEFAULT_CONFIG,
) -> MarketContext:
    """Load market data using pysnowball first and local cache as fallback."""
    normalized_symbol = normalize_symbol(symbol)
    rows, source = get_kline_data(normalized_symbol, count=kline_count)
    if not rows:
        raise ValueError(f"无法获取 {normalized_symbol} 的日线数据")

    normalized_rows = _normalize_rows(rows)
    if len(normalized_rows) < cfg.ma_long_window:
        raise ValueError(f"{normalized_symbol} 的日线数据不足 {cfg.ma_long_window} 根，无法计算 MA{cfg.ma_long_window}")

    last_row = normalized_rows[-1]
    last_close = float(last_row["close"])
    last_trade_date = _extract_trade_date(last_row)

    warnings: list[str] = []
    current_price = get_current_price(normalized_symbol)
    if current_price is None:
        current_price = last_close
        warnings.append("current_price_fallback_to_last_close")
    if source == "local":
        warnings.append("using_local_kline_cache")

    return MarketContext(
        symbol=normalized_symbol,
        instrument_type=cfg.instrument_type,
        price_precision=cfg.price_precision,
        shares=shares,
        rows=normalized_rows,
        data_source=source,
        current_price=float(current_price),
        last_close=last_close,
        last_trade_date=last_trade_date,
        warnings=warnings,
    )


def _normalize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized_rows: list[dict[str, Any]] = []
    for row in rows:
        normalized_rows.append(
            {
                "timestamp": row.get("timestamp"),
                "open": _to_float(row.get("open")),
                "high": _to_float(row.get("high")),
                "low": _to_float(row.get("low")),
                "close": _to_float(row.get("close")),
                "volume": row.get("volume"),
            }
        )
    normalized_rows.sort(key=lambda item: item.get("timestamp") or 0)
    return normalized_rows


def _extract_trade_date(row: dict[str, Any]) -> str:
    timestamp = row.get("timestamp")
    if timestamp is None:
        return "unknown"
    try:
        return datetime.fromtimestamp(float(timestamp) / 1000, tz=_SHANGHAI_TZ).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return "unknown"


def _to_float(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return math.nan
    return result if math.isfinite(result) else math.nan
