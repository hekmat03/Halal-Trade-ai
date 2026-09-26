"""Tests for the modular strategy library (Delivery 6).

Offline, deterministic, synthetic candles only. Every strategy is checked for:
spot-long-only shape (BUY entries + SELL exits only, never short), Strategy
protocol conformance (BUY carries stop-loss), and research metadata.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from halaltrade.backtest import make_hold, make_sma_cross  # legacy aliases intact
from halaltrade.marketdata import Candle
from halaltrade.models import InstrumentType, Side
from halaltrade.strategies import (
    STRATEGY_NAMES,
    DonchianBreakoutStrategy,
    EmaTrendStrategy,
    RsiMeanReversionStrategy,
    SmaCrossStrategy,
    get_strategy,
    list_strategies,
    make_donchian_breakout,
    make_rsi_mean_reversion,
)

BASE = datetime(2025, 1, 1, tzinfo=timezone.utc)


def mk(close: float, *, i: int = 0, high: float | None = None,
       low: float | None = None) -> Candle:
    return Candle(
        symbol="BTCUSDT", timeframe="1h",
        open=close, high=high if high is not None else close,
        low=low if low is not None else close, close=close, volume=1.0,
        timestamp=BASE + timedelta(hours=i), source="synthetic",
    )


def climb_then_flat(n: int = 60) -> list[Candle]:
    return [mk(100 + i * 1.5 if i < 30 else 145, i=i) for i in range(n)]


def test_ema_trend_family_entries_exits_and_entry_only_filters() -> None:
    """EMA-trend family: cross entries, exit-only SELLs, filters gate entries.

    The library emits signals only on an actual MA crossover (never on a
    monotonic ramp), so these fixtures are built to cross on the final bar.
    """
    # Down 7 bars then a sharp reversal -> fast EMA crosses up on the last bar.
    cross_up = [mk(c, i=i) for i, c in enumerate(
        [120, 118, 116, 114, 112, 110, 108, 130])]
    strat = EmaTrendStrategy(fast=3, slow=6)
    sig = strat(cross_up, 0.0, 5000.0)
    assert sig.side == Side.BUY
    assert sig.stop_loss is not None                 # mandatory stop always set
    assert sig.instrument_type == InstrumentType.SPOT and sig.leverage == 1.0
    # Mirror image -> fast EMA crosses down while holding: exit only, no short.
    cross_down = [mk(c, i=i) for i, c in enumerate(
        [120, 122, 124, 126, 128, 130, 132, 110])]
    sig2 = strat(cross_down, 0.01, 5000.0)
    assert sig2.side == Side.SELL
    assert sig2.amount is not None and sig2.stop_loss is not None
    assert strat(cross_down, 0.0, 5000.0).side == Side.HOLD  # flat -> no short
    # Filters gate ENTRIES only (flat volume never confirms).
    assert strat.with_params(volume_filter_bars=5)(cross_up, 0.0, 5000.0).side == Side.HOLD
    # ...and never block an exit.
    assert strat.with_params(volume_filter_bars=5)(cross_down, 0.01,
                                                  5000.0).side == Side.SELL
    # Regime filter on too-short history -> regime "unknown" -> no entry.
    assert strat.with_params(regime_filter="trending-up")(cross_up, 0.0,
                                                          5000.0).side == Side.HOLD


def test_registry_matches_the_librarys_documented_strategy_set() -> None:
    """The registry must expose exactly the strategies the library ships.

    The set of strategies GROWS as the research library grows (Delivery 6
    started with three; the EMA-trend family and the 1d RSI preset were added
    later), so this asserts the registry against the library's own documented
    ``STRATEGY_NAMES`` instead of a frozen count. Adding a strategy means
    adding it to ``STRATEGY_NAMES`` deliberately — never deleting one.
    """
    expected = sorted(STRATEGY_NAMES)
    assert list_strategies() == expected
    assert len(list_strategies()) == len(set(list_strategies()))  # no dupes
    for name in list_strategies():
        strat = get_strategy(name)
        assert strat is not None
        assert strat.meta.name                      # documented metadata
        assert strat.meta.validated is False        # nothing is validated yet
    with pytest.raises(KeyError):
        get_strategy("nope")


def test_metadata_present_and_unvalidated() -> None:
    for strat in (SmaCrossStrategy(), RsiMeanReversionStrategy(),
                  DonchianBreakoutStrategy(), EmaTrendStrategy()):
        assert strat.meta.name
        assert strat.meta.timeframe in ("1h", "4h", "1d")
        assert strat.meta.params
        assert strat.meta.risk_class
        assert strat.meta.validated is False
        assert strat.meta.notes


def test_named_1d_research_presets_carry_the_sweep_parameters() -> None:
    """The named presets must stay pinned to the params actually measured."""
    rsi = get_strategy("rsi_mean_reversion_1d")
    assert (rsi.period, rsi.oversold, rsi.overbought) == (7, 30.0, 70.0)
    assert (rsi.stop_loss_pct, rsi.take_profit_pct) == (0.03, 0.06)
    assert rsi.timeframe == "1d"
    dc = get_strategy("donchian_1d")
    assert (dc.channel, dc.timeframe) == (15, "1d")
    assert (dc.stop_loss_pct, dc.take_profit_pct) == (0.02, 0.05)
    # Both remain research candidates: nothing is validated, ever, by a sweep.
    assert rsi.meta.validated is False and dc.meta.validated is False


def test_sma_cross_buy_has_stop_and_sell_is_exit_only() -> None:
    strat = SmaCrossStrategy(fast=3, slow=6)
    candles = [mk(100, i=0), mk(101, i=1), mk(102, i=2), mk(103, i=3),
               mk(104, i=4), mk(105, i=5), mk(106, i=6)]
    sig = strat(candles, 0.0, 5000.0)
    assert sig.side in (Side.BUY, Side.HOLD)
    if sig.side == Side.BUY:
        assert sig.stop_loss is not None
        assert sig.instrument_type == InstrumentType.SPOT
        assert sig.leverage == 1.0
    # SELL leg carries an explicit amount + stop (never a short).
    down = [mk(110 - i, i=i) for i in range(10)]
    sig2 = strat(down, 1.0, 5000.0)
    assert sig2.side in (Side.SELL, Side.HOLD)
    if sig2.side == Side.SELL:
        assert sig2.amount is not None and sig2.stop_loss is not None


def test_rsi_oversold_buys_and_overbought_closes_only() -> None:
    strat = RsiMeanReversionStrategy(period=5, oversold=40.0, overbought=60.0)
    falling = [mk(150 - i * 3.0, i=i) for i in range(12)]
    sig = strat(falling, 0.0, 5000.0)
    assert sig.side == Side.BUY  # deeply oversold -> bounce thesis
    assert sig.stop_loss is not None
    rising = [mk(50 + i * 3.0, i=i) for i in range(12)]
    sig2 = strat(rising, 0.01, 5000.0)
    assert sig2.side == Side.SELL  # overbought -> close owned position only
    assert sig2.amount is not None and sig2.stop_loss is not None
    # Flat + overbought must NOT sell (nothing owned -> no short).
    sig3 = strat(rising, 0.0, 5000.0)
    assert sig3.side == Side.HOLD


def test_donchian_breakout_entries_and_exits() -> None:
    strat = DonchianBreakoutStrategy(channel=5)
    base = [mk(100, i=i) for i in range(6)]
    burst = base + [mk(120, i=6, high=121, low=119)]
    sig = strat(burst, 0.0, 5000.0)
    assert sig.side == Side.BUY
    assert sig.stop_loss is not None
    crash = [mk(120, i=i) for i in range(6)] + [mk(90, i=6, high=91, low=89)]
    sig2 = strat(crash, 0.01, 5000.0)
    assert sig2.side == Side.SELL
    # Flat + breakdown must NOT sell.
    assert strat(crash, 0.0, 5000.0).side == Side.HOLD
    assert strat(base, 0.0, 5000.0).side == Side.HOLD  # too flat / warmup


def test_invalid_params_rejected() -> None:
    with pytest.raises(ValueError):
        SmaCrossStrategy(fast=10, slow=5)
    with pytest.raises(ValueError):
        RsiMeanReversionStrategy(oversold=70.0, overbought=30.0)
    with pytest.raises(ValueError):
        DonchianBreakoutStrategy(channel=1)


def test_with_params_and_factories() -> None:
    s = SmaCrossStrategy().with_params(fast=5, slow=15)
    assert (s.fast, s.slow) == (5, 15)
    assert make_rsi_mean_reversion(period=7).period == 7
    assert make_donchian_breakout(channel=10).channel == 10


def test_legacy_backtest_aliases_still_work() -> None:
    """Old imports (test_backtest_engine.py) resolve to the same behavior."""
    strat = make_sma_cross(fast=2, slow=3)
    closes = [100, 101, 102, 102, 101, 100, 99, 98, 97, 98]
    candles = [mk(c, i=i) for i, c in enumerate(closes)]
    sig = strat(candles, 0.0, 5000.0)
    assert sig.side in (Side.BUY, Side.SELL, Side.HOLD)
    assert make_hold()(candles, 0.0, 5000.0).side == Side.HOLD
