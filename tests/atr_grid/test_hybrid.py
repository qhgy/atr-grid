"""Tests for atr_grid.hybrid (Phase 4 Trend-Hybrid 模块).

独立验证：位置分位 · 分档 · 资金配额 · 现金地板 guard · 应急通道。
不依赖 engine / paper，确保 MVP 阶段新模块与主循环零耦合。
"""

from __future__ import annotations

import pandas as pd
import pytest

from atr_grid.config import GridConfig, for_profile
from atr_grid.hybrid import (
    CapitalAllocation,
    CashFloorDecision,
    PositionBand,
    cash_floor_guard,
    compute_capital_allocation,
    default_bands_from_config,
    position_percentile,
    resolve_band,
    should_emergency_refill,
)


# ---------------------------------------------------------------------------
# position_percentile
# ---------------------------------------------------------------------------


def _make_frame(highs, lows, closes):
    return pd.DataFrame({"high": highs, "low": lows, "close": closes})


def test_position_percentile_mid():
    frame = _make_frame(
        highs=[1.5, 1.6, 1.4, 1.7, 1.5],
        lows=[1.0, 1.1, 1.0, 1.2, 1.1],
        closes=[1.3, 1.4, 1.2, 1.5, 1.35],
    )
    pct = position_percentile(frame, window=5)
    # span = max(1.7) - min(1.0) = 0.7; price=1.35; (1.35-1.0)/0.7 = 0.5 -> 50
    assert pct is not None
    assert abs(pct - 50.0) < 1e-6


def test_position_percentile_at_high():
    frame = _make_frame(
        highs=[1.5, 1.6, 1.4, 1.7, 2.0],
        lows=[1.0, 1.1, 1.0, 1.2, 1.1],
        closes=[1.3, 1.4, 1.2, 1.5, 2.0],  # close == high == 2.0
    )
    pct = position_percentile(frame, window=5)
    assert pct == pytest.approx(100.0)


def test_position_percentile_at_low():
    frame = _make_frame(
        highs=[1.5, 1.6, 1.4, 1.7, 1.5],
        lows=[1.0, 1.1, 0.8, 1.2, 1.1],
        closes=[1.3, 1.4, 0.8, 1.5, 0.8],  # close == min low
    )
    pct = position_percentile(frame, window=5)
    assert pct == pytest.approx(0.0)


def test_position_percentile_empty_returns_none():
    assert position_percentile(pd.DataFrame(), window=60) is None


def test_position_percentile_missing_column_returns_none():
    frame = pd.DataFrame({"foo": [1, 2, 3]})
    assert position_percentile(frame, window=5) is None


def test_position_percentile_zero_span_returns_none():
    frame = _make_frame(
        highs=[1.0, 1.0, 1.0],
        lows=[1.0, 1.0, 1.0],
        closes=[1.0, 1.0, 1.0],
    )
    assert position_percentile(frame, window=3) is None


def test_position_percentile_window_larger_than_data():
    # window=60 但只有 5 行，应用可用数据而不抛错
    frame = _make_frame(
        highs=[1.5, 1.6, 1.4, 1.7, 1.8],
        lows=[1.0, 1.1, 1.0, 1.2, 1.1],
        closes=[1.3, 1.4, 1.2, 1.5, 1.45],
    )
    pct = position_percentile(frame, window=60)
    # span = 1.8 - 1.0 = 0.8; price=1.45; (1.45-1.0)/0.8 = 0.5625 -> 56.25
    assert pct is not None
    assert abs(pct - 56.25) < 1e-6


# ---------------------------------------------------------------------------
# resolve_band
# ---------------------------------------------------------------------------


def test_default_bands_are_ordered():
    cfg = GridConfig()
    bands = default_bands_from_config(cfg)
    assert [b.name for b in bands] == ["low", "mid_low", "mid_high", "high"]
    for a, b in zip(bands, bands[1:]):
        assert a.high == b.low  # 连续无空隙
    # 高位档“只卖不买”
    assert bands[-1].only_sell is True


