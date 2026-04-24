"""Data loading helpers for the ETF ATR grid MVP.

数据源优先级（load_market_context）：
1. 完整 cookies 雪球（atr_grid.data_snowball，默认读
   D:\\000trae\\A股数据\\aaa\\pysnowball\\dca_dashboard\\cookies.txt）
2. 旧链路 core.market_data.get_kline_data（pip pysnowball → 新浪 → akshare → 本地 JSON）
3. 全部失败时抛 ValueError

实例返回的 MarketContext.data_source 可能值：
- snowball-full ：走了新增的完整 cookies 雪球。
- api / api-cache / sina / akshare / local：core.market_data 原有标记。

若环境变量 ATRGRID_DISABLE_SNOWBALL_FULL=1，则跳过 snowball-full 直接走旧链路。
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any

from core.market_data import get_current_price, get_kline_data

from .config import DEFAULT_CONFIG, GridConfig
from .data_snowball import (
    SnowballCookieError,
    SnowballFetchError,
    fetch_kline_rows as _snowball_fetch_kline_rows,
)
from .fund_eastmoney import fetch_fund_meta as _fetch_fund_meta

_SHANGHAI_TZ = timezone(timedelta(hours=8))
_LOGGER = logging.getLogger(__name__)


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
    fund_meta: dict[str, Any] | None = None


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


def _fetch_kline_with_preference(
    symbol: str,
    *,
    count: int,
) -> tuple[list[dict[str, Any]], str, list[str]]:
    """按优先级拉日线。返回 (rows, source_label, extra_warnings)。"""
    extra_warnings: list[str] = []
    if os.environ.get("ATRGRID_DISABLE_SNOWBALL_FULL") != "1":
        try:
            rows = _snowball_fetch_kline_rows(symbol, count=count)
            if rows:
                return rows, "snowball-full", extra_warnings
        except SnowballCookieError as exc:
            _LOGGER.info("snowball-full 不可用（cookies 缺失），回退旧链路：%s", exc)
            extra_warnings.append("snowball_full_cookies_missing")
        except SnowballFetchError as exc:
            _LOGGER.warning("snowball-full 请求失败，回退旧链路：%s", exc)
            extra_warnings.append("snowball_full_fetch_failed")
        except Exception as exc:  # noqa: BLE001 - 健壮性回退
            _LOGGER.warning("snowball-full 意外异常，回退旧链路：%s", exc)
            extra_warnings.append("snowball_full_unexpected_error")

    rows, source = get_kline_data(symbol, count=count)
    return rows or [], source, extra_warnings


def load_market_context(
    symbol: str,
    *,
    shares: int = 200,
    kline_count: int = 120,
    cfg: GridConfig = DEFAULT_CONFIG,
    include_fund_meta: bool = True,
) -> MarketContext:
    """Load market data using snowball-full first and local cache as fallback."""
    normalized_symbol = normalize_symbol(symbol)
    rows, source, extra_warnings = _fetch_kline_with_preference(
        normalized_symbol, count=kline_count
    )
    if not rows:
        raise ValueError(f"无法获取 {normalized_symbol} 的日线数据")

    normalized_rows = _normalize_rows(rows)
    if len(normalized_rows) < cfg.ma_long_window:
        raise ValueError(
            f"{normalized_symbol} 的日线数据不足 {cfg.ma_long_window} 根，无法计算 MA{cfg.ma_long_window}"
        )

    last_row = normalized_rows[-1]
    last_close = float(last_row["close"])
    last_trade_date = _extract_trade_date(last_row)

    warnings: list[str] = list(extra_warnings)
    current_price = get_current_price(normalized_symbol)
    if current_price is None:
        current_price = last_close
        warnings.append("current_price_fallback_to_last_close")
    if source == "local":
        warnings.append("using_local_kline_cache")

    fund_meta: dict[str, Any] | None = None
    if include_fund_meta and cfg.instrument_type == "etf":
        try:
            fund_meta = _fetch_fund_meta(normalized_symbol)
        except Exception as exc:  # noqa: BLE001 - 基金元数据为非关键路径
            _LOGGER.info("东财基金元数据拉取失败，忽略：%s", exc)
            warnings.append("fund_meta_fetch_failed")

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
        fund_meta=fund_meta,
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
