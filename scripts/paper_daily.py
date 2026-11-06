"""Phase 4 · paper_daily：每日决策报告脚本（MVP）。

位置：这个脚本是「engine 层决定好」的纯读口。不改账本、不模拟成交，
只回答一句话：「今天该干什么？」

工作流：
    1. 拉行情 K 线 → generate_plan_with_hybrid
    2. 拿到 plan + CapitalAllocation + 位置分档 + 应急状态
    3. 以文本、Markdown 或 JSON 形式输出

所有参数可从命令行传入，不写死：

    uv run python scripts/paper_daily.py --symbol SH515880 --profile trend_hybrid --total-equity 200000

输出格式（--format md/text/json）默认 text。
--format json 适合给 Server 酱这种订阅方消费。

**这个脚本不动 paper_logs/——真正的账本模拟（atr_grid.paper simulate_day）
留到下一轮接线。**
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from atr_grid import hybrid as hybrid_mod
from atr_grid.config import DEFAULT_CONFIG, GridConfig, for_profile
from atr_grid.data import load_market_context
from atr_grid.engine import apply_hybrid_overlay, build_plan_with_frame


def _resolve_cfg(profile: str, overrides: dict[str, Any]) -> GridConfig:
    """获取 profile 配置，用 CLI 数字覆盖软硬写字段。"""
    cfg = DEFAULT_CONFIG if profile == "stable" else for_profile(profile)
    if overrides:
        # for_profile 返回新 dataclass，打搅可变字段。
        from dataclasses import replace
        cfg = replace(cfg, **overrides)
    return cfg


def _collect_overrides(args: argparse.Namespace) -> dict[str, Any]:
    """从 CLI 拿以标准字段名提供的覆盖值。全部可选。"""
    mapping = {
                "base_position_ratio": args.base_position_ratio,
                "cash_floor_ratio": args.cash_floor_ratio,
                "position_window": args.position_window,
                "emergency_refill_drop_pct": args.emergency_refill_drop_pct,
                "emergency_refill_lookback": args.emergency_refill_lookback,
            }
    return {k: v for k, v in mapping.items() if v is not None}


def run_daily(
    *,
    symbol: str,
    profile: str,
    total_equity: float,
    shares: int,
    kline_count: int,
    overrides: dict[str, Any],
) -> dict[str, Any]:
    """执行一日决策，返回结构化结果 dict（纯函数所子）。不调 I/O。"""
    cfg = _resolve_cfg(profile, overrides)
    context = load_market_context(
        symbol, shares=shares, kline_count=kline_count, cfg=cfg
    )
    plan, frame = build_plan_with_frame(context, cfg=cfg)
    plan, allocation = apply_hybrid_overlay(
        plan, frame, total_equity=total_equity, cfg=cfg
    )

    # 应急补仓判定（只在启用 hybrid 时计算，以免无用）
    emergency = (
        hybrid_mod.should_emergency_refill(frame, cfg)
        if cfg.trend_hybrid_enabled
        else False
    )

    percentile = hybrid_mod.position_percentile(
        frame, window=cfg.position_window
    ) if cfg.trend_hybrid_enabled else None

    # Phase 5.2：对 primary_buy 做一次 cash_floor 预检，告诉用户"今天如果有买信号，地板会不会拦"。
    # 这不改 engine 的 plan，只往 payload 里多写一个预警字段。
    cash_floor_check: dict[str, Any] | None = None
    if (
        cfg.trend_hybrid_enabled
        and plan.primary_buy is not None
        and total_equity > 0
    ):
        tranche = cfg.reference_tranche_shares
        # 保守估算：按 0.3% 滑点 + 佣金粗略（正式 commission 会更低，偏保守更安全）
        intended = tranche * plan.primary_buy * 1.003
        cash_before = max(0.0, total_equity - shares * plan.current_price)
        decision = hybrid_mod.cash_floor_guard(
            cash_before=cash_before,
            intended_amount=intended,
            total_equity=total_equity,
            cfg=cfg,
            emergency_unlocked=emergency,
        )
        approved_full = decision.approved_amount + 1e-6 >= intended
        cash_floor_check = {
            "would_block": not approved_full,
            "intended_amount": round(intended, 2),
            "approved_amount": round(decision.approved_amount, 2),
            "cash_before_estimate": round(cash_before, 2),
            "reason": decision.reason,
        }

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "symbol": plan.symbol,
        "profile": profile,
        "hybrid_enabled": cfg.trend_hybrid_enabled,
        "last_trade_date": plan.last_trade_date,
        "current_price": plan.current_price,
        "regime": plan.regime,
        "mode": plan.mode,
        "strategy_name": plan.strategy_name,
        "headline": plan.headline_action,
        "action_steps": list(plan.action_steps),
        "primary_buy": plan.primary_buy,
        "primary_sell": plan.primary_sell,
        "buy_levels": list(plan.buy_levels),
        "sell_levels": list(plan.sell_levels),
        "lower_invalidation": plan.lower_invalidation,
        "upper_breakout": plan.upper_breakout,
        "warnings": list(plan.warnings),
        "position_percentile": percentile,
        "emergency_refill": emergency,
        "allocation": asdict(allocation) if allocation is not None else None,
        "cash_floor_check": cash_floor_check,
    }


def format_text(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"== {payload['symbol']} 今日决策 ==")
    lines.append(f"生成时间：{payload['generated_at']}  |  数据日：{payload['last_trade_date']}")
    lines.append(f"profile：{payload['profile']}  hybrid：{'开' if payload['hybrid_enabled'] else '关'}")
    lines.append(f"当前价：¥{payload['current_price']:.3f}  regime：{payload['regime']}  mode：{payload['mode']}")
    if payload["hybrid_enabled"]:
        pct = payload["position_percentile"]
        lines.append(
            f"位置刻度：{pct:.1f}/100" if pct is not None else "位置刻度：数据不足"
        )
        alloc = payload["allocation"]
        if alloc:
            band = alloc["band"]
            lines.append(
                f"档位：{band['name']} ([{band['low']:.0f}, {band['high']:.0f})) "
                f"swing乘数：{band['swing_ratio']:.2f}"
                + ("  【高位只卖】" if alloc["only_sell"] else "")
            )
            lines.append(
                f"资金分配：总资产¥{alloc['total_equity']:.0f} = 底仓¥{alloc['base_budget']:.0f} "
                f"+ 网格层¥{alloc['swing_budget']:.0f} + 现金地板¥{alloc['cash_floor']:.0f}"
            )
        lines.append(f"应急补仓通道：{'已解锁' if payload['emergency_refill'] else '未触发'}")
        cfc = payload.get("cash_floor_check")
        if cfc is not None:
            if cfc["would_block"]:
                gap = cfc["intended_amount"] - cfc["approved_amount"]
                lines.append(
                    f"地板预警：⚠ 主买档会被拦（打算花￥{cfc['intended_amount']:.0f}，只放过￥{cfc['approved_amount']:.0f}，差￥{gap:.0f}）"
                )
            else:
                lines.append(
                    f"地板预警：✓ 主买档可过（打算花￥{cfc['intended_amount']:.0f}，现金估￥{cfc['cash_before_estimate']:.0f}）"
                )
    lines.append("")
    lines.append(f"策略：{payload['strategy_name']}")
    lines.append(f"一句话：{payload['headline']}")
    if payload["primary_sell"] is not None:
        lines.append(f"主卖点：¥{payload['primary_sell']:.3f}")
    if payload["primary_buy"] is not None:
        lines.append(f"主买点：¥{payload['primary_buy']:.3f}")
    if payload["lower_invalidation"] is not None:
        lines.append(f"失效下沿：¥{payload['lower_invalidation']:.3f}")
    if payload["action_steps"]:
        lines.append("")
        lines.append("动作步骤：")
        for i, step in enumerate(payload["action_steps"], 1):
            lines.append(f"  {i}. {step}")
    if payload["warnings"]:
        lines.append("")
        lines.append(f"warnings：{', '.join(payload['warnings'])}")
    return "\n".join(lines)


def format_markdown(payload: dict[str, Any]) -> str:
    """给 Server 酱/微信用的 Markdown。"""
    alloc = payload["allocation"]
    pct = payload["position_percentile"]
    lines = [
        f"# {payload['symbol']} 今日决策",
        f"- 数据日：`{payload['last_trade_date']}`  生成：`{payload['generated_at']}`",
        f"- profile：`{payload['profile']}`  hybrid：`{'on' if payload['hybrid_enabled'] else 'off'}`",
        f"- 当前价：**¥{payload['current_price']:.3f}**  regime：`{payload['regime']}`  mode：`{payload['mode']}`",
    ]
    if payload["hybrid_enabled"] and alloc:
        band = alloc["band"]
        lines.append(
            f"- 位置：**{(pct if pct is not None else float('nan')):.1f}/100**"
            f"  档位：`{band['name']}`"
            + ("  【高位只卖】" if alloc["only_sell"] else "")
        )
        lines.append(
            f"- 资金：总¥{alloc['total_equity']:.0f} → 底仓¥{alloc['base_budget']:.0f}"
            f" + 网格¥{alloc['swing_budget']:.0f} + 地板¥{alloc['cash_floor']:.0f}"
        )
        lines.append(f"- 应急：`{'解锁' if payload['emergency_refill'] else '未触发'}`")
        cfc = payload.get("cash_floor_check")
        if cfc is not None:
            if cfc["would_block"]:
                gap = cfc["intended_amount"] - cfc["approved_amount"]
                lines.append(f"- 地板预警：⚠ 拦截（缺￥{gap:.0f}）")
            else:
                lines.append("- 地板预警：✓ 可买")
    lines.append("")
    lines.append(f"**{payload['strategy_name']}**\n")
    lines.append(f"> {payload['headline']}")
    if payload["action_steps"]:
        lines.append("")
        for step in payload["action_steps"]:
            lines.append(f"- {step}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="ATR grid 每日决策报告（只读）")
    p.add_argument("--symbol", required=True, help="标的代码，例如 SH515880")
    p.add_argument("--profile", default="trend_hybrid",
                   help="profile 名：stable/dev/aggressive/balanced/yield/trend_hybrid")
    p.add_argument("--total-equity", type=float, required=True,
                   help="账户总资产（元），hybrid 分层的基德")
    p.add_argument("--shares", type=int, default=2000, help="当前持仓股数（用于 trim 计算）")
    p.add_argument("--kline-count", type=int, default=240, help="拉多少根 K 线")
    p.add_argument("--format", choices=["text", "md", "json"], default="text")
    # 常用热改字段（全可选，对照 hybrid.md 文档）
    p.add_argument("--base-position-ratio", type=float, dest="base_position_ratio",
                   default=None, help="底仓比例覆写（0-1）")
    p.add_argument("--cash-floor-ratio", type=float, dest="cash_floor_ratio",
                   default=None, help="现金地板比例覆写（0-1）")
    p.add_argument("--position-window", type=int, dest="position_window",
                   default=None, help="位置刻度窗口覆写（默认 60）")
    p.add_argument("--emergency-refill-drop-pct", type=float,
                   dest="emergency_refill_drop_pct", default=None,
                   help="应急补仓跌幅阈值（0-1，默认 0.10）")
    p.add_argument("--emergency-refill-lookback", type=int,
                   dest="emergency_refill_lookback", default=None,
                   help="应急判定窗口（默认 20）")

    args = p.parse_args(argv)
    overrides = _collect_overrides(args)

    payload = run_daily(
        symbol=args.symbol,
        profile=args.profile,
        total_equity=args.total_equity,
        shares=args.shares,
        kline_count=args.kline_count,
        overrides=overrides,
    )

    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    elif args.format == "md":
        print(format_markdown(payload))
    else:
        print(format_text(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
