"""策略全量参数（单一事实来源）。

设计纪律：
- 所有阈值粗粒度，禁止对着近期行情微调（防过拟合）。
- 任何参数改动须经 walkforward 训练段/验证段双重检验。
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace


@dataclass(frozen=True, slots=True)
class StrategyConfig:
    # -- 标的与资金 --
    symbol: str = "SH515880"
    index_symbols: tuple[str, ...] = ("SZ399001", "SZ399006")
    initial_capital: float = 100_000.0
    lot_size: int = 100
    price_precision: int = 3

    # -- 成本模型（A 股 ETF：免印花税，低佣金）--
    commission_rate: float = 1e-4      # 万 1
    commission_min: float = 0.1        # 最低佣金（免5户型；有5元下限的券商改 5.0）
    slippage_ticks: int = 1            # 市价单滑点 = 1 个最小价差

    # -- 底仓（趋势层，Faber 2007 + Moreira-Muir 2017 改良）--
    # 默认值经双机制 walk-forward 检验（2026-06-10）：压力段(2020~23)验生存，
    # AI 时代段(2024+)选参，120 日样本外确认。relative 波动模式在前 6 名中占 5，
    # base_ratio=0.6 一致占优；与 tactical+cash_floor 之和超 1 的部分由现金地板
    # guard 自然钳制（实测最高敞口 ~80%）。
    base_ratio: float = 0.60           # 底仓目标占总资产比例
    trend_window: int = 200            # 长期趋势线 MA200
    trend_confirm_days: int = 5        # 连续 N 日收盘越线才确认翻转
    base_step_tranches: int = 3        # 底仓增减分几批走（每日最多一批）
    vol_mode: str = "relative"         # relative: 与自身1年波动中位数比；absolute: 固定目标
    vol_ref_window: int = 244          # 相对模式的参照窗口（约1年）
    vol_target_annual: float = 0.30    # absolute 模式的波动率目标（年化）
    vol_window: int = 20               # 已实现波动率窗口
    trading_days_per_year: int = 244   # A 股年交易日

    # -- 机动仓（反转层）--
    tactical_ratio: float = 0.30       # 机动仓目标占比
    tactical_tranches: int = 3         # 机动仓拆几档（单轮只动一档）
    atr_window: int = 14
    grid_k: float = 1.3                # 卖出价 = 收盘 + k*ATR；接回价 = 卖出价 - k*ATR
    abandon_atr_mult: float = 1.0      # 卖出后价格反向上行超此倍数 ATR → 放弃接回，解锁新轮次
    freeze_atr_mult: float = 1.0       # 接回后再跌超此倍数 ATR → 冻结全部买入
    unfreeze_ma_window: int = 20       # 收盘站回 MA20 → 解冻

    # -- 指数过滤（量化定义，替代盘感）--
    index_ma_window: int = 20          # 同步走弱：两指数收盘均 < MA20
    index_weak_ret_days: int = 5       # 且 5 日收益均为负
    index_stab_ma_window: int = 5      # 企稳：收盘站回 MA5 且未创 5 日新低

    # -- 现金地板 --
    cash_floor_ratio: float = 0.20
    emergency_drop_pct: float = 0.10   # 20 日回撤≥10% 解锁应急通道
    emergency_lookback: int = 20
    emergency_use_ratio: float = 0.5   # 应急时地板可动用一半

    # -- 数据 --
    kline_count: int = 1800            # 515880 自 2019 年上市全历史


DEFAULT_CONFIG = StrategyConfig()

_FIELD_NAMES = {f.name for f in fields(StrategyConfig)}


def with_overrides(cfg: StrategyConfig = DEFAULT_CONFIG, **overrides) -> StrategyConfig:
    """带字段校验的参数覆盖，避免静默拼错参数名。"""
    unknown = set(overrides) - _FIELD_NAMES
    if unknown:
        raise ValueError(f"未知参数: {sorted(unknown)}")
    return replace(cfg, **overrides)
