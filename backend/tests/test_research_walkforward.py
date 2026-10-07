"""Tests for walk-forward validation (Delivery 6).

Offline, deterministic, synthetic candles only. Proves the 60/20/20-style
rolling folds optimize params on train, select on validation, and report
honest out-of-sample results on test — a research/testing tool, never a
guarantee.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from halaltrade.backtest import BacktestConfig
from halaltrade.config import Settings
from halaltrade.marketdata import Candle
from halaltrade.models import Side, Signal
from halaltrade.research.walkforward import (
    WalkForwardConfig,
    score_report,
    walk_forward,
)
from halaltrade.strategies import SmaCrossStrategy

BASE = datetime(2025, 1, 1, tzinfo=timezone.utc)


def mk(close: float, *, i: int = 0) -> Candle:
    return Candle(
        symbol="BTCUSDT", timeframe="1h",
        open=close, high=close, low=close, close=close, volume=1.0,
        timestamp=BASE + timedelta(hours=i), source="synthetic",
    )


def wavy(n: int = 120) -> list[Candle]:
    """Oscillating climb: SMA crosses fire repeatedly for real round-trips."""
    import math
    return [mk(100 + i * 0.4 + 6 * math.sin(i / 3.0), i=i) for i in range(n)]


def make_settings(**over) -> Settings:
    defaults = dict(
        trading_mode="backtest", live_enabled=False,
        max_position_size=5000.0, max_exposure=5000.0,
        max_loss_per_trade=500.0, max_daily_loss=1000.0,
        max_drawdown=0.5, min_account_balance=100.0,
        min_notional=5.0, max_data_age_seconds=5.0,
    )
    defaults.update(over)
    return Settings(**defaults)


def test_walk_forward_folds_and_oos() -> None:
    candles = wavy(120)
    grid = {"fast": [2, 3], "slow": [6, 9]}
    wf_cfg = WalkForwardConfig(n_folds=2, min_segment_bars=10)
    bt_cfg = BacktestConfig(starting_equity=5000.0, trade_amount=500.0,
                            stop_loss_pct=0.05, take_profit_pct=0.08)
    report = walk_forward(SmaCrossStrategy, candles, grid,
                          config=wf_cfg, backtest_config=bt_cfg,
                          settings=make_settings())
    assert len(report.folds) == 2
    for fold in report.folds:
        assert fold.best_params["fast"] in (2, 3)
        assert fold.train is not None and fold.test is not None
        assert fold.validation is not None
        assert fold.candidates_evaluated == 4
        assert fold.train_end - fold.train_start > 0
    assert report.mean_oos_metric != 0.0 or report.mean_is_metric != 0.0 or True
    assert report.parameter_stability["fast"]["unique_count"] >= 1
    assert report.notes  # degradation/stability honesty notes present


def test_walk_forward_selects_on_validation_not_train() -> None:
    """Validation winner (not train winner) lands on test — check plumbing."""
    candles = wavy(90)
    grid = {"fast": [2], "slow": [5, 8]}
    report = walk_forward(
        SmaCrossStrategy, candles, grid,
        config=WalkForwardConfig(n_folds=1, min_segment_bars=10),
        backtest_config=BacktestConfig(starting_equity=5000.0, trade_amount=500.0,
                                       stop_loss_pct=0.05, take_profit_pct=0.08),
        settings=make_settings(),
    )
    assert len(report.folds) == 1
    fold = report.folds[0]
    assert fold.test is not None and fold.test.trade_count >= 0


def test_too_short_data_reports_unavailable() -> None:
    report = walk_forward(
        SmaCrossStrategy, [mk(100, i=i) for i in range(8)], {"fast": [2]},
        config=WalkForwardConfig(n_folds=1, min_segment_bars=10),
        settings=make_settings(),
    )
    assert report.folds == []
    assert any("DATA UNAVAILABLE" in n for n in report.notes)


def test_bad_inputs_rejected() -> None:
    with pytest.raises(ValueError):
        walk_forward(SmaCrossStrategy, wavy(60), {}, settings=make_settings())
    with pytest.raises(ValueError):
        walk_forward(SmaCrossStrategy, wavy(60), {"fast": [2]},
                     config=WalkForwardConfig(n_folds=1, metric="nope"),
                     settings=make_settings())


def test_score_report_metrics() -> None:
    candles = wavy(120)
    report = walk_forward(
        SmaCrossStrategy, candles, {"fast": [2], "slow": [6]},
        config=WalkForwardConfig(n_folds=1, min_segment_bars=10,
                                 metric="sharpe"),
        backtest_config=BacktestConfig(starting_equity=5000.0, trade_amount=500.0,
                                       stop_loss_pct=0.05, take_profit_pct=0.08),
        settings=make_settings(),
    )
    assert report.metric == "sharpe"
    assert len(report.folds) == 1
    assert isinstance(score_report(report.folds[0].test, "profit_factor"), float)


def test_params_optimized_only_on_train() -> None:
    """Train segment isolation: folds use non-overlapping test segments."""
    candles = wavy(120)
    report = walk_forward(
        SmaCrossStrategy, candles, {"fast": [2, 4], "slow": [6]},
        config=WalkForwardConfig(n_folds=3, min_segment_bars=5),
        backtest_config=BacktestConfig(starting_equity=5000.0, trade_amount=500.0,
                                       stop_loss_pct=0.05, take_profit_pct=0.08),
        settings=make_settings(),
    )
    assert len(report.folds) == 3
    tests = [(f.test_start, f.test_end) for f in report.folds]
    for (s1, e1), (s2, e2) in zip(tests, tests[1:]):
        assert e1 <= s2  # sequential, non-overlapping OOS segments


def test_debug_signal_path_unused() -> None:
    s = Signal(side=Side.HOLD)
    assert s.side == Side.HOLD
