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
    previous_atr14: float | None
    atr_change_3d_pct: float | None
    atr_change_5d_pct: float | None
    previous_step: float | None
    step_change_pct: float | None
    volatility_note: str
    spacing_note: str
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
    diagnostics: _GridDiagnostics
    cfg: GridConfig


@dataclass(slots=True)
class _StepContext:
    step: float | None = None
    atr14: float | None = None
    band_width: float | None = None
    driver: str | None = None


@dataclass(slots=True)
class _GridDiagnostics:
    previous_atr14: float | None = None
    atr_change_3d_pct: float | None = None
    atr_change_5d_pct: float | None = None
    previous_step: float | None = None
    step_change_pct: float | None = None
    volatility_note: str = "ATR 历史不足，暂时不判断波动变化。"
    spacing_note: str = "数据还不够，暂时无法比较今天和上一交易日的网格间距。"


def generate_plan(symbol: str, *, shares: int = 200, kline_count: int = 120, cfg: GridConfig = DEFAULT_CONFIG) -> GridPlan:
    """Generate a fresh grid plan from live/cache-backed data."""
    context = load_market_context(symbol, shares=shares, kline_count=kline_count, cfg=cfg)
    return build_plan_from_context(context, cfg=cfg)


def build_plan_from_context(context: MarketContext, cfg: GridConfig = DEFAULT_CONFIG) -> GridPlan:
    """Build a grid plan from a normalized market context."""
    frame = build_indicator_frame(context.rows, cfg)
    snapshot = latest_snapshot(frame)
    regime = classify_regime(frame, snapshot, cfg)
    diagnostics = _build_grid_diagnostics(frame, context.price_precision, cfg)
    return _assemble_plan(context, snapshot, regime, cfg, diagnostics=diagnostics)


def _assemble_plan(
    context: MarketContext,
    snapshot: IndicatorSnapshot,
    regime: RegimeResult,
    cfg: GridConfig = DEFAULT_CONFIG,
    diagnostics: _GridDiagnostics | None = None,
) -> GridPlan:
    """Assemble a GridPlan given precomputed snapshot and regime."""
    warnings = list(context.warnings)
    diagnostics = diagnostics or _GridDiagnostics()

    if snapshot.bb_lower is None or snapshot.bb_middle is None or snapshot.bb_upper is None or snapshot.atr14 is None:
        return _disabled_plan(context, snapshot, regime, warnings, cfg, diagnostics)

    center = quantize_price(snapshot.bb_middle, context.price_precision)
    lower = quantize_price(snapshot.bb_lower, context.price_precision)
    upper = quantize_price(snapshot.bb_upper, context.price_precision)
    if upper <= lower:
        warnings.append("invalid_boll_band")
        return _disabled_plan(
            context,
            snapshot,
            RegimeResult("disabled", False, "布林带上下沿无效"),
            warnings,
            cfg,
            diagnostics,
        )

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
        diagnostics=diagnostics,
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


def _build_grid_diagnostics(frame, precision: int, cfg: GridConfig = DEFAULT_CONFIG) -> _GridDiagnostics:
    """Build plain-language volatility and grid-spacing diagnostics."""
    if frame.empty:
        return _GridDiagnostics()

    atr_series = frame["atr14"].dropna()
    previous_atr14 = float(atr_series.iloc[-2]) if len(atr_series) >= 2 else None
    atr_change_3d_pct = _series_pct_change(atr_series, 3)
    atr_change_5d_pct = _series_pct_change(atr_series, 5)

    current_step = _step_context(latest_snapshot(frame), precision, cfg)
    previous_step = _step_context(latest_snapshot(frame.iloc[:-1]), precision, cfg) if len(frame) >= 2 else _StepContext()
    step_change_pct = _pct_change_value(current_step.step, previous_step.step)

    return _GridDiagnostics(
        previous_atr14=previous_atr14,
        atr_change_3d_pct=atr_change_3d_pct,
        atr_change_5d_pct=atr_change_5d_pct,
        previous_step=previous_step.step,
        step_change_pct=step_change_pct,
        volatility_note=_build_volatility_note(atr_change_3d_pct, atr_change_5d_pct, cfg),
        spacing_note=_build_spacing_note(current_step, previous_step, step_change_pct, cfg),
    )


def _series_pct_change(series, periods: int) -> float | None:
    """Return pct change against *periods* trading days ago."""
    if len(series) <= periods:
        return None
    return _pct_change_value(float(series.iloc[-1]), float(series.iloc[-periods - 1]))


