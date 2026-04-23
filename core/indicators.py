"""Shared technical indicator calculations."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def to_frame(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Convert kline rows to a sorted DataFrame."""
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    if "timestamp" in frame.columns:
        frame["date"] = pd.to_datetime(frame["timestamp"], unit="ms")
        frame = frame.sort_values("date").reset_index(drop=True)
    return frame


def apply_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    """Apply MA, RSI, BOLL and ATR columns to the frame."""
    if frame.empty:
        return frame

    result = frame.copy()
    close = pd.to_numeric(result["close"], errors="coerce")
    high = pd.to_numeric(result["high"], errors="coerce")
    low = pd.to_numeric(result["low"], errors="coerce")

    result["ma5"] = close.rolling(window=5).mean()
    result["ma10"] = close.rolling(window=10).mean()
    result["ma20"] = close.rolling(window=20).mean()
    result["ma60"] = close.rolling(window=60).mean()

    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(window=14).mean()
    rs = gain / loss
    result["rsi"] = 100 - (100 / (1 + rs))
    result.loc[loss == 0, "rsi"] = 100

    result["bb_middle"] = close.rolling(window=20).mean()
    result["bb_std"] = close.rolling(window=20).std()
    result["bb_upper"] = result["bb_middle"] + 2 * result["bb_std"]
    result["bb_lower"] = result["bb_middle"] - 2 * result["bb_std"]

    high_low = high - low
    high_close = (high - close.shift()).abs()
    low_close = (low - close.shift()).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    result["atr"] = true_range.rolling(window=14).mean()

    exp1 = close.ewm(span=12, adjust=False).mean()
    exp2 = close.ewm(span=26, adjust=False).mean()
    result["macd"] = exp1 - exp2
    result["signal"] = result["macd"].ewm(span=9, adjust=False).mean()
    result["macd_hist"] = result["macd"] - result["signal"]

    return result


def latest_snapshot(frame: pd.DataFrame) -> dict[str, float | None]:
    """Extract the latest indicator snapshot from a kline frame."""
    if frame.empty:
        return {}
    latest = frame.iloc[-1]
    return {
        "ma5": _as_float(latest.get("ma5")),
        "ma10": _as_float(latest.get("ma10")),
        "ma20": _as_float(latest.get("ma20")),
        "ma60": _as_float(latest.get("ma60")),
        "rsi": _as_float(latest.get("rsi")),
        "macd": _as_float(latest.get("macd")),
        "signal": _as_float(latest.get("signal")),
        "bb_upper": _as_float(latest.get("bb_upper")),
        "bb_middle": _as_float(latest.get("bb_middle")),
        "bb_lower": _as_float(latest.get("bb_lower")),
        "atr": _as_float(latest.get("atr")),
        "high_100d": _as_float(pd.to_numeric(frame["high"], errors="coerce").max()),
        "low_100d": _as_float(pd.to_numeric(frame["low"], errors="coerce").min()),
        "high_20d": _as_float(pd.to_numeric(frame["high"], errors="coerce").tail(20).max()),
        "low_20d": _as_float(pd.to_numeric(frame["low"], errors="coerce").tail(20).min()),
    }


def close_metrics(rows: list[dict[str, Any]]) -> dict[str, float | None]:
    """Return the subset of indicators used by the analysis script."""
    frame = apply_indicators(to_frame(rows))
    snapshot = latest_snapshot(frame)
    return {
        "ma5": snapshot.get("ma5"),
        "ma10": snapshot.get("ma10"),
        "ma20": snapshot.get("ma20"),
        "ma60": snapshot.get("ma60"),
        "rsi": snapshot.get("rsi"),
        "boll_upper": snapshot.get("bb_upper"),
        "boll_mid": snapshot.get("bb_middle"),
        "boll_lower": snapshot.get("bb_lower"),
    }


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if np.isnan(numeric):
        return None
    return numeric
