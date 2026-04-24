"""Phase 1.2 纯函数 simulate_day 单测。

覆盖 8 个核心分支：
1. baseline（首日，未触发成交）
2. trend_down_hold（下跌趋势不动）
3. disabled（数据异常不动）
4. sell 命中（向上跨档）
5. buy 命中（向下跨档）
6. invalidation 抑制买但不抑制卖
7. stop_loss_trigger 冻结买
8. cash 不足时 buy 中止

纯函数不触网不读写文件，plan 用 duck-typed fake 对象。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from atr_grid.paper import PaperState, simulate_day, commission


@dataclass
class FakePlan:
    """最小子集，只提供 simulate_day 实际用到的字段。"""

    current_price: float
    regime: str = "range"
    reason: str = ""
    last_trade_date: str = "2026-04-24"
    lower_invalidation: float | None = None
    sell_levels: list[float] = field(default_factory=list)
    buy_levels: list[float] = field(default_factory=list)
    reference_sell_ladder: list[float] = field(default_factory=list)
    reference_rebuy_ladder: list[float] = field(default_factory=list)


# ------------------------------------------------------------ 1. baseline
def test_baseline_first_day_returns_unchanged_state():
    state = PaperState(shares=2000, cash=10_000.0, last_price=None)
    plan = FakePlan(current_price=1.30, regime="range",
                    sell_levels=[1.35], buy_levels=[1.25])
    new_state, events = simulate_day(state, plan)
    assert new_state == state               # 状态完全不变
    assert len(events) == 1
    assert events[0]["type"] == "baseline"
    assert events[0]["price"] == 1.30


# ------------------------------------------------------------ 2. trend_down
def test_trend_down_regime_holds_without_trade():
    state = PaperState(shares=2000, cash=0.0, last_price=1.40)
    plan = FakePlan(current_price=1.20, regime="trend_down",
                    reason="均线空头", sell_levels=[1.35], buy_levels=[1.25])
    new_state, events = simulate_day(state, plan)
    assert new_state == state
    assert [e["type"] for e in events] == ["trend_down_hold"]
    assert events[0]["reason"] == "均线空头"


# ------------------------------------------------------------ 3. disabled
def test_disabled_regime_holds_without_trade():
    state = PaperState(shares=2000, cash=0.0, last_price=1.30)
    plan = FakePlan(current_price=1.30, regime="disabled", reason="数据不足")
    new_state, events = simulate_day(state, plan)
    assert new_state == state
    assert [e["type"] for e in events] == ["disabled"]


# ------------------------------------------------------------ 4. sell
def test_sell_when_price_crosses_up_through_sell_level():
    state = PaperState(shares=2000, cash=0.0, last_price=1.30, trades_count=0)
    plan = FakePlan(current_price=1.36, regime="range",
                    sell_levels=[1.35, 1.40], buy_levels=[1.25])
    new_state, events = simulate_day(state, plan, trade_shares=200)
    sells = [e for e in events if e["type"] == "sell"]
    assert len(sells) == 1                  # 一天只卖一档
    assert sells[0]["price"] == 1.35
    assert sells[0]["shares"] == 200
    expected_fee = commission(200 * 1.35)
    assert new_state.shares == 1800
    assert new_state.cash == pytest.approx(200 * 1.35 - expected_fee)
    assert new_state.trades_count == 1


# ------------------------------------------------------------ 5. buy
def test_buy_when_price_crosses_down_through_buy_level():
    state = PaperState(shares=2000, cash=10_000.0, last_price=1.30, trades_count=0)
    plan = FakePlan(current_price=1.24, regime="range",
                    sell_levels=[1.35], buy_levels=[1.25, 1.20])
    new_state, events = simulate_day(state, plan, trade_shares=200)
    buys = [e for e in events if e["type"] == "buy"]
    assert len(buys) == 1                   # 一天只买一档
    assert buys[0]["price"] == 1.25
    assert buys[0]["shares"] == 200
    expected_fee = commission(200 * 1.25)
    assert new_state.shares == 2200
    assert new_state.cash == pytest.approx(10_000.0 - 200 * 1.25 - expected_fee)
    assert new_state.trades_count == 1


# ------------------------------------------------------------ 6. invalidation
def test_invalidation_blocks_buy_but_allows_sell():
    """跌破失效下沿时，买信号应被抑制，但卖信号不受影响。"""
    # 构造一个同日同时跨上卖档和下买档的极端场景：
    # prev=1.28 → current=1.10：下跨 1.25 买档，下跨 1.15 买档；但也跌破 lower_invalidation=1.15
    # 结果：invalidation 抑制买
    state = PaperState(shares=2000, cash=10_000.0, last_price=1.28)
    plan = FakePlan(current_price=1.10, regime="range",
                    sell_levels=[], buy_levels=[1.25, 1.20, 1.15],
                    lower_invalidation=1.15)
    new_state, events = simulate_day(state, plan)
    types = [e["type"] for e in events]
    assert "invalidation" in types
    assert "buy" not in types                           # 买被抑制
    assert new_state.shares == 2000                     # 未成交
    assert new_state.cash == pytest.approx(10_000.0)

    # 再验证卖信号不受 invalidation 影响：prev=1.30 → current=1.10（但上卖档在 1.10 以上无从请触发）
    # 改一个场景：prev=1.10 → current=1.36（向上穿），but lower_invalidation=1.15 不会触发
    # 所以另构造一个“先日已在失效区间、今日反跳过卖档”的状况
    state2 = PaperState(shares=2000, cash=0.0, last_price=1.14)   # prev 已低于 lower_inv
    plan2 = FakePlan(current_price=1.36, regime="range",
                     sell_levels=[1.35], buy_levels=[1.15],
                     lower_invalidation=1.15)
    new_state2, events2 = simulate_day(state2, plan2)
    types2 = [e["type"] for e in events2]
    assert "sell" in types2                              # 向上穿卖档——卖仍可触发
    assert new_state2.shares == 1800


# ------------------------------------------------------------ 7. stop_loss_trigger
def test_stop_loss_trigger_freezes_and_blocks_buy():
    state = PaperState(shares=2000, cash=10_000.0, last_price=1.30,
                       stop_price=1.20, frozen=False)
    plan = FakePlan(current_price=1.10, regime="range",
                    sell_levels=[], buy_levels=[1.25, 1.15],
                    last_trade_date="2026-04-24")
    new_state, events = simulate_day(state, plan)
    types = [e["type"] for e in events]
    assert "stop_loss_trigger" in types
    assert "buy" not in types                            # 冻结后不能再买
    assert new_state.frozen is True
    assert new_state.frozen_at == "2026-04-24"
    assert new_state.frozen_price == 1.10
    assert new_state.shares == 2000                      # 无交易
    assert new_state.cash == pytest.approx(10_000.0)


# ------------------------------------------------------------ 8. cash 不足
def test_buy_aborts_when_cash_insufficient():
    """cash 不足 amount+fee 时，buy 中止，不产生 buy 事件和交易计数。"""
    state = PaperState(shares=2000, cash=10.0, last_price=1.30, trades_count=0)   # 根本买不起
    plan = FakePlan(current_price=1.24, regime="range",
                    sell_levels=[], buy_levels=[1.25])
    new_state, events = simulate_day(state, plan, trade_shares=200)
    types = [e["type"] for e in events]
    assert "buy" not in types
    assert new_state.shares == 2000
    assert new_state.cash == pytest.approx(10.0)
    assert new_state.trades_count == 0
    # 未触发任何事件 → 应归于 hold
    assert types == ["hold"]


# ------------------------------------------------------------ 额外：纯函数 invariant
def test_state_immutability_original_unchanged():
    """验证 simulate_day 不修改传入的 state。"""
    state = PaperState(shares=2000, cash=10_000.0, last_price=1.30)
    plan = FakePlan(current_price=1.36, regime="range", sell_levels=[1.35])
    before = state
    new_state, _ = simulate_day(state, plan, trade_shares=200)
    assert state is before
    assert state.shares == 2000                          # 原 state 未被 mutate
    assert state.cash == 10_000.0
    assert new_state.shares == 1800                      # 新 state 是独立对象


# ------------------------------------------------------------ Phase 5.1：底仓保护
def test_base_shares_lock_allows_sell_until_floor():
    """base_shares=800 时，股数 1000 可卖 200 卒好碰到底仓线。"""
    state = PaperState(shares=1000, cash=0.0, last_price=1.30, base_shares=800)
    plan = FakePlan(current_price=1.36, regime="range",
                    sell_levels=[1.35], buy_levels=[])
    new_state, events = simulate_day(state, plan, trade_shares=200)
    sells = [e for e in events if e["type"] == "sell"]
    assert len(sells) == 1                     # 边界条件——卖完正好等于底仓，允许
    assert new_state.shares == 800
    assert new_state.base_shares == 800        # replace 自动保留


def test_base_shares_lock_blocks_sell_through_floor():
    """shares 已等于 base_shares 时，再有卖档不能再卖（否则会砍破底仓）。"""
    state = PaperState(shares=800, cash=0.0, last_price=1.35, base_shares=800)
    plan = FakePlan(current_price=1.41, regime="range",
                    sell_levels=[1.40], buy_levels=[])
    new_state, events = simulate_day(state, plan, trade_shares=200)
    sells = [e for e in events if e["type"] == "sell"]
    assert sells == []                         # 底仓保护：不卖
    assert new_state.shares == 800             # 持仓不动
    assert new_state.trades_count == 0


def test_base_shares_zero_equivalent_to_legacy_behavior():
    """base_shares=0（默认）时，卖单条件 shares-trade >= 0 等价旧版 shares >= trade。

    shares=200, base_shares=0 → 可卖穿到 0。保障非 hybrid profile 行为不变。
    """
    state = PaperState(shares=200, cash=0.0, last_price=1.30)  # base_shares 缺省 = 0
    plan = FakePlan(current_price=1.36, regime="range",
                    sell_levels=[1.35], buy_levels=[])
    new_state, events = simulate_day(state, plan, trade_shares=200)
    sells = [e for e in events if e["type"] == "sell"]
    assert len(sells) == 1                     # 老行为：能卖到完
    assert new_state.shares == 0
    assert new_state.base_shares == 0


# ------------------------------------------------------------ Phase 5.2：现金地板 + 应急解锁
def test_cash_floor_blocks_buy_when_cash_below_floor():
    """cash_floor_ratio=1.0 → 地板=总权益 → spendable=0 → 任何买单被拒。"""
    from atr_grid.config import GridConfig
    cfg = GridConfig(cash_floor_ratio=1.0)
    state = PaperState(shares=0, cash=50.0, last_price=1.00)
    plan = FakePlan(current_price=0.94, regime="range",
                    sell_levels=[], buy_levels=[0.95])
    new_state, events = simulate_day(
        state, plan, trade_shares=30,
        cash_floor=50.0, total_equity=50.0, cfg=cfg,
        emergency_unlocked=False,
    )
    types = [e["type"] for e in events]
    assert "buy" not in types
    assert "cash_floor_block" in types
    assert new_state.shares == 0
    assert new_state.cash == pytest.approx(50.0)
    assert new_state.trades_count == 0


def test_cash_floor_emergency_unlocked_allows_buy():
    """emergency_unlocked=True + use_ratio=1.0 → 地板完全解锁 → 买单放行。"""
    from atr_grid.config import GridConfig
    cfg = GridConfig(cash_floor_ratio=1.0, emergency_refill_use_ratio=1.0)
    state = PaperState(shares=0, cash=50.0, last_price=1.00)
    plan = FakePlan(current_price=0.94, regime="range",
                    sell_levels=[], buy_levels=[0.95])
    new_state, events = simulate_day(
        state, plan, trade_shares=30,
        cash_floor=50.0, total_equity=50.0, cfg=cfg,
        emergency_unlocked=True,
    )
    types = [e["type"] for e in events]
    assert "buy" in types
    assert "cash_floor_block" not in types
    assert new_state.shares == 30
    assert new_state.trades_count == 1


def test_cash_floor_not_engaged_when_kwargs_default():
    """kwargs 不传时（cash_floor=0 / cfg=None）买单完全走老路径，行为不变。

    这条保证 live paper_daily.py 和单体测试在不启用 hybrid 时完全无感知。
    """
    state = PaperState(shares=0, cash=50.0, last_price=1.00)
    plan = FakePlan(current_price=0.94, regime="range",
                    sell_levels=[], buy_levels=[0.95])
    new_state, events = simulate_day(state, plan, trade_shares=30)
    types = [e["type"] for e in events]
    assert "buy" in types
    assert "cash_floor_block" not in types
    assert new_state.shares == 30
