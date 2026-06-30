"""撮合规则测试：T+1、整手、佣金、滑点、一字板、限价成交。"""

from dev.config import with_overrides
from dev.backtest.broker import Portfolio, commission, execute_day
from dev.strategy.engine import Order

CFG = with_overrides(
    commission_rate=1e-4, commission_min=0.1, slippage_ticks=1, price_precision=3
)

BAR = {"open": 3.000, "high": 3.100, "low": 2.900, "close": 3.050}


def test_commission_floor():
    assert commission(600.0, CFG) == 0.1          # 600×0.0001=0.06 → 下限 0.1
    assert commission(50_000.0, CFG) == 5.0       # 万1


def test_open_buy_with_slippage():
    p = Portfolio(cash=10_000)
    fills = execute_day(p, [Order("open", "buy", 1000, "base")], BAR, "2026-01-01", CFG)
    assert len(fills) == 1
    assert fills[0].trade.price == 3.001          # 开盘 + 1 tick
    assert p.base_shares == 1000
    assert p.cash < 10_000 - 3001 + 1             # 扣了本金和佣金


def test_open_sell_with_slippage():
    p = Portfolio(cash=0, base_shares=1000)
    fills = execute_day(p, [Order("open", "sell", 1000, "base")], BAR, "2026-01-01", CFG)
    assert fills[0].trade.price == 2.999          # 开盘 − 1 tick


def test_limit_buy_fills_only_if_low_touches():
    p = Portfolio(cash=10_000)
    no_fill = execute_day(p, [Order("limit", "buy", 100, "tactical", price=2.80)], BAR, "d", CFG)
    assert no_fill == []
    fills = execute_day(p, [Order("limit", "buy", 100, "tactical", price=2.95)], BAR, "d", CFG)
    assert fills[0].trade.price == 2.95           # 开盘高于限价 → 按限价


def test_limit_buy_better_fill_at_open():
    bar = {"open": 2.92, "high": 3.0, "low": 2.9, "close": 2.95}
    p = Portfolio(cash=10_000)
    fills = execute_day(p, [Order("limit", "buy", 100, "tactical", price=2.95)], bar, "d", CFG)
    assert fills[0].trade.price == 2.92           # 低开 → 按更优的开盘价


def test_limit_sell_fills_only_if_high_touches():
    p = Portfolio(cash=0, tactical_shares=300)
    no_fill = execute_day(p, [Order("limit", "sell", 300, "tactical", price=3.20)], BAR, "d", CFG)
    assert no_fill == []
    fills = execute_day(p, [Order("limit", "sell", 300, "tactical", price=3.05)], BAR, "d", CFG)
    assert fills[0].trade.price == 3.05


def test_one_price_board_no_fill():
    bar = {"open": 3.0, "high": 3.0, "low": 3.0, "close": 3.0}
    p = Portfolio(cash=10_000, base_shares=1000)
    fills = execute_day(
        p,
        [Order("open", "buy", 100, "base"), Order("open", "sell", 100, "base")],
        bar, "d", CFG,
    )
    assert fills == []


def test_t_plus_1_cannot_sell_same_day_bought_tactical():
    p = Portfolio(cash=10_000)
    orders = [
        Order("limit", "buy", 300, "tactical", price=2.95),
        Order("limit", "sell", 300, "tactical", price=3.05),
    ]
    fills = execute_day(p, orders, BAR, "d", CFG)
    # 卖单先撮合（无持仓→不成交），买单成交；当日买入不可卖
    assert [f.trade.side for f in fills] == ["buy"]
    assert p.tactical_shares == 300


def test_sell_clamped_to_holdings_and_lot():
    p = Portfolio(cash=0, tactical_shares=250)  # 非整手持仓
    fills = execute_day(p, [Order("open", "sell", 300, "tactical")], BAR, "d", CFG)
    assert fills[0].trade.shares == 200           # 250 → 卖 200（整手）
    assert p.tactical_shares == 50


def test_buy_shrinks_when_cash_insufficient():
    p = Portfolio(cash=1000)
    fills = execute_day(p, [Order("open", "buy", 1000, "base")], BAR, "d", CFG)
    assert fills[0].trade.shares == 300           # 1000 元只够 3 手
    assert p.cash >= 0
