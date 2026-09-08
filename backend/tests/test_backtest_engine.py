"""Tests for the Backtest engine (Delivery 3).

Everything here is **offline and deterministic**: candles are synthetic, built
in-test, and no network is touched. The tests prove the engine's economics
(equity math, fees, slippage), the mandatory stop-loss / take-profit exits, the
four-policy-gate enforcement before any simulated fill, that it never shorts,
and that every report metric is present and sane.

Example strategies (``make_sma_cross`` / ``make_hold``) are used together with
small bespoke deterministic strategies to control the exact fill path.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from halaltrade.backtest import BacktestConfig, BacktestEngine, make_hold, make_sma_cross
from halaltrade.config import Settings
from halaltrade.marketdata import Candle
from halaltrade.models import InstrumentType, Side, Signal
from halaltrade.simulation import FeeConfig

BASE = datetime(2025, 1, 1, tzinfo=timezone.utc)


def mk(close: float, *, i: int = 0, high: float | None = None,
       low: float | None = None, open_: float | None = None) -> Candle:
    """One synthetic one-price candle. Defaults keep high/low open == close."""
    return Candle(
        symbol="BTCUSDT",
        timeframe="1m",
        open=open_ if open_ is not None else close,
        high=high if high is not None else close,
        low=low if low is not None else close,
        close=close,
        volume=1.0,
        timestamp=BASE + timedelta(minutes=i),
        source="synthetic",
    )


def make_settings(**over) -> Settings:
    """A Settings with roomy caps so BUY/SELL signals can pass the Risk gate."""
    defaults = dict(
        trading_mode="backtest",
        live_enabled=False,
        max_position_size=5000.0,
        max_exposure=5000.0,
        max_loss_per_trade=500.0,
        max_daily_loss=1000.0,
        max_drawdown=0.5,
        min_account_balance=100.0,
        min_notional=5.0,
        max_data_age_seconds=5.0,
    )
    defaults.update(over)
    return Settings(**defaults)


def make_roundtrip(buy_index: int = 0, sell_index: int = 2) -> object:
    """Deterministic BUY at *buy_index*, SELL at *sell_index*, else HOLD.

    The SELL carries an explicit amount and a stop (any value) so it passes the
    Risk gate — the engine only ever closes an owned position on this SELL and
    never short-sells.
    """
    def strat(candles: list[Candle], position: float, equity: float) -> Signal:
        i = len(candles) - 1
        if not position and i == buy_index:
            return Signal(side=Side.BUY)
        if position and i == sell_index:
            return Signal(side=Side.SELL, amount=1000.0, stop_loss=1.0)
        return Signal(side=Side.HOLD)
    strat.__name__ = "roundtrip"  # type: ignore[attr-defined]
    return strat


def make_buy_only() -> object:
    """BUY on the first bar, then never trade again (exit via stop/TP/flat)."""
    def strat(candles: list[Candle], position: float, equity: float) -> Signal:
        if not position and len(candles) == 1:
            return Signal(side=Side.BUY)
        return Signal(side=Side.HOLD)
    strat.__name__ = "buy_only"  # type: ignore[attr-defined]
    return strat


# --------------------------------------------------------------------------------------
# Deterministic economics / equity math
# --------------------------------------------------------------------------------------

def test_deterministic_round_trip_equity_math() -> None:
    """A fixed BUY(100)->SELL(102) path yields exact equity/return numbers.

    No slippage, 10 bps taker fee.
      BUY 1000@100: qty=10, fee=1 -> usdt 3999
      SELL @102: notional=1020, fee=1.02 -> usdt 5017.98, btc 0
      Final mark (bar 2, before the close fill) = 3999 + 10*102 = 5019
    """
    candles = [mk(100, i=0), mk(101, i=1), mk(102, i=2)]
    result = BacktestEngine(make_settings()).run(
        make_roundtrip(), candles,
        BacktestConfig(starting_equity=5000.0, trade_amount=1000.0,
                       fee_config=FeeConfig(taker_bps=10)),
    )
    r = result.report
    assert r.trade_count == 1
    assert r.ending_equity == pytest.approx(5019.0)
    assert r.total_return == pytest.approx(0.0038)          # 19/5000
    assert r.pnl == pytest.approx(19.0)
    assert r.fees_paid == pytest.approx(2.02)               # 1.0 + 1.02
    assert r.win_rate == pytest.approx(1.0)
    assert r.wins == 1 and r.losses == 0
    # Sharpe must be a finite, non-negative number on this rising curve.
    assert r.sharpe >= 0
    assert r.closed_trades[0].exit_reason == "SIGNAL"
    assert r.closed_trades[0].pnl == pytest.approx(10 * (102 - 100) - 2.02)


def test_hold_strategy_trades_nothing_and_preserves_equity() -> None:
    """make_hold never BUYs/SELLs: no fees, flat equity, empty report metrics."""
    candles = [mk(100, i=0), mk(105, i=1), mk(95, i=2), mk(110, i=3)]
    result = BacktestEngine(make_settings()).run(
        make_hold(), candles, BacktestConfig(starting_equity=5000.0)
    )
    r = result.report
    assert r.trade_count == 0
    assert r.fees_paid == 0.0
    assert r.ending_equity == pytest.approx(5000.0)
    assert r.total_return == 0.0
    assert r.win_rate == 0.0 and r.profit_factor == 0.0
    assert len(r.equity_curve) == len(candles)


# --------------------------------------------------------------------------------------
# Fees & slippage
# --------------------------------------------------------------------------------------

def test_fees_paid_positive_and_fills_reflect_slippage() -> None:
    """With slippage 50 bps, BUY fills higher and SELL fills lower; fees are real.

    BUY 1000 @100 -> fill 100.5, qty = 1000/100.5; SELL at final close 100 ->
    fill 99.5. Fees are always > 0.
    """
    candles = [mk(100, i=0), mk(100, i=1), mk(100, i=2), mk(100, i=3)]
    result = BacktestEngine(make_settings()).run(
        make_buy_only(), candles,
        BacktestConfig(starting_equity=5000.0, trade_amount=1000.0,
                       fee_config=FeeConfig(taker_bps=10, slippage_bps=50)),
    )
    r = result.report
    assert r.fees_paid > 0
    trade = r.closed_trades[-1]            # flat-close at the last bar
    # BUY slippage: quantity is reduced because the fill price is higher.
    assert trade.quantity == pytest.approx(1000.0 / 100.5)
    # SELL slippage: closed at 99.5 (100 * (1 - 50/10000)).
    assert trade.exit_price == pytest.approx(99.5)


# --------------------------------------------------------------------------------------
# Stop-loss / take-profit enforcement
# --------------------------------------------------------------------------------------

def test_stop_loss_enforced_on_down_candle() -> None:
    """After a BUY @100 (auto stop 98), a down candle low<=98 closes at 98."""
    candles = [
        mk(100, i=0),                    # BUY bar, stop auto-set at 98
        mk(99, i=1, low=97, high=99),    # low 97 <= 98 -> STOP_LOSS @ 98
    ]
    result = BacktestEngine(make_settings()).run(
        make_buy_only(), candles,
        BacktestConfig(starting_equity=5000.0, trade_amount=1000.0,
                       stop_loss_pct=0.02,
                       fee_config=FeeConfig(taker_bps=10)),
    )
    assert result.account.btc == 0.0             # position closed
    assert len(result.report.closed_trades) == 1
    trade = result.report.closed_trades[0]
    assert trade.exit_reason == "STOP_LOSS"
    assert trade.exit_price == pytest.approx(98.0)
    assert trade.pnl < 0                          # bounded by the stop distance + fees
    assert result.report.trade_count == 1


def test_take_profit_enforced_on_up_candle() -> None:
    """After a BUY @100 (auto TP 105), an up candle high>=105 closes at 105."""
    candles = [
        mk(100, i=0),                    # BUY bar, TP auto-set at 105 (5%)
        mk(103, i=1, low=100, high=105), # high 105 >= 105 -> TAKE_PROFIT @ 105
    ]
    result = BacktestEngine(make_settings()).run(
        make_buy_only(), candles,
        BacktestConfig(starting_equity=5000.0, trade_amount=1000.0,
                       take_profit_pct=0.05,
                       fee_config=FeeConfig(taker_bps=10)),
    )
    assert result.account.btc == 0.0
    assert len(result.report.closed_trades) == 1
    trade = result.report.closed_trades[0]
    assert trade.exit_reason == "TAKE_PROFIT"
    assert trade.exit_price == pytest.approx(105.0)
    assert trade.pnl > 0


# --------------------------------------------------------------------------------------
# Report metric sanity (using the example SMA-cross strategy)
# --------------------------------------------------------------------------------------

def test_sma_cross_produces_sane_report_metrics() -> None:
    """make_sma_cross on a synthetic climb-then-fall series yields real numbers."""
    closes = [100, 101, 102, 102, 101, 100, 99, 98, 97, 98]
    candles = [mk(c, i=idx) for idx, c in enumerate(closes)]
    result = BacktestEngine(make_settings()).run(
        make_sma_cross(fast=2, slow=3), candles,
        BacktestConfig(starting_equity=5000.0, trade_amount=1000.0,
                       fee_config=FeeConfig(taker_bps=10)),
    )
    r = result.report
    assert r.trade_count >= 1
    assert 0.0 <= r.win_rate <= 1.0
    assert r.profit_factor >= 0.0
    assert 0.0 <= r.max_drawdown <= 1.0
    assert r.max_drawdown_usd >= 0.0
    assert r.fees_paid > 0
    assert len(r.equity_curve) >= 1
    assert r.equity_curve[-1] == pytest.approx(r.ending_equity)
    # Every closed trade is a real round-trip with a reason label we understand.
    for t in r.closed_trades:
        assert t.exit_reason in ("SIGNAL", "STOP_LOSS", "TAKE_PROFIT", "FLAT_CLOSE")
        assert t.quantity > 0


# --------------------------------------------------------------------------------------
# Pipeline gate enforcement & no-shorting
# --------------------------------------------------------------------------------------

def test_shariah_rejection_yields_no_position() -> None:
    """A BUY signal for a FUTURES instrument must be gate-rejected -> no trade."""
    def futures_buy(candles: list[Candle], position: float, equity: float) -> Signal:
        if not position:
            return Signal(side=Side.BUY, instrument_type=InstrumentType.FUTURES)
        return Signal(side=Side.HOLD)
    futures_buy.__name__ = "futures_buy"  # type: ignore[attr-defined]

    candles = [mk(100, i=0), mk(101, i=1), mk(102, i=2)]
    result = BacktestEngine(make_settings()).run(
        futures_buy, candles,
        BacktestConfig(starting_equity=5000.0, trade_amount=1000.0),
    )
    assert result.account.btc == 0.0
    assert result.account.usdt == pytest.approx(5000.0)
    assert result.report.trade_count == 0
    assert result.report.fees_paid == 0.0


def test_risk_cap_rejection_yields_no_position() -> None:
    """A BUY whose amount exceeds max_position_size must be rejected -> no trade."""
    def oversized_buy(candles: list[Candle], position: float, equity: float) -> Signal:
        if not position:
            return Signal(side=Side.BUY)
        return Signal(side=Side.HOLD)
    oversized_buy.__name__ = "oversized_buy"  # type: ignore[attr-defined]

    candles = [mk(100, i=0), mk(101, i=1)]
    result = BacktestEngine(
        make_settings(max_position_size=250.0, max_exposure=500.0)
    ).run(
        oversized_buy, candles,
        BacktestConfig(starting_equity=5000.0, trade_amount=1000.0),
    )
    assert result.account.btc == 0.0
    assert result.account.usdt == pytest.approx(5000.0)
    assert result.report.trade_count == 0


def test_never_shorts_even_if_strategy_always_sells() -> None:
    """A strategy that emits SELL every bar, flat from the start, must not short.

    The SELL carries an amount + stop so it passes the gates; the engine still
    does nothing because there is no owned position to dispose of.
    """
    def always_sell(candles: list[Candle], position: float, equity: float) -> Signal:
        return Signal(side=Side.SELL, amount=1000.0, stop_loss=1.0,
                      price=candles[-1].close)
    always_sell.__name__ = "always_sell"  # type: ignore[attr-defined]

    candles = [mk(100, i=0), mk(101, i=1), mk(102, i=2), mk(99, i=3)]
    result = BacktestEngine(make_settings()).run(
        always_sell, candles,
        BacktestConfig(starting_equity=5000.0, trade_amount=1000.0),
    )
    assert result.account.btc == 0.0                 # never a negative position
    assert result.account.usdt == pytest.approx(5000.0)
    assert result.report.trade_count == 0
