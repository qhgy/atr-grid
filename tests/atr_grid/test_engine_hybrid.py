"""engine.apply_hybrid_overlay 的单元测试。

覆盖：
- disabled profile → 透明返回 (plan, None)
- 低位 / 中位 → plan 不变 + allocation 非空
- 高位 → plan 被剔除买单侧 + allocation.only_sell=True
- build_plan_with_frame 返回的 plan 和 build_plan_from_context 一致
- _restrict_plan_for_only_sell 独立测试：清买留卖
不开网络，不读码庌。
"""

from __future__ import annotations

import pandas as pd

from atr_grid.config import DEFAULT_CONFIG, GridConfig, for_profile
from atr_grid.data import MarketContext
from atr_grid.engine import (
    _restrict_plan_for_only_sell,
    apply_hybrid_overlay,
    build_plan_from_context,
    build_plan_with_frame,
)


# ---------------------------------------------------------------------------
# 合成用道具
# ---------------------------------------------------------------------------


def _flat_rows(n: int = 80, base: float = 10.0, amp: float = 0.3) -> list[dict]:
    """生成 n 行轻微振荡的 K 线，图択出 range regime。"""
    rows = []
    for i in range(n):
        # 小幅周期振荡，保证 bb_std>0，atr>0
        delta = amp * ((i % 4) - 1.5) / 1.5
        close = base + delta
        rows.append(
            {
                "timestamp": 1_700_000_000_000 + i * 86_400_000,
                "open": close - 0.05,
                "high": close + 0.1,
                "low": close - 0.1,
                "close": close,
                "volume": 1000,
            }
        )
    return rows


def _make_context(current_price: float, rows: list[dict], shares: int = 1000) -> MarketContext:
    return MarketContext(
        symbol="TEST",
        instrument_type="etf",
        price_precision=3,
        shares=shares,
        rows=rows,
        data_source="synthetic",
        current_price=current_price,
        last_close=current_price,
        last_trade_date="2026-04-16",
        warnings=[],
    )


# ---------------------------------------------------------------------------
# build_plan_with_frame
# ---------------------------------------------------------------------------


def test_build_plan_with_frame_returns_same_plan_as_build_plan_from_context():
    ctx = _make_context(10.0, _flat_rows())
    plan_a = build_plan_from_context(ctx)
    ctx2 = _make_context(10.0, _flat_rows())
    plan_b, frame = build_plan_with_frame(ctx2)

    assert plan_a.regime == plan_b.regime
    assert plan_a.mode == plan_b.mode
    assert plan_a.primary_buy == plan_b.primary_buy
    assert plan_a.primary_sell == plan_b.primary_sell
    # frame 是 pandas.DataFrame，有 close 列
    assert hasattr(frame, "columns")
    assert "close" in frame.columns
    assert len(frame) == len(ctx2.rows)


# ---------------------------------------------------------------------------
# apply_hybrid_overlay
# ---------------------------------------------------------------------------


def test_apply_hybrid_disabled_returns_plan_unchanged_and_none_allocation():
    ctx = _make_context(10.0, _flat_rows())
    plan, frame = build_plan_with_frame(ctx)
    # DEFAULT_CONFIG 默认没开 hybrid
    assert DEFAULT_CONFIG.trend_hybrid_enabled is False

    new_plan, allocation = apply_hybrid_overlay(
        plan, frame, total_equity=100_000.0, cfg=DEFAULT_CONFIG
    )
    assert new_plan is plan  # 透明对象，零侵入
    assert allocation is None


