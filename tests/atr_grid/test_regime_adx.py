"""Phase 2.1: regime 加 ADX 确认的专项测试。

验证新逻辑：
- ADX 足强 + MA 趋势结构 → trend_up/down
- ADX 不足但 MA 趋势结构 → 降级为 range（假趋势过滤）
- ADX 缺失 → 向后兼容：旧 MA 版判断
- 横盘无明显趋势 → range
"""

from __future__ import annotations

import pandas as pd

from atr_grid.config import GridConfig
from atr_grid.indicators import IndicatorSnapshot
from atr_grid.regime import classify_regime


def _make_frame(ma20_values: list[float]) -> pd.DataFrame:
    """最小 frame，classify_regime 只用到 ma20.tail(lookback)。"""
    return pd.DataFrame({"ma20": ma20_values})


def test_trend_up_confirmed_when_adx_strong():
    cfg = GridConfig()  # lookback=5, slope=0.25, adx_threshold=25
    frame = _make_frame([1.0, 1.1, 1.2, 1.3, 1.4, 1.5])
    snapshot = IndicatorSnapshot(
        close=1.6, atr14=0.1,
        bb_upper=1.7, bb_middle=1.5, bb_lower=1.3,
        ma20=1.5, ma60=1.3,
        adx14=30.0, bbw=0.27, bbw_percentile=0.5,
    )
    result = classify_regime(frame, snapshot, cfg=cfg)
    assert result.regime == "trend_up"
    assert not result.grid_enabled
    assert "ADX" in result.reason


def test_trend_up_degraded_to_range_when_adx_weak():
    """ADX 不足 → 即使 MA 趋势也要降级为 range 让网格工作。"""
    cfg = GridConfig()
    frame = _make_frame([1.0, 1.1, 1.2, 1.3, 1.4, 1.5])
    snapshot = IndicatorSnapshot(
        close=1.6, atr14=0.1,
        bb_upper=1.7, bb_middle=1.5, bb_lower=1.3,
        ma20=1.5, ma60=1.3,
        adx14=15.0,  # < 25 → 弱趋势
        bbw=0.27, bbw_percentile=0.5,
    )
    result = classify_regime(frame, snapshot, cfg=cfg)
    assert result.regime == "range"
    assert result.grid_enabled
    assert "ADX" in result.reason


def test_trend_down_confirmed_when_adx_strong():
    cfg = GridConfig()
    frame = _make_frame([1.5, 1.4, 1.3, 1.2, 1.1, 1.0])
    snapshot = IndicatorSnapshot(
        close=0.9, atr14=0.1,
        bb_upper=1.2, bb_middle=1.0, bb_lower=0.8,
        ma20=1.0, ma60=1.2,
        adx14=32.0, bbw=0.4, bbw_percentile=0.8,
    )
    result = classify_regime(frame, snapshot, cfg=cfg)
    assert result.regime == "trend_down"
    assert not result.grid_enabled


def test_trend_down_degraded_to_range_when_adx_weak():
    cfg = GridConfig()
    frame = _make_frame([1.5, 1.4, 1.3, 1.2, 1.1, 1.0])
    snapshot = IndicatorSnapshot(
        close=0.9, atr14=0.1,
        bb_upper=1.2, bb_middle=1.0, bb_lower=0.8,
        ma20=1.0, ma60=1.2,
        adx14=18.0,  # 弱趋势
        bbw=0.4, bbw_percentile=0.8,
    )
    result = classify_regime(frame, snapshot, cfg=cfg)
    assert result.regime == "range"
    assert result.grid_enabled


def test_adx_missing_falls_back_to_ma_logic():
    """ADX 缺失（预热不足）→ 向后兼容，按旧 MA 逻辑判定 trend_up。"""
    cfg = GridConfig()
    frame = _make_frame([1.0, 1.1, 1.2, 1.3, 1.4, 1.5])
    snapshot = IndicatorSnapshot(
        close=1.6, atr14=0.1,
        bb_upper=1.7, bb_middle=1.5, bb_lower=1.3,
        ma20=1.5, ma60=1.3,
        adx14=None,  # 预热不足
        bbw=None, bbw_percentile=None,
    )
    result = classify_regime(frame, snapshot, cfg=cfg)
    assert result.regime == "trend_up"
    assert not result.grid_enabled
    assert "ADX" not in result.reason  # ADX 不在 reason 里


def test_range_when_ma_structure_flat():
    """横盘：MA 结构不多够也不空够 → range。与 ADX 无关。"""
    cfg = GridConfig()
    frame = _make_frame([1.5, 1.5, 1.5, 1.5, 1.5, 1.5])
    snapshot = IndicatorSnapshot(
        close=1.5, atr14=0.1,
        bb_upper=1.7, bb_middle=1.5, bb_lower=1.3,
        ma20=1.5, ma60=1.5,
        adx14=10.0, bbw=0.27, bbw_percentile=0.1,
    )
    result = classify_regime(frame, snapshot, cfg=cfg)
    assert result.regime == "range"
    assert result.grid_enabled


def test_disabled_when_key_indicators_missing():
    cfg = GridConfig()
    frame = _make_frame([1.5, 1.5, 1.5, 1.5, 1.5, 1.5])
    snapshot = IndicatorSnapshot(
        close=1.5, atr14=None,  # ATR 缺失
        bb_upper=1.7, bb_middle=1.5, bb_lower=1.3,
        ma20=1.5, ma60=1.5,
        adx14=30.0,
    )
    result = classify_regime(frame, snapshot, cfg=cfg)
    assert result.regime == "disabled"
    assert not result.grid_enabled
