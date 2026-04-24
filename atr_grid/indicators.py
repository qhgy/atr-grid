"""Indicator calculations used by the ATR grid MVP.

Phase 2.1 扩展：新增 ADX14（Wilder）+ BBW（布林带宽度）+ BBW 分位。
- ADX 用于 regime 趋势强度确认；
- BBW 用于识别 squeeze/快突破 / expansion。
"""

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
    # Phase 2.1 新增，为保持向后兼容均为可选 None，放在末尾
    adx14: float | None = None
    bbw: float | None = None  # (upper-lower)/middle
    bbw_percentile: float | None = None  # rolling 252 分位值 0–1


def build_indicator_frame(rows: list[dict], cfg: GridConfig = DEFAULT_CONFIG) -> pd.DataFrame:
    """Build an analysis frame with ATR/BOLL/MA/ADX/BBW columns."""
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

    # BBW: 布林带宽度 = (upper - lower) / middle，显示波动度历史相对值
    with np.errstate(divide="ignore", invalid="ignore"):
        frame["bbw"] = (frame["bb_upper"] - frame["bb_lower"]) / frame["bb_middle"]
    frame["bbw_percentile"] = (
        frame["bbw"]
        .rolling(window=cfg.bbw_percentile_window, min_periods=max(20, cfg.bbw_percentile_window // 4))
        .rank(pct=True)
    )

    # TR + ATR (原有 SMA 近似，保持向后兼容)
    high_low = high - low
    high_close = (high - close.shift()).abs()
    low_close = (low - close.shift()).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    frame["atr14"] = true_range.rolling(window=cfg.atr_window).mean()

    # ADX14 (Wilder’s smoothing)
    frame["adx14"] = _wilder_adx(high, low, close, true_range, window=cfg.adx_window)

    return frame


def _wilder_adx(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    true_range: pd.Series,
    *,
    window: int = 14,
) -> pd.Series:
    """Wilder ADX：经典定义，EMA 平滑用 alpha=1/window 近似 SMMA。"""
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    plus_dm = pd.Series(plus_dm, index=high.index)
    minus_dm = pd.Series(minus_dm, index=high.index)

    alpha = 1.0 / window
    atr = true_range.ewm(alpha=alpha, adjust=False, min_periods=window).mean()
    plus_di = 100.0 * plus_dm.ewm(alpha=alpha, adjust=False, min_periods=window).mean() / atr
    minus_di = 100.0 * minus_dm.ewm(alpha=alpha, adjust=False, min_periods=window).mean() / atr

    denom = (plus_di + minus_di).replace(0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / denom
    adx = dx.ewm(alpha=alpha, adjust=False, min_periods=window).mean()
    return adx


def latest_snapshot(frame: pd.DataFrame) -> IndicatorSnapshot:
    """Return the latest indicator snapshot."""
    if frame.empty:
        return IndicatorSnapshot(
            close=None,
            atr14=None,
            bb_upper=None,
            bb_middle=None,
            bb_lower=None,
            ma20=None,
            ma60=None,
        )

    latest = frame.iloc[-1]
    return IndicatorSnapshot(
        close=_as_float(latest.get("close")),
        atr14=_as_float(latest.get("atr14")),
        bb_upper=_as_float(latest.get("bb_upper")),
        bb_middle=_as_float(latest.get("bb_middle")),
        bb_lower=_as_float(latest.get("bb_lower")),
        ma20=_as_float(latest.get("ma20")),
        ma60=_as_float(latest.get("ma60")),
        adx14=_as_float(latest.get("adx14")),
        bbw=_as_float(latest.get("bbw")),
        bbw_percentile=_as_float(latest.get("bbw_percentile")),
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
