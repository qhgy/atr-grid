"""Phase 1.3 backtest 引擎基础单测。

用合成 K 线验证：
1. 回测能跑通（不报错，equity curve 长度合理）
2. 震荡市确实产生 round-trip（胜率 / 赔率可算）
3. FIFO 配对逻辑正确（单元测试 _pair_round_trips）
4. 纯单边上涨市 => benchmark 明显赢过 strategy
5. 循环中 state 跟随成交正确演化
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from atr_grid.backtest import (
    BacktestResult,
    RoundTrip,
    _max_drawdown,
    _pair_round_trips,
    _sharpe,
    run_backtest,
)


# ---------------------------------------------------------------- K 线合成工具

_EPOCH = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _make_bar(day_offset: int, close: float, *, amp: float = 0.015) -> dict:
    """合成一根 K 线。和雪球的 K 线结构对齐：有 timestamp(ms) 字段。"""
    dt = _EPOCH + timedelta(days=day_offset)
    return {
        "timestamp": int(dt.timestamp() * 1000),
        "open": close * (1 - amp * 0.2),
        "high": close * (1 + amp),
        "low": close * (1 - amp),
        "close": close,
        "volume": 1_000_000,
    }


def _sinusoidal_rows(n: int = 200, *, center: float = 1.30, amp: float = 0.08, period: int = 20) -> list[dict]:
    """正弦震荡行情：center 上下 ±amp 周期性挥动。适合网格策略测试。"""
    rows = []
    for i in range(n):
        close = center + amp * math.sin(2 * math.pi * i / period)
        rows.append(_make_bar(i, close))
    return rows


def _trending_rows(n: int = 200, *, start: float = 1.00, drift_per_day: float = 0.005) -> list[dict]:
    """纯单边上涨行情。例：start=1.00，0.5%/天 → 200 天翻倍多。"""
    rows = []
    price = start
    for i in range(n):
        price *= 1 + drift_per_day
        rows.append(_make_bar(i, price))
    return rows


# ---------------------------------------------------------------- 1. 单元测 _pair_round_trips

def test_pair_round_trips_simple_fifo():
    """买 100@1.0 → 卖 100@1.1 → 1 笔 round-trip，gross_pnl=10。"""
    trades = [
        {"type": "buy", "date": "D1", "price": 1.00, "shares": 100, "fee": 0.0},
        {"type": "sell", "date": "D2", "price": 1.10, "shares": 100, "fee": 0.0},
    ]
    rts = _pair_round_trips(trades)
    assert len(rts) == 1
    assert rts[0].buy_price == 1.00
    assert rts[0].sell_price == 1.10
    assert rts[0].shares == 100
    assert rts[0].gross_pnl == pytest.approx(10.0, abs=1e-6)
    assert rts[0].net_pnl == pytest.approx(10.0, abs=1e-6)
    assert rts[0].return_pct == pytest.approx(10.0, abs=1e-6)


def test_pair_round_trips_partial_fifo():
    """买 100@1.0 + 买 100@1.2 + 卖 150@1.3 → 产生 2 笔 round-trip。

    第一笔：100@1.0 配 100@1.3 = gross 30
    第二笔：50@1.2 配 50@1.3 = gross 5。
    """
    trades = [
        {"type": "buy", "date": "D1", "price": 1.00, "shares": 100, "fee": 0.0},
        {"type": "buy", "date": "D2", "price": 1.20, "shares": 100, "fee": 0.0},
        {"type": "sell", "date": "D3", "price": 1.30, "shares": 150, "fee": 0.0},
    ]
    rts = _pair_round_trips(trades)
    assert len(rts) == 2
    assert rts[0].shares == 100
    assert rts[0].gross_pnl == pytest.approx(30.0, abs=1e-6)
    assert rts[1].shares == 50
    assert rts[1].gross_pnl == pytest.approx(5.0, abs=1e-6)


def test_pair_round_trips_sell_without_buy_ignored():
    """卖出前未有买入——模拟初始持仓卖出——不计入 round-trip。"""
    trades = [
        {"type": "sell", "date": "D1", "price": 1.30, "shares": 200, "fee": 0.0},
    ]
    rts = _pair_round_trips(trades)
    assert rts == []


# ---------------------------------------------------------------- 2. 单元测 _max_drawdown / _sharpe

def test_max_drawdown_monotonic_up():
    """单调上升 → MDD = 0。"""
    curve = [{"equity": 100 + i} for i in range(20)]
    assert _max_drawdown(curve) == 0.0


def test_max_drawdown_peak_then_crash():
    """先升到 150 再降到 90 → MDD = (150-90)/150 = 40%。"""
    curve = [{"equity": v} for v in [100, 120, 150, 130, 90, 110]]
    assert _max_drawdown(curve) == pytest.approx(40.0, abs=1e-6)


def test_sharpe_zero_volatility():
    """equity 完全平坦 → std=0 → Sharpe=0（避免 div-by-zero）。"""
    curve = [{"equity": 100.0} for _ in range(30)]
    assert _sharpe(curve) == 0.0


# ---------------------------------------------------------------- 3. 集成测 run_backtest (震荡市)

def test_run_backtest_on_oscillating_market_produces_trades():
    """网格策略在震荡市上应产生 round-trip。"""
    rows = _sinusoidal_rows(n=200, center=1.30, amp=0.10, period=20)
    result = run_backtest(
        rows=rows,
        symbol="TEST_OSC",
        initial_cash=100_000.0,
        initial_shares=2000,
        trade_shares=200,
        warmup_bars=60,
    )
    assert isinstance(result, BacktestResult)
    assert result.symbol == "TEST_OSC"
    assert result.bars > 100
    # equity curve 每日一个点
    assert len(result.equity_curve) == result.bars
    # 震荡市 → 应有交易
    assert result.trade_count > 0
    # 有买有卖，应产生 round-trip
    if result.buy_count > 0 and result.sell_count > 0:
        assert result.round_trip_count >= 1
    # KPI 字段存在且类型正确
    assert 0.0 <= result.win_rate <= 1.0
    assert result.max_drawdown_pct >= 0.0
    assert isinstance(result.events_summary, dict)


def test_run_backtest_trending_up_regime_protection():
    """单边上涨市：regime=trend_up 下网格被禁用，strategy 应与全持仓 benchmark 一致。

    这验证了一个关键设计保护：上涨趋势时不硬抓网格回接，避免“卖飞”。
    如果将来引入 trend_trim直接在回测中成交，这个断言需要重写。
    """
    rows = _trending_rows(n=200, start=1.00, drift_per_day=0.004)
    result = run_backtest(
        rows=rows,
        symbol="TEST_UP",
        initial_cash=50_000.0,
        initial_shares=2000,
        trade_shares=200,
        warmup_bars=60,
    )
    # 单边上涨 + 当前 trend_up 不触发 plan 中的 primary_buy/sell 穿越 => 无成交
    assert result.trade_count == 0
    assert result.round_trip_count == 0
    # strategy 等价 buy-and-hold（因为从未卖出）
    assert result.total_return_pct == pytest.approx(result.benchmark_return_pct, abs=0.01)
    # events_summary 中大量 hold，肯定没有 buy/sell
    assert result.events_summary.get("buy", 0) == 0
    assert result.events_summary.get("sell", 0) == 0


def test_run_backtest_equity_curve_shape():
    """equity curve 每日记录必要字段，且第一天起点合理。"""
    rows = _sinusoidal_rows(n=150)
    result = run_backtest(
        rows=rows,
        symbol="TEST",
        initial_cash=100_000.0,
        initial_shares=1000,
        trade_shares=200,
        warmup_bars=60,
    )
    assert len(result.equity_curve) >= 1
    first = result.equity_curve[0]
    for key in ("date", "price", "shares", "cash", "equity", "benchmark", "regime", "grid_enabled"):
        assert key in first
    # equity = cash + shares * price
    assert first["equity"] == pytest.approx(first["cash"] + first["shares"] * first["price"], abs=0.01)


def test_run_backtest_raises_when_not_enough_bars():
    rows = _sinusoidal_rows(n=30)
    with pytest.raises(ValueError):
        run_backtest(
            rows=rows,
            symbol="TOO_SHORT",
            initial_cash=100_000.0,
            initial_shares=2000,
            warmup_bars=60,
        )


def test_run_backtest_requires_rows_or_symbol():
    with pytest.raises(ValueError):
        run_backtest()


# ---------------------------------------------------------------- Phase 5.1：hybrid overlay 接入回测

def test_trend_hybrid_profile_locks_base_shares_in_uptrend():
    """Phase 5.1 核心保险丝：

    trend_hybrid profile 下，run_backtest 把 initial_shares 作为底仓线足计入。
    单边上涨行情 + hybrid high 档 only_sell 原本会把持仓卖穿，接入后 final_shares
    不会低于 initial_shares。
    """
    from atr_grid.config import for_profile

    rows = _trending_rows(n=200, start=1.00, drift_per_day=0.005)
    cfg = for_profile("trend_hybrid")
    result = run_backtest(
        rows=rows,
        symbol="TEST_HYBRID_LOCK",
        cfg=cfg,
        profile_name="trend_hybrid",
        initial_cash=10_000.0,
        initial_shares=1000,
        warmup_bars=60,
    )
    # 底仓线 1000 被锁——final_shares 必然 >= 1000
    assert result.final_shares >= 1000, (
        f"hybrid 底仓应被锁住为 1000，实际 final_shares={result.final_shares}"
    )


def test_non_hybrid_profile_backtest_results_unchanged():
    """非 hybrid profile 下 apply_hybrid_overlay 透明返回，回测行为和 Phase 5 之前等价。

    用震荡市纯网格跑一趟，断言买卖均产生 + trade_count > 0。
    """
    rows = _sinusoidal_rows(n=200, center=1.30, amp=0.10, period=20)
    result = run_backtest(
        rows=rows,
        symbol="TEST_STABLE_UNCHANGED",
        initial_cash=100_000.0,
        initial_shares=2000,
        trade_shares=200,
        warmup_bars=60,
    )
    assert result.trade_count > 0
    assert result.buy_count > 0
    assert result.sell_count > 0


# ---------------------------------------------------------------- Phase 5.1：hybrid overlay 接入回测

def test_trend_hybrid_profile_locks_base_shares_in_uptrend():
    """Phase 5.1 核心保险丝：

    trend_hybrid profile 下，run_backtest 把 initial_shares 作为底仓线足计入。
    单边上涨行情 + hybrid high 档 only_sell 原本会把持仓卖穿，接入后 final_shares
    不会低于 initial_shares。
    """
    from atr_grid.config import for_profile

    rows = _trending_rows(n=200, start=1.00, drift_per_day=0.005)
    cfg = for_profile("trend_hybrid")
    result = run_backtest(
        rows=rows,
        symbol="TEST_HYBRID_LOCK",
        cfg=cfg,
        profile_name="trend_hybrid",
        initial_cash=10_000.0,
        initial_shares=1000,
        warmup_bars=60,
    )
    # 底仓线 1000 被锁——final_shares 必然 >= 1000
    assert result.final_shares >= 1000, (
        f"hybrid 底仓应被锁住为 1000，实际 final_shares={result.final_shares}"
    )


def test_non_hybrid_profile_backtest_results_unchanged():
    """非 hybrid profile 下 apply_hybrid_overlay 透明返回，回测行为和 Phase 5 之前等价。

    用震荡市纯网格跑一趟，断言买卖均产生 + trade_count > 0。
    """
    rows = _sinusoidal_rows(n=200, center=1.30, amp=0.10, period=20)
    result = run_backtest(
        rows=rows,
        symbol="TEST_STABLE_UNCHANGED",
        initial_cash=100_000.0,
        initial_shares=2000,
        trade_shares=200,
        warmup_bars=60,
    )
    assert result.trade_count > 0
    assert result.buy_count > 0
    assert result.sell_count > 0
