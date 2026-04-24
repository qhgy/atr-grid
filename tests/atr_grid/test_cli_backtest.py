"""CLI `backtest` 子命令的轻量级测试。

通过 monkeypatch 替换 run_backtest，验证 argparse + handler + 输出序列化。
不依赖网络或 snowball 数据。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from atr_grid import cli as cli_mod
from atr_grid.backtest import BacktestResult, RoundTrip


def _fake_result(symbol: str = "SH515880", profile: str = "stable") -> BacktestResult:
    rt = RoundTrip(
        buy_date="2024-01-10",
        buy_price=1.000,
        sell_date="2024-01-20",
        sell_price=1.050,
        shares=200,
        gross_pnl=10.0,
        fees=0.0,
        net_pnl=10.0,
        return_pct=5.0,
    )
    return BacktestResult(
        symbol=symbol,
        profile=profile,
        start_date="2024-01-01",
        end_date="2024-12-31",
        bars=240,
        initial_cash=100_000.0,
        initial_shares=2000,
        initial_price=1.000,
        final_cash=99_900.0,
        final_shares=2200,
        final_price=1.050,
        final_equity=102_210.0,
        benchmark_equity=101_000.0,
        total_return_pct=2.21,
        benchmark_return_pct=1.00,
        excess_return_pct=1.21,
        trade_count=2,
        buy_count=1,
        sell_count=1,
        round_trip_count=1,
        win_count=1,
        loss_count=0,
        win_rate=1.0,
        avg_win=10.0,
        avg_loss=0.0,
        payoff_ratio=float("inf"),
        profit_factor=float("inf"),
        max_drawdown_pct=0.5,
        sharpe_ratio=1.8,
        events_summary={"baseline": 1, "hold": 200, "buy": 1, "sell": 1},
        trades=[],
        round_trips=[rt],
        equity_curve=[],
        warnings=[],
    )


def test_backtest_cli_prints_kpi_and_writes_json(tmp_path, monkeypatch, capsys):
    captured_kwargs: dict = {}

    def fake_run_backtest(**kwargs):
        captured_kwargs.update(kwargs)
        return _fake_result(symbol=kwargs["symbol"], profile=kwargs["profile_name"])

    monkeypatch.setattr(cli_mod, "run_backtest", fake_run_backtest)

    json_out = tmp_path / "result.json"
    exit_code = cli_mod.main(
        [
            "backtest",
            "SH515880",
            "--profile",
            "stable",
            "--kline-count",
            "900",
            "--warmup-bars",
            "60",
            "--json-out",
            str(json_out),
        ]
    )
    assert exit_code == 0

    # 底层参数序列正确
    assert captured_kwargs["symbol"] == "SH515880"
    assert captured_kwargs["profile_name"] == "stable"
    assert captured_kwargs["kline_count"] == 900
    assert captured_kwargs["warmup_bars"] == 60
    assert captured_kwargs["initial_cash"] == 100_000.0
    assert captured_kwargs["initial_shares"] == 2000
    # Phase 2.2：CLI 默认不传 --trade-shares 时透传 None，run_backtest 内部从 cfg 兑底。
    assert captured_kwargs["trade_shares"] is None

    out = capsys.readouterr().out
    assert "[SH515880]" in out
    assert "profile=stable" in out
    assert "胜率: 100.00%" in out
    assert "赔率(payoff): inf" in out  # inf 正确渲染
    assert "total=2" in out
    assert "excess: +1.21%" in out

    # JSON 落盘 + inf 序列化为字符串
    data = json.loads(json_out.read_text(encoding="utf-8"))
    assert data["symbol"] == "SH515880"
    assert data["profile"] == "stable"
    assert data["payoff_ratio"] == "inf"
    assert data["profit_factor"] == "inf"
    assert data["round_trip_count"] == 1
    assert len(data["round_trips"]) == 1
    assert data["round_trips"][0]["buy_price"] == 1.000


def test_backtest_cli_no_save_skips_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        cli_mod,
        "run_backtest",
        lambda **kw: _fake_result(symbol=kw["symbol"], profile=kw["profile_name"]),
    )

    exit_code = cli_mod.main(["backtest", "SH515880", "--profile", "default", "--no-save"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "JSON 已写出" not in out


def test_backtest_cli_accepts_all_registered_profiles(monkeypatch):
    captured_profiles: list[str] = []

    def fake_run_backtest(**kwargs):
        captured_profiles.append(kwargs["profile_name"])
        return _fake_result(symbol=kwargs["symbol"], profile=kwargs["profile_name"])

    monkeypatch.setattr(cli_mod, "run_backtest", fake_run_backtest)

    for profile in ("balanced", "yield", "trend_hybrid"):
        exit_code = cli_mod.main([
            "backtest",
            "SH515880",
            "--profile",
            profile,
            "--no-save",
        ])
        assert exit_code == 0

    assert captured_profiles == ["balanced", "yield", "trend_hybrid"]


def test_backtest_cli_default_json_path(tmp_path, monkeypatch, capsys):
    """不指定 --json-out 也不 --no-save 时，应落在 output/backtest/ 下。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        cli_mod,
        "run_backtest",
        lambda **kw: _fake_result(symbol=kw["symbol"], profile=kw["profile_name"]),
    )

    exit_code = cli_mod.main(["backtest", "SH515880", "--profile", "aggressive"])
    assert exit_code == 0
    default_path = tmp_path / "output" / "backtest" / "SH515880_aggressive_20241231.json"
    assert default_path.exists()
    data = json.loads(default_path.read_text(encoding="utf-8"))
    assert data["profile"] == "aggressive"


def test_backtest_cli_invalid_profile_exits():
    with pytest.raises(SystemExit):
        cli_mod.main(["backtest", "SH515880", "--profile", "nonexistent"])
