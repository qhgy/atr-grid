"""Market regime detection for the ATR grid MVP.

Phase 2.1 升级：加入 ADX14 趋势强度确认。
- MA 结构 + 斜率判定候选趋势；若 ADX 足够强才归为 trend_up/down（默认禁用网格）。
- ADX 不够强→ 降级为 range（启用网格），避免 MA 叫了但实际仍在震荡的假趋势误伤网格。
- ADX 缺失或预热不足时，降级为旧 MA 版判断（向后兼容）。
"""

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


def classify_regime(
    frame: pd.DataFrame,
    snapshot: IndicatorSnapshot,
    cfg: GridConfig = DEFAULT_CONFIG,
) -> RegimeResult:
    """Classify the latest market regime using MA structure + ADX confirmation."""
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
        return RegimeResult(
            "disabled", False, f"MA20 历史窗口不足 {lookback} 根，无法判断趋势斜率"
        )

    slope_ratio = abs(float(ma20_window.iloc[-1] - ma20_window.iloc[0])) / snapshot.atr14

    # --- Phase 2.1: ADX 趋势确认 ---
    # 若 ADX 可用：需结构 + 斜率 + ADX 足强 三者同时满足，才归为 trend_*
    # 若 ADX 不可用（样本未热身）：退回旧 MA 版判断。
    adx = snapshot.adx14
    adx_confirmed_trend = adx is not None and adx >= cfg.adx_trend_threshold
    adx_missing = adx is None

    bullish_structure = (
        snapshot.close > snapshot.ma20 > snapshot.ma60
        and ma20_window.is_monotonic_increasing
        and slope_ratio >= threshold
    )
    bearish_structure = (
        snapshot.close < snapshot.ma20 < snapshot.ma60
        and ma20_window.is_monotonic_decreasing
        and slope_ratio >= threshold
    )

    if bullish_structure and (adx_confirmed_trend or adx_missing):
        reason = "价格与均线呈多头趋势，MVP 默认禁用逆势双向网格"
        if adx is not None:
            reason += f"（ADX={adx:.1f} ≥ {cfg.adx_trend_threshold:.0f}）"
        return RegimeResult("trend_up", False, reason)

    if bearish_structure and (adx_confirmed_trend or adx_missing):
        reason = "价格与均线呈空头趋势，MVP 默认禁用抄底型网格"
        if adx is not None:
            reason += f"（ADX={adx:.1f} ≥ {cfg.adx_trend_threshold:.0f}）"
        return RegimeResult("trend_down", False, reason)

    # 结构呈现但 ADX 不足：被降级为 range（假趋势过滤）
    if (bullish_structure or bearish_structure) and adx is not None and adx < cfg.adx_trend_threshold:
        return RegimeResult(
            "range",
            True,
            f"MA 结构看趋势但 ADX={adx:.1f} < {cfg.adx_trend_threshold:.0f}，"
            f"判为弱趋势/震荡，允许网格",
        )

    return RegimeResult("range", True, "价格围绕中轨震荡，允许使用 ATR 网格")
