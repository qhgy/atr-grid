"""事件驱动回测主循环。

时间线（无前视）：
    t 日收盘 → decide() 产出订单与新状态
    t+1 日   → broker 在 t+1 的 OHLC 上撮合 → 成交回写状态机
    t+1 收盘 → 记录净值，进入下一轮 decide()

基准：同一起始日以开盘价一次性满仓买入持有（同样扣佣金/滑点）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from ..config import DEFAULT_CONFIG, StrategyConfig
from ..indicators import enrich_index, enrich_symbol
from ..strategy import rules
from ..strategy.engine import Decision, Order, PortfolioView, decide
from ..strategy.states import StrategyState, TacticalState, on_rebuy_filled, on_sell_filled
from .broker import Fill, Portfolio, Trade, commission, execute_day


@dataclass(slots=True)
class RoundTrip:
    sell_date: str
    rebuy_date: str
    shares: int
    sell_price: float
    rebuy_price: float
    fees: float
    pnl: float          # (卖价-接回价)*股数 - 双边费用

    @property
    def win(self) -> bool:
        return self.pnl > 0


@dataclass(slots=True)
class BacktestResult:
    symbol: str
    start: str
    end: str
    equity: pd.Series
    benchmark: pd.Series
    trades: list[Trade]
    round_trips: list[RoundTrip]
    abandoned_rounds: int
    final_portfolio: Portfolio
    final_state: StrategyState
    last_decision: Decision | None
    warnings: list[str] = field(default_factory=list)


def _index_lookup(
    bundle: dict[str, pd.DataFrame],
    main_dates: pd.Series,
    cfg: StrategyConfig,
) -> dict[str, pd.DataFrame]:
    """指数指标对齐到主标的交易日（ffill，停牌/缺数取前值）。"""
    aligned: dict[str, pd.DataFrame] = {}
    for symbol in cfg.index_symbols:
        frame = bundle.get(symbol)
        if frame is None or frame.empty:
            continue
        enriched = enrich_index(frame, cfg).set_index("date")
        aligned[symbol] = enriched.reindex(pd.Index(main_dates), method="ffill")
    return aligned


def run_backtest(
    bundle: dict[str, pd.DataFrame],
    cfg: StrategyConfig = DEFAULT_CONFIG,
    *,
    start: str | None = None,
    end: str | None = None,
) -> BacktestResult:
    raw = bundle[cfg.symbol]
    if end:
        raw = raw[raw["date"] <= pd.Timestamp(end)].reset_index(drop=True)
    df = enrich_symbol(raw, cfg)

    # 暖机：趋势线/ATR/波动率齐备才开始决策
    valid = df[["ma_trend", "atr", "rvol"]].notna().all(axis=1)
    if not valid.any():
        raise ValueError(f"{cfg.symbol} 历史不足以计算 MA{cfg.trend_window}，无法回测")
    start_i = int(valid.idxmax())
    if start:
        start_ts = pd.Timestamp(start)
        later = df.index[(df["date"] >= start_ts) & valid]
        if len(later) == 0:
            raise ValueError(f"起始日 {start} 之后无可用数据")
        start_i = int(later[0])

    index_frames = _index_lookup(bundle, df["date"], cfg)

    portfolio = Portfolio(cash=cfg.initial_capital)
    state = StrategyState()
    pending: list[Order] = []
    trades: list[Trade] = []
    round_trips: list[RoundTrip] = []
    abandoned = 0
    open_round_sell: Trade | None = None
    equity_dates: list[pd.Timestamp] = []
    equity_values: list[float] = []
    last_decision: Decision | None = None

    for i in range(start_i, len(df)):
        row = df.iloc[i]
        date_str = row["date"].strftime("%Y-%m-%d")
        bar = {"open": row["open"], "high": row["high"], "low": row["low"], "close": row["close"]}

        # 1) 撮合昨日订单
        fills = execute_day(portfolio, pending, bar, date_str, cfg)
        for f in fills:
            trades.append(f.trade)
            state, open_round_sell, completed = _apply_fill_to_state(
                state, f, open_round_sell, cfg
            )
            if completed is not None:
                round_trips.append(completed)

        # 2) 收盘决策（内部含 daily_update；TRIMMED→FROZEN/向上脱离 均视为弃轮）
        was_trimmed = state.tactical == TacticalState.TRIMMED
        decision = decide(
            row=row.to_dict(),
            index_rows={
                s: frame.loc[row["date"]].to_dict()
                for s, frame in index_frames.items()
                if row["date"] in frame.index
            },
            portfolio=PortfolioView(
                cash=portfolio.cash,
                base_shares=portfolio.base_shares,
                tactical_shares=portfolio.tactical_shares,
            ),
            state=state,
            cfg=cfg,
        )
        if was_trimmed and decision.state.tactical != TacticalState.TRIMMED:
            abandoned += 1
            open_round_sell = None
        state = decision.state
        pending = decision.orders
        last_decision = decision

        # 3) 记录净值
        equity_dates.append(row["date"])
        equity_values.append(portfolio.equity(float(row["close"])))

    equity = pd.Series(equity_values, index=pd.Index(equity_dates, name="date"), name="strategy")
    benchmark = _buy_and_hold(df.iloc[start_i:], cfg)

    return BacktestResult(
        symbol=cfg.symbol,
        start=equity.index[0].strftime("%Y-%m-%d"),
        end=equity.index[-1].strftime("%Y-%m-%d"),
        equity=equity,
        benchmark=benchmark,
        trades=trades,
        round_trips=round_trips,
        abandoned_rounds=abandoned,
        final_portfolio=portfolio,
        final_state=state,
        last_decision=last_decision,
    )


def _apply_fill_to_state(
    state: StrategyState,
    fill: Fill,
    open_round_sell: Trade | None,
    cfg: StrategyConfig,
) -> tuple[StrategyState, Trade | None, RoundTrip | None]:
    """把机动仓限价单成交映射为状态机事件，必要时配对出完整轮次。"""
    order, trade = fill.order, fill.trade
    if order.layer != "tactical" or order.kind != "limit":
        return state, open_round_sell, None

    if order.side == "sell" and state.tactical == TacticalState.IDLE:
        offset = order.ref if order.ref is not None else 0.0
        rebuy_target = round(trade.price - offset, cfg.price_precision)
        new_state = on_sell_filled(
            state, price=trade.price, shares=trade.shares, rebuy_target=rebuy_target
        )
        return new_state, trade, None

    if order.side == "buy" and state.tactical == TacticalState.TRIMMED:
        new_state = on_rebuy_filled(state, price=trade.price)
        completed: RoundTrip | None = None
        if open_round_sell is not None:
            fees = open_round_sell.fee + trade.fee
            pnl = (open_round_sell.price - trade.price) * trade.shares - fees
            completed = RoundTrip(
                sell_date=open_round_sell.date,
                rebuy_date=trade.date,
                shares=trade.shares,
                sell_price=open_round_sell.price,
                rebuy_price=trade.price,
                fees=fees,
                pnl=pnl,
            )
        return new_state, None, completed

    return state, open_round_sell, None


def _buy_and_hold(df: pd.DataFrame, cfg: StrategyConfig) -> pd.Series:
    """基准：起始日次日开盘满仓买入并持有（含成本），首日按现金计。"""
    dates = list(df["date"])
    closes = list(df["close"])
    opens = list(df["open"])
    values: list[float] = [cfg.initial_capital]
    if len(df) < 2:
        return pd.Series(values[: len(df)], index=pd.Index(dates[: len(df)], name="date"), name="benchmark")

    tick = 10.0 ** -cfg.price_precision
    entry = opens[1] + cfg.slippage_ticks * tick
    shares = rules.round_lot(cfg.initial_capital / entry, cfg.lot_size)
    cost = shares * entry
    cash = cfg.initial_capital - cost - commission(cost, cfg)
    for close in closes[1:]:
        values.append(shares * close + cash)
    return pd.Series(values, index=pd.Index(dates, name="date"), name="benchmark")
