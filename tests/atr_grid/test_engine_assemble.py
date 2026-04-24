"""End-to-end smoke tests for plan assembly — cover disabled/trend_up/trend_down/range branches."""

from __future__ import annotations

from atr_grid.data import MarketContext
from atr_grid.engine import _assemble_plan, build_plan_from_context
from atr_grid.indicators import IndicatorSnapshot
from atr_grid.regime import RegimeResult


def _ctx(current: float = 10.0, shares: int = 1000, **overrides) -> MarketContext:
    base = dict(
        symbol="SH515880",
        instrument_type="etf",
        price_precision=3,
        shares=shares,
        rows=[],
        data_source="test",
        current_price=current,
        last_close=current,
        last_trade_date="2026-04-16",
        warnings=[],
    )
    base.update(overrides)
    return MarketContext(**base)


def _snap(**overrides) -> IndicatorSnapshot:
    base = dict(close=10.0, atr14=0.5, bb_upper=11.0, bb_middle=10.0, bb_lower=9.0, ma20=10.0, ma60=9.5)
    base.update(overrides)
    return IndicatorSnapshot(**base)


class TestAssembleDisabledPath:
    def test_missing_atr_takes_disabled_branch(self):
        plan = _assemble_plan(_ctx(), _snap(atr14=None), RegimeResult("disabled", False, "缺 ATR"))
        assert plan.mode == "disabled"
        assert plan.grid_enabled is False
        assert plan.strategy_name == "数据不足，先不动作"

    def test_invalid_boll_band_takes_disabled_branch(self):
        # upper quantizes to the same value as lower → band invalid
        plan = _assemble_plan(_ctx(), _snap(bb_lower=10.0, bb_upper=10.0), RegimeResult("range", True, "ok"))
        assert plan.mode == "disabled"
        assert "invalid_boll_band" in plan.warnings


class TestAssembleRangePath:
    def test_range_regime_enables_grid(self):
        plan = _assemble_plan(
            _ctx(current=10.0, shares=1000),
            _snap(close=10.0),
            RegimeResult("range", True, "围绕中轨"),
        )
        assert plan.mode == "range_grid"
        assert plan.regime == "range"
        assert plan.primary_buy is not None
        assert plan.primary_sell is not None
        assert plan.grid_enabled is True
        # Tactical 20% of 1000 = 200, already lot-aligned
        assert plan.tactical_shares == 200

    def test_price_outside_band_disables_grid_but_keeps_range_mode(self):
        plan = _assemble_plan(
            _ctx(current=15.0, shares=1000),  # way above upper
            _snap(close=10.0),
            RegimeResult("range", True, "围绕中轨"),
        )
        assert plan.mode == "range_grid"
        assert plan.grid_enabled is False
        assert "current_price_outside_active_grid" in plan.warnings

    def test_warnings_not_mutated_across_calls(self):
        # Same context reused; previous warning append must not leak.
        ctx = _ctx(current=15.0, shares=1000)
        plan1 = _assemble_plan(ctx, _snap(close=10.0), RegimeResult("range", True, "first"))
        plan2 = _assemble_plan(ctx, _snap(close=10.0), RegimeResult("range", True, "second"))
        # Original context.warnings must stay empty even after two grid-disabled runs.
        assert ctx.warnings == []
        assert plan1.warnings.count("current_price_outside_active_grid") == 1
        assert plan2.warnings.count("current_price_outside_active_grid") == 1


class TestAssembleTrendUpPath:
    def test_trend_up_with_enough_shares_produces_trim_plan(self):
        plan = _assemble_plan(
            _ctx(current=11.5, shares=1000),
            _snap(close=11.5),
            RegimeResult("trend_up", False, "多头"),
        )
        assert plan.mode == "trend_trim"
        assert plan.trim_shares == 100  # 1000 * 0.10
        assert plan.primary_sell is not None
        assert plan.primary_buy is not None  # rebuy price populated
        assert plan.prealert_sell is not None
        assert plan.prealert_buy is not None

    def test_trend_up_with_few_shares_suggests_observe(self):
        plan = _assemble_plan(
            _ctx(current=11.5, shares=200),
            _snap(close=11.5),
            RegimeResult("trend_up", False, "多头"),
        )
        assert plan.mode == "trend_trim"
        assert plan.trim_shares == 0
        assert "观察" in plan.headline_action or "观察" in plan.reason


class TestAssembleTrendDownPath:
    def test_trend_down_takes_avoid_branch(self):
        plan = _assemble_plan(
            _ctx(current=8.5, shares=1000),
            _snap(close=8.5),
            RegimeResult("trend_down", False, "空头"),
        )
        assert plan.mode == "trend_avoid"
        assert plan.grid_enabled is False
        assert plan.strategy_name == "下跌趋势先观望"


class TestBuildPlanFromContextIntegration:
    """Light integration: build from a synthetic rows list, no network."""

    def test_flat_market_produces_range_plan(self):
        # 80 rows of flat price → range regime expected
        rows = [
            {"timestamp": 1_700_000_000_000 + i * 86_400_000, "open": 10.0, "high": 10.1, "low": 9.9, "close": 10.0, "volume": 1000}
            for i in range(80)
        ]
        ctx = MarketContext(
            symbol="TEST",
            instrument_type="etf",
            price_precision=3,
            shares=1000,
            rows=rows,
            data_source="synthetic",
            current_price=10.0,
            last_close=10.0,
            last_trade_date="2026-04-16",
            warnings=[],
        )
        plan = build_plan_from_context(ctx)
        assert plan.regime in ("range", "disabled")  # depends on bb_std near zero
