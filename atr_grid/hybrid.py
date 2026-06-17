"""Phase 4 · Trend-Hybrid 资金分层模块。

设计原则（与 engine.py 解耦）：

- 纯函数 + 明确 IO，engine/paper 只需按需调用，不强制依赖。
- 参数全部走 GridConfig，无硬编码数字，便于回测/调参/热切换。
- 默认关闭（`cfg.trend_hybrid_enabled=False`），现有调用方零影响。
- 四层语义：
  1. 底仓（base）：启动时一次性买入并死扛，不参与网格决策。
  2. 网格层（swing）：原 ATR 网格，按"位置分位"动态缩放可用资金。
  3. 顶部止盈（top-trim）：位置进入最高档时，网格只卖不买（在此模块里仅
     给出标志位；真正的买卖侧抑制放在 engine 接线时执行）。
  4. 现金地板（cash floor）：任何买单不得击穿地板；大跌时才动用豁免额度。

engine / paper 预期调用顺序：

    pct = position_percentile(frame, window=cfg.position_window)
    band = resolve_band(pct, cfg)
    alloc = compute_capital_allocation(total_equity, band, cfg)
    decision = cash_floor_guard(cash_before, intended_amount, total_equity, cfg,
                                emergency_unlocked=...)

这样把"位置判断 → 分档 → 资金配额 → 下单闸门"四件事收敛到一个文件，
未来只改这里即可升级策略，不需要动 engine 主循环。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

import math

import numpy as np
import pandas as pd

from .config import DEFAULT_CONFIG, GridConfig


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class PositionBand:
    """一个位置档位的定义。

    percentile 区间为 [low, high)（最高档含 100）。swing_ratio 是该档
    下"网格层"可动用资金占"总网格预算"的比例；only_sell=True 表示此档
    网格只卖不买（engine 接线时读这个字段决定是否屏蔽买单）。
    """

    name: str
    low: float  # 0..100
    high: float  # 0..100
    swing_ratio: float  # 0..1
    only_sell: bool = False


@dataclass(slots=True, frozen=True)
class CapitalAllocation:
    """资金分配结果，单位与 total_equity 一致（元）。"""

    total_equity: float
    position_pct: float  # 0..100
    band: PositionBand
    base_budget: float  # 底仓锁定资金
    swing_budget: float  # 网格层当前可动用资金（已按位置档缩放）
    cash_floor: float  # 现金地板硬下限
    only_sell: bool  # 该档是否只卖不买


@dataclass(slots=True, frozen=True)
class CashFloorDecision:
    """`cash_floor_guard` 的返回结果。"""

    approved_amount: float  # 实际放行的下单金额（<= intended_amount）
    rejected: bool  # 是否被完全拒绝
    reason: str


# ---------------------------------------------------------------------------
# 位置分位
# ---------------------------------------------------------------------------


def position_percentile(
    frame: pd.DataFrame,
    *,
    window: int = 60,
    price_column: str = "close",
    high_column: str | None = "high",
    low_column: str | None = "low",
) -> float | None:
    """返回当前价在最近 window 根 K 线的位置刻度（0-100）。

    公式：``(price - min(window)) / (max(window) - min(window)) * 100``

    - 若数据不足（长度 < window），使用可用长度，返回值仍在 0-100。
    - high/low 列提供时用 rolling(max/min)，否则退化到 close 自身。
    - 返回 None 表示无法计算（空 frame 或区间退化为一点）。
    """
    if frame is None or frame.empty:
        return None
    if price_column not in frame.columns:
        return None

    window = max(2, int(window))
    tail = frame.tail(window)
    if tail.empty:
        return None

    price = _last_float(tail[price_column])
    if price is None:
        return None

    high_series = tail[high_column] if high_column and high_column in tail.columns else tail[price_column]
    low_series = tail[low_column] if low_column and low_column in tail.columns else tail[price_column]

    high = _max_float(high_series)
    low = _min_float(low_series)
    if high is None or low is None:
        return None
    span = high - low
    if span <= 0 or math.isnan(span):
        return None

    pct = (price - low) / span * 100.0
    # 允许轻微越界（因为 close vs high/low 可能给到 >100），收敛到 0..100
    return max(0.0, min(100.0, float(pct)))


# ---------------------------------------------------------------------------
# 分档解析
# ---------------------------------------------------------------------------


def default_bands_from_config(cfg: GridConfig = DEFAULT_CONFIG) -> tuple[PositionBand, ...]:
    """从 GridConfig 读取 4 档位置分档定义。

    由小到大依次：low -> mid_low -> mid_high -> high。阈值和比例完全
    参数化，不写死。
    """
    return (
        PositionBand(
            name="low",
            low=0.0,
            high=cfg.position_band_low,
            swing_ratio=cfg.position_alloc_low,
            only_sell=False,
        ),
        PositionBand(
            name="mid_low",
            low=cfg.position_band_low,
            high=cfg.position_band_mid,
            swing_ratio=cfg.position_alloc_mid_low,
            only_sell=False,
        ),
        PositionBand(
            name="mid_high",
            low=cfg.position_band_mid,
            high=cfg.position_band_high,
            swing_ratio=cfg.position_alloc_mid_high,
            only_sell=False,
        ),
        PositionBand(
            name="high",
            low=cfg.position_band_high,
            high=100.0 + 1e-6,  # 上限含 100
            swing_ratio=cfg.position_alloc_high,
            only_sell=True,
        ),
    )


def resolve_band(
    percentile: float | None,
    cfg: GridConfig = DEFAULT_CONFIG,
    *,
    bands: Sequence[PositionBand] | None = None,
) -> PositionBand:
    """找到 percentile 所属档位。None 时返回保守档（mid_low）。"""
    use_bands = tuple(bands) if bands is not None else default_bands_from_config(cfg)
    if percentile is None:
        # 数据不足时走保守策略：中低档（只用一部分网格资金，不误开 only_sell）
        for b in use_bands:
            if b.name == "mid_low":
                return b
        return use_bands[0]

    pct = max(0.0, min(100.0, float(percentile)))
    for b in use_bands:
        if b.low <= pct < b.high:
            return b
    return use_bands[-1]


# ---------------------------------------------------------------------------
# 资金分配
# ---------------------------------------------------------------------------


def compute_capital_allocation(
    total_equity: float,
    percentile: float | None,
    cfg: GridConfig = DEFAULT_CONFIG,
    *,
    bands: Sequence[PositionBand] | None = None,
) -> CapitalAllocation:
    """按总资金 + 当前位置，计算四层预算。

    切分逻辑：

        base_budget  = total_equity * cfg.base_position_ratio
        cash_floor   = total_equity * cfg.cash_floor_ratio
        swing_pool   = total_equity - base_budget - cash_floor
        swing_budget = swing_pool * band.swing_ratio

    - base_position_ratio + cash_floor_ratio 必须 <= 1，否则抛 ValueError。
    - swing_pool < 0 时按 0 处理（防御性保护）。
    - 未启用 hybrid（trend_hybrid_enabled=False）时，也照常返回分配结果，
      调用方自行决定是否应用。纯计算，没有副作用。
    """
    if total_equity < 0:
        raise ValueError("total_equity must be >= 0")
    if not (0.0 <= cfg.base_position_ratio <= 1.0):
        raise ValueError("base_position_ratio must be in [0, 1]")
    if not (0.0 <= cfg.cash_floor_ratio <= 1.0):
        raise ValueError("cash_floor_ratio must be in [0, 1]")
    if cfg.base_position_ratio + cfg.cash_floor_ratio > 1.0 + 1e-9:
        raise ValueError(
            "base_position_ratio + cash_floor_ratio must be <= 1 "
            f"(got {cfg.base_position_ratio} + {cfg.cash_floor_ratio})"
        )

    band = resolve_band(percentile, cfg, bands=bands)

    base_budget = total_equity * cfg.base_position_ratio
    cash_floor = total_equity * cfg.cash_floor_ratio
    swing_pool = max(0.0, total_equity - base_budget - cash_floor)
    swing_budget = swing_pool * max(0.0, min(1.0, band.swing_ratio))

    return CapitalAllocation(
        total_equity=float(total_equity),
        position_pct=float(percentile) if percentile is not None else float("nan"),
        band=band,
        base_budget=float(base_budget),
        swing_budget=float(swing_budget),
        cash_floor=float(cash_floor),
        only_sell=bool(band.only_sell),
    )


# ---------------------------------------------------------------------------
# 现金地板 guard
# ---------------------------------------------------------------------------


def cash_floor_guard(
    cash_before: float,
    intended_amount: float,
    total_equity: float,
    cfg: GridConfig = DEFAULT_CONFIG,
    *,
    emergency_unlocked: bool = False,
) -> CashFloorDecision:
    """在下单之前检查是否会击穿现金地板。

    规则：

    - 若 intended_amount <= 0：放行（卖单或零金额）。
    - 正常情况：允许花到 ``cash_before - cfg.cash_floor_ratio * total_equity``；
      若完全够，原样放行；若部分够，只放行能花的部分（按 lot 精度由上层处理）；
      若完全不够，拒绝。
    - 应急通道（emergency_unlocked=True）：把允许动用的额度抬高为
      ``cash_before - (1 - emergency_refill_use_ratio) * cfg.cash_floor_ratio * total_equity``，
      即地板的 `emergency_refill_use_ratio` 部分可被动用；仍不会低于
      ``(1 - use_ratio) * cash_floor_ratio * total_equity`` 的硬底。
    """
    if intended_amount <= 0:
        return CashFloorDecision(
            approved_amount=float(intended_amount),
            rejected=False,
            reason="non_buy_or_zero",
        )
    if total_equity <= 0:
        return CashFloorDecision(
            approved_amount=0.0,
            rejected=True,
            reason="total_equity_non_positive",
        )

    base_floor = total_equity * cfg.cash_floor_ratio
    if emergency_unlocked:
        use_ratio = max(0.0, min(1.0, cfg.emergency_refill_use_ratio))
        effective_floor = base_floor * (1.0 - use_ratio)
        tag = "emergency_unlock"
    else:
        effective_floor = base_floor
        tag = "normal"

    spendable = max(0.0, cash_before - effective_floor)
    if spendable <= 0:
        return CashFloorDecision(
            approved_amount=0.0,
            rejected=True,
            reason=f"cash_floor_blocked/{tag}/floor={effective_floor:.2f}",
        )

    if intended_amount <= spendable:
        return CashFloorDecision(
            approved_amount=float(intended_amount),
            rejected=False,
            reason=f"approved_full/{tag}",
        )

    return CashFloorDecision(
        approved_amount=float(spendable),
        rejected=False,
        reason=f"approved_partial/{tag}/spendable={spendable:.2f}",
    )


# ---------------------------------------------------------------------------
# 应急补仓触发
# ---------------------------------------------------------------------------


def should_emergency_refill(
    frame: pd.DataFrame,
    cfg: GridConfig = DEFAULT_CONFIG,
    *,
    lookback: int | None = None,
) -> bool:
    """判断是否进入"应急补仓"通道（允许动用部分现金地板）。

    触发条件（默认）：过去 ``emergency_refill_lookback`` 根 K 线的最高价
    到当前收盘价的跌幅 >= ``emergency_refill_drop_pct``。
    """
    if frame is None or frame.empty:
        return False
    window = int(lookback if lookback is not None else cfg.emergency_refill_lookback)
    if window <= 0:
        return False
    tail = frame.tail(window)
    if tail.empty:
        return False

    price = _last_float(tail["close"]) if "close" in tail.columns else None
    if price is None or price <= 0:
        return False
    high_col = tail["high"] if "high" in tail.columns else tail["close"]
    high = _max_float(high_col)
    if high is None or high <= 0:
        return False

    drawdown = (high - price) / high
    return drawdown >= cfg.emergency_refill_drop_pct


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _last_float(series: pd.Series) -> float | None:
    if series is None or series.empty:
        return None
    value = series.iloc[-1]
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(numeric):
        return None
    return numeric


def _max_float(series: pd.Series) -> float | None:
    if series is None or series.empty:
        return None
    try:
        numeric = float(np.nanmax(series.to_numpy(dtype=float)))
    except (TypeError, ValueError):
        return None
    if math.isnan(numeric):
        return None
    return numeric


def _min_float(series: pd.Series) -> float | None:
    if series is None or series.empty:
        return None
    try:
        numeric = float(np.nanmin(series.to_numpy(dtype=float)))
    except (TypeError, ValueError):
        return None
    if math.isnan(numeric):
        return None
    return numeric


__all__ = [
    "PositionBand",
    "CapitalAllocation",
    "CashFloorDecision",
    "position_percentile",
    "default_bands_from_config",
    "resolve_band",
    "compute_capital_allocation",
    "cash_floor_guard",
    "should_emergency_refill",
]
