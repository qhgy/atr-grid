"""决策引擎：每日收盘后产出次日订单 + 新状态 + 中文原因链。

无前视：决策只用截至当日收盘的数据，订单在次日成交（由 broker 模拟）。

三层资金：
- 底仓（趋势层）：trend_on 时目标 = base_ratio × vol_scalar × 总资产，
  分批（每日最多一档）向目标靠拢；趋势确认转弱 → 目标归零、分批退出。
- 机动仓（反转层）：trend_on 时持有 tactical_ratio × 总资产；
  IDLE 时挂限价卖单（收盘 + k×ATR，一档）；TRIMMED 时挂限价接回单
  （卖价 − k×ATR）。指数走弱只许接回不许开新仓；冻结时停止一切买入。
- 现金地板：所有买单过 cash_floor_approved；应急通道按 20 日回撤解锁。
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass, field

from ..config import DEFAULT_CONFIG, StrategyConfig
from . import rules
from .states import StrategyState, TacticalState, daily_update


@dataclass(frozen=True, slots=True)
class Order:
    kind: str          # "open"（次日开盘市价）| "limit"（次日盘中限价）
    side: str          # "buy" | "sell"
    shares: int
    layer: str         # "base" | "tactical"
    price: float | None = None  # limit 单价格
    note: str = ""
    ref: float | None = None    # 高卖单携带 k×ATR 偏移，供成交后推算接回价


@dataclass(slots=True)
class PortfolioView:
    """引擎所需的组合只读快照。"""

    cash: float
    base_shares: int
    tactical_shares: int

    @property
    def total_shares(self) -> int:
        return self.base_shares + self.tactical_shares

    def equity(self, price: float) -> float:
        return self.cash + self.total_shares * price


@dataclass(slots=True)
class Decision:
    state: StrategyState
    orders: list[Order] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    diagnostics: dict = field(default_factory=dict)


def decide(
    *,
    row: dict[str, float | None],
    index_rows: dict[str, dict[str, float | None]],
    portfolio: PortfolioView,
    state: StrategyState,
    cfg: StrategyConfig = DEFAULT_CONFIG,
) -> Decision:
    """row 为主标的当日指标行（close/atr/ma_trend/ma_unfreeze/rvol/high_lookback）。"""
    close = float(row["close"])  # 必有
    atr = _f(row.get("atr"))
    ma_trend = _f(row.get("ma_trend"))
    ma_unfreeze = _f(row.get("ma_unfreeze"))
    rvol = _f(row.get("rvol"))
    rvol_ref = _f(row.get("rvol_ref"))
    high_lookback = _f(row.get("high_lookback"))

    # 1) 每日状态转移（趋势确认 / 冻结 / 解冻）
    state, reasons = daily_update(
        state, close=close, ma_trend=ma_trend, ma_unfreeze=ma_unfreeze, atr=atr, cfg=cfg
    )

    # 2) 环境评估
    index_filter = rules.evaluate_index_filter(index_rows, cfg)
    scalar = rules.vol_scalar(rvol, cfg, rvol_ref=rvol_ref)
    emergency = rules.emergency_unlocked(high_lookback, close, cfg)
    equity = portfolio.equity(close)
    frozen = state.tactical == TacticalState.FROZEN

    orders: list[Order] = []

    # 3) 底仓分批调整
    base_target = rules.round_lot(cfg.base_ratio * scalar * equity / close, cfg.lot_size) if state.trend_on else 0
    base_tranche = max(
        rules.round_lot(cfg.base_ratio * equity / close / cfg.base_step_tranches, cfg.lot_size),
        cfg.lot_size,
    )
    base_delta = base_target - portfolio.base_shares
    if base_delta >= cfg.lot_size:
        step = min(base_delta, base_tranche)
        if frozen:
            reasons.append("底仓待加仓，但买入冻结中，今日不加")
        elif index_filter.weak:
            reasons.append("底仓待加仓，但指数同步走弱，买入降级，今日不加")
        else:
            orders.append(Order("open", "buy", rules.round_lot(step, cfg.lot_size), "base", note="底仓向目标靠拢"))
    elif base_delta <= -cfg.lot_size:
        step = min(-base_delta, base_tranche)
        orders.append(Order("open", "sell", rules.round_lot(step, cfg.lot_size), "base", note="底仓向目标退出"))

    # 4) 机动仓
    tactical_target = rules.round_lot(cfg.tactical_ratio * equity / close, cfg.lot_size) if state.trend_on else 0
    tactical_tranche = max(
        rules.round_lot(cfg.tactical_ratio * equity / close / cfg.tactical_tranches, cfg.lot_size),
        cfg.lot_size,
    )
    # 待接回的股数视作已占用额度，避免建仓买回刚卖出的轮次
    committed = portfolio.tactical_shares + state.sell_shares
    tac_delta = tactical_target - committed
    if tac_delta >= cfg.lot_size:
        step = min(tac_delta, tactical_tranche)
        if frozen:
            reasons.append("机动仓待建仓，但买入冻结中")
        elif index_filter.weak:
            reasons.append("机动仓待建仓，但指数同步走弱，不开新仓")
        else:
            orders.append(Order("open", "buy", rules.round_lot(step, cfg.lot_size), "tactical", note="机动仓建仓"))
    elif tac_delta <= -cfg.lot_size and state.tactical == TacticalState.IDLE:
        step = min(-tac_delta, tactical_tranche)
        sell_n = min(rules.round_lot(step, cfg.lot_size), portfolio.tactical_shares)
        if sell_n >= cfg.lot_size:
            orders.append(Order("open", "sell", sell_n, "tactical", note="趋势转弱，机动仓退出"))

    # 4a) 高卖：IDLE 且趋势在、有持仓 → 挂一档限价卖
    if (
        state.tactical == TacticalState.IDLE
        and state.trend_on
        and atr is not None
        and portfolio.tactical_shares >= cfg.lot_size
    ):
        sell_n = min(tactical_tranche, portfolio.tactical_shares)
        sell_price = round(close + cfg.grid_k * atr, cfg.price_precision)
        rebuy_offset = round(cfg.grid_k * atr, cfg.price_precision)
        orders.append(
            Order("limit", "sell", sell_n, "tactical", price=sell_price,
                  note=f"机动仓高卖一档（接回参考 {sell_price - rebuy_offset:.3f}）",
                  ref=rebuy_offset)
        )

    # 4b) 接回：TRIMMED 且未冻结 → 挂限价买（指数弱时仍允许：这是完成上一轮）
    if state.tactical == TacticalState.TRIMMED and state.rebuy_target is not None:
        if frozen:
            reasons.append("待接回轮次因冻结暂停")
        else:
            orders.append(
                Order("limit", "buy", state.sell_shares, "tactical",
                      price=state.rebuy_target, note="接回上一轮卖出的机动仓")
            )
            if index_filter.weak:
                reasons.append("指数走弱：仅允许完成本轮接回，不开新仓")

    # 5) 现金地板：按顺序审批买单（接回优先级最高）
    orders = _apply_cash_floor(orders, portfolio, equity, close, cfg, emergency, reasons)

    if emergency:
        reasons.append(
            f"近 {cfg.emergency_lookback} 日高点回撤超 {cfg.emergency_drop_pct:.0%}，应急通道解锁（地板可动用一半）"
        )
    reasons.append(f"指数过滤：{index_filter.detail}")
    diagnostics = {
        "equity": round(equity, 2),
        "vol_scalar": round(scalar, 3),
        "base_target": base_target,
        "tactical_target": tactical_target,
        "index_weak": index_filter.weak,
        "emergency": emergency,
        "trend_on": state.trend_on,
        "tactical_state": state.tactical.value,
    }
    return Decision(state=state, orders=orders, reasons=reasons, diagnostics=diagnostics)


def _apply_cash_floor(
    orders: list[Order],
    portfolio: PortfolioView,
    equity: float,
    close: float,
    cfg: StrategyConfig,
    emergency: bool,
    reasons: list[str],
) -> list[Order]:
    """按优先级（接回 > 底仓 > 建仓）审批买单，卖单原样放行。

    市价单用当日收盘价预占资金（次日开盘价未知，收盘是无前视的最优近似）。
    """
    def priority(order: Order) -> int:
        if order.side == "sell":
            return 0
        if order.kind == "limit" and order.layer == "tactical":
            return 1  # 接回
        if order.layer == "base":
            return 2
        return 3

    approved: list[Order] = []
    cash_left = portfolio.cash
    for order in sorted(orders, key=priority):
        if order.side == "sell":
            approved.append(order)
            continue
        price = order.price if order.price is not None else close
        intended = order.shares * price
        allowed = rules.cash_floor_approved(cash_left, intended, equity, cfg, emergency=emergency)
        shares_ok = rules.round_lot(allowed / price, cfg.lot_size)
        if shares_ok >= cfg.lot_size:
            cash_left -= shares_ok * price
            if shares_ok < order.shares:
                reasons.append(f"现金地板限制：{order.note} 由 {order.shares} 股缩减为 {shares_ok} 股")
            approved.append(dataclasses.replace(order, shares=shares_ok))
        else:
            reasons.append(f"现金地板拦截：{order.note}（{order.shares} 股）今日不执行")
    return approved


def _f(value) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None
