"""Phase 1.3 历史 walk-forward 回测引擎。

设计原则：
- 复用 paper.simulate_day 纯函数作为每日决策内核（保证回测与虚拟盘语义一致）
- 复用 engine._assemble_plan 做每日滑动 plan 生成（无未来函数：i 日的 plan 只用 rows[:i+1]）
- 成交窗口对齐 simulate_day 的“close-to-close 穿越”语义，不用 intraday high/low（
  Phase 1.3 MVP限制：这会低估网格命中次数，后续 Phase 1.5 再升级 intraday）
- FIFO 配对买卖，产出 round-trip 级胜率 / 赔率 / profit factor / MDD / Sharpe
公开接口：
- run_backtest(...) 返回 BacktestResult dataclass
- BacktestResult / RoundTrip 都是 slots dataclass，可 asdict() JSON 序列化
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from math import sqrt
from statistics import mean, stdev
from typing import Any

from .config import DEFAULT_CONFIG, GridConfig
from .data import MarketContext, load_market_context
from .engine import _assemble_plan
from .indicators import build_indicator_frame, latest_snapshot
from .paper import DEFAULT_TRADE_SHARES, PaperState, commission, simulate_day
from .regime import classify_regime


# ---------------------------------------------------------------- dataclasses

@dataclass(slots=True)
class RoundTrip:
    """单笔 FIFO 配对成的完整买-卖回合。"""

    buy_date: str
    buy_price: float
    sell_date: str
    sell_price: float
    shares: int
    gross_pnl: float        # (sell - buy) * shares
    fees: float             # 分摊到该 round-trip 的 买+卖 手续费
    net_pnl: float          # gross_pnl - fees
    return_pct: float       # net_pnl / (buy_price * shares) * 100


@dataclass(slots=True)
class BacktestResult:
    """回测产出：KPI + 详细交易日志 + equity 曲线。"""

    # 元信息
    symbol: str
    profile: str
    start_date: str
    end_date: str
    bars: int                       # 实际回测天数（排除 warmup）
    # 起终状态
    initial_cash: float
    initial_shares: int
    initial_price: float
    final_cash: float
    final_shares: int
    final_price: float
    final_equity: float
    benchmark_equity: float         # 买入持有对照组
    total_return_pct: float
    benchmark_return_pct: float
    excess_return_pct: float
    # 交易统计
    trade_count: int
    buy_count: int
    sell_count: int
    round_trip_count: int
    win_count: int
    loss_count: int
    # 核心 KPI
    win_rate: float                 # 胜率  = win_count / round_trip_count
    avg_win: float
    avg_loss: float                 # 负数
    payoff_ratio: float             # 赔率  = avg_win / |avg_loss|
    profit_factor: float            # gross_win / gross_loss
    max_drawdown_pct: float         # >=0, 正百分比表示
    sharpe_ratio: float             # 年化 Sharpe (rf=0, periods_per_year=252)
    # 事件 / 详细日志
    events_summary: dict[str, int]
    trades: list[dict]
    round_trips: list[RoundTrip]
    equity_curve: list[dict]
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- 主函数

def run_backtest(
    *,
    symbol: str | None = None,
    rows: list[dict] | None = None,
    price_precision: int = 3,
    instrument_type: str = "etf",
    cfg: GridConfig = DEFAULT_CONFIG,
    profile_name: str = "default",
    initial_shares: int = 0,
    initial_cash: float = 100_000.0,
    trade_shares: int = DEFAULT_TRADE_SHARES,
    warmup_bars: int = 60,
    kline_count: int | None = None,
    stop_pct: float | None = None,
    chandelier_atr_mult: float | None = None,
    chandelier_lookback: int = 22,
) -> BacktestResult:
    """Walk-forward 回测。

    两种调用模式：
    1. 直接传 rows（单测 / 合成 K 线）
    2. 传 symbol，由 load_market_context 拉全量 K 线

    Phase 3.3 止损（默认关闭，详见 docs/phase3_3_stop_loss.md）：
    - stop_pct: 固定成本止损，初始止损价 = initial_price * (1 - stop_pct)。
      跳破触发 simulate_day 内 stop_loss_trigger → frozen → 永久停接回（仅允许卖出）。
      这条路径适合 live paper（人工 resume），回测中开启会永久冻结。
    - chandelier_atr_mult: 动态追踪止损 ATR 倍数。走回测独立路径：每日 simulate_day
      之后维护局部 chand_line = max(prev, highest_high[近 N 日] - M*ATR14)。跳破 → 强制
      卖 trade_shares 股（不触发 paper.frozen），下次自然反弹 grid 可接回。
    - chandelier_lookback: chandelier 回望窗口，默认 22 日（~1 个月）。

    详见模块 docstring。
    """
    if rows is None:
        if not symbol:
            raise ValueError("Either `rows` or `symbol` must be provided")
        effective_count = kline_count if kline_count is not None else max(warmup_bars + 200, 300)
        context = load_market_context(
            symbol,
            shares=max(initial_shares, 2000),
            kline_count=effective_count,
            cfg=cfg,
        )
        rows = list(context.rows)
        price_precision = context.price_precision
        instrument_type = context.instrument_type
        symbol = context.symbol
    else:
        rows = list(rows)
        symbol = symbol or "SYNTHETIC"

    if len(rows) < warmup_bars + 2:
        raise ValueError(
            f"Not enough bars: len(rows)={len(rows)} < warmup_bars({warmup_bars}) + 2"
        )

    # 滑动指标：一次性计算，每日 iloc 切片 → 可接受的未来函数泄露为零
    full_frame = build_indicator_frame(rows, cfg)
    start_index = max(cfg.ma_long_window, warmup_bars)
    if start_index >= len(rows) - 1:
        raise ValueError(
            f"warmup_bars({warmup_bars}) too large for bars={len(rows)}"
        )

    initial_price = float(rows[start_index]["close"])
    initial_equity = initial_cash + initial_shares * initial_price
    initial_stop = (initial_price * (1.0 - stop_pct)) if stop_pct is not None else None
    state = PaperState(
        shares=initial_shares,
        cash=initial_cash,
        last_price=None,
        stop_price=initial_stop,
    )

    trades_log: list[dict] = []
    all_events: list[dict] = []
    equity_curve: list[dict] = []
    # Phase 3.3 chandelier trailing stop line：独立于 paper.stop_price，仅用于回测内部强制减仓。
    chand_line: float | None = None

    for i in range(start_index, len(rows)):
        row = rows[i]
        close = float(row["close"])
        date = _row_date(row, i)

        sub_frame = full_frame.iloc[: i + 1]
        snap = latest_snapshot(sub_frame)
        reg = classify_regime(sub_frame, snap, cfg)

        history_ctx = MarketContext(
            symbol=symbol,
            instrument_type=instrument_type,
            price_precision=price_precision,
            shares=max(state.shares, 0),
            rows=rows[: i + 1],
            data_source="backtest",
            current_price=close,
            last_close=close,
            last_trade_date=date,
            warnings=[],
            fund_meta=None,
        )
        plan = _assemble_plan(history_ctx, snap, reg, cfg)

        state, events = simulate_day(state, plan, trade_shares=trade_shares)
        # 与 cmd_run 对齐: last_price 在 simulate_day 后执行更新
        state = replace(state, last_price=close)

        for ev in events:
            ev = dict(ev)  # copy, 不 mutate 纯函数返回的事件
            ev["date"] = date
            if ev.get("type") in ("buy", "sell"):
                trades_log.append(ev)
            all_events.append(ev)

        # Phase 3.3 chandelier: 在 grid 成交之后独立评估是否强制减仓。
        # 不走 paper.frozen 路径（避免永久冻结）；触发后卖 trade_shares，下次自然反弹再买回。
        if (chandelier_atr_mult is not None
                and state.shares > 0
                and snap.atr14 is not None):
            lb_start = max(0, i - chandelier_lookback + 1)
            highest = max(float(rows[k].get("high", rows[k]["close"]))
                          for k in range(lb_start, i + 1))
            trailing = highest - chandelier_atr_mult * snap.atr14
            if chand_line is None or trailing > chand_line:
                chand_line = trailing
            if chand_line is not None and close < chand_line:
                exit_shares = min(state.shares, trade_shares)
                if exit_shares > 0:
                    proceeds = close * exit_shares
                    fee = commission(proceeds)
                    state = replace(
                        state,
                        shares=state.shares - exit_shares,
                        cash=state.cash + proceeds - fee,
                        trades_count=state.trades_count + 1,
                    )
                    trade_ev = {
                        "type": "sell",
                        "price": close,
                        "shares": exit_shares,
                        "amount": proceeds,
                        "fee": fee,
                        "tag": "chandelier_exit",
                        "regime": plan.regime,
                        "date": date,
                    }
                    trades_log.append(trade_ev)
                    all_events.append(trade_ev)
                    all_events.append({
                        "type": "chandelier_exit",
                        "current": close,
                        "chand_line": chand_line,
                        "shares_exited": exit_shares,
                        "date": date,
                    })

        equity = state.cash + state.shares * close
        benchmark_equity = initial_cash + initial_shares * close
        equity_curve.append({
            "date": date,
            "price": close,
            "shares": state.shares,
            "cash": round(state.cash, 4),
            "equity": round(equity, 4),
            "benchmark": round(benchmark_equity, 4),
            "regime": plan.regime,
            "grid_enabled": plan.grid_enabled,
        })

    # —————————————— round-trip & KPI
    round_trips = _pair_round_trips(trades_log)
    final_price = float(rows[-1]["close"])
    final_equity = state.cash + state.shares * final_price
    bench_final = initial_cash + initial_shares * final_price

    def _pct(a: float, b: float) -> float:
        return ((a / b) - 1.0) * 100 if b > 0 else 0.0

    total_return_pct = _pct(final_equity, initial_equity)
    bench_return_pct = _pct(bench_final, initial_equity)
    excess_return_pct = total_return_pct - bench_return_pct

    buy_count = sum(1 for t in trades_log if t.get("type") == "buy")
    sell_count = sum(1 for t in trades_log if t.get("type") == "sell")

    win_trips = [rt for rt in round_trips if rt.net_pnl > 0]
    loss_trips = [rt for rt in round_trips if rt.net_pnl < 0]
    rtc = len(round_trips)
    win_rate = (len(win_trips) / rtc) if rtc else 0.0
    avg_win = mean(rt.net_pnl for rt in win_trips) if win_trips else 0.0
    avg_loss = mean(rt.net_pnl for rt in loss_trips) if loss_trips else 0.0
    gross_win = sum(rt.net_pnl for rt in win_trips)
    gross_loss = abs(sum(rt.net_pnl for rt in loss_trips))
    if loss_trips:
        payoff_ratio = avg_win / abs(avg_loss) if avg_loss else 0.0
    elif win_trips:
        payoff_ratio = float("inf")
    else:
        payoff_ratio = 0.0
    if gross_loss > 0:
        profit_factor = gross_win / gross_loss
    elif gross_win > 0:
        profit_factor = float("inf")
    else:
        profit_factor = 0.0

    mdd = _max_drawdown(equity_curve)
    sharpe = _sharpe(equity_curve)

    evt_summary: dict[str, int] = {}
    for ev in all_events:
        t = ev.get("type", "unknown")
        evt_summary[t] = evt_summary.get(t, 0) + 1

    return BacktestResult(
        symbol=symbol or "UNKNOWN",
        profile=profile_name,
        start_date=_row_date(rows[start_index], start_index),
        end_date=_row_date(rows[-1], len(rows) - 1),
        bars=len(rows) - start_index,
        initial_cash=initial_cash,
        initial_shares=initial_shares,
        initial_price=initial_price,
        final_cash=round(state.cash, 2),
        final_shares=state.shares,
        final_price=final_price,
        final_equity=round(final_equity, 2),
        benchmark_equity=round(bench_final, 2),
        total_return_pct=round(total_return_pct, 4),
        benchmark_return_pct=round(bench_return_pct, 4),
        excess_return_pct=round(excess_return_pct, 4),
        trade_count=len(trades_log),
        buy_count=buy_count,
        sell_count=sell_count,
        round_trip_count=rtc,
        win_count=len(win_trips),
        loss_count=len(loss_trips),
        win_rate=round(win_rate, 4),
        avg_win=round(avg_win, 4),
        avg_loss=round(avg_loss, 4),
        payoff_ratio=(round(payoff_ratio, 4) if payoff_ratio != float("inf") else float("inf")),
        profit_factor=(round(profit_factor, 4) if profit_factor != float("inf") else float("inf")),
        max_drawdown_pct=round(mdd, 4),
        sharpe_ratio=round(sharpe, 4),
        events_summary=evt_summary,
        trades=trades_log,
        round_trips=round_trips,
        equity_curve=equity_curve,
    )


# ---------------------------------------------------------------- 内部工具

def _row_date(row: dict, fallback_index: int) -> str:
    """从 K 线 row 中抽取日期字符串。

    优先级: date(已格式化) > timestamp(ms→ISO) > trade_date > day > index 兑底。
    真实 K 线只有 timestamp (ms)；合成测试 K 线有 date 字符串。
    """
    val = row.get("date")
    if val:
        return str(val)
    ts = row.get("timestamp")
    if ts:
        try:
            dt = datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc)
            return dt.strftime("%Y-%m-%d")
        except (ValueError, TypeError, OSError):
            pass
    for key in ("trade_date", "day"):
        val = row.get(key)
        if val:
            return str(val)
    return str(fallback_index)


def _pair_round_trips(trades: list[dict]) -> list[RoundTrip]:
    """FIFO 配对：每笔 sell 消耗最早的 buy。

    buy_q 元素：{date, price, shares_left, fee_per_share}
    trades 中每笔的 fee 是整笔交易费，分摊到配对段时按股数按比例分。"""
    buy_q: list[dict[str, Any]] = []
    closed: list[RoundTrip] = []

    for t in trades:
        t_type = t.get("type")
        if t_type == "buy":
            shares = int(t["shares"])
            if shares <= 0:
                continue
            price = float(t["price"])
            fee = float(t.get("fee", commission(shares * price)))
            buy_q.append({
                "date": t.get("date", ""),
                "price": price,
                "shares_left": shares,
                "fee_per_share": fee / shares,
            })
        elif t_type == "sell":
            remaining = int(t["shares"])
            if remaining <= 0:
                continue
            sell_price = float(t["price"])
            sell_fee_total = float(t.get("fee", commission(remaining * sell_price)))
            sell_fee_per_share = sell_fee_total / remaining

            while remaining > 0 and buy_q:
                head = buy_q[0]
                take = min(remaining, head["shares_left"])
                buy_price = head["price"]
                gross = (sell_price - buy_price) * take
                fees = (head["fee_per_share"] + sell_fee_per_share) * take
                net = gross - fees
                cost_basis = buy_price * take
                ret_pct = (net / cost_basis * 100) if cost_basis > 0 else 0.0
                closed.append(RoundTrip(
                    buy_date=str(head["date"]),
                    buy_price=buy_price,
                    sell_date=str(t.get("date", "")),
                    sell_price=sell_price,
                    shares=take,
                    gross_pnl=round(gross, 4),
                    fees=round(fees, 4),
                    net_pnl=round(net, 4),
                    return_pct=round(ret_pct, 4),
                ))
                head["shares_left"] -= take
                remaining -= take
                if head["shares_left"] <= 0:
                    buy_q.pop(0)
            # remaining > 0 表示卖出时没有对应买入（来自初始持仓）——不计 round-trip

    return closed


def _max_drawdown(curve: list[dict]) -> float:
    """返回 MDD 作为正百分比（15.3 表示 15.3%）。"""
    if not curve:
        return 0.0
    peak = float(curve[0]["equity"])
    max_dd = 0.0
    for p in curve:
        eq = float(p["equity"])
        if eq > peak:
            peak = eq
        if peak > 0:
            dd = (peak - eq) / peak
            if dd > max_dd:
                max_dd = dd
    return max_dd * 100


def _sharpe(curve: list[dict], periods_per_year: int = 252) -> float:
    """按日 equity 算 return，然后年化 Sharpe。rf = 0。"""
    if len(curve) < 3:
        return 0.0
    returns: list[float] = []
    prev = float(curve[0]["equity"])
    for p in curve[1:]:
        cur = float(p["equity"])
        if prev > 0:
            returns.append((cur - prev) / prev)
        prev = cur
    if len(returns) < 2:
        return 0.0
    avg = mean(returns)
    sd = stdev(returns)
    if sd == 0:
        return 0.0
    return (avg / sd) * sqrt(periods_per_year)
