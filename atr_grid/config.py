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

    # -- Phase 2.1: ADX + BBW --
    adx_window: int = 14
    adx_trend_threshold: float = 25.0  # ADX > 25 才确认趋势
    bbw_percentile_window: int = 252  # BBW 分位滑动窗口

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

    # -- Phase 3.1: 非对称 ladder step 乘数 --
    # sell_step > 1.0 → 卖档疏（赚单拉大）；rebuy_step < 1.0 → 买档密（接回更易）
    ladder_sell_step_multiplier: float = 1.0
    ladder_rebuy_step_multiplier: float = 1.0

    # -- pre-alert band --
    # 有效预警距 = max(prealert_abs_buffer, prealert_buffer_pct * target_price)
    # prealert_buffer_pct 让预警距随价位同比伸缩，避免贵标偏紧、贱标偏宽。
    prealert_abs_buffer: float = 0.005
    prealert_buffer_pct: float = 0.0

    # -- notification fallback threshold (远未触发预警带时用) --
    notify_threshold_pct: float = 1.5

    # -- adaptive step floor (Phase 2 正式使用，Phase 1 先预留字段) --
    min_step_pct: float = 0.0

    # ---- Phase 4: Trend-Hybrid 分层（默认全关闭，零侵入现有行为）----
    # 设计：engine/paper 在每日决策时调用 atr_grid.hybrid 的函数，拿到
    # 「底仓 + 网格层预算 + 现金地板」三段切分；engine 侧接线时只读这些
    # 字段，不直接写硬编码数字。默认 0/0/False 表示「这几层全不启用」。
    trend_hybrid_enabled: bool = False
    base_position_ratio: float = 0.0  # 底仓占总资产比例（0-1）
    cash_floor_ratio: float = 0.0  # 现金地板占总资产比例（0-1）

    # 位置分位窗口 + 分档阈值（刻度 0-100）
    position_window: int = 60
    position_band_low: float = 30.0  # <low：低位，swing 资金满用
    position_band_mid: float = 70.0  # [low, mid)：中段正常
    position_band_high: float = 85.0  # [mid, high)：偏高减仓；>=high：只卖不买

    # 每一档下「网格层预算」乘数（相对 swing_pool = equity - base - floor）
    position_alloc_low: float = 1.0
    position_alloc_mid_low: float = 0.67
    position_alloc_mid_high: float = 0.33
    position_alloc_high: float = 0.0  # 只卖不买（配合 only_sell=True）

    # 应急补仓通道（允许临时动用一部分现金地板）
    emergency_refill_drop_pct: float = 0.10  # 近 N 日高点到今日跌幅阈值
    emergency_refill_lookback: int = 20  # 近 N 日窗口
    emergency_refill_use_ratio: float = 0.5  # 可动用地板的比例（0-1）


# Singleton default config for convenience.
DEFAULT_CONFIG = GridConfig()


# ---------------------------------------------------------------------------
# 参数 profile 工厂
# ---------------------------------------------------------------------------
#
# stable        · 与 main 分支行为一致，用作回测基线。
# dev           · 激进一点：regime 阈值收紧、预警距加百分比保底、step 加 0.8% 下限。
# aggressive    · dev 基础上更敏感，用于 A/B 边界测试。
# balanced      · Phase 2.2：trade_shares=300。收益与 MDD 均衡（~8.8% excess / ~2% MDD）。
# yield         · Phase 2.2：trade_shares=400 + step_max=1/4。高胜率高 PF（~71% / PF ~20）。
# trend_hybrid  · Phase 4：底仓 40% + 现金地板 20% + 位置分档网格，参数全可调。
_PROFILES: dict[str, dict[str, float | int | bool]] = {
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
    "balanced": {
        "reference_tranche_shares": 300,
    },
    "yield": {
        "reference_tranche_shares": 400,
        "step_max_fraction": 1 / 4,
    },
    "trend_hybrid": {
        # 继承 balanced 的单笔手数，在此之上启用分层
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
        # 高位档自动让卖方力度 > 买方力度（engine 接线后生效）
        "ladder_sell_step_multiplier": 1.2,
        "ladder_rebuy_step_multiplier": 0.9,
    },
}


def for_profile(name: str = "stable", **overrides) -> GridConfig:
    """Return a GridConfig tuned for a named profile.

    Unknown profiles fall back to stable. Keyword overrides win over the
    preset defaults, so CLI experiments can override a single knob without
    forking the whole profile.
    """
    preset = _PROFILES.get(name, {})
    merged: dict[str, float | int | bool] = {**preset, **overrides}
    return replace(GridConfig(), **merged) if merged else GridConfig()


def available_profiles() -> list[str]:
    """Return the list of registered profile names (for CLI --help etc.)."""
    return sorted(_PROFILES.keys())
