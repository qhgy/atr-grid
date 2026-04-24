"""ATR 网格虚拟盘（Paper Trading）

用 atr_grid 每日生成的 plan 驱动一个虚拟持仓，跨越真实成交之前积累纪律证据。

**核心训练目标**：治"持有时舍不得卖、最后坐过山车"的散户毛病。
默认按 2000 股底仓演练 → 分 10 份 → 每到目标档位加减 200 股。

数据文件（在 aaa/paper_logs/ 下，自动创建）：
- {SYMBOL}_state.json   — 当前虚拟持仓快照
- {SYMBOL}.jsonl        — 每日流水（含 plan 概要 + 成交事件 + 组合净值）

用法（在 aaa 目录下）：
    python -m atr_grid.paper init SH515880 --shares 2000
    python -m atr_grid.paper run SH515880
    python -m atr_grid.paper status SH515880

成交规则（兼容三种 regime）：
- range（震荡）：双向网格，按 plan.sell_levels / buy_levels 跨档触发
- trend_up（多头趋势）：按 plan.reference_sell_ladder 跨档卖（涨一档卖一份），
  跌回 reference_rebuy_ladder 也按档接回——这就是"机动仓锁利润"
- trend_down（空头趋势）：完全不动作（不接飞刀，也不恐慌减仓）
- 失效下沿（lower_invalidation）触发 → 停止买入接回，但允许卖出
- 同一日只触发一次卖 + 一次买（先卖后买，符合 A 股 T+1）
- 手续费 max(成交额 × 0.01%, 5 元) —— 小资金网格的真实成本

**plan 调用时会传入虚拟盘真实持仓**，让 atr_grid 的 trim_shares 计算贴合实际，
而不是默认按 200 股算出"最多卖 0 股"的退化结果。
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, asdict, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# atr_grid 项目根 = paper.py 的父目录的父目录
AAA_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = AAA_ROOT / "output" / "paper_logs"

COMMISSION_RATE = 0.0001   # 0.01% 佣金
COMMISSION_MIN = 5.0       # 5 元起征
DEFAULT_INITIAL_SHARES = 2000
DEFAULT_TRADE_SHARES = 200


@dataclass(slots=True)
class Portfolio:
    """虚拟组合状态。"""

    symbol: str
    shares: int
    cash: float
    last_price: float | None
    initial_shares: int
    initial_cash: float
    initial_price: float
    created_at: str
    trades_count: int = 0
    last_trade_date: str | None = None  # 幂等：记录上一次 run 的 plan trade_date
    stop_price: float | None = None     # 成本止损线，跌破即冻结买入
    frozen: bool = False                # 是否已触发止损（停止接回，仍允许卖出）
    frozen_at: str | None = None        # 触发日期（plan.last_trade_date）
    frozen_price: float | None = None   # 触发时的成交价

    def equity(self, price: float) -> float:
        return self.shares * price + self.cash

    def benchmark_equity(self, price: float) -> float:
        """纯持仓基准（什么都不做的对照组）。"""
        return self.initial_shares * price + self.initial_cash


def _state_path(symbol: str) -> Path:
    return LOG_DIR / f"{symbol}_state.json"


def _journal_path(symbol: str) -> Path:
    return LOG_DIR / f"{symbol}.jsonl"


def clear_journal(symbol: str) -> None:
    path = _journal_path(symbol)
    if path.exists():
        path.unlink()


def load_portfolio(symbol: str) -> Portfolio | None:
    path = _state_path(symbol)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    # 向后兼容：老 state 文件没有止损字段，给默认值
    data.setdefault("stop_price", None)
    data.setdefault("frozen", False)
    data.setdefault("frozen_at", None)
    data.setdefault("frozen_price", None)
    return Portfolio(**data)


def save_portfolio(p: Portfolio) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _state_path(p.symbol).write_text(
        json.dumps(asdict(p), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def append_journal(symbol: str, record: dict[str, Any]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with _journal_path(symbol).open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def read_journal(symbol: str) -> list[dict[str, Any]]:
    path = _journal_path(symbol)
    if not path.exists():
        return []
    out = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


def commission(amount: float) -> float:
    """A 股 ETF 佣金：成交额 × 0.01%，最低 5 元。"""
    return max(amount * COMMISSION_RATE, COMMISSION_MIN)


def _trade_shares() -> int:
    """Fixed simulated trade size: +/- 200 shares around the 2000-share base."""
    return DEFAULT_TRADE_SHARES


def _resolve_levels(plan: Any) -> tuple[list[float], list[float]]:
    """统一卖/买价位来源。

    - range 模式：plan.sell_levels / plan.buy_levels 已填好
    - trend_up 模式：plan.sell_levels 为空，价位在 plan.reference_sell_ladder
    - 两个都拿，谁有用谁
    """
    sell_levels = list(plan.sell_levels or []) or list(plan.reference_sell_ladder or [])
    buy_levels = list(plan.buy_levels or []) or list(plan.reference_rebuy_ladder or [])
    return sell_levels, buy_levels


# ===== Pure-function core (Phase 1.2) =======================================
#
# `simulate_day` 是整个成交决策的纯函数化内核：
#   输入 (PaperState, plan)  →  输出 (new_state, events)
#
# 不触网络、不读写文件、不读时钟。相同输入必然给相同输出。
# 供 `_simulate_fills` (paper run) 和即将到来的 backtest 引擎 (Phase 1.3) 共用。
#
# 行为完全等价于抽取前的 `_simulate_fills`，包括：
#   - regime=trend_down / disabled / baseline 的早退
#   - 成本止损 (stop_price) 跌破冻结
#   - 失效下沿 (lower_invalidation) 停买不停卖
#   - 向上跨档卖一档 / 向下跨档买一档
#   - 现金不足时买单中止
#   - hold 语义（没跨档 或 只在失效区间）


@dataclass(frozen=True, slots=True)
class PaperState:
    """虚拟盘的可变量状态（用于纯函数 simulate_day）。

    注意：这是不可变 dataclass——simulate_day 通过 `dataclasses.replace` 生成新对象，
    不修改原 state。`Portfolio` 仍是可变容器（负责持久化和 CLI 展示），
    两者互相转换由 _simulate_fills 完成。
    """

    shares: int
    cash: float
    last_price: float | None = None
    trades_count: int = 0
    stop_price: float | None = None
    frozen: bool = False
    frozen_at: str | None = None
    frozen_price: float | None = None


def simulate_day(
    state: PaperState,
    plan: Any,
    *,
    trade_shares: int = DEFAULT_TRADE_SHARES,
) -> tuple[PaperState, list[dict[str, Any]]]:
    """根据上次 vs 本次 current_price 的跨越，决定当日虚拟成交。

    这是 paper 模块和 backtest 引擎的共享内核。纯函数：不触 IO、不读时钟。

    Args:
        state:        当前虚拟盘状态（不会被修改）。
        plan:         engine.generate_plan 生成的 GridPlan（或 duck-typed 等价物）。
                      仅读取：current_price / regime / reason / lower_invalidation /
                      sell_levels / buy_levels / reference_sell_ladder /
                      reference_rebuy_ladder / last_trade_date。
        trade_shares: 每档成交股数，默认 200。

    Returns:
        (new_state, events)
    """
    events: list[dict[str, Any]] = []
    current = float(plan.current_price)
    regime = getattr(plan, "regime", "unknown")

    # 1. trend_down：完全持有不动
    if regime == "trend_down":
        events.append({
            "type": "trend_down_hold",
            "regime": regime,
            "reason": plan.reason,
            "note": "下跌趋势期间持有不动，等市场结构修复",
        })
        return state, events

    # 2. 数据异常：不动
    if regime == "disabled":
        events.append({"type": "disabled", "reason": plan.reason})
        return state, events

    sell_levels, buy_levels = _resolve_levels(plan)

    if state.last_price is None:
        events.append({"type": "baseline", "price": current, "note": "首日基线，不触发成交"})
        return state, events

    prev = state.last_price
    shares = state.shares
    cash = state.cash
    trades_count = state.trades_count
    frozen = state.frozen
    frozen_at = state.frozen_at
    frozen_price = state.frozen_price

    # 2.5 成本止损：跌破即冻结买入（仍允许触发卖出）
    if state.stop_price is not None and current < state.stop_price and not frozen:
        frozen = True
        frozen_at = plan.last_trade_date
        frozen_price = current
        events.append({
            "type": "stop_loss_trigger",
            "current": current,
            "stop_price": state.stop_price,
            "note": f"跌破成本止损 ¥{state.stop_price:.3f}，冻结接回；resume 命令可解冻",
        })

    # 3. 失效下沿警示（不阻止本日卖出，但停止接回）
    invalidated = (plan.lower_invalidation is not None
                   and current < plan.lower_invalidation)
    if invalidated:
        events.append({
            "type": "invalidation",
            "current": current,
            "invalidation": plan.lower_invalidation,
            "note": "跌破失效下沿，停止买入接回（仍允许触发卖出）",
        })

    # 4. 向上跨越某档卖点 → 卖 1 tranche
    for lvl in sorted([x for x in sell_levels if x is not None]):
        if prev < lvl <= current and shares >= trade_shares:
            amount = trade_shares * lvl
            fee = commission(amount)
            shares -= trade_shares
            cash += amount - fee
            trades_count += 1
            events.append({
                "type": "sell",
                "price": lvl,
                "shares": trade_shares,
                "amount": round(amount, 2),
                "fee": round(fee, 2),
                "net": round(amount - fee, 2),
                "regime": regime,
            })
            break  # 一天只卖一档

    # 5. 向下跨越某档买点 → 买 1 tranche（仅在 invalidation 未触发 且 未止损冻结时）
    if not invalidated and not frozen:
        for lvl in sorted([x for x in buy_levels if x is not None], reverse=True):
            if prev > lvl >= current:
                amount = trade_shares * lvl
                fee = commission(amount)
                if cash < amount + fee:
                    break
                shares += trade_shares
                cash -= amount + fee
                trades_count += 1
                events.append({
                    "type": "buy",
                    "price": lvl,
                    "shares": trade_shares,
                    "amount": round(amount, 2),
                    "fee": round(fee, 2),
                    "net": round(-(amount + fee), 2),
                    "regime": regime,
                })
                break

    # 6. 没成交也没特殊事件 → hold
    has_action = any(e["type"] in ("sell", "buy") for e in events)
    if not has_action and not events:
        events.append({"type": "hold", "price": current, "note": "未跨越任何价位"})
    elif not has_action and all(e["type"] == "invalidation" for e in events):
        events.append({"type": "hold", "price": current, "note": "失效区间内，未跨越卖点"})

    new_state = replace(
        state,
        shares=shares,
        cash=cash,
        trades_count=trades_count,
        frozen=frozen,
        frozen_at=frozen_at,
        frozen_price=frozen_price,
    )
    return new_state, events


def _simulate_fills(p: Portfolio, plan: Any) -> list[dict[str, Any]]:
    """根据上次 vs 本次 current_price 的跨越判断是否触发成交。

    成交规则按 regime 分流：
    - trend_down → 完全不动（不接飞刀，不恐慌减仓）
    - disabled  → 完全不动（数据异常）
    - range / trend_up → 都允许双向，价位来自 _resolve_levels
    - invalidation 触发 → 仅停买，不停卖

    内部委托给纯函数 `simulate_day`（Phase 1.2 抽取），再把新状态回写到 Portfolio。
    """
    state = PaperState(
        shares=p.shares,
        cash=p.cash,
        last_price=p.last_price,
        trades_count=p.trades_count,
        stop_price=p.stop_price,
        frozen=p.frozen,
        frozen_at=p.frozen_at,
        frozen_price=p.frozen_price,
    )
    new_state, events = simulate_day(state, plan, trade_shares=_trade_shares())
    # 回写可变字段（last_price/stop_price 由外部 cmd_run 负责）
    p.shares = new_state.shares
    p.cash = new_state.cash
    p.trades_count = new_state.trades_count
    p.frozen = new_state.frozen
    p.frozen_at = new_state.frozen_at
    p.frozen_price = new_state.frozen_price
    return events


# ----- CLI subcommands ------------------------------------------------------


def _import_plan(symbol: str, shares: int = 200):
    """延迟 import + 把虚拟盘的真实 shares 传给 atr_grid，
    让 trim_shares = shares × 10% 计算正确（默认 200 股 → trim=0 是退化值）。
    """
    from atr_grid.engine import generate_plan
    return generate_plan(symbol, shares=shares)


def cmd_init(args: argparse.Namespace) -> int:
    sym = args.symbol
    existing = load_portfolio(sym)
    if existing and not args.force:
        print(f"[init] {sym} 已存在虚拟持仓（shares={existing.shares} cash={existing.cash:.2f}）")
        print("       加 --force 可重置")
        return 1
    if args.force:
        clear_journal(sym)

    plan = _import_plan(sym, shares=args.shares)
    price = float(plan.current_price)
    initial_cash = args.cash if args.cash is not None else 0.0

    p = Portfolio(
        symbol=sym,
        shares=args.shares,
        cash=initial_cash,
        last_price=None,
        initial_shares=args.shares,
        initial_cash=initial_cash,
        initial_price=price,
        created_at=datetime.now().isoformat(timespec="seconds"),
        stop_price=args.stop_price,
    )
    save_portfolio(p)
    print(f"[init] {sym} 虚拟盘已建立")
    print(f"       起始持仓：{args.shares} 股 @ ¥{price:.3f}（依据当日收盘）")
    print(f"       起始现金：¥{initial_cash:.2f}")
    if args.stop_price is not None:
        dist_pct = (price - args.stop_price) / price * 100
        print(f"       成本止损：¥{args.stop_price:.3f}（距当前 -{dist_pct:.2f}%）")
    print(f"       数据文件：{_state_path(sym)}")
    print(f"       下一步：每个交易日收盘后跑 python -m atr_grid.paper run {sym}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    sym = args.symbol
    p = load_portfolio(sym)
    if p is None:
        print(f"[run] {sym} 未初始化，请先 python -m atr_grid.paper init {sym} --shares {DEFAULT_INITIAL_SHARES}",
              file=sys.stderr)
        return 1

    plan = _import_plan(sym, shares=p.shares)
    trade_date = plan.last_trade_date

    if p.last_trade_date == trade_date and not args.force:
        print(f"[run] {sym} 的交易日 {trade_date} 已记录，--force 可覆盖")
        return 0

    events = _simulate_fills(p, plan)
    current = float(plan.current_price)
    p.last_price = current
    p.last_trade_date = trade_date
    save_portfolio(p)

    equity = p.equity(current)
    benchmark = p.benchmark_equity(current)
    diff = equity - benchmark
    diff_pct = diff / benchmark * 100 if benchmark else 0.0

    sell_levels_used, buy_levels_used = _resolve_levels(plan)

    record = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "trade_date": trade_date,
        "symbol": sym,
        "current_price": current,
        "regime": plan.regime,
        "mode": plan.mode,
        "grid_enabled": plan.grid_enabled,
        "reason": plan.reason,
        "main_buy": plan.primary_buy,
        "main_sell": plan.primary_sell,
        "sell_levels_used": sell_levels_used,
        "buy_levels_used": buy_levels_used,
        "lower_invalidation": plan.lower_invalidation,
        "upper_breakout": plan.upper_breakout,
        "events": events,
        "portfolio": {
            "shares": p.shares,
            "cash": round(p.cash, 2),
            "equity": round(equity, 2),
            "trades_count": p.trades_count,
        },
        "benchmark_equity": round(benchmark, 2),
        "diff_vs_benchmark": round(diff, 2),
        "diff_pct": round(diff_pct, 4),
    }
    append_journal(sym, record)

    # 终端摘要
    print(f"[run] {sym} trade_date={trade_date}  当前 ¥{current:.3f}  "
          f"regime={plan.regime}  mode={plan.mode}")
    if sell_levels_used:
        print(f"       卖出阶梯：{' / '.join(f'¥{x:.3f}' for x in sell_levels_used)}")
    if buy_levels_used:
        print(f"       接回阶梯：{' / '.join(f'¥{x:.3f}' for x in buy_levels_used)}")
    for e in events:
        if e["type"] in ("sell", "buy"):
            tag = "SELL" if e["type"] == "sell" else "BUY"
            print(f"       ★ {tag} {e['shares']} 股 @ ¥{e['price']:.3f}  "
                  f"手续费 ¥{e['fee']:.2f}  ({e.get('regime', '?')})")
        elif e["type"] == "invalidation":
            print(f"       ⚠ 跌破失效下沿 ¥{e['invalidation']:.3f}（当前 ¥{e['current']:.3f}），仅停接回")
        elif e["type"] == "stop_loss_trigger":
            print(f"       ⛔ 跌破成本止损 ¥{e['stop_price']:.3f}（当前 ¥{e['current']:.3f}）→ 已冻结接回")
            print(f"          趋势确立后跑：python -m atr_grid.paper resume {sym}")
        elif e["type"] == "trend_down_hold":
            print(f"       · 下跌趋势期持有不动：{e['reason']}")
        elif e["type"] == "disabled":
            print(f"       · 数据异常未操作：{e['reason']}")
        elif e["type"] == "baseline":
            print(f"       · 首日基线 ¥{e['price']:.3f}，未触发成交")
        elif e["type"] == "hold":
            print(f"       · {e['note']}")
    print(f"       组合：shares={p.shares}  cash=¥{p.cash:.2f}  equity=¥{equity:.2f}")
    print(f"       对比纯持仓：¥{diff:+.2f} ({diff_pct:+.3f}%)  累计交易 {p.trades_count} 次")
    if p.stop_price is not None:
        if p.frozen:
            print(f"       止损状态：⛔ 冻结中（{p.frozen_at} @ ¥{p.frozen_price:.3f}）")
        else:
            dist_pct = (current - p.stop_price) / current * 100
            print(f"       止损距离：¥{p.stop_price:.3f} ↓{dist_pct:.2f}%")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    sym = args.symbol
    p = load_portfolio(sym)
    if p is None:
        print(f"[status] {sym} 未初始化")
        return 1

    records = read_journal(sym)
    if not records:
        print(f"[status] {sym} 尚无运行记录")
        return 0

    last = records[-1]

    print(f"\n===== {sym} 虚拟盘报告 =====")
    print(f"  起始：{p.created_at[:10]}  初始持仓 {p.initial_shares} 股 @ ¥{p.initial_price:.3f}")
    print(f"  最新：{last['trade_date']}  当前价 ¥{last['current_price']:.3f}")
    print(f"  当前持仓：{p.shares} 股 + 现金 ¥{p.cash:.2f}")
    print(f"  当前净值：¥{last['portfolio']['equity']:.2f}")
    print(f"  纯持仓基准：¥{last['benchmark_equity']:.2f}")
    print(f"  网格策略超额：¥{last['diff_vs_benchmark']:+.2f}  ({last['diff_pct']:+.3f}%)")
    print(f"  累计交易：{p.trades_count} 次")
    if p.stop_price is not None:
        cur = float(last["current_price"])
        if p.frozen:
            print(f"  止损状态：⛔ 冻结中（{p.frozen_at} @ ¥{p.frozen_price:.3f}，止损线 ¥{p.stop_price:.3f}）")
        else:
            dist_pct = (cur - p.stop_price) / cur * 100
            print(f"  止损线：¥{p.stop_price:.3f}（距当前 ↓{dist_pct:.2f}%）")

    # 信号统计
    buys = sum(1 for r in records for e in r.get("events", []) if e["type"] == "buy")
    sells = sum(1 for r in records for e in r.get("events", []) if e["type"] == "sell")
    trend_down_days = sum(1 for r in records if r.get("regime") == "trend_down")
    invalidation_days = sum(1 for r in records for e in r.get("events", [])
                            if e["type"] == "invalidation")
    total_fees = sum(e["fee"] for r in records for e in r.get("events", [])
                     if e["type"] in ("buy", "sell"))

    # regime 分布
    regime_counter = {}
    for r in records:
        regime_counter[r.get("regime", "unknown")] = regime_counter.get(r.get("regime", "unknown"), 0) + 1

    print(f"\n  累计运行天数：{len(records)}")
    print(f"  买入触发：{buys} 次 | 卖出触发：{sells} 次")
    print(f"  Regime 分布：{dict(sorted(regime_counter.items(), key=lambda x: -x[1]))}")
    print(f"  下跌趋势日：{trend_down_days} | 失效区间日：{invalidation_days}")
    print(f"  累计手续费：¥{total_fees:.2f}")

    # 最近 5 次成交
    recent_trades = []
    for r in reversed(records):
        for e in r.get("events", []):
            if e["type"] in ("buy", "sell"):
                recent_trades.append((r["trade_date"], e))
                if len(recent_trades) >= 5:
                    break
        if len(recent_trades) >= 5:
            break

    if recent_trades:
        print("\n  最近 5 笔成交：")
        for date, e in recent_trades:
            sign = "卖" if e["type"] == "sell" else "买"
            print(f"    {date}  {sign} {e['shares']} 股 @ ¥{e['price']:.3f}  "
                  f"费 ¥{e['fee']:.2f}  ({e.get('regime', '?')})")
    print()
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    sym = args.symbol
    p = load_portfolio(sym)
    if p is None:
        print(f"[resume] {sym} 未初始化", file=sys.stderr)
        return 1
    if not p.frozen:
        print(f"[resume] {sym} 当前未冻结，无需操作")
        if args.stop_price is not None:
            p.stop_price = args.stop_price
            save_portfolio(p)
            print(f"       止损线已更新为 ¥{args.stop_price:.3f}")
        return 0
    print(f"[resume] {sym} 解冻：原冻结于 {p.frozen_at} @ ¥{p.frozen_price:.3f}（止损线 ¥{p.stop_price}）")
    p.frozen = False
    p.frozen_at = None
    p.frozen_price = None
    if args.stop_price is not None:
        p.stop_price = args.stop_price
        print(f"       止损线已更新为 ¥{args.stop_price:.3f}")
    elif args.clear_stop:
        p.stop_price = None
        print(f"       止损线已清空（不再监控）")
    save_portfolio(p)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="atr_grid.paper",
        description="ATR 网格虚拟盘：每天跑一次，累积成交记录和纯持仓基准的差",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="初始化虚拟持仓")
    p_init.add_argument("symbol", help="标的代码，如 SH510300")
    p_init.add_argument("--shares", type=int, default=DEFAULT_INITIAL_SHARES, help=f"起始股数（默认 {DEFAULT_INITIAL_SHARES}）")
    p_init.add_argument("--cash", type=float, default=0.0, help="起始现金（默认 0）")
    p_init.add_argument("--stop-price", type=float, default=None,
                        help="成本止损价，跌破即冻结接回（仍允许卖出）；如 --stop-price 1.0")
    p_init.add_argument("--force", action="store_true", help="覆盖已有状态")
    p_init.set_defaults(func=cmd_init)

    p_run = sub.add_parser("run", help="执行当日 plan + 虚拟成交 + 写日志")
    p_run.add_argument("symbol", help="标的代码")
    p_run.add_argument("--force", action="store_true", help="同一 trade_date 强制重跑")
    p_run.set_defaults(func=cmd_run)

    p_status = sub.add_parser("status", help="查看虚拟盘累计表现")
    p_status.add_argument("symbol", help="标的代码")
    p_status.set_defaults(func=cmd_status)

    p_resume = sub.add_parser("resume", help="解除止损冻结（趋势确立后重启接回）")
    p_resume.add_argument("symbol", help="标的代码")
    p_resume.add_argument("--stop-price", type=float, default=None,
                          help="同时更新止损价（如 --stop-price 0.95）")
    p_resume.add_argument("--clear-stop", action="store_true",
                          help="清空止损线（不再监控）")
    p_resume.set_defaults(func=cmd_resume)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
