"""指标与绩效指标的黄金值测试。"""

import math

import numpy as np
import pandas as pd
import pytest

from dev.backtest.metrics import perf_stats
from dev.config import with_overrides
from dev.indicators import atr, enrich_symbol, realized_vol_annual, sma


def _frame(closes, highs=None, lows=None):
    n = len(closes)
    closes = pd.Series(closes, dtype=float)
    return pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=n),
            "open": closes,
            "high": pd.Series(highs, dtype=float) if highs else closes,
            "low": pd.Series(lows, dtype=float) if lows else closes,
            "close": closes,
            "volume": [1000] * n,
        }
    )


def test_sma_golden():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    out = sma(s, 3)
    assert math.isnan(out.iloc[1])
    assert out.iloc[2] == pytest.approx(2.0)
    assert out.iloc[4] == pytest.approx(4.0)


def test_atr_golden():
    frame = _frame(
        closes=[10.0, 10.5, 10.2],
        highs=[10.2, 10.8, 10.6],
        lows=[9.8, 10.1, 10.0],
    )
    out = atr(frame, 2)
    # TR2 = max(0.7, |10.8-10|, |10.1-10|) = 0.8; TR3 = max(0.6, 0.1, 0.5) = 0.6
    assert out.iloc[2] == pytest.approx((0.8 + 0.6) / 2)


def test_realized_vol_constant_price_is_zero():
    vol = realized_vol_annual(pd.Series([5.0] * 30), window=20)
    assert vol.iloc[-1] == pytest.approx(0.0)


def test_enrich_handles_gap_series_without_nan_explosion():
    # 模拟除息式跳空：前复权序列应已连续，但指标计算不能因单日大波动产生 NaN
    closes = list(np.linspace(10, 12, 250)) + [11.0, 11.1, 11.2]
    frame = _frame(closes)
    cfg = with_overrides(trend_window=200)
    out = enrich_symbol(frame, cfg)
    tail = out.iloc[-1]
    assert not math.isnan(tail["ma_trend"])
    assert not math.isnan(tail["atr"])
    assert not math.isnan(tail["rvol"])


def test_perf_stats_golden():
    # 两年翻倍：CAGR = sqrt(2) - 1
    n = 488  # 2 年 × 244
    values = pd.Series(
        np.geomspace(100_000, 200_000, n),
        index=pd.date_range("2024-01-01", periods=n),
    )
    stats = perf_stats(values, "测试", trading_days=244)
    assert stats.total_return == pytest.approx(1.0)
    assert stats.cagr == pytest.approx(math.sqrt(2) - 1, rel=1e-2)
    assert stats.max_drawdown == pytest.approx(0.0)


def test_perf_stats_max_drawdown():
    values = pd.Series(
        [100.0, 120.0, 90.0, 110.0],
        index=pd.date_range("2026-01-01", periods=4),
    )
    stats = perf_stats(values, "测试")
    assert stats.max_drawdown == pytest.approx(90 / 120 - 1)  # -25%