def _pct_change_value(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return round((current - previous) / previous * 100, 2)


def _step_context(snapshot: IndicatorSnapshot, precision: int, cfg: GridConfig = DEFAULT_CONFIG) -> _StepContext:
    if snapshot.atr14 is None or snapshot.bb_lower is None or snapshot.bb_upper is None:
        return _StepContext()

    lower = quantize_price(snapshot.bb_lower, precision)
    upper = quantize_price(snapshot.bb_upper, precision)
    if upper <= lower:
        return _StepContext(atr14=snapshot.atr14)

    band_width = upper - lower
    min_step = band_width * cfg.step_min_fraction
    max_step = band_width * cfg.step_max_fraction
    if snapshot.atr14 < min_step:
        driver = "min_band"
    elif snapshot.atr14 > max_step:
        driver = "max_band"
    else:
        driver = "atr"

    return _StepContext(
        step=_effective_step(snapshot.atr14, lower, upper, precision, cfg),
        atr14=snapshot.atr14,
        band_width=band_width,
        driver=driver,
    )


def _build_volatility_note(
    atr_change_3d_pct: float | None,
    atr_change_5d_pct: float | None,
    cfg: GridConfig = DEFAULT_CONFIG,
) -> str:
    if atr_change_3d_pct is None and atr_change_5d_pct is None:
        return "ATR 历史不足，暂时不判断波动变化。"

    change_text = f"ATR14 近3日{_fmt_pct_text(atr_change_3d_pct)}，近5日{_fmt_pct_text(atr_change_5d_pct)}。"
    rises_fast = (
        (atr_change_3d_pct is not None and atr_change_3d_pct >= cfg.atr_alert_3d_pct)
        or (atr_change_5d_pct is not None and atr_change_5d_pct >= cfg.atr_alert_5d_pct)
    )
    cools_fast = (
        (atr_change_3d_pct is not None and atr_change_3d_pct <= -cfg.atr_alert_3d_pct)
        or (atr_change_5d_pct is not None and atr_change_5d_pct <= -cfg.atr_alert_5d_pct)
    )
    if rises_fast:
        return change_text + "波动明显抬升，机动仓要切小、少追单，先防止被急涨急跌来回打乱节奏。"
    if cools_fast:
        return change_text + "波动在降温，但不要手动把网格改得过密，仍按系统档位慢慢来。"
    return change_text + "波动变化不大，按当前计划执行。"


def _build_spacing_note(
    current: _StepContext,
    previous: _StepContext,
    step_change_pct: float | None,
    cfg: GridConfig = DEFAULT_CONFIG,
) -> str:
    if current.step is None:
        return "关键指标还不完整，暂时无法生成可靠网格间距。"

    driver_note = _step_driver_note(current.driver)
    if previous.step is None or step_change_pct is None:
        return f"今日网格间距为 ¥{current.step:.3f}。{driver_note}"

    if abs(step_change_pct) < cfg.step_change_alert_pct:
        return f"网格间距维持在 ¥{current.step:.3f} 附近，较上一交易日变化 {step_change_pct:+.2f}%。{driver_note}"

    direction = "变宽" if step_change_pct > 0 else "变窄"
    return (
        f"网格间距从 ¥{previous.step:.3f} 调到 ¥{current.step:.3f}，"
        f"{direction} {abs(step_change_pct):.2f}%。{driver_note}"
    )


def _step_driver_note(driver: str | None) -> str:
    if driver == "min_band":
        return "主要是布林带最小间距在起作用，系统主动防止格子太密。"
    if driver == "max_band":
        return "ATR 已经超过布林带允许的最大间距，系统主动封顶，防止格子拉得太散。"
    if driver == "atr":
        return "主要跟着 ATR14 走，真实波幅越大，格子越宽。"
    return "驱动原因暂时无法判断。"


def _fmt_pct_text(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:+.2f}%"


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
        previous_atr14=None,
        atr_change_3d_pct=None,
        atr_change_5d_pct=None,
        previous_step=None,
        step_change_pct=None,
        volatility_note="ATR 历史不足，暂时不判断波动变化。",
        spacing_note="数据还不够，暂时无法比较今天和上一交易日的网格间距。",
        reference_sell_ladder=[],
        reference_rebuy_ladder=[],
        trend_sell_limit_tranches=0,
        trend_sell_limit_shares=0,
    )
    defaults.update(asdict(overrides.pop("diagnostics")) if "diagnostics" in overrides else {})
    defaults.update(overrides)
    return GridPlan(**defaults)


def _disabled_plan(
    context: MarketContext,
    snapshot: IndicatorSnapshot,
    regime: RegimeResult,
    warnings: list[str],
    cfg: GridConfig = DEFAULT_CONFIG,
    diagnostics: _GridDiagnostics | None = None,
) -> GridPlan:
    return _make_plan(
        context, snapshot, regime, warnings, cfg,
        diagnostics=diagnostics or _GridDiagnostics(),
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
        diagnostics=pctx.diagnostics,
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
        diagnostics=pctx.diagnostics,
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
        diagnostics=pctx.diagnostics,
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
