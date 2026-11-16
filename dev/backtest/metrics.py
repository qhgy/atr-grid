"""绩效指标：胜率/赔率/期望（轮次层）+ CAGR/MaxDD/Sharpe/Calmar/换手（组合层）。"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..config import DEFAULT_CONFIG, StrategyConfig
from .runner import BacktestResult


@dataclass(slots=True)
class PerfStats:
    label: str
    final_value: float
    total_return: float      # 总收益率
    cagr: float
    max_drawdown: float      # 负数，如 -0.32
    sharpe: float
    calmar: float


@dataclass(slots=True)
class TripStats:
    count: int
    wins: int
    win_rate: float          # 胜率
    avg_win: float
    avg_loss: float          # 正数表示平均亏损额
    payoff: float            # 赔率 = avg_win / avg_loss
    expectancy: float        # 每轮期望（元）
    total_pnl: float
    abandoned: int


@dataclass(slots=True)
class MetricsBundle:
    strategy: PerfStats
    benchmark: PerfStats
    trips: TripStats
    turnover_annual: float   # 年化换手（双边成交额 / 平均净值）
    total_fees: float
    trade_count: int


def perf_stats(
    equity: pd.Series, label: str, trading_days: int = 244
) -> PerfStats:
    values = equity.astype(float)
    start_v, end_v = float(values.iloc[0]), float(values.iloc[-1])
    n = len(values)
    total_return = end_v / start_v - 1.0
    years = max(n / trading_days, 1e-9)
    cagr = (end_v / start_v) ** (1.0 / years) - 1.0 if end_v > 0 else -1.0

    running_max = values.cummax()
    drawdown = values / running_max - 1.0
    max_dd = float(drawdown.min())

    daily_ret = values.pct_change().dropna()
    if len(daily_ret) > 1 and daily_ret.std() > 0:
        sharpe = float(daily_ret.mean() / daily_ret.std() * math.sqrt(trading_days))
    else:
        sharpe = 0.0
    calmar = cagr / abs(max_dd) if max_dd < 0 else float("inf")

    return PerfStats(
        label=label,
        final_value=round(end_v, 2),
        total_return=total_return,
        cagr=cagr,
        max_drawdown=max_dd,
        sharpe=sharpe,
        calmar=calmar,
    )


def trip_stats(result: BacktestResult) -> TripStats:
    trips = result.round_trips
    if not trips:
        return TripStats(0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, result.abandoned_rounds)
    pnls = np.array([t.pnl for t in trips], dtype=float)
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = float(-losses.mean()) if len(losses) else 0.0
    win_rate = len(wins) / len(pnls)
    payoff = avg_win / avg_loss if avg_loss > 0 else float("inf")
    expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss
    return TripStats(
        count=len(pnls),
        wins=int(len(wins)),
        win_rate=win_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        payoff=payoff,
        expectancy=expectancy,
        total_pnl=float(pnls.sum()),
        abandoned=result.abandoned_rounds,
    )


def compute_metrics(
    result: BacktestResult, cfg: StrategyConfig = DEFAULT_CONFIG
) -> MetricsBundle:
    days = cfg.trading_days_per_year
    strategy = perf_stats(result.equity, "策略", days)
    benchmark = perf_stats(result.benchmark, "买入持有", days)
    trips = trip_stats(result)

    traded_amount = sum(t.amount for t in result.trades)
    total_fees = sum(t.fee for t in result.trades)
    avg_equity = float(result.equity.mean()) or 1.0
    years = max(len(result.equity) / days, 1e-9)
    turnover_annual = traded_amount / avg_equity / years

    return MetricsBundle(
        strategy=strategy,
        benchmark=benchmark,
        trips=trips,
        turnover_annual=turnover_annual,
        total_fees=round(total_fees, 2),
        trade_count=len(result.trades),
    )
