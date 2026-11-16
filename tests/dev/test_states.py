"""状态机全转移路径测试。"""

import pytest

from dev.config import with_overrides
from dev.strategy.states import (
    StrategyState,
    TacticalState,
    daily_update,
    on_rebuy_filled,
    on_sell_filled,
)

CFG = with_overrides(trend_confirm_days=3, freeze_atr_mult=1.0)


def test_sell_then_rebuy_round_trip():
    s0 = StrategyState()
    s1 = on_sell_filled(s0, price=3.0, shares=300, rebuy_target=2.9)
    assert s1.tactical == TacticalState.TRIMMED
    assert s1.sell_shares == 300 and s1.rebuy_target == 2.9

    s2 = on_rebuy_filled(s1, price=2.88)
    assert s2.tactical == TacticalState.IDLE
    assert s2.sell_shares == 0 and s2.last_rebuy_fill == 2.88


def test_sell_requires_idle():
    trimmed = on_sell_filled(StrategyState(), price=3.0, shares=300, rebuy_target=2.9)
    with pytest.raises(ValueError):
        on_sell_filled(trimmed, price=3.1, shares=300, rebuy_target=3.0)


def test_rebuy_requires_trimmed():
    with pytest.raises(ValueError):
        on_rebuy_filled(StrategyState(), price=2.9)


def test_trend_confirm_needs_consecutive_days():
    s = StrategyState()
    for _ in range(2):
        s, _ = daily_update(s, close=3.0, ma_trend=2.8, ma_unfreeze=None, atr=0.05, cfg=CFG)
    assert not s.trend_on  # 只有 2 天，不够确认
    s, reasons = daily_update(s, close=3.0, ma_trend=2.8, ma_unfreeze=None, atr=0.05, cfg=CFG)
    assert s.trend_on
    assert any("确认上行趋势" in r for r in reasons)


def test_trend_streak_resets_on_cross():
    s = StrategyState()
    s, _ = daily_update(s, close=3.0, ma_trend=2.8, ma_unfreeze=None, atr=0.05, cfg=CFG)
    s, _ = daily_update(s, close=2.7, ma_trend=2.8, ma_unfreeze=None, atr=0.05, cfg=CFG)
    assert s.above_streak == 0 and s.below_streak == 1


def test_trend_off_after_confirm_days_below():
    s = StrategyState(trend_on=True)
    for _ in range(3):
        s, _ = daily_update(s, close=2.5, ma_trend=2.8, ma_unfreeze=None, atr=0.05, cfg=CFG)
    assert not s.trend_on


def test_freeze_after_drop_below_rebuy_fill():
    s = StrategyState(last_rebuy_fill=3.0)
    # 跌幅未超 1×ATR：不冻结
    s1, _ = daily_update(s, close=2.96, ma_trend=None, ma_unfreeze=3.1, atr=0.05, cfg=CFG)
    assert s1.tactical == TacticalState.IDLE
    # 跌破 接回价 - 1×ATR：冻结
    s2, reasons = daily_update(s, close=2.94, ma_trend=None, ma_unfreeze=3.1, atr=0.05, cfg=CFG)
    assert s2.tactical == TacticalState.FROZEN
    assert any("冻结" in r for r in reasons)


def test_unfreeze_when_back_above_ma():
    frozen = StrategyState(tactical=TacticalState.FROZEN, last_rebuy_fill=3.0)
    s, reasons = daily_update(frozen, close=3.2, ma_trend=None, ma_unfreeze=3.1, atr=0.05, cfg=CFG)
    assert s.tactical == TacticalState.IDLE
    assert s.last_rebuy_fill is None  # 解冻清空记忆
    assert any("解除买入冻结" in r for r in reasons)


def test_trimmed_can_freeze_and_abandon_round():
    trimmed = on_sell_filled(StrategyState(last_rebuy_fill=3.0), price=3.1, shares=300, rebuy_target=3.0)
    s, _ = daily_update(trimmed, close=2.90, ma_trend=None, ma_unfreeze=3.2, atr=0.05, cfg=CFG)
    assert s.tactical == TacticalState.FROZEN


def test_abandon_rebuy_when_price_runs_away_up():
    trimmed = on_sell_filled(StrategyState(), price=3.0, shares=300, rebuy_target=2.9)
    # 未超过 卖价 + 1×ATR：仍等待接回
    s1, _ = daily_update(trimmed, close=3.04, ma_trend=None, ma_unfreeze=None, atr=0.05, cfg=CFG)
    assert s1.tactical == TacticalState.TRIMMED
    # 涨过 卖价 + 1×ATR：放弃接回，解锁新轮次
    s2, reasons = daily_update(trimmed, close=3.06, ma_trend=None, ma_unfreeze=None, atr=0.05, cfg=CFG)
    assert s2.tactical == TacticalState.IDLE
    assert s2.sell_shares == 0 and s2.sell_price is None
    assert s2.last_rebuy_fill is None  # 弃轮不产生冻结基准
    assert any("放弃本轮接回" in r for r in reasons)
