"""双机制参数检验（dual-regime walk-forward）。

背景：515880 成分股在 AI 大潮中整体换血，2024 年前后统计上是两个资产
（年化波动 30%→42%，对创业板 beta 0.72→0.98）。因此：

- 旧机制段（起始 ~ regime_split，芯片牛熊完整周期）**不用于预测收益**，
  只作"题材崩塌彩排"：参数必须在这段里活下来（回撤硬约束）；
- 新机制段（regime_split ~ 今）是参数选择的主战场，但留出末尾
  holdout_days 个交易日做样本外检验，防止对着新机制曲线拟合。

协议：
    1. 每组参数跑两次：压力段（end=split）、训练段（start=split, end=holdout 起点）；
    2. 可行性 = 压力段策略 MaxDD 浅于 买入持有 MaxDD × MAX_DD_VS_BENCH；
    3. 可行组按训练段 CAGR 排名，第一名再跑 holdout 段作样本外报告；
    4. holdout 与训练段大幅劣化 → 判过拟合，回退默认参数。
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import pandas as pd

from ..config import DEFAULT_CONFIG, StrategyConfig, with_overrides
from .metrics import MetricsBundle, compute_metrics
from .runner import run_backtest

DEFAULT_GRID: dict[str, list] = {
    "vol_mode": ["relative", "absolute"],
    "grid_k": [1.0, 1.3, 1.6],
    "base_ratio": [0.40, 0.50, 0.60],
    "trend_confirm_days": [5, 8],
}

MAX_DD_VS_BENCH = 0.70  # 压力段回撤约束：≤ 买入持有回撤的 70%


@dataclass(slots=True)
class TrialResult:
    params: dict
    stress: MetricsBundle
    train: MetricsBundle
    feasible: bool
    score: float


@dataclass(slots=True)
class WalkForwardResult:
    regime_split: str
    holdout_start: str
    best_params: dict
    stress: MetricsBundle
    train: MetricsBundle
    holdout: MetricsBundle | None
    trials: list[TrialResult] = field(default_factory=list)


def run_walkforward(
    bundle: dict[str, pd.DataFrame],
    cfg: StrategyConfig = DEFAULT_CONFIG,
    *,
    regime_split: str = "2024-01-01",
    holdout_days: int = 120,
    grid: dict[str, list] | None = None,
) -> WalkForwardResult:
    grid = grid or DEFAULT_GRID
    dates = bundle[cfg.symbol]["date"]
    if holdout_days >= len(dates):
        raise ValueError("holdout_days 超过数据长度")
    holdout_start = dates.iloc[-holdout_days].strftime("%Y-%m-%d")

    keys = list(grid)
    trials: list[TrialResult] = []
    for combo in itertools.product(*(grid[k] for k in keys)):
        params = dict(zip(keys, combo))
        trial_cfg = with_overrides(cfg, **params)
        stress_m = compute_metrics(run_backtest(bundle, trial_cfg, end=regime_split), trial_cfg)
        train_m = compute_metrics(
            run_backtest(bundle, trial_cfg, start=regime_split, end=holdout_start), trial_cfg
        )
        feasible = (
            stress_m.strategy.max_drawdown
            >= stress_m.benchmark.max_drawdown * MAX_DD_VS_BENCH
        )
        score = train_m.strategy.cagr if feasible else float("-inf")
        trials.append(TrialResult(params, stress_m, train_m, feasible, score))

    trials.sort(key=lambda t: (t.score, t.train.strategy.sharpe), reverse=True)
    best = trials[0]

    holdout_m: MetricsBundle | None = None
    best_cfg = with_overrides(cfg, **best.params)
    try:
        holdout_m = compute_metrics(
            run_backtest(bundle, best_cfg, start=holdout_start), best_cfg
        )
    except ValueError:
        pass  # holdout 段数据不足

    return WalkForwardResult(
        regime_split=regime_split,
        holdout_start=holdout_start,
        best_params=best.params,
        stress=best.stress,
        train=best.train,
        holdout=holdout_m,
        trials=trials,
    )


def render_walkforward(wf: WalkForwardResult, top_n: int = 10) -> str:
    lines: list[str] = []
    lines.append("# 双机制参数检验（dual-regime walk-forward）")
    lines.append("")
    lines.append(
        f"压力段（旧成分，只验生存）：起始 ~ {wf.regime_split}　"
        f"训练段（AI 时代）：{wf.regime_split} ~ {wf.holdout_start}　"
        f"样本外：{wf.holdout_start} ~ 末尾"
    )
    lines.append(f"压力段硬约束：策略 MaxDD ≤ 买入持有 MaxDD × {MAX_DD_VS_BENCH:.0%}")
    lines.append("")
    lines.append(f"## 入选参数：`{wf.best_params}`")
    lines.append("")
    lines.append("| 段 | CAGR | MaxDD | Sharpe | 持有CAGR | 持有MaxDD | 轮次 | 每轮期望 |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    lines.append(_row("压力(2020~23)", wf.stress))
    lines.append(_row("训练(2024+)", wf.train))
    if wf.holdout is not None:
        lines.append(_row("样本外(holdout)", wf.holdout))
    lines.append("")
    lines.append(f"## 敏感性（按训练段 CAGR 前 {top_n} 组）")
    lines.append("")
    lines.append("| 参数 | 可行 | 训练CAGR | 训练Sharpe | 压力MaxDD | 压力CAGR |")
    lines.append("|---|---|---:|---:|---:|---:|")
    for t in wf.trials[:top_n]:
        lines.append(
            f"| `{t.params}` | {'✓' if t.feasible else '✗'} "
            f"| {t.train.strategy.cagr:+.1%} | {t.train.strategy.sharpe:.2f} "
            f"| {t.stress.strategy.max_drawdown:.1%} | {t.stress.strategy.cagr:+.1%} |"
        )
    lines.append("")
    lines.append(
        "判读：样本外与训练段大幅劣化 → 过拟合，回退默认；"
        "压力段不可行(✗)的参数无论训练段多好看都不许上线。"
    )
    lines.append("")
    return "\n".join(lines)


def _row(label: str, m: MetricsBundle) -> str:
    return (
        f"| {label} | {m.strategy.cagr:+.1%} | {m.strategy.max_drawdown:.1%} "
        f"| {m.strategy.sharpe:.2f} | {m.benchmark.cagr:+.1%} | {m.benchmark.max_drawdown:.1%} "
        f"| {m.trips.count} | ¥{m.trips.expectancy:,.0f} |"
    )
