"""撮合与组合记账：T+1、整手、佣金、滑点、一字板不成交。

成交规则（订单在 t 日收盘后下达，t+1 日撮合）：
- open 市价单：以次日开盘价 ± slippage_ticks×最小价差成交；
- limit 买单：次日 low ≤ 限价才成交，成交价 = min(开盘, 限价)；
- limit 卖单：次日 high ≥ 限价才成交，成交价 = max(开盘, 限价)；
- 一字板（high == low）当日视为无对手盘，所有订单不成交；
- 先撮合卖单再撮合买单（卖出回款当日可用，符合 A 股资金 T+0）；
- 当日买入的股份记入 bought_today，当日不可再卖（T+1 防御性校验）。

费用：佣金 = max(成交额 × commission_rate, commission_min)，ETF 免印花税。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import DEFAULT_CONFIG, StrategyConfig
from ..strategy.engine import Order


@dataclass(slots=True)
class Trade:
    date: str
    layer: str          # base | tactical
    side: str           # buy | sell
    shares: int
    price: float
    amount: float
    fee: float
    kind: str           # open | limit
    note: str = ""


@dataclass(slots=True)
class Portfolio:
    cash: float
    base_shares: int = 0
    tactical_shares: int = 0
    bought_today: int = 0  # T+1：当日买入不可卖

    @property
    def total_shares(self) -> int:
        return self.base_shares + self.tactical_shares

    def equity(self, price: float) -> float:
        return self.cash + self.total_shares * price


@dataclass(slots=True)
class Fill:
    order: Order
    trade: Trade


def commission(amount: float, cfg: StrategyConfig = DEFAULT_CONFIG) -> float:
    return max(amount * cfg.commission_rate, cfg.commission_min)


def _tick(cfg: StrategyConfig) -> float:
    return 10.0 ** -cfg.price_precision


def execute_day(
    portfolio: Portfolio,
    orders: list[Order],
    bar: dict[str, float],
    date: str,
    cfg: StrategyConfig = DEFAULT_CONFIG,
) -> list[Fill]:
    """在 bar（次日 OHLC）上撮合订单，原地更新组合，返回成交列表。"""
    portfolio.bought_today = 0
    open_, high, low = float(bar["open"]), float(bar["high"]), float(bar["low"])
    fills: list[Fill] = []

    if high == low:
        return fills  # 一字板：无成交

    ordered = sorted(orders, key=lambda o: 0 if o.side == "sell" else 1)
    for order in ordered:
        price = _fill_price(order, open_, high, low, cfg)
        if price is None or order.shares <= 0:
            continue
        if order.side == "sell":
            held = portfolio.base_shares if order.layer == "base" else portfolio.tactical_shares
            sellable = held - (portfolio.bought_today if order.layer == "tactical" else 0)
            shares = min(order.shares, max(sellable, 0))
            shares = (shares // cfg.lot_size) * cfg.lot_size
            if shares <= 0:
                continue
            amount = shares * price
            fee = commission(amount, cfg)
            portfolio.cash += amount - fee
            _adjust(portfolio, order.layer, -shares)
            fills.append(Fill(order, Trade(date, order.layer, "sell", shares, price, amount, fee, order.kind, order.note)))
        else:
            shares = order.shares
            amount = shares * price
            fee = commission(amount, cfg)
            if portfolio.cash < amount + fee:
                # 资金不足按可用资金缩股（现金地板在引擎层已审批，这里兜底）
                affordable = int(portfolio.cash / (price * (1 + cfg.commission_rate)) // cfg.lot_size) * cfg.lot_size
                if affordable <= 0:
                    continue
                shares = affordable
                amount = shares * price
                fee = commission(amount, cfg)
            portfolio.cash -= amount + fee
            _adjust(portfolio, order.layer, shares)
            portfolio.bought_today += shares
            fills.append(Fill(order, Trade(date, order.layer, "buy", shares, price, amount, fee, order.kind, order.note)))
    return fills


def _fill_price(
    order: Order, open_: float, high: float, low: float, cfg: StrategyConfig
) -> float | None:
    tick = _tick(cfg)
    if order.kind == "open":
        slip = cfg.slippage_ticks * tick
        return round(open_ + slip, cfg.price_precision) if order.side == "buy" else round(
            max(open_ - slip, tick), cfg.price_precision
        )
    # limit
    assert order.price is not None
    if order.side == "buy":
        if low <= order.price:
            return round(min(open_, order.price), cfg.price_precision)
        return None
    if high >= order.price:
        return round(max(open_, order.price), cfg.price_precision)
    return None


def _adjust(portfolio: Portfolio, layer: str, delta: int) -> None:
    if layer == "base":
        portfolio.base_shares += delta
        if portfolio.base_shares < 0:
            raise AssertionError("base_shares 不可为负")
    else:
        portfolio.tactical_shares += delta
        if portfolio.tactical_shares < 0:
            raise AssertionError("tactical_shares 不可为负")
