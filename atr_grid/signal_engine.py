"""Signal engine for MacroRsi14HardV2 daily grid signals.

Fetches 515880 + NVDA data, computes ATR14/RSI14, outputs grid orders.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

import numpy as np
import pandas as pd

from .config import DEFAULT_CONFIG, GridConfig
from .data import load_market_context
from .indicators import build_indicator_frame, _as_float

_BJT = timezone(timedelta(hours=8))


@dataclass
class GridOrder:
    level: int
    side: str  # "buy" or "sell"
    price: float
    shares: int
    note: str = ""


@dataclass
class SignalResult:
    date: str
    close: float
    atr14: float
    rsi14: float | None
    nvda_ret: float | None
    grid_step: float
    buy_orders: list[GridOrder] = field(default_factory=list)
    sell_orders: list[GridOrder] = field(default_factory=list)
    risk_action: str = "正常"
    rsi_state: str = "正常"
    generated_at: str = ""
    data_source: str = ""
    warnings: list[str] = field(default_factory=list)


def _calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _fetch_nvda_return() -> float | None:
    """Fetch NVDA's latest daily return via yfinance (fallback)."""
    try:
        import yfinance as yf
        ticker = yf.Ticker("NVDA")
        hist = ticker.history(period="5d")
        if len(hist) >= 2:
            prev_close = hist["Close"].iloc[-2]
            last_close = hist["Close"].iloc[-1]
            return (last_close - prev_close) / prev_close * 100
    except Exception:
        pass
    return None


def generate_signal(*, disable_nvda: bool = False, cfg: GridConfig = DEFAULT_CONFIG) -> SignalResult:
    """Generate daily grid signal for 515880."""
    ctx = load_market_context("SH515880", kline_count=100)
    frame = build_indicator_frame(ctx.rows)

    close_series = pd.to_numeric(frame["close"], errors="coerce")
    frame["rsi14"] = _calc_rsi(close_series, 14)

    latest = frame.iloc[-1]
    close = float(latest["close"])
    atr14 = _as_float(latest.get("atr14"))
    rsi14 = _as_float(latest.get("rsi14"))
    bar_date = ctx.last_trade_date

    nvda_ret = None if disable_nvda else _fetch_nvda_return()

    risk_action = "正常"
    if nvda_ret is not None:
        if nvda_ret < cfg.signal_nvda_hard_threshold:
            risk_action = "全部清仓+冻结2天"
        elif nvda_ret < cfg.signal_nvda_cautious_threshold:
            risk_action = "减仓到60%"

    rsi_state = "正常"
    buy_mult = 1.0
    sell_mult = 1.0
    if rsi14 is not None:
        if rsi14 < cfg.signal_rsi_oversold:
            rsi_state = f"超卖加倍(×{cfg.signal_rsi_multiplier})"
            buy_mult = cfg.signal_rsi_multiplier
        elif rsi14 > cfg.signal_rsi_overbought:
            rsi_state = f"超买加倍(×{cfg.signal_rsi_multiplier})"
            sell_mult = cfg.signal_rsi_multiplier

    grid_step = atr14 * cfg.signal_grid_atr_mult if atr14 else 0
    base_budget = cfg.signal_initial_capital * 0.8
    base_shares = int(base_budget / close) if close > 0 else 0
    per_grid = max((base_shares // cfg.signal_grid_levels // 100) * 100, 100)

    buy_scale = cfg.signal_cautious_buy_scale if risk_action == "减仓到60%" else 1.0

    buy_orders = []
    sell_orders = []

    if risk_action == "全部清仓+冻结2天":
        sell_orders = [GridOrder(0, "sell", close, 0, "NVDA暴跌 → 全部清仓")]
    else:
        position = max(int(per_grid * buy_mult * buy_scale // 100) * 100, 100)
        for level in range(1, cfg.signal_grid_levels + 1):
            buy_price = round(close - grid_step * level, 3)
            if buy_price > 0:
                note = ""
                if rsi_state.startswith("超卖"):
                    note = "RSI超卖加倍"
                if buy_scale < 1:
                    note = "NVDA警戒减半"
                buy_orders.append(GridOrder(level, "buy", buy_price, position, note))

        sell_shares = max(int(per_grid * sell_mult // 100) * 100, 100)
        for level in range(1, cfg.signal_grid_levels + 1):
            sell_price = round(close + grid_step * level, 3)
            note = "RSI超买加倍" if rsi_state.startswith("超买") else ""
            sell_orders.append(GridOrder(level, "sell", sell_price, sell_shares, note))

    warnings = list(ctx.warnings)
    if nvda_ret is None and not disable_nvda:
        warnings.append("NVDA数据获取失败，信号已关闭")

    return SignalResult(
        date=bar_date,
        close=close,
        atr14=atr14 or 0,
        rsi14=rsi14,
        nvda_ret=nvda_ret,
        grid_step=round(grid_step, 4),
        buy_orders=buy_orders,
        sell_orders=sell_orders,
        risk_action=risk_action,
        rsi_state=rsi_state,
        generated_at=datetime.now(_BJT).strftime("%Y-%m-%d %H:%M"),
        data_source=ctx.data_source,
        warnings=warnings,
    )
