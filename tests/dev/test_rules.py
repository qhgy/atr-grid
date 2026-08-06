"""量化规则测试：指数过滤、波动率目标、现金地板、应急通道。"""

import pytest

from dev.config import with_overrides
from dev.strategy.rules import (
    cash_floor_approved,
    emergency_unlocked,
    evaluate_index_filter,
    round_lot,
    vol_scalar,
)

CFG = with_overrides(
    vol_target_annual=0.25,
    cash_floor_ratio=0.20,
    emergency_drop_pct=0.10,
    emergency_use_ratio=0.5,
)


def _index_row(close, ma_filter, ret_n, ma_stab=None, low_n=None, low=None):
    return {
        "close": close, "ma_filter": ma_filter, "ret_n": ret_n,
        "ma_stab": ma_stab, "low_n": low_n, "low": low,
    }


def test_index_weak_requires_both_below_ma_and_negative_return():
    rows = {
        "SZ399001": _index_row(close=9.0, ma_filter=10.0, ret_n=-0.03),
        "SZ399006": _index_row(close=18.0, ma_filter=20.0, ret_n=-0.05),
    }
    assert evaluate_index_filter(rows, CFG).weak is True


def test_index_not_weak_if_one_holds_up():
    rows = {
        "SZ399001": _index_row(close=9.0, ma_filter=10.0, ret_n=-0.03),
        "SZ399006": _index_row(close=21.0, ma_filter=20.0, ret_n=0.01),
    }
    assert evaluate_index_filter(rows, CFG).weak is False


def test_index_missing_data_is_neutral():
    result = evaluate_index_filter({}, CFG)
    assert result.weak is False and result.stabilized is True


def test_index_stabilized_definition():
    rows = {
        "SZ399001": _index_row(9.5, 10.0, -0.01, ma_stab=9.4, low_n=9.2, low=9.3),
    }
    assert evaluate_index_filter(rows, CFG).stabilized is True
    rows_new_low = {
        "SZ399001": _index_row(9.5, 10.0, -0.01, ma_stab=9.4, low_n=9.2, low=9.1),
    }
    assert evaluate_index_filter(rows_new_low, CFG).stabilized is False


def test_vol_scalar_absolute_mode():
    cfg = with_overrides(CFG, vol_mode="absolute")
    assert vol_scalar(0.20, cfg) == 1.0            # 波动低于目标 → 不放大
    assert vol_scalar(0.50, cfg) == pytest.approx(0.5)
    assert vol_scalar(None, cfg) == 1.0            # 缺数据保守用 1


def test_vol_scalar_relative_mode_adapts_to_new_normal():
    cfg = with_overrides(CFG, vol_mode="relative")
    # 当前波动 = 自身常态 → 满仓系数；高于常态才按比例降
    assert vol_scalar(0.42, cfg, rvol_ref=0.42) == 1.0
    assert vol_scalar(0.60, cfg, rvol_ref=0.42) == pytest.approx(0.7)
    # 波动低于常态 → 封顶 1，不加杠杆
    assert vol_scalar(0.30, cfg, rvol_ref=0.42) == 1.0
    # 参照缺失 → 保守用 1
    assert vol_scalar(0.60, cfg, rvol_ref=None) == 1.0


def test_cash_floor_blocks_and_partially_approves():
    equity = 100_000.0  # 地板 = 20_000
    assert cash_floor_approved(50_000, 20_000, equity, CFG) == 20_000  # 全额放行
    assert cash_floor_approved(25_000, 20_000, equity, CFG) == 5_000   # 部分放行
    assert cash_floor_approved(20_000, 10_000, equity, CFG) == 0.0     # 触地板拦截


def test_cash_floor_emergency_halves_floor():
    equity = 100_000.0  # 应急地板 = 10_000
    assert cash_floor_approved(20_000, 15_000, equity, CFG, emergency=True) == 10_000


def test_emergency_unlocked_at_drawdown_threshold():
    assert emergency_unlocked(high_lookback=10.0, close=9.0, cfg=CFG) is True   # -10%
    assert emergency_unlocked(high_lookback=10.0, close=9.2, cfg=CFG) is False  # -8%
    assert emergency_unlocked(high_lookback=None, close=9.0, cfg=CFG) is False


def test_round_lot():
    assert round_lot(250, 100) == 200
    assert round_lot(99, 100) == 0
    assert round_lot(-5, 100) == 0
