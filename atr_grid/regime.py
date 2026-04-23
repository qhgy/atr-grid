"""Market regime detection for the ATR grid MVP."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .config import DEFAULT_CONFIG, GridConfig
from .indicators import IndicatorSnapshot


@dataclass(slots=True)
class RegimeResult:
    """The current market regime used to gate the grid."""

    regime: str
    grid_enabled: bool
    reason: str


def classify_regime(frame: pd.DataFrame, snapshot: IndicatorSnapshot, cfg: GridConfig = DEFAULT_CONFIG) -> RegimeResult:
    """Classify the latest market regime using MA20/MA60 structure."""
    if frame.empty:
        return RegimeResult("disabled", False, "日线数据为空，无法判断市场状态")
    if (
        snapshot.close is None
        or snapshot.atr14 is None
        or snapshot.atr14 <= 0
        or snapshot.bb_upper is None
        or snapshot.bb_middle is None
        or snapshot.bb_lower is None
        or snapshot.ma20 is None
        or snapshot.ma60 is None
    ):
        return RegimeResult("disabled", False, "关键指标缺失或 ATR 无效，无法启用网格")

    lookback = cfg.regime_ma_lookback
    threshold = cfg.regime_slope_threshold

    ma20_window = frame["ma20"].dropna().tail(lookback)
    if len(ma20_window) < lookback:
        return RegimeResult("disabled", False, f"MA20 历史窗口不足 {lookback} 根，无法判断趋势斜率")

    slope_ratio = abs(float(ma20_window.iloc[-1] - ma20_window.iloc[0])) / snapshot.atr14

    if (
        snapshot.close > snapshot.ma20 > snapshot.ma60
        and ma20_window.is_monotonic_increasing
        and slope_ratio >= threshold
    ):
        return RegimeResult("trend_up", False, "价格与均线呈多头趋势，MVP 默认禁用逆势双向网格")

    if (
        snapshot.close < snapshot.ma20 < snapshot.ma60
        and ma20_window.is_monotonic_decreasing
        and slope_ratio >= threshold
    ):
        return RegimeResult("trend_down", False, "价格与均线呈空头趋势，MVP 默认禁用抄底型网格")

    return RegimeResult("range", True, "价格围绕中轨震荡，允许使用 ATR 网格")
