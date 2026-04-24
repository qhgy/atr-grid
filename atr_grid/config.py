"""Centralized configuration for the ETF ATR grid MVP."""

from __future__ import annotations

from dataclasses import dataclass


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
    trim_ratio: float = 0.10   # trend_up: sell 10% as tactical lot
    tactical_ratio: float = 0.20  # range: use 20% as tactical lot
    lot_size: int = 100  # A-share minimum lot

    # -- reference ladder --
    ladder_tranches: int = 3

    # -- pre-alert band --
    prealert_abs_buffer: float = 0.005


# Singleton default config for convenience.
DEFAULT_CONFIG = GridConfig()