def test_resolve_band_low_mid_high():
    cfg = GridConfig()
    assert resolve_band(10.0, cfg).name == "low"
    assert resolve_band(50.0, cfg).name == "mid_low"
    assert resolve_band(75.0, cfg).name == "mid_high"
    assert resolve_band(90.0, cfg).name == "high"
    assert resolve_band(100.0, cfg).name == "high"


def test_resolve_band_none_returns_conservative():
    # 无数据时走保守档，不能恐慌性开 only_sell
    band = resolve_band(None, GridConfig())
    assert band.name == "mid_low"
    assert band.only_sell is False


def test_resolve_band_boundaries_inclusive_of_low():
    cfg = GridConfig()
    assert resolve_band(cfg.position_band_low, cfg).name == "mid_low"  # [30, 70)
    assert resolve_band(cfg.position_band_mid, cfg).name == "mid_high"  # [70, 85)
    assert resolve_band(cfg.position_band_high, cfg).name == "high"  # [85, 100]


# ---------------------------------------------------------------------------
# compute_capital_allocation
# ---------------------------------------------------------------------------


def test_allocation_low_band_max_swing():
    cfg = for_profile("trend_hybrid")
    alloc = compute_capital_allocation(100_000.0, percentile=10.0, cfg=cfg)
    assert alloc.band.name == "low"
    assert alloc.base_budget == pytest.approx(40_000.0)
    assert alloc.cash_floor == pytest.approx(20_000.0)
    # swing_pool = 100000 - 40000 - 20000 = 40000; low band ratio=1.0
    assert alloc.swing_budget == pytest.approx(40_000.0)
    assert alloc.only_sell is False


def test_allocation_high_band_only_sell_zero_swing():
    cfg = for_profile("trend_hybrid")
    alloc = compute_capital_allocation(100_000.0, percentile=95.0, cfg=cfg)
    assert alloc.band.name == "high"
    assert alloc.swing_budget == pytest.approx(0.0)
    assert alloc.only_sell is True


def test_allocation_mid_high_band_partial_swing():
    cfg = for_profile("trend_hybrid")
    alloc = compute_capital_allocation(100_000.0, percentile=80.0, cfg=cfg)
    assert alloc.band.name == "mid_high"
    # swing_pool=40000; mid_high ratio=0.33
    assert alloc.swing_budget == pytest.approx(40_000.0 * 0.33)


def test_allocation_rejects_impossible_ratios():
    cfg = GridConfig(base_position_ratio=0.7, cash_floor_ratio=0.5)
    with pytest.raises(ValueError):
        compute_capital_allocation(100_000.0, percentile=50.0, cfg=cfg)


def test_allocation_defaults_disabled_behaviour():
    # 默认 GridConfig 下 hybrid 关闭，base/floor=0，swing_pool == equity
    cfg = GridConfig()
    alloc = compute_capital_allocation(100_000.0, percentile=50.0, cfg=cfg)
    assert alloc.base_budget == pytest.approx(0.0)
    assert alloc.cash_floor == pytest.approx(0.0)
    # 默认 mid_low swing_ratio=0.67
    assert alloc.swing_budget == pytest.approx(100_000.0 * 0.67)


def test_allocation_negative_equity_raises():
    cfg = for_profile("trend_hybrid")
    with pytest.raises(ValueError):
        compute_capital_allocation(-1.0, percentile=50.0, cfg=cfg)


# ---------------------------------------------------------------------------
# cash_floor_guard
# ---------------------------------------------------------------------------


def test_guard_non_buy_passes_through():
    cfg = for_profile("trend_hybrid")
    d = cash_floor_guard(cash_before=0.0, intended_amount=-500.0, total_equity=100_000.0, cfg=cfg)
    assert d.approved_amount == -500.0
    assert d.rejected is False


def test_guard_full_approval():
    cfg = for_profile("trend_hybrid")
    # floor = 20000; cash=50000; 想花 10000 -> spendable=30000 够
    d = cash_floor_guard(cash_before=50_000.0, intended_amount=10_000.0, total_equity=100_000.0, cfg=cfg)
    assert d.approved_amount == pytest.approx(10_000.0)
    assert d.rejected is False
    assert "approved_full" in d.reason


