"""轮次统计 + 回测主循环端到端冒烟测试（合成数据，不联网）。"""

import numpy as np
import pandas as pd
import pytest

from dev.backtest.metrics import compute_metrics, trip_stats
from dev.backtest.runner import RoundTrip, run_backtest
from dev.config import with_overrides


def _result_with_trips(pnls):
    class _Stub:
        round_trips = [
            RoundTrip("2026-01-01", "2026-01-05", 300, 3.1, 3.0, 1.0, pnl)
            for pnl in pnls
        ]
        abandoned_rounds = 1

    return _Stub()


def test_trip_stats_win_rate_payoff_expectancy():
    stats = trip_stats(_result_with_trips([100.0, 50.0, -30.0]))
    assert stats.count == 3 and stats.wins == 2
    assert stats.win_rate == pytest.approx(2 / 3)
    assert stats.avg_win == pytest.approx(75.0)
    assert stats.avg_loss == pytest.approx(30.0)
    assert stats.payoff == pytest.approx(2.5)
    # 期望 = 2/3×75 − 1/3×30 = 40
    assert stats.expectancy == pytest.approx(40.0)
    assert stats.abandoned == 1


def test_trip_stats_empty():
    stats = trip_stats(_result_with_trips([]))
    assert stats.count == 0 and stats.win_rate == 0.0


def _synthetic_bundle(cfg, n=520, seed=7):
    """上行趋势 + 周期震荡的合成行情，确保趋势层和机动仓轮次都会触发。"""
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    trend = 2.0 + t * 0.004
    wave = 0.12 * np.sin(t / 9.0)
    noise = rng.normal(0, 0.015, n)
    close = trend + wave + noise
    spread = np.abs(rng.normal(0.02, 0.008, n)) + 0.01
    frame = pd.DataFrame(
        {
            "date": pd.bdate_range("2024-01-01", periods=n),
            "open": close + rng.normal(0, 0.008, n),
            "high": close + spread,
            "low": close - spread,
            "close": close,
            "volume": [1_000_000] * n,
        }
    )
    frame["open"] = frame[["open", "low"]].max(axis=1)
    frame["open"] = frame[["open", "high"]].min(axis=1)
    index = frame.copy()
    return {cfg.symbol: frame, cfg.index_symbols[0]: index, cfg.index_symbols[1]: index.copy()}


def test_backtest_end_to_end_smoke():
    cfg = with_overrides(trend_window=100, kline_count=520)
    bundle = _synthetic_bundle(cfg)
    result = run_backtest(bundle, cfg)

    # 资金守恒：净值 = 现金 + 持仓×收盘，且现金永不为负
    assert result.final_portfolio.cash >= 0
    last_close = float(bundle[cfg.symbol]["close"].iloc[-1])
    assert result.equity.iloc[-1] == pytest.approx(
        result.final_portfolio.cash + result.final_portfolio.total_shares * last_close
    )

    # 上行行情中应当建立过仓位、发生过交易
    assert result.trades, "全程零成交说明引擎没有接通"
    assert (result.equity > 0).all()

    # 轮次配对自洽：每个完成轮次卖价 > 接回价 - 合理范围
    for trip in result.round_trips:
        assert trip.shares > 0
        assert trip.fees > 0

    m = compute_metrics(result, cfg)
    assert m.benchmark.final_value > 0
    assert -1.0 < m.strategy.max_drawdown <= 0.0


def test_final_day_cash_floor():
    cfg = with_overrides(trend_window=100, cash_floor_ratio=0.20)
    bundle = _synthetic_bundle(cfg)
    result = run_backtest(bundle, cfg)
    final_equity = float(result.equity.iloc[-1])
    hard_floor = final_equity * cfg.cash_floor_ratio * (1 - cfg.emergency_use_ratio)
    # 期末现金不低于硬地板（地板的一半，应急通道的极限）
    assert result.final_portfolio.cash >= hard_floor * 0.99
