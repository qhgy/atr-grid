"""Plan generation and replay logic for the ETF ATR grid MVP."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal, ROUND_HALF_UP

from .config import DEFAULT_CONFIG, GridConfig
from .data import MarketContext, load_market_context
from .indicators import IndicatorSnapshot, build_indicator_frame, latest_snapshot
from .regime import RegimeResult, classify_regime


@dataclass(slots=True)
class GridPlan:
    """Structured output for the ETF ATR grid MVP."""

    symbol: str
    instrument_type: str
    data_source: str
    current_price: float
    last_close: float
    last_trade_date: str
    price_precision: int
    snapshot: IndicatorSnapshot
    strategy_name: str
    headline_action: str
    tactical_shares: int
    action_steps: list[str]
    reference_position_shares: int
    reference_tranche_shares: int
    reference_sell_ladder: list[float]
    reference_rebuy_ladder: list[float]
    trend_sell_limit_tranches: int
    trend_sell_limit_shares: int
    trend_adjustment_note: str
    mode: str
    regime: str
    grid_enabled: bool
    reason: str
    center: float | None
    step: float | None
    primary_buy: float | None
    primary_sell: float | None
    buy_levels: list[float]
    sell_levels: list[float]
    lower_invalidation: float | None
    upper_breakout: float | None
    trim_shares: int
    rebuy_price: float | None
    shares: int
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _PlanContext:
    """Precomputed values shared by regime-specific plan builders."""

    context: MarketContext
    snapshot: IndicatorSnapshot
    regime: RegimeResult
    warnings: list[str]
    center: float
    step: float
    lower: float
    upper: float
    lower_invalidation: float
    upper_breakout: float
    reference_sell_ladder: list[float]
    reference_rebuy_ladder: list[float]
    cfg: GridConfig


def generate_plan(symbol: str, *, shares: int = 200, kline_count: int = 120, cfg: GridConfig = DEFAULT_CONFIG) -> GridPlan:
    """Generate a fresh grid plan from live/cache-backed data."""
    context = load_market_context(symbol, shares=shares, kline_count=kline_count, cfg=cfg)
    return build_plan_from_context(context, cfg=cfg)


def build_plan_from_context(context: MarketContext, cfg: GridConfig = DEFAULT_CONFIG) -> GridPlan:
    """Build a grid plan from a normalized market context."""
    frame = build_indicator_frame(context.rows, cfg)
    snapshot = latest_snapshot(frame)
    regime = classify_regime(frame, snapshot, cfg)
    return _assemble_plan(context, snapshot, regime, cfg)


def _assemble_plan(
    context: MarketContext,
    snapshot: IndicatorSnapshot,
    regime: RegimeResult,
    cfg: GridConfig = DEFAULT_CONFIG,
) -> GridPlan:
    """Assemble a GridPlan given precomputed snapshot and regime."""
    warnings = list(context.warnings)

    if snapshot.bb_lower is None or snapshot.bb_middle is None or snapshot.bb_upper is None or snapshot.atr14 is None:
        return _disabled_plan(context, snapshot, regime, warnings, cfg)

    center = quantize_price(snapshot.bb_middle, context.price_precision)
    lower = quantize_price(snapshot.bb_lower, context.price_precision)
    upper = quantize_price(snapshot.bb_upper, context.price_precision)
    if upper <= lower:
        warnings.append("invalid_boll_band")
        return _disabled_plan(context, snapshot, RegimeResult("disabled", False, "布林带上下沿无效"), warnings, cfg)

    step = _effective_step(snapshot.atr14, lower, upper, context.price_precision, cfg)
    lower_invalidation = quantize_price(lower - snapshot.atr14, context.price_precision)
    upper_breakout = quantize_price(upper + snapshot.atr14, context.price_precision)
    ladder_anchor_sell = quantize_price(max(context.current_price, upper), context.price_precision)
    reference_sell_ladder, reference_rebuy_ladder = _build_reference_ladder(
        ladder_anchor_sell, snapshot.atr14, context.price_precision, cfg
    )

    pctx = _PlanContext(
        context=context,
        snapshot=snapshot,
        regime=regime,
        warnings=warnings,
        center=center,
        step=step,
        lower=lower,
        upper=upper,
        lower_invalidation=lower_invalidation,
        upper_breakout=upper_breakout,
        reference_sell_ladder=reference_sell_ladder,
        reference_rebuy_ladder=reference_rebuy_ladder,
        cfg=cfg,
    )

    if regime.regime != "range":
        if regime.regime == "trend_up":
            return _build_trend_up_plan(pctx)
        return _build_trend_down_plan(pctx)

    return _build_range_plan(pctx)


def replay_symbol(symbol: str, *, lookback: int = 60, shares: int = 200, kline_count: int = 240, cfg: GridConfig = DEFAULT_CONFIG) -> dict[str, int | str]:
    """Replay the plan on rolling daily closes and measure next-day hits."""
    context = load_market_context(symbol, shares=shares, kline_count=max(kline_count, lookback + 120), cfg=cfg)
    rows = context.rows
    start_index = max(cfg.ma_long_window - 1, len(rows) - lookback - 1)

    # Pre-compute indicators once; per-day snapshots come from slicing.
    full_frame = build_indicator_frame(rows, cfg)

    buy_hits = 0
    sell_hits = 0
    invalidations = 0
    breakouts = 0
    days_grid_enabled = 0

    for index in range(start_index, len(rows) - 1):
        sub_frame = full_frame.iloc[: index + 1]
        snapshot = latest_snapshot(sub_frame)
        regime = classify_regime(sub_frame, snapshot, cfg)

        close_today = float(rows[index]["close"])
        history_context = MarketContext(
            symbol=context.symbol,
            instrument_type=context.instrument_type,
            price_precision=context.price_precision,
            shares=context.shares,
            rows=rows,
            data_source="replay",
            current_price=close_today,
            last_close=close_today,
            last_trade_date=context.last_trade_date,
            warnings=[],
        )
        plan = _assemble_plan(history_context, snapshot, regime, cfg)
        next_row = rows[index + 1]

        if not plan.grid_enabled:
            continue

        days_grid_enabled += 1
        next_high = float(next_row["high"])
        next_low = float(next_row["low"])

        if plan.primary_buy is not None and next_low <= plan.primary_buy:
            buy_hits += 1
        if plan.primary_sell is not None and next_high >= plan.primary_sell:
            sell_hits += 1
        if plan.lower_invalidation is not None and next_low <= plan.lower_invalidation:
            invalidations += 1
        if plan.upper_breakout is not None and next_high >= plan.upper_breakout:
            breakouts += 1

    return {
        "symbol": context.symbol,
        "lookback": min(lookback, max(len(rows) - 1, 0)),
        "buy_hits": buy_hits,
        "sell_hits": sell_hits,
        "invalidations": invalidations,
        "breakouts": breakouts,
        "days_grid_enabled": days_grid_enabled,
        "data_source": context.data_source,
    }


def plan_to_dict(plan: GridPlan) -> dict:
    """Convert a plan dataclass into JSON-safe output."""
    payload = asdict(plan)
    payload["snapshot"] = asdict(plan.snapshot)
    return payload


def quantize_price(value: float, precision: int) -> float:
    """Quantize price using decimal half-up rounding."""
    step = Decimal("1").scaleb(-precision)
    return float(Decimal(str(value)).quantize(step, rounding=ROUND_HALF_UP))


def _effective_step(atr14: float, lower: float, upper: float, precision: int, cfg: GridConfig = DEFAULT_CONFIG) -> float:
    band_width = upper - lower
    min_step = band_width * cfg.step_min_fraction
    max_step = band_width * cfg.step_max_fraction
    raw_step = atr14
    step = min(max(raw_step, min_step), max_step)
    return quantize_price(step, precision)


def _generate_buy_levels(center: float, step: float, lower: float, precision: int, cfg: GridConfig = DEFAULT_CONFIG) -> list[float]:
    levels: list[float] = []
    for index in range(1, cfg.grid_level_count + 1):
        candidate = quantize_price(center - index * step, precision)
        if candidate >= lower:
            levels.append(candidate)
    return levels


def _generate_sell_levels(center: float, step: float, upper: float, precision: int, cfg: GridConfig = DEFAULT_CONFIG) -> list[float]:
    levels: list[float] = []
    for index in range(1, cfg.grid_level_count + 1):
        candidate = quantize_price(center + index * step, precision)
        if candidate <= upper:
            levels.append(candidate)
    return levels


def _make_plan(
    context: MarketContext,
    snapshot: IndicatorSnapshot,
    regime: RegimeResult,
    warnings: list[str],
    cfg: GridConfig = DEFAULT_CONFIG,
    **overrides,
) -> GridPlan:
    """Factory: build a GridPlan with common fields pre-filled."""
    defaults = dict(
        symbol=context.symbol,
        instrument_type=context.instrument_type,
        data_source=context.data_source,
        current_price=context.current_price,
        last_close=context.last_close,
        last_trade_date=context.last_trade_date,
        price_precision=context.price_precision,
        snapshot=snapshot,
        regime=regime.regime,
        shares=context.shares,
        warnings=warnings,
        reference_position_shares=cfg.reference_position_shares,
        reference_tranche_shares=cfg.reference_tranche_shares,
        # safe defaults for optional fields
        tactical_shares=0,
        grid_enabled=False,
        center=None,
        step=None,
        primary_buy=None,
        primary_sell=None,
        buy_levels=[],
        sell_levels=[],
        lower_invalidation=None,
        upper_breakout=None,
        trim_shares=0,
        rebuy_price=None,
        reference_sell_ladder=[],
        reference_rebuy_ladder=[],
        trend_sell_limit_tranches=0,
        trend_sell_limit_shares=0,
    )
    defaults.update(overrides)
    return GridPlan(**defaults)


def _disabled_plan(
    context: MarketContext,
    snapshot: IndicatorSnapshot,
    regime: RegimeResult,
    warnings: list[str],
    cfg: GridConfig = DEFAULT_CONFIG,
) -> GridPlan:
    return _make_plan(
        context, snapshot, regime, warnings, cfg,
        strategy_name="数据不足，先不动作",
        headline_action="关键指标不完整，先不要做交易动作，等数据恢复后再看。",
        action_steps=["先确认数据是否更新到最近交易日。", "若数据恢复，再重新生成计划。"],
        trend_adjustment_note="数据不足，不给机械卖出模板。",
        mode="disabled",
        reason=regime.reason,
    )


def _suggest_trim_shares(total_shares: int, cfg: GridConfig = DEFAULT_CONFIG) -> int:
    """Return a tactical lot rounded down to lot_size for trend trimming."""
    if total_shares <= 0:
        return 0
    return (int(total_shares * cfg.trim_ratio) // cfg.lot_size) * cfg.lot_size


def _suggest_tactical_shares(total_shares: int, cfg: GridConfig = DEFAULT_CONFIG) -> int:
    """Return a conservative tactical lot for range trading."""
    if total_shares <= 0:
        return 0
    return max((int(total_shares * cfg.tactical_ratio) // cfg.lot_size) * cfg.lot_size, 0)


def _build_reference_ladder(anchor_sell: float, atr14: float, precision: int, cfg: GridConfig = DEFAULT_CONFIG) -> tuple[list[float], list[float]]:
    """Build a simple sell ladder and corresponding rebuy ladder."""
    sell_ladder: list[float] = []
    rebuy_ladder: list[float] = []
    min_tick = 10.0 ** -precision  # smallest representable price at this precision
    for index in range(cfg.ladder_tranches):
        sell_price = quantize_price(anchor_sell + index * atr14, precision)
        rebuy_price = quantize_price(max(sell_price - atr14, min_tick), precision)
        sell_ladder.append(sell_price)
        rebuy_ladder.append(rebuy_price)
    return sell_ladder, rebuy_ladder


def _build_trend_trim_steps(trim_shares: int, sell_trigger: float, rebuy_price: float, lower_invalidation: float | None) -> list[str]:
    if trim_shares <= 0:
        return [
            "当前持仓太少，不拆机动仓。",
            "继续持有观察，不追涨加仓。",
        ]
    steps = [
        f"第1步：在 ¥{sell_trigger:.3f} 附近先卖出 {trim_shares} 股机动仓，先把一部分利润落袋。",
        f"第2步：如果回落到 ¥{rebuy_price:.3f} 左右，再把这 {trim_shares} 股接回来。",
    ]
    if lower_invalidation is not None:
        steps.append(f"第3步：若后续明显走弱并逼近 ¥{lower_invalidation:.3f} 下方，停止用这套上涨回接思路。")
    return steps


def _build_trend_avoid_steps(lower_invalidation: float | None) -> list[str]:
    steps = [
        "第1步：先不新开网格，不抢反弹。",
        "第2步：等价格重新回到震荡区，再考虑区间策略。",
    ]
    if lower_invalidation is not None:
        steps.append(f"第3步：若继续走弱并跌向 ¥{lower_invalidation:.3f} 一带，更要控制动作频率。")
    return steps


def _build_range_steps(
    tactical_shares: int,
    primary_buy: float | None,
    primary_sell: float | None,
    lower_invalidation: float | None,
) -> list[str]:
    steps: list[str] = []
    if tactical_shares > 0 and primary_sell is not None:
        steps.append(f"第1步：若价格触及 ¥{primary_sell:.3f}，先卖出 {tactical_shares} 股机动仓。")
    elif primary_sell is not None:
        steps.append(f"第1步：先观察 ¥{primary_sell:.3f} 附近的卖点反应。")

    if tactical_shares > 0 and primary_buy is not None:
        steps.append(f"第2步：若价格回落到 ¥{primary_buy:.3f} 左右，再把这 {tactical_shares} 股买回。")
    elif primary_buy is not None:
        steps.append(f"第2步：关注 ¥{primary_buy:.3f} 附近的回补机会。")

    if lower_invalidation is not None:
        steps.append(f"第3步：若跌破 ¥{lower_invalidation:.3f}，先停掉这轮区间交易。")
    return steps


def _build_trend_up_plan(pctx: _PlanContext) -> GridPlan:
    """Build a plan for trend-up regime (trim tactical shares)."""
    ctx = pctx.context
    cfg = pctx.cfg
    atr14 = pctx.snapshot.atr14

    sell_trigger = quantize_price(max(ctx.current_price, pctx.upper), ctx.price_precision)
    rebuy_price = quantize_price(max(sell_trigger - atr14, ctx.current_price - atr14), ctx.price_precision)
    trim_shares = _suggest_trim_shares(ctx.shares, cfg)

    if trim_shares > 0:
        reason = (
            f"{pctx.regime.reason} 建议在 ¥{sell_trigger:.3f} 附近先减机动仓 {trim_shares} 股，"
            f"若回落到 ¥{rebuy_price:.3f} 左右再接回。"
        )
        headline = (
            f"保留大部分底仓，只把 {trim_shares} 股机动仓在 ¥{sell_trigger:.3f} 附近先卖掉，"
            f"若回落到 ¥{rebuy_price:.3f} 左右再接回。"
        )
        trend_note = "当前还在上涨，先只执行第一档卖出，不连续卖第二档、第三档。"
    else:
        reason = f"{pctx.regime.reason} 当前持仓不足以拆出 10% 机动仓，先观察为主。"
        headline = "当前持仓不足以拆出机动仓，先持有观察，不急着做动作。"
        trend_note = "当前趋势向上，但你的实际持仓太小，先看模板价位，不急着拆仓。"

    return _make_plan(
        ctx, pctx.snapshot, pctx.regime, pctx.warnings, cfg,
        strategy_name="底仓 + 机动仓锁利润",
        headline_action=headline,
        tactical_shares=trim_shares,
        action_steps=_build_trend_trim_steps(trim_shares, sell_trigger, rebuy_price, pctx.lower_invalidation),
        reference_sell_ladder=pctx.reference_sell_ladder,
        reference_rebuy_ladder=pctx.reference_rebuy_ladder,
        trend_sell_limit_tranches=1 if trim_shares > 0 else 0,
        trend_sell_limit_shares=trim_shares,
        trend_adjustment_note=trend_note,
        mode="trend_trim",
        reason=reason,
        center=pctx.center,
        step=pctx.step,
        primary_buy=rebuy_price,
        primary_sell=sell_trigger,
        lower_invalidation=pctx.lower_invalidation,
        upper_breakout=pctx.upper_breakout,
        trim_shares=trim_shares,
        rebuy_price=rebuy_price,
    )


def _build_trend_down_plan(pctx: _PlanContext) -> GridPlan:
    """Build a plan for trend-down regime (avoid grid)."""
    ctx = pctx.context
    cfg = pctx.cfg

    return _make_plan(
        ctx, pctx.snapshot, pctx.regime, pctx.warnings, cfg,
        strategy_name="下跌趋势先观望",
        headline_action="现在先别硬做网格，不抢反弹，等重新站回震荡区或趋势明显修复。",
        action_steps=_build_trend_avoid_steps(pctx.lower_invalidation),
        reference_sell_ladder=pctx.reference_sell_ladder,
        reference_rebuy_ladder=pctx.reference_rebuy_ladder,
        trend_adjustment_note="当前偏弱，不按卖出锁利模板做动作，先等反弹或结构修复。",
        mode="trend_avoid",
        reason=pctx.regime.reason,
        center=pctx.center,
        step=pctx.step,
        lower_invalidation=pctx.lower_invalidation,
        upper_breakout=pctx.upper_breakout,
    )


def _build_range_plan(pctx: _PlanContext) -> GridPlan:
    """Build a plan for range/oscillation regime (active grid)."""
    ctx = pctx.context
    cfg = pctx.cfg
    atr14 = pctx.snapshot.atr14
    precision = ctx.price_precision

    buy_levels = _generate_buy_levels(pctx.center, pctx.step, pctx.lower, precision, cfg)
    sell_levels = _generate_sell_levels(pctx.center, pctx.step, pctx.upper, precision, cfg)
    primary_buy = next((level for level in buy_levels if level < ctx.current_price), None)
    primary_sell = next((level for level in sell_levels if level > ctx.current_price), None)

    grid_enabled = primary_buy is not None and primary_sell is not None
    reason = pctx.regime.reason
    warnings = list(pctx.warnings)  # local copy; never mutate the shared list
    tactical_shares = _suggest_tactical_shares(ctx.shares, cfg)

    if not grid_enabled:
        reason = "当前价已接近或突破布林边界，等待回归区间或突破确认"
        warnings.append("current_price_outside_active_grid")

    ref_sell = pctx.reference_sell_ladder
    ref_rebuy = pctx.reference_rebuy_ladder
    if primary_sell is not None:
        ref_sell, ref_rebuy = _build_reference_ladder(primary_sell, atr14, precision, cfg)

    return _make_plan(
        ctx, pctx.snapshot, pctx.regime, warnings, cfg,
        strategy_name="高胜率区间网格",
        headline_action=(
            f"只动 {tactical_shares} 股机动仓做区间来回，不碰大部分底仓。"
            if tactical_shares > 0
            else "当前持仓不足以拆出机动仓，先观察主买卖点，不急着做交易。"
        ),
        tactical_shares=tactical_shares,
        action_steps=_build_range_steps(tactical_shares, primary_buy, primary_sell, pctx.lower_invalidation),
        reference_sell_ladder=ref_sell,
        reference_rebuy_ladder=ref_rebuy,
        trend_sell_limit_tranches=cfg.ladder_tranches,
        trend_sell_limit_shares=cfg.ladder_tranches * cfg.reference_tranche_shares,
        trend_adjustment_note=f"当前偏震荡，标准 {cfg.reference_position_shares} 股模板可以按{cfg.ladder_tranches}档各卖 {cfg.reference_tranche_shares} 股慢慢执行。",
        mode="range_grid",
        grid_enabled=grid_enabled,
        reason=reason,
        center=pctx.center,
        step=pctx.step,
        primary_buy=primary_buy,
        primary_sell=primary_sell,
        buy_levels=buy_levels,
        sell_levels=sell_levels,
        lower_invalidation=pctx.lower_invalidation,
        upper_breakout=pctx.upper_breakout,
    )
