"""量化规则：指数过滤、波动率目标、现金地板、应急通道。

全部纯函数。每条规则都是总纲模糊表述的可检验定义：

- 指数同步走弱 = 两指数收盘均 < 各自 MA20 且 5 日收益均为负
- 指数企稳     = 收盘站回 MA5 且当日未创 5 日新低
- 波动率目标   = min(1, 目标年化波动 / 已实现年化波动)
- 应急通道     = 20 日最高价回撤 ≥ 10%，地板可动用一半
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..config import DEFAULT_CONFIG, StrategyConfig


@dataclass(frozen=True, slots=True)
class IndexFilterResult:
    weak: bool          # 同步走弱 → 买入降级
    stabilized: bool    # 企稳 → 恢复接回资格
    detail: str


def evaluate_index_filter(
    index_rows: dict[str, dict[str, float | None]],
    cfg: StrategyConfig = DEFAULT_CONFIG,
) -> IndexFilterResult:
    """输入各指数当日指标行（close/ma_filter/ret_n/ma_stab/low_n/low）。

    数据缺失时降级为中性（weak=False, stabilized=True）——过滤器失效
    不应阻塞主策略，但 detail 会注明。
    """
    if not index_rows:
        return IndexFilterResult(False, True, "指数数据缺失，过滤器中性")

    weak_flags: list[bool] = []
    stab_flags: list[bool] = []
    parts: list[str] = []
    for symbol, row in index_rows.items():
        close = _f(row.get("close"))
        ma_filter = _f(row.get("ma_filter"))
        ret_n = _f(row.get("ret_n"))
        ma_stab = _f(row.get("ma_stab"))
        low_n = _f(row.get("low_n"))
        low = _f(row.get("low"))
        if close is None or ma_filter is None or ret_n is None:
            parts.append(f"{symbol} 指标不足，按中性")
            weak_flags.append(False)
            stab_flags.append(True)
            continue
        is_weak = close < ma_filter and ret_n < 0
        weak_flags.append(is_weak)
        # 企稳 = 收盘站回 MA5 且当日未创 N 日新低（low_n 含当日，故用 >=）
        if ma_stab is None:
            is_stab = True
        elif low_n is None or low is None:
            is_stab = close > ma_stab
        else:
            is_stab = close > ma_stab and low >= low_n
        stab_flags.append(bool(is_stab))
        parts.append(
            f"{symbol} 收盘{'低于' if close < ma_filter else '高于'}MA{cfg.index_ma_window}"
            f"，{cfg.index_weak_ret_days}日收益 {ret_n * 100:+.1f}%"
        )

    weak = all(weak_flags) and len(weak_flags) > 0
    stabilized = all(stab_flags)
    if weak:
        parts.append("两指数同步走弱 → 买入降级（只完成接回，不开新仓）")
    return IndexFilterResult(weak, stabilized, "；".join(parts))


def vol_scalar(
    rvol: float | None,
    cfg: StrategyConfig = DEFAULT_CONFIG,
    *,
    rvol_ref: float | None = None,
) -> float:
    """波动率目标化缩放系数，∈ (0, 1]。

    - relative 模式：与资产自身近 vol_ref_window 日波动中位数比较，
      波动高于自己的常态才减仓（成分股换血导致波动水位整体抬升时不误伤）；
    - absolute 模式：与固定年化目标比较（Moreira-Muir 原始形态）；
    - 数据缺失一律保守用 1。
    """
    if rvol is None or not math.isfinite(rvol) or rvol <= 0:
        return 1.0
    if cfg.vol_mode == "relative":
        if rvol_ref is None or not math.isfinite(rvol_ref) or rvol_ref <= 0:
            return 1.0
        return min(1.0, rvol_ref / rvol)
    return min(1.0, cfg.vol_target_annual / rvol)


def emergency_unlocked(
    high_lookback: float | None,
    close: float,
    cfg: StrategyConfig = DEFAULT_CONFIG,
) -> bool:
    """20 日高点回撤 ≥ 阈值 → 解锁应急通道（地板可动用一半）。"""
    if high_lookback is None or high_lookback <= 0:
        return False
    return (high_lookback - close) / high_lookback >= cfg.emergency_drop_pct


def cash_floor_approved(
    cash: float,
    intended: float,
    equity: float,
    cfg: StrategyConfig = DEFAULT_CONFIG,
    *,
    emergency: bool = False,
) -> float:
    """返回放行金额（0 ≤ 放行 ≤ intended），买单不得击穿现金地板。"""
    if intended <= 0 or equity <= 0:
        return 0.0
    floor = equity * cfg.cash_floor_ratio
    if emergency:
        floor *= 1.0 - cfg.emergency_use_ratio
    return max(0.0, min(intended, cash - floor))


def round_lot(shares: float, lot_size: int) -> int:
    """向下取整到整手。"""
    if shares <= 0:
        return 0
    return int(shares // lot_size) * lot_size


def _f(value) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None
