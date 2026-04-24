"""Tests for paper CLI profile persistence and hybrid handoff."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pandas as pd
import pytest

from atr_grid import paper as paper_mod


@dataclass
class FakeAllocation:
    total_equity: float
    position_pct: float
    band: dict
    base_budget: float
    swing_budget: float
    cash_floor: float
    only_sell: bool


class FakePlan:
    symbol = "TEST"
    current_price = 1.0
    last_trade_date = "2026-04-24"
    regime = "range"
    mode = "range_grid"
    grid_enabled = True
    reason = "test"
    primary_buy = 0.95
    primary_sell = 1.05
    sell_levels = []
    buy_levels = []
    reference_sell_ladder = [1.05]
    reference_rebuy_ladder = [0.95]
    lower_invalidation = None
    upper_breakout = None


@pytest.fixture
def isolated_logs(tmp_path, monkeypatch):
    log_dir = tmp_path / "paper_logs"
    monkeypatch.setattr(paper_mod, "LOG_DIR", log_dir)
    return log_dir


def test_load_portfolio_defaults_legacy_state_to_stable(isolated_logs):
    isolated_logs.mkdir(parents=True)
    path = isolated_logs / "TEST_state.json"
    path.write_text(
        """{
  "symbol": "TEST",
  "shares": 1000,
  "cash": 100.0,
  "last_price": null,
  "initial_shares": 1000,
  "initial_cash": 100.0,
  "initial_price": 1.0,
  "created_at": "2026-04-24T00:00:00"
}""",
        encoding="utf-8",
    )

    portfolio = paper_mod.load_portfolio("TEST")

    assert portfolio is not None
    assert portfolio.profile == "stable"


def test_paper_run_hands_hybrid_controls_to_simulate_day(isolated_logs, monkeypatch):
    p = paper_mod.Portfolio(
        symbol="TEST",
        profile="trend_hybrid",
        shares=1000,
        cash=500.0,
        last_price=0.90,
        initial_shares=1000,
        initial_cash=500.0,
        initial_price=0.90,
        created_at="2026-04-24T00:00:00",
    )
    paper_mod.save_portfolio(p)

    frame = pd.DataFrame({"close": [0.90, 1.00], "high": [0.92, 1.01]})
    monkeypatch.setattr(
        paper_mod,
        "_build_plan_and_frame",
        lambda symbol, *, shares, cfg: (FakePlan(), frame),
    )

    calls: dict = {}

    def fake_apply_hybrid_overlay(plan, frame_arg, *, total_equity, cfg):
        calls["overlay"] = {
            "total_equity": total_equity,
            "hybrid": cfg.trend_hybrid_enabled,
        }
        return plan, FakeAllocation(
            total_equity=total_equity,
            position_pct=50.0,
            band={"name": "mid", "low": 30.0, "high": 70.0, "swing_ratio": 0.67, "only_sell": False},
            base_budget=total_equity * cfg.base_position_ratio,
            swing_budget=100.0,
            cash_floor=total_equity * cfg.cash_floor_ratio,
            only_sell=False,
        )

    monkeypatch.setattr("atr_grid.engine.apply_hybrid_overlay", fake_apply_hybrid_overlay)

    def fake_simulate_day(state, plan, **kwargs):
        calls["simulate"] = {
            "base_shares": state.base_shares,
            "trade_shares": kwargs["trade_shares"],
            "cash_floor": kwargs["cash_floor"],
            "total_equity": kwargs["total_equity"],
            "hybrid": kwargs["cfg"].trend_hybrid_enabled,
        }
        return state, [{"type": "hold", "price": plan.current_price, "note": "test hold"}]

    monkeypatch.setattr(paper_mod, "simulate_day", fake_simulate_day)
    monkeypatch.setattr(paper_mod, "append_journal", lambda symbol, record: calls.setdefault("record", record))

    exit_code = paper_mod.cmd_run(SimpleNamespace(symbol="TEST", profile=None, force=True))

    assert exit_code == 0
    assert calls["overlay"] == {"total_equity": 1500.0, "hybrid": True}
    assert calls["simulate"]["base_shares"] == 1000
    assert calls["simulate"]["trade_shares"] == 300
    assert calls["simulate"]["cash_floor"] == pytest.approx(300.0)
    assert calls["simulate"]["total_equity"] == pytest.approx(1500.0)
    assert calls["simulate"]["hybrid"] is True
    assert calls["record"]["profile"] == "trend_hybrid"
    assert calls["record"]["hybrid_enabled"] is True
    assert calls["record"]["hybrid_allocation"]["cash_floor"] == pytest.approx(300.0)


def test_paper_run_profile_override_is_persisted(isolated_logs, monkeypatch):
    p = paper_mod.Portfolio(
        symbol="TEST",
        profile="stable",
        shares=1000,
        cash=500.0,
        last_price=0.90,
        initial_shares=1000,
        initial_cash=500.0,
        initial_price=0.90,
        created_at="2026-04-24T00:00:00",
    )
    paper_mod.save_portfolio(p)

    monkeypatch.setattr(
        paper_mod,
        "_build_plan_and_frame",
        lambda symbol, *, shares, cfg: (FakePlan(), pd.DataFrame({"close": [1.0]})),
    )
    monkeypatch.setattr(
        "atr_grid.engine.apply_hybrid_overlay",
        lambda plan, frame, *, total_equity, cfg: (plan, None),
    )
    monkeypatch.setattr(
        paper_mod,
        "simulate_day",
        lambda state, plan, **kwargs: (state, [{"type": "hold", "price": plan.current_price, "note": "test hold"}]),
    )
    monkeypatch.setattr(paper_mod, "append_journal", lambda symbol, record: None)

    exit_code = paper_mod.cmd_run(SimpleNamespace(symbol="TEST", profile="balanced", force=True))

    assert exit_code == 0
    assert paper_mod.load_portfolio("TEST").profile == "balanced"


def test_paper_run_profile_override_persists_even_when_trade_date_seen(isolated_logs, monkeypatch):
    p = paper_mod.Portfolio(
        symbol="TEST",
        profile="stable",
        shares=1000,
        cash=500.0,
        last_price=0.90,
        initial_shares=1000,
        initial_cash=500.0,
        initial_price=0.90,
        created_at="2026-04-24T00:00:00",
        last_trade_date="2026-04-24",
    )
    paper_mod.save_portfolio(p)

    monkeypatch.setattr(
        paper_mod,
        "_build_plan_and_frame",
        lambda symbol, *, shares, cfg: (FakePlan(), pd.DataFrame({"close": [1.0]})),
    )
    monkeypatch.setattr(
        "atr_grid.engine.apply_hybrid_overlay",
        lambda plan, frame, *, total_equity, cfg: (plan, None),
    )

    exit_code = paper_mod.cmd_run(SimpleNamespace(symbol="TEST", profile="balanced", force=False))

    assert exit_code == 0
    assert paper_mod.load_portfolio("TEST").profile == "balanced"