def test_apply_hybrid_enabled_low_percentile_does_not_modify_plan():
    cfg = for_profile("trend_hybrid")
    assert cfg.trend_hybrid_enabled is True

    # 构造 rows：高点 20，当前 10——位置在低位
    rows = _flat_rows(n=80, base=20.0, amp=0.3)
    # 最后一根收盘跌下来变为 10，新建低点
    rows[-1] = dict(rows[-1], close=10.0, low=10.0, high=10.0, open=10.0)
    ctx = _make_context(10.0, rows)
    plan, frame = build_plan_with_frame(ctx, cfg=cfg)

    new_plan, allocation = apply_hybrid_overlay(
        plan, frame, total_equity=100_000.0, cfg=cfg
    )
    assert allocation is not None
    assert allocation.only_sell is False
    assert allocation.band.name in ("low", "mid_low")
    # 低位：plan 应该原样返回（不清买单侧）
    assert new_plan.buy_levels == plan.buy_levels
    assert new_plan.primary_buy == plan.primary_buy


def test_apply_hybrid_enabled_high_percentile_strips_buy_side():
    cfg = for_profile("trend_hybrid")
    # 构造 rows：低点 10，当前 20——位置在高位
    rows = _flat_rows(n=80, base=10.0, amp=0.3)
    rows[-1] = dict(rows[-1], close=20.0, low=20.0, high=20.0, open=20.0)
    ctx = _make_context(20.0, rows)
    plan, frame = build_plan_with_frame(ctx, cfg=cfg)

    new_plan, allocation = apply_hybrid_overlay(
        plan, frame, total_equity=100_000.0, cfg=cfg
    )
    assert allocation is not None
    assert allocation.only_sell is True
    assert allocation.band.name == "high"
    # 高位锁死买单侧
    assert new_plan.buy_levels == []
    assert new_plan.primary_buy is None
    assert new_plan.prealert_buy is None
    assert new_plan.reference_rebuy_ladder == []
    assert new_plan.rebuy_price is None
    assert "hybrid_only_sell_applied" in new_plan.warnings
    # 不影响卖单侧：卡在文字上不强制，但至少 plan.reason 里要有提示
    assert "高位" in new_plan.reason


def test_apply_hybrid_empty_frame_defaults_to_conservative_mid_low():
    cfg = for_profile("trend_hybrid")
    # 空 frame → position_percentile 返回 None → resolve_band 走保守档 mid_low
    empty_frame = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    # 暂用空 rows 建 plan 会挂，改用最小类提供 plan
    rows = _flat_rows(n=80, base=10.0, amp=0.3)
    ctx = _make_context(10.0, rows)
    plan, _real_frame = build_plan_with_frame(ctx, cfg=cfg)

    new_plan, allocation = apply_hybrid_overlay(
        plan, empty_frame, total_equity=100_000.0, cfg=cfg
    )
    assert allocation is not None
    assert allocation.band.name == "mid_low"
    assert allocation.only_sell is False
    assert new_plan is plan  # 保守档不清买单侧


# ---------------------------------------------------------------------------
# _restrict_plan_for_only_sell
# ---------------------------------------------------------------------------


def test_restrict_plan_for_only_sell_clears_buy_side():
    ctx = _make_context(10.0, _flat_rows())
    plan = build_plan_from_context(ctx)
    # 前提：为有买单侧
    # 若 disabled / out-of-band 会没买单，此时跳过断言
    restricted = _restrict_plan_for_only_sell(plan)
    assert restricted.buy_levels == []
    assert restricted.primary_buy is None
    assert restricted.prealert_buy is None
    assert restricted.reference_rebuy_ladder == []
    assert restricted.rebuy_price is None
    assert "hybrid_only_sell_applied" in restricted.warnings
    # 原 plan 不被修改（dataclass replace 返回新对象）
    assert restricted is not plan


def test_restrict_plan_idempotent_warning_not_duplicated():
    ctx = _make_context(10.0, _flat_rows())
    plan = build_plan_from_context(ctx)
    r1 = _restrict_plan_for_only_sell(plan)
    r2 = _restrict_plan_for_only_sell(r1)
    # 警告不应重复
    assert r2.warnings.count("hybrid_only_sell_applied") == 1
