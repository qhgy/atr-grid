"""Weak external-market context for 515880.

The overnight AI-chain filter is intentionally advisory. It should adjust
buying aggressiveness, not override 515880's own price/trend state.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from typing import Any

from .config import GridConfig

_BJT = timezone(timedelta(hours=8))


@dataclass(slots=True)
class ExternalMarketContext:
    enabled: bool
    status: str
    label: str
    note: str
    avg_return_pct: float | None = None
    strong_count: int = 0
    weak_count: int = 0
    symbols: dict[str, float] | None = None
    generated_at: str = ""
    warning: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def fetch_external_ai_context(cfg: GridConfig) -> ExternalMarketContext:
    """Fetch overnight AI-chain returns and classify them as a weak filter."""
    if not cfg.external_ai_enabled:
        return ExternalMarketContext(
            enabled=False,
            status="disabled",
            label="外盘 AI 链未启用",
            note="未启用隔夜外盘过滤。",
            generated_at=_now_bjt(),
        )

    try:
        returns = _fetch_yfinance_returns(cfg.external_ai_symbols)
    except Exception as exc:
        return ExternalMarketContext(
            enabled=True,
            status="missing",
            label="外盘 AI 链数据缺失",
            note="外盘数据获取失败，本轮不调整买入积极性。",
            generated_at=_now_bjt(),
            warning=f"external_ai_fetch_failed:{type(exc).__name__}",
        )

    if not returns:
        return ExternalMarketContext(
            enabled=True,
            status="missing",
            label="外盘 AI 链数据缺失",
            note="未取得有效隔夜涨跌幅，本轮不调整买入积极性。",
            generated_at=_now_bjt(),
            warning="external_ai_no_valid_returns",
        )

    avg = round(sum(returns.values()) / len(returns), 2)
    strong_count = sum(1 for value in returns.values() if value >= cfg.external_ai_strong_avg_threshold)
    weak_count = sum(1 for value in returns.values() if value <= cfg.external_ai_cautious_avg_threshold)

    if avg <= cfg.external_ai_severe_avg_threshold:
        status = "severe_weak"
        label = "隔夜 AI 链明显走弱"
        note = "降低当日买入积极性；即使 515880 到支撑区，也只允许小仓确认。"
    elif avg <= cfg.external_ai_cautious_avg_threshold:
        status = "weak"
        label = "隔夜 AI 链偏弱"
        note = "降低追买和第二笔加仓积极性，优先等待 515880 自身企稳确认。"
    elif avg >= cfg.external_ai_strong_avg_threshold and strong_count >= max(2, len(returns) // 3):
        status = "strong"
        label = "隔夜 AI 链偏强"
        note = "增强趋势信心，但不作为追高买入理由。"
    else:
        status = "neutral"
        label = "隔夜 AI 链中性"
        note = "不改变 515880 主判断。"

    return ExternalMarketContext(
        enabled=True,
        status=status,
        label=label,
        note=note,
        avg_return_pct=avg,
        strong_count=strong_count,
        weak_count=weak_count,
        symbols={symbol: round(value, 2) for symbol, value in sorted(returns.items())},
        generated_at=_now_bjt(),
    )


def _fetch_yfinance_returns(symbols: tuple[str, ...]) -> dict[str, float]:
    import yfinance as yf

    data = yf.download(
        list(symbols),
        period="5d",
        interval="1d",
        auto_adjust=False,
        progress=False,
        threads=True,
        timeout=8,
    )
    returns: dict[str, float] = {}
    if data is None or data.empty:
        return returns

    for symbol in symbols:
        try:
            if len(symbols) == 1:
                close = data["Close"].dropna()
            else:
                close = data["Close"][symbol].dropna()
            if len(close) < 2:
                continue
            prev_close = float(close.iloc[-2])
            last_close = float(close.iloc[-1])
            if prev_close:
                returns[symbol] = (last_close - prev_close) / prev_close * 100
        except Exception:
            continue
    return returns


def _now_bjt() -> str:
    return datetime.now(_BJT).strftime("%Y-%m-%d %H:%M")
