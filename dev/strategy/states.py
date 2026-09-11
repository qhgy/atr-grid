"""显式状态机：机动仓轮次 + 趋势确认 + 买入冻结。

把总纲里的文字纪律变成可测试的转移函数：

    高位卖出 → 跌到接回价 → 接回 → 接回后续跌超 1×ATR → 冻结买入
    → 收盘站回 MA20 → 解冻

所有函数都是纯函数：输入旧状态，返回新状态，绝不原地修改。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from ..config import DEFAULT_CONFIG, StrategyConfig


class TacticalState(str, Enum):
    IDLE = "idle"          # 无挂起轮次，可开新一轮卖出
    TRIMMED = "trimmed"    # 已卖出一档，等待接回
    FROZEN = "frozen"      # 买入冻结（接回后继续下跌触发）


@dataclass(frozen=True, slots=True)
class StrategyState:
    """跨日持久的策略状态。"""

    tactical: TacticalState = TacticalState.IDLE
    sell_price: float | None = None     # 本轮卖出成交价
    sell_shares: int = 0                # 本轮已卖出、待接回的股数
    rebuy_target: float | None = None   # 本轮接回参考价
    last_rebuy_fill: float | None = None  # 上一轮接回成交价（冻结判定基准）
    trend_on: bool = False              # 长期趋势确认状态
    above_streak: int = 0               # 收盘连续高于趋势线的天数
    below_streak: int = 0               # 收盘连续低于趋势线的天数


# ---------------------------------------------------------------------------
# 成交事件转移
# ---------------------------------------------------------------------------


def on_sell_filled(
    state: StrategyState, *, price: float, shares: int, rebuy_target: float
) -> StrategyState:
    """机动仓卖出成交：IDLE → TRIMMED。"""
    if state.tactical != TacticalState.IDLE:
        raise ValueError(f"卖出成交只能发生在 IDLE 状态，当前 {state.tactical}")
    return replace(
        state,
        tactical=TacticalState.TRIMMED,
        sell_price=float(price),
        sell_shares=int(shares),
        rebuy_target=float(rebuy_target),
    )


def on_rebuy_filled(state: StrategyState, *, price: float) -> StrategyState:
    """接回成交：TRIMMED → IDLE，记录接回价供冻结判定。"""
    if state.tactical != TacticalState.TRIMMED:
        raise ValueError(f"接回成交只能发生在 TRIMMED 状态，当前 {state.tactical}")
    return replace(
        state,
        tactical=TacticalState.IDLE,
        sell_price=None,
        sell_shares=0,
        rebuy_target=None,
        last_rebuy_fill=float(price),
    )


# ---------------------------------------------------------------------------
# 每日收盘转移（趋势确认 + 冻结/解冻）
# ---------------------------------------------------------------------------


def daily_update(
    state: StrategyState,
    *,
    close: float,
    ma_trend: float | None,
    ma_unfreeze: float | None,
    atr: float | None,
    cfg: StrategyConfig = DEFAULT_CONFIG,
) -> tuple[StrategyState, list[str]]:
    """用当日收盘更新趋势确认与冻结状态，返回 (新状态, 原因列表)。"""
    reasons: list[str] = []
    new = state

    # -- 趋势确认（连续 N 日越线才翻转，防止单日噪音）--
    if ma_trend is not None:
        if close > ma_trend:
            new = replace(new, above_streak=new.above_streak + 1, below_streak=0)
        elif close < ma_trend:
            new = replace(new, below_streak=new.below_streak + 1, above_streak=0)
        if not new.trend_on and new.above_streak >= cfg.trend_confirm_days:
            new = replace(new, trend_on=True)
            reasons.append(
                f"收盘连续 {cfg.trend_confirm_days} 日高于 MA{cfg.trend_window}，确认上行趋势"
            )
        elif new.trend_on and new.below_streak >= cfg.trend_confirm_days:
            new = replace(new, trend_on=False)
            reasons.append(
                f"收盘连续 {cfg.trend_confirm_days} 日低于 MA{cfg.trend_window}，趋势确认转弱，底仓转入退出通道"
            )

    # -- 放弃接回：卖出后价格向上脱离（涨过卖价 + abandon_atr_mult×ATR）--
    # 不解锁会让状态永远卡在 TRIMMED，机动仓停止循环（强趋势标的的致命伤）。
    if (
        new.tactical == TacticalState.TRIMMED
        and new.sell_price is not None
        and atr is not None
        and close > new.sell_price + cfg.abandon_atr_mult * atr
    ):
        new = replace(
            new,
            tactical=TacticalState.IDLE,
            sell_price=None,
            sell_shares=0,
            rebuy_target=None,
        )
        reasons.append(
            f"收盘 {close:.3f} 已向上脱离卖出价，放弃本轮接回（不追高买回），解锁新轮次"
        )

    # -- 冻结：接回后继续下跌超过 freeze_atr_mult × ATR --
    if (
        new.tactical != TacticalState.FROZEN
        and new.last_rebuy_fill is not None
        and atr is not None
        and close < new.last_rebuy_fill - cfg.freeze_atr_mult * atr
    ):
        new = replace(new, tactical=TacticalState.FROZEN)
        reasons.append(
            f"收盘 {close:.3f} 较接回价 {new.last_rebuy_fill:.3f} 回落超 "
            f"{cfg.freeze_atr_mult}×ATR，冻结全部买入"
        )

    # -- 解冻：收盘站回 MA20 --
    if (
        new.tactical == TacticalState.FROZEN
        and ma_unfreeze is not None
        and close > ma_unfreeze
    ):
        new = replace(
            new,
            tactical=TacticalState.IDLE,
            last_rebuy_fill=None,
            sell_price=None,
            sell_shares=0,
            rebuy_target=None,
        )
        reasons.append(f"收盘站回 MA{cfg.unfreeze_ma_window}，解除买入冻结")

    return new, reasons
