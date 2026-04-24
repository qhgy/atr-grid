"""Tests for atr_grid.config profile factory and adaptive prealert buffer."""

from __future__ import annotations

import pytest

from atr_grid.config import (
    DEFAULT_CONFIG,
    GridConfig,
    available_profiles,
    for_profile,
)
from atr_grid.engine import _prealert_buy_price, _prealert_sell_price


class TestForProfile:
    def test_stable_matches_defaults(self):
        cfg = for_profile("stable")
        assert cfg == GridConfig()
        # 确保 main 分支行为不被动到：stable 预警距百分比为 0
        assert cfg.prealert_buffer_pct == 0.0
        assert cfg.notify_threshold_pct == 1.5

    def test_dev_profile_tightens_knobs(self):
        cfg = for_profile("dev")
        assert cfg.regime_ma_lookback == 7
        assert cfg.regime_slope_threshold == pytest.approx(0.35)
        assert cfg.prealert_buffer_pct == pytest.approx(0.003)
        assert cfg.notify_threshold_pct == pytest.approx(1.0)
        assert cfg.min_step_pct == pytest.approx(0.008)
        # step 最小分母从 1/8 上浮到 1/6
        assert cfg.step_min_fraction == pytest.approx(1 / 6)

    def test_unknown_profile_falls_back_to_stable(self):
        assert for_profile("does-not-exist") == GridConfig()

    def test_overrides_beat_preset(self):
        cfg = for_profile("dev", notify_threshold_pct=0.5, prealert_buffer_pct=0.01)
        assert cfg.notify_threshold_pct == pytest.approx(0.5)
        assert cfg.prealert_buffer_pct == pytest.approx(0.01)
        # 未覆盖的字段保持 dev 预设
        assert cfg.regime_ma_lookback == 7

    def test_available_profiles_listed(self):
        names = available_profiles()
        assert "stable" in names
        assert "dev" in names
        assert "aggressive" in names


class TestAdaptivePrealertBuffer:
    def test_pct_kicks_in_for_high_price_targets(self):
        # dev 配置下，预警距 = max(0.005, 0.003 * price)。price=5 时百分比胜出。
        cfg = for_profile("dev")
        sell = _prealert_sell_price(5.000, precision=3, cfg=cfg)
        # 0.003 * 5 = 0.015 > 0.005，所以预警价 = 5.000 - 0.015 = 4.985
        assert sell == pytest.approx(4.985)

        buy = _prealert_buy_price(5.000, precision=3, cfg=cfg)
        assert buy == pytest.approx(5.015)

    def test_abs_floor_kicks_in_for_low_price_targets(self):
        # price=1 时 0.003*1=0.003 < 0.005，绝对值底板胜出，与 stable 一致。
        cfg = for_profile("dev")
        sell = _prealert_sell_price(1.000, precision=3, cfg=cfg)
        assert sell == pytest.approx(0.995)

    def test_stable_profile_behaviour_unchanged(self):
        # main 分支基线：只有绝对值缓冲。
        cfg = for_profile("stable")
        assert cfg.prealert_buffer_pct == 0.0
        sell = _prealert_sell_price(5.000, precision=3, cfg=cfg)
        assert sell == pytest.approx(4.995)

    def test_default_config_matches_stable(self):
        # DEFAULT_CONFIG 应等价于 stable，以保证模块级代码行为无意外漂移。
        assert DEFAULT_CONFIG == for_profile("stable")
