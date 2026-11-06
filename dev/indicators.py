"""纯函数指标。所有函数输入 DataFrame/Series，输出 Series，不修改入参。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import DEFAULT_CONFIG, StrategyConfig


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window, min_periods=window).mean()


def atr(frame: pd.DataFrame, window: int) -> pd.Series:
    """Wilder 真实波幅的简单均值（与现有 atr_grid 口径一致，便于对照）。"""
    high, low, close = frame["high"], frame["low"], frame["close"]
    prev_close = close.shift()
    true_range = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return true_range.rolling(window=window, min_periods=window).mean()


def realized_vol_annual(
    close: pd.Series, window: int, trading_days: int = 244
) -> pd.Series:
    """已实现波动率（对数收益标准差，年化）。"""
    log_ret = np.log(close / close.shift())
    return log_ret.rolling(window=window, min_periods=window).std() * np.sqrt(trading_days)


def rolling_low(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window, min_periods=window).min()


def pct_return(close: pd.Series, periods: int) -> pd.Series:
    return close.pct_change(periods=periods)


def enrich_symbol(frame: pd.DataFrame, cfg: StrategyConfig = DEFAULT_CONFIG) -> pd.DataFrame:
    """主标的指标列。"""
    out = frame.copy()
    close = out["close"]
    out["ma_trend"] = sma(close, cfg.trend_window)
    out["ma_unfreeze"] = sma(close, cfg.unfreeze_ma_window)
    out["atr"] = atr(out, cfg.atr_window)
    out["rvol"] = realized_vol_annual(close, cfg.vol_window, cfg.trading_days_per_year)
    # 相对波动参照：自身已实现波动的滚动中位数（资产"变身"后自动适配新常态）
    out["rvol_ref"] = out["rvol"].rolling(
        window=cfg.vol_ref_window, min_periods=cfg.vol_window * 3
    ).median()
    out["high_lookback"] = out["high"].rolling(
        window=cfg.emergency_lookback, min_periods=1
    ).max()
    return out


def enrich_index(frame: pd.DataFrame, cfg: StrategyConfig = DEFAULT_CONFIG) -> pd.DataFrame:
    """过滤指数指标列。"""
    out = frame.copy()
    close = out["close"]
    out["ma_filter"] = sma(close, cfg.index_ma_window)
    out["ret_n"] = pct_return(close, cfg.index_weak_ret_days)
    out["ma_stab"] = sma(close, cfg.index_stab_ma_window)
    out["low_n"] = rolling_low(out["low"], cfg.index_stab_ma_window)
    return out
