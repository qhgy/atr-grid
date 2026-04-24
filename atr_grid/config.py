"""Centralized configuration for the ETF ATR grid MVP."""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(slots=True)
class GridConfig:
    """All tunable strategy parameters in one place.

    Default values match the original hard-coded behaviour.
    Override any field when constructing to experiment with parameters.
    """

    # -- instrument defaults --
    instrument_type: str = "etf"
    price_precision: int = 3

    # -- indicator windows --
    ma_short_window: int = 20
    ma_long_window: int = 60
    bb_window: int = 20
    bb_num_std: float = 2.0
    atr_window: int = 14

    # -- regime detection --
    regime_ma_lookback: int = 5
    regime_slope_threshold: float = 0.25

    # -- grid step boundaries (as fraction of band width) --
    step_min_fraction: float = 1 / 8  # band_width / 8
    step_max_fraction: float = 1 / 3  # band_width / 3
    grid_level_count: int = 3

    # -- position sizing --
    reference_position_shares: int = 2000
    reference_tranche_shares: int = 200
    trim_ratio: float = 0.10  # trend_up: sell 10% as tactical lot
    tactical_ratio: float = 0.20  # range: use 20% as tactical lot
    lot_size: int = 100  # A-share minimum lot

    # -- reference ladder --
    ladder_tranches: int = 3

    # -- pre-alert band --
    # 有效预警距 = max(prealert_abs_buffer, prealert_buffer_pct * target_price)
    # prealert_buffer_pct 让预警距随价位同比伸缩，避免贵标偏紧、贱标偏宽。
    prealert_abs_buffer: float = 0.005
    prealert_buffer_pct: float = 0.0

    # -- notification fallback threshold (远未触发预警带时用) --
    notify_threshold_pct: float = 1.5

    # -- adaptive step floor (Phase 2 正式使用，Phase 1 先预留字段) --
    min_step_pct: float = 0.0


# Singleton default config for convenience.
DEFAULT_CONFIG = GridConfig()


# ---------------------------------------------------------------------------
# 参数 profile 工厂
# ---------------------------------------------------------------------------
#
# stable     · 与 main 分支行为一致，用作回测基线。
# dev        · 激进一点：regime 阈值收紧、预警距加百分比保底、step 加 0.8% 下限。
# aggressive · dev 基础上更敏感，用于 A/B 边界测试。
_PROFILES: dict[str, dict[str, float | int]] = {
    "stable": {},
    "dev": {
        "regime_ma_lookback": 7,
        "regime_slope_threshold": 0.35,
        "step_min_fraction": 1 / 6,
        "prealert_buffer_pct": 0.003,
        "notify_threshold_pct": 1.0,
        "min_step_pct": 0.008,
    },
    "aggressive": {
        "regime_ma_lookback": 7,
        "regime_slope_threshold": 0.40,
        "step_min_fraction": 1 / 5,
        "prealert_buffer_pct": 0.005,
        "notify_threshold_pct": 0.8,
        "min_step_pct": 0.010,
        "grid_level_count": 4,
    },
}


def for_profile(name: str = "stable", **overrides) -> GridConfig:
    """Return a GridConfig tuned for a named profile.

    Unknown profiles fall back to stable. Keyword overrides win over the
    preset defaults, so CLI experiments can override a single knob without
    forking the whole profile.
    """
    preset = _PROFILES.get(name, {})
    merged: dict[str, float | int] = {**preset, **overrides}
    return replace(GridConfig(), **merged) if merged else GridConfig()


def available_profiles() -> list[str]:
    """Return the list of registered profile names (for CLI --help etc.)."""
    return sorted(_PROFILES.keys())
