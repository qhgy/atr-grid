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
    atr_alert_3d_pct: float = 10.0
    atr_alert_5d_pct: float = 15.0
    step_change_alert_pct: float = 5.0

    # -- grid step boundaries (as fraction of band width) --
    step_min_fraction: float = 1 / 8  # band_width / 8
    step_max_fraction: float = 1 / 3  # band_width / 3
    grid_level_count: int = 3

    # -- position sizing --
    reference_position_shares: int = 2000
    reference_tranche_shares: int = 200
    trim_ratio: float = 0.10   # trend_up: sell 10% as tactical lot
    tactical_ratio: float = 0.20  # range: use 20% as tactical lot
    lot_size: int = 100  # A-share minimum lot

    # -- reference ladder --
    ladder_tranches: int = 3

    # ---- Phase 4: Trend-Hybrid 分层（默认全关闭，零侵入现有行为）----
    trend_hybrid_enabled: bool = False
    base_position_ratio: float = 0.0   # 底仓占总资产比例（0-1）
    cash_floor_ratio: float = 0.0      # 现金地板占总资产比例（0-1）

    # 位置分位窗口 + 分档阈值（刻度 0-100）
    position_window: int = 60
    position_band_low: float = 30.0    # <low：低位，swing 资金满用
    position_band_mid: float = 70.0    # [low, mid)：中段正常
    position_band_high: float = 85.0   # [mid, high)：偏高减仓；>=high：只卖不买

    # 每一档下「网格层预算」乘数
    position_alloc_low: float = 1.0
    position_alloc_mid_low: float = 0.67
    position_alloc_mid_high: float = 0.33
    position_alloc_high: float = 0.0   # 只卖不买

    # 应急补仓通道
    emergency_refill_drop_pct: float = 0.10
    emergency_refill_lookback: int = 20
    emergency_refill_use_ratio: float = 0.5

    # ---- MacroRsi14HardV2 信号引擎参数 ----
    signal_grid_levels: int = 8          # 网格档位数
    signal_grid_atr_mult: float = 0.7    # 网格步长 = ATR14 × 此倍数
    signal_rsi_oversold: float = 30.0    # RSI 超卖阈值（加倍买）
    signal_rsi_overbought: float = 75.0  # RSI 超买阈值（加倍卖）
    signal_rsi_multiplier: float = 2.5   # RSI 极端时的下单倍数
    signal_nvda_hard_threshold: float = -5.0   # NVDA 跌幅触发清仓
    signal_nvda_cautious_threshold: float = -4.0  # NVDA 跌幅触发减仓
    signal_cautious_target_pct: float = 0.6   # 减仓后目标仓位比例
    signal_cautious_buy_scale: float = 0.5    # 警戒时买单缩减比例
    signal_initial_capital: float = 100_000.0  # 信号引擎模拟初始资金


# Singleton default config for convenience.
DEFAULT_CONFIG = GridConfig()


# ---------------------------------------------------------------------------
# 参数 profile 工厂
# ---------------------------------------------------------------------------
_PROFILES: dict[str, dict[str, float | int | bool]] = {
    "stable": {},
    "dev": {
        "regime_ma_lookback": 7,
        "regime_slope_threshold": 0.35,
        "step_min_fraction": 1 / 6,
    },
    "aggressive": {
        "regime_ma_lookback": 7,
        "regime_slope_threshold": 0.40,
        "step_min_fraction": 1 / 5,
        "grid_level_count": 4,
    },
    "balanced": {
        "reference_tranche_shares": 300,
    },
    "yield": {
        "reference_tranche_shares": 400,
        "step_max_fraction": 1 / 4,
    },
    "trend_hybrid": {
        "reference_tranche_shares": 300,
        "trend_hybrid_enabled": True,
        "base_position_ratio": 0.40,
        "cash_floor_ratio": 0.20,
        "position_window": 60,
        "position_band_low": 30.0,
        "position_band_mid": 70.0,
        "position_band_high": 85.0,
        "position_alloc_low": 1.0,
        "position_alloc_mid_low": 0.67,
        "position_alloc_mid_high": 0.33,
        "position_alloc_high": 0.0,
        "emergency_refill_drop_pct": 0.10,
        "emergency_refill_lookback": 20,
        "emergency_refill_use_ratio": 0.5,
    },
}


def for_profile(name: str = "stable", **overrides) -> GridConfig:
    """Return a GridConfig tuned for a named profile."""
    preset = _PROFILES.get(name, {})
    merged: dict[str, float | int | bool] = {**preset, **overrides}
    return replace(GridConfig(), **merged) if merged else GridConfig()


def available_profiles() -> list[str]:
    """Return the list of registered profile names."""
    return sorted(_PROFILES.keys())