def test_guard_partial_approval_when_near_floor():
    cfg = for_profile("trend_hybrid")
    # floor = 20000; cash=25000; spendable=5000; 想花 10000 -> 部分放行 5000
    d = cash_floor_guard(cash_before=25_000.0, intended_amount=10_000.0, total_equity=100_000.0, cfg=cfg)
    assert d.approved_amount == pytest.approx(5_000.0)
    assert d.rejected is False
    assert "approved_partial" in d.reason


def test_guard_rejects_when_below_floor():
    cfg = for_profile("trend_hybrid")
    # cash=20000 == floor -> spendable=0 拒绝
    d = cash_floor_guard(cash_before=20_000.0, intended_amount=5_000.0, total_equity=100_000.0, cfg=cfg)
    assert d.approved_amount == 0.0
    assert d.rejected is True
    assert "cash_floor_blocked" in d.reason


def test_guard_emergency_unlock_allows_deeper():
    cfg = for_profile("trend_hybrid")  # use_ratio=0.5, floor=20000
    # 应急后 effective_floor = 20000 * (1 - 0.5) = 10000
    # cash=15000 -> spendable=5000 部分放行
    d = cash_floor_guard(
        cash_before=15_000.0,
        intended_amount=8_000.0,
        total_equity=100_000.0,
        cfg=cfg,
        emergency_unlocked=True,
    )
    assert d.rejected is False
    assert d.approved_amount == pytest.approx(5_000.0)
    assert "emergency_unlock" in d.reason


def test_guard_total_equity_zero_rejects():
    cfg = for_profile("trend_hybrid")
    d = cash_floor_guard(cash_before=0.0, intended_amount=1000.0, total_equity=0.0, cfg=cfg)
    assert d.rejected is True


# ---------------------------------------------------------------------------
# should_emergency_refill
# ---------------------------------------------------------------------------


def test_emergency_refill_triggers_on_big_drop():
    cfg = for_profile("trend_hybrid")  # drop_pct=0.10, lookback=20
    highs = [1.0] * 19 + [1.5]  # 最高 1.5
    lows = [0.9] * 20
    closes = [1.0] * 19 + [1.30]  # 今日 1.30 -> drawdown = (1.5-1.3)/1.5 ≈ 13.3% >= 10%
    frame = _make_frame(highs, lows, closes)
    assert should_emergency_refill(frame, cfg) is True


def test_emergency_refill_not_triggered_on_small_drop():
    cfg = for_profile("trend_hybrid")
    highs = [1.0] * 19 + [1.5]
    lows = [0.9] * 20
    closes = [1.0] * 19 + [1.42]  # drawdown ≈ 5.3% < 10%
    frame = _make_frame(highs, lows, closes)
    assert should_emergency_refill(frame, cfg) is False


def test_emergency_refill_empty_frame():
    cfg = for_profile("trend_hybrid")
    assert should_emergency_refill(pd.DataFrame(), cfg) is False


def test_emergency_refill_respects_config_drop_pct():
    # 自定义阈值 5%，drawdown 5.3% -> 触发
    cfg = for_profile("trend_hybrid", emergency_refill_drop_pct=0.05)
    highs = [1.0] * 19 + [1.5]
    closes = [1.0] * 19 + [1.42]
    frame = _make_frame(highs, [0.9] * 20, closes)
    assert should_emergency_refill(frame, cfg) is True


# ---------------------------------------------------------------------------
# profile 集成
# ---------------------------------------------------------------------------


def test_trend_hybrid_profile_is_registered():
    cfg = for_profile("trend_hybrid")
    assert cfg.trend_hybrid_enabled is True
    assert cfg.base_position_ratio == pytest.approx(0.40)
    assert cfg.cash_floor_ratio == pytest.approx(0.20)
    assert cfg.reference_tranche_shares == 300


def test_existing_profiles_have_hybrid_disabled_by_default():
    for name in ("stable", "dev", "aggressive", "balanced", "yield"):
        cfg = for_profile(name)
        assert cfg.trend_hybrid_enabled is False, f"profile {name} should default-off hybrid"
        assert cfg.base_position_ratio == 0.0, f"profile {name} should have no base layer"
        assert cfg.cash_floor_ratio == 0.0, f"profile {name} should have no cash floor"
