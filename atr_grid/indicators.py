"""Indicator calculations used by the ATR grid MVP."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import DEFAULT_CONFIG, GridConfig


@dataclass(slots=True)
class IndicatorSnapshot:
    """Latest indicator values required by the engine."""

    close: float | None
    atr14: float | None
    bb_upper: float | None
    bb_middle: float | None
    bb_lower: float | None
    ma20: float | None
    ma60: float | None


def build_indicator_frame(rows: list[dict], cfg: GridConfig = DEFAULT_CONFIG) -> pd.DataFrame:
    """Build an analysis frame with ATR/BOLL/MA columns."""
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame

    frame["date"] = pd.to_datetime(frame["timestamp"], unit="ms")
    frame = frame.sort_values("date").reset_index(drop=True)

    close = pd.to_numeric(frame["close"], errors="coerce")
    high = pd.to_numeric(frame["high"], errors="coerce")
    low = pd.to_numeric(frame["low"], errors="coerce")

    frame["ma20"] = close.rolling(window=cfg.ma_short_window).mean()
    frame["ma60"] = close.rolling(window=cfg.ma_long_window).mean()

    frame["bb_middle"] = frame["ma20"]
    frame["bb_std"] = close.rolling(window=cfg.bb_window).std()
    frame["bb_upper"] = frame["bb_middle"] + cfg.bb_num_std * frame["bb_std"]
    frame["bb_lower"] = frame["bb_middle"] - cfg.bb_num_std * frame["bb_std"]

    high_low = high - low
    high_close = (high - close.shift()).abs()
    low_close = (low - close.shift()).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    frame["atr14"] = true_range.rolling(window=cfg.atr_window).mean()

    return frame


def latest_snapshot(frame: pd.DataFrame) -> IndicatorSnapshot:
    """Return the latest indicator snapshot."""
    if frame.empty:
        return IndicatorSnapshot(None, None, None, None, None, None, None)

    latest = frame.iloc[-1]
    return IndicatorSnapshot(
        close=_as_float(latest.get("close")),
        atr14=_as_float(latest.get("atr14")),
        bb_upper=_as_float(latest.get("bb_upper")),
        bb_middle=_as_float(latest.get("bb_middle")),
        bb_lower=_as_float(latest.get("bb_lower")),
        ma20=_as_float(latest.get("ma20")),
        ma60=_as_float(latest.get("ma60")),
    )


def _as_float(value) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if np.isnan(numeric):
        return None
    return numeric
