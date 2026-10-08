"""Delivery 8 — paper-run configuration, reports and run-script tests.

Covers:
* Telegram Settings defaults (every toggle OFF) and notifier fail-safety with
  empty settings (no token/chat => returns False, never raises).
* Daily/weekly report formatting + the pure stats helpers.
* Schedule helpers (next daily 18:00 UTC, next weekly Sunday 12:00 UTC).
* Candle hygiene: the still-forming bar is dropped, stale/cached bars refuse.
* Signal stamping: the owner's explicit size + mandatory stop-loss are always
  present (the four gates reject a non-HOLD signal without them).
* Run-script smoke: ``--once``-style evaluation against synthetic data (no
  network), including a real paper fill and the recorded progression evidence.

Everything here runs fully offline.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from halaltrade.config import Settings
from halaltrade.marketdata.models import Candle, Ticker
from halaltrade.models import Side, Signal
from halaltrade.notifications.reports import (
    PROFIT_FACTOR_CAP,
    FillRecord,
    PerformanceTracker,
    compute_stats,
    finite_profit_factor,
    format_daily_report,
    format_weekly_report,
    max_drawdown_pct,
)
from halaltrade.notifications.telegram import AlertType, TelegramNotifier
from halaltrade.progression import (
    NullProgressionStore,
    ProgressionMachine,
    ProgressionStage,
    ProgressionLimits,
)
from halaltrade.strategies import get_strategy

import run_paper_trading as run_mod
from run_paper_trading import (
    PaperRun,
    candles_are_stale,
    drop_unclosed_candles,
    next_daily_utc,
    next_weekly_utc,
    newest_bar_age_seconds,
    parse_hhmm,
    prepare_signal,
    select_live_rest_base,
)

NOW = datetime(2026, 10, 8, 4, 30, tzinfo=timezone.utc)


def _bar(open_time: datetime, close: float, *, high: float | None = None,
         low: float | None = None, source: str = "binance") -> Candle:
    return Candle(
        symbol="BTCUSDT",
        timeframe="1d",
        open=close,
        high=high if high is not None else close,
        low=low if low is not None else close,
        close=close,
        volume=100.0,
        timestamp=open_time,
        source=source,
    )


def _daily_history(closes: list[float], *, start: datetime) -> list[Candle]:
    return [_bar(start + timedelta(days=i), c) for i, c in enumerate(closes)]


class FakeSource:
    """Offline stand-in for the live market-data source."""

    name = "fake"

    def __init__(self, ticker: Ticker, candles: list[Candle]) -> None:
        self._ticker = ticker
        self._candles = candles
        self.candle_calls = 0

    async def get_ticker(self, symbol: str = "BTCUSDT") -> Ticker:
        return self._ticker

    async def get_candles(self, symbol: str = "BTCUSDT", timeframe: str = "1d",
                          limit: int = 100) -> list[Candle]:
        self.candle_calls += 1
        return list(self._candles)


def _settings(**over) -> Settings:
    base = dict(
        trading_mode="paper",
        live_enabled=False,
        paper_starting_usdt=1000.0,
        paper_trade_amount_usdt=100.0,
        max_position_size=500.0,
        max_exposure=2000.0,
        max_loss_per_trade=50.0,
        max_daily_loss=100.0,
        min_account_balance=100.0,
        paper_poll_seconds=3600.0,
    )
    base.update(over)
    return Settings(**base)


# ---------------------------------------------------------------------------
# 1. Telegram settings + notifier fail-safety
# ---------------------------------------------------------------------------


def test_telegram_settings_default_to_off():
    s = Settings()
    assert s.telegram_enabled is False
    assert s.telegram_bot_token == ""
    assert s.telegram_chat_id == ""
    for field in (
        "telegram_alert_daily_pnl",
        "telegram_alert_strategy_change",
        "telegram_alert_emergency_stop",
        "telegram_alert_risk_rejection",
        "telegram_alert_system_error",
    ):
        assert getattr(s, field) is False, field


async def test_notifier_is_fail_safe_with_empty_settings():
    notifier = TelegramNotifier(Settings())
    assert await notifier.send(AlertType.DAILY_PNL, "hello") is False
    # enabled but no token/chat => still False, still no exception
    partial = Settings(telegram_enabled=True, telegram_alert_daily_pnl=True)
    assert await TelegramNotifier(partial).send(AlertType.DAILY_PNL, "hello") is False


# ---------------------------------------------------------------------------
# 2. Report stats + formatting
# ---------------------------------------------------------------------------


def test_profit_factor_helper_is_finite_and_honest():
    assert finite_profit_factor(0.0, 0.0) == 0.0
    assert finite_profit_factor(10.0, 0.0) == PROFIT_FACTOR_CAP
    assert finite_profit_factor(10.0, 5.0) == pytest.approx(2.0)


def test_max_drawdown_pct():
    assert max_drawdown_pct([100, 110, 99, 120]) == pytest.approx((110 - 99) / 110 * 100)
    assert max_drawdown_pct([]) == 0.0


def test_report_formatting_daily_and_weekly():
    tracker = PerformanceTracker(1000.0)
    tracker.record_equity(1000.0, NOW - timedelta(days=1))
    fill = FillRecord(
        timestamp=NOW - timedelta(hours=2),
        side="SELL",
        quantity=0.001,
        price=85000.0,
        notional=85.0,
        fee=0.05,
        realized_pnl=12.34,
    )
    tracker._fills.append(fill)
    tracker.record_equity(1012.34, NOW)

    stats = tracker.stats(
        label="WEEKLY",
        period_start=NOW - timedelta(days=7),
        stage="PAPER",
        paper_days=4.5,
        data_source="binance",
    )
    text = format_weekly_report(stats)
    assert "Paper Report" in text
    assert "$1,000.00 -> $1,012.34" in text
    assert "Period P&L: +12.34 USDT" in text
    assert "wins: 1, losses: 0" in text
    assert "win rate: 100.0%" in text
    assert "Stage: PAPER — paper day 4.5 of 30" in text
    assert "not a profit guarantee" in text

    daily = format_daily_report(stats)
    assert "Daily Paper Report" in daily


def test_compute_stats_win_rate_and_empty_period():
    fills = [
        FillRecord(NOW, "SELL", 0.001, 85000, 85.0, 0.05, 10.0),
        FillRecord(NOW, "SELL", 0.001, 84000, 84.0, 0.05, -5.0),
        FillRecord(NOW, "BUY", 0.001, 80000, 80.0, 0.04, 0.0),
    ]
    stats = compute_stats(
        label="DAILY",
        fills=fills,
        equity_curve=[(NOW - timedelta(hours=1), 1000.0), (NOW, 1005.0)],
        fallback_starting_equity=1000.0,
    )
    assert stats.trades == 3
    assert stats.closed_trades == 2
    assert stats.wins == 1 and stats.losses == 1
    assert stats.win_rate_pct == pytest.approx(50.0)
    assert stats.profit_factor == pytest.approx(2.0)

    empty = compute_stats(label="DAILY", fills=[], fallback_starting_equity=1000.0)
    assert empty.trades == 0
    assert empty.profit_factor == 0.0
    assert empty.win_rate_pct == 0.0
    assert empty.pnl == 0.0


# ---------------------------------------------------------------------------
# 3. Schedule helpers
# ---------------------------------------------------------------------------


def test_parse_hhmm_and_next_daily():
    assert parse_hhmm("18:00").hour == 18
    with pytest.raises(ValueError):
        parse_hhmm("nonsense")
    early = datetime(2026, 10, 8, 4, 0, tzinfo=timezone.utc)
    assert next_daily_utc(early, "18:00") == datetime(2026, 10, 8, 18, 0, tzinfo=timezone.utc)
    late = datetime(2026, 10, 8, 19, 0, tzinfo=timezone.utc)
    assert next_daily_utc(late, "18:00") == datetime(2026, 10, 9, 18, 0, tzinfo=timezone.utc)


def test_next_weekly_is_sunday_1200():
    # 2026-10-08 is a Thursday -> next Sunday is 2026-10-11 12:00 UTC
    assert datetime(2026, 10, 8, tzinfo=timezone.utc).weekday() == 3
    assert next_weekly_utc(NOW, "12:00", "SUN") == datetime(
        2026, 10, 11, 12, 0, tzinfo=timezone.utc
    )
    with pytest.raises(ValueError):
        next_weekly_utc(NOW, "12:00", "FUNDAY")


# ---------------------------------------------------------------------------
# 4. Candle hygiene (never trade on a half-formed or stale bar)
# ---------------------------------------------------------------------------


def test_drop_unclosed_candles_removes_forming_bar():
    raw = [
        _bar(datetime(2026, 10, 6, tzinfo=timezone.utc), 85000.0),
        _bar(datetime(2026, 10, 7, tzinfo=timezone.utc), 85500.0),
        _bar(datetime(2026, 10, 8, tzinfo=timezone.utc), 82847.0),  # forming
    ]
    closed = drop_unclosed_candles(raw, "1d", NOW)
    assert [c.timestamp.day for c in closed] == [6, 7]
    assert newest_bar_age_seconds(closed, "1d", NOW) == pytest.approx(
        (NOW - datetime(2026, 10, 8, tzinfo=timezone.utc)).total_seconds()
    )


def test_candle_staleness_and_cache_refusal():
    old = [_bar(datetime(2026, 10, 4, tzinfo=timezone.utc), 85000.0)]
    stale, why = candles_are_stale(old, "1d", NOW, 2.0)
    assert stale is True and "stale candles" in why

    cached = [_bar(datetime(2026, 10, 7, tzinfo=timezone.utc), 85500.0, source="cache")]
    stale, why = candles_are_stale(cached, "1d", NOW, 2.0)
    assert stale is True and "cache" in why

    ok, why = candles_are_stale(
        drop_unclosed_candles(
            [_bar(datetime(2026, 10, 7, tzinfo=timezone.utc), 85500.0)], "1d", NOW
        ),
        "1d",
        NOW,
        2.0,
    )
    assert ok is False and why == ""


def test_select_live_rest_base_prefers_a_source_that_serves_both():
    probes = [
        {"name": "mainnet", "rest_base": "https://api.binance.com",
         "ticker_ok": False, "candles_ok": False},
        {"name": "coingecko", "rest_base": "coingecko",
         "ticker_ok": True, "candles_ok": False},
        {"name": "mirror", "rest_base": "https://data-api.binance.vision",
         "ticker_ok": True, "candles_ok": True},
    ]
    assert select_live_rest_base(probes) == "https://data-api.binance.vision"
    assert select_live_rest_base(probes[:2]) is None


# ---------------------------------------------------------------------------
# 5. Signal stamping
# ---------------------------------------------------------------------------


def test_prepare_signal_stamps_user_size_and_stop():
    sig = Signal(side=Side.BUY, price=80000.0, stop_loss=78000.0)
    out = prepare_signal(
        sig,
        symbol="BTCUSDT",
        bar_timestamp=NOW,
        price=80000.0,
        trade_amount=100.0,
        stop_loss_pct=0.02,
    )
    assert out.amount == 100.0              # the owner's explicit size
    assert out.stop_loss == 78000.0         # strategy stop kept
    assert out.client_order_id and out.client_order_id.startswith("paper-BTCUSDT-")
    assert out.metadata["paper_run"] is True

    bare = prepare_signal(
        Signal(side=Side.BUY),
        symbol="BTCUSDT",
        bar_timestamp=NOW,
        price=80000.0,
        trade_amount=250.0,
        stop_loss_pct=0.02,
    )
    assert bare.amount == 250.0
    assert bare.stop_loss == pytest.approx(80000.0 * 0.98)

    hold = prepare_signal(
        Signal(side=Side.HOLD),
        symbol="BTCUSDT", bar_timestamp=NOW, price=1.0,
        trade_amount=10.0, stop_loss_pct=0.02,
    )
    assert hold.side == Side.HOLD and hold.amount is None


# ---------------------------------------------------------------------------
# 6. Run-script smoke (offline, synthetic data)
# ---------------------------------------------------------------------------


def _breakout_source(price: float = 90000.0) -> FakeSource:
    """A 1d history whose newest closed bar breaks the Donchian(15) high."""
    start = datetime(2026, 9, 18, tzinfo=timezone.utc)
    closes = [80000.0 + i * 10 for i in range(20)]  # flat-ish run-up
    candles = _daily_history(closes, start=start)
    candles[-1] = _bar(candles[-1].timestamp, price, high=price, low=price)
    ticker = Ticker(
        symbol="BTCUSDT", price=price, timestamp=NOW, source="binance", cached=False
    )
    return FakeSource(ticker, candles)


async def test_run_once_smoke_buy_path_offline():
    settings = _settings()
    store = NullProgressionStore()
    machine = ProgressionMachine(settings=settings, store=store)
    source = _breakout_source()
    run = PaperRun(
        settings,
        source=source,
        strategy=get_strategy("donchian_1d", timeframe="1d", trade_amount=100.0),
        store=store,
        machine=machine,
        now=lambda: NOW,
    )
    await run.start()
    outcome = await run.tick()
    assert outcome.executed is True
    assert outcome.side == "BUY"
    assert run.broker.btc_holdings > 0
    assert run.broker.usdt_balance < settings.paper_starting_usdt
    assert len(run.tracker.fills) == 1

    # a second poll on the same bar must NOT re-enter
    again = await run.tick()
    assert again.action == "NO_NEW_BAR"
    assert again.executed is False
    await run.stop()


async def test_run_evidence_and_paper_days_recorded():
    settings = _settings()
    store = NullProgressionStore()
    machine = ProgressionMachine(settings=settings, store=store)
    started = NOW - timedelta(days=31)
    run = PaperRun(
        settings,
        source=_breakout_source(),
        strategy=get_strategy("donchian_1d", timeframe="1d", trade_amount=100.0),
        store=store,
        machine=machine,
        now=lambda: NOW,
        paper_start=started,
    )
    await run.start()
    await run.tick()
    evidence = machine.evidence
    assert evidence.paper_days == pytest.approx(31.0, abs=0.01)
    assert evidence.paper_trades == 1
    assert any(e.get("event_type") == "paper_run_start" for e in store.events)
    # the >=30-day request is attempted and never raises, whatever the machine decides
    await run.maybe_request_extended_paper()


async def test_report_send_is_fail_safe_without_telegram():
    settings = _settings()
    run = PaperRun(
        settings,
        source=_breakout_source(),
        strategy=get_strategy("donchian_1d", timeframe="1d", trade_amount=100.0),
        store=NullProgressionStore(),
        notifier=TelegramNotifier(settings),  # disabled settings
        now=lambda: NOW,
    )
    await run.start()
    record = await run.send_report("daily")
    assert record["delivered"] is False          # nothing was configured
    assert "Paper Report" in record["text"]      # ...but the report is complete


async def test_stale_candles_refuse_trade():
    settings = _settings()
    stale = _daily_history([80000.0] * 20, start=datetime(2026, 7, 1, tzinfo=timezone.utc))
    ticker = Ticker(symbol="BTCUSDT", price=80000.0, timestamp=NOW,
                    source="binance", cached=False)
    run = PaperRun(
        settings,
        source=FakeSource(ticker, stale),
        strategy=get_strategy("donchian_1d", timeframe="1d", trade_amount=100.0),
        store=NullProgressionStore(),
        now=lambda: NOW,
    )
    await run.start()
    outcome = await run.tick()
    assert outcome.action == "DATA_UNAVAILABLE"
    assert outcome.executed is False
    assert run.broker.btc_holdings == 0.0


async def test_run_forever_shuts_down_cleanly_and_never_dies():
    settings = _settings()
    store = NullProgressionStore()
    run = PaperRun(
        settings,
        source=_breakout_source(),
        strategy=get_strategy("donchian_1d", timeframe="1d", trade_amount=100.0),
        store=store,
        now=lambda: NOW,
    )
    stop = asyncio.Event()
    await run.run_forever(poll_seconds=0.01, stop=stop, max_iterations=2)
    assert any(e.get("event_type") == "paper_run_stop" for e in store.events)
    assert run.outcomes  # at least one poll happened


async def test_iteration_errors_do_not_kill_the_loop():
    class ExplodingSource(FakeSource):
        async def get_ticker(self, symbol: str = "BTCUSDT") -> Ticker:
            raise RuntimeError("boom")

    settings = _settings()
    run = PaperRun(
        settings,
        source=ExplodingSource(
            Ticker(symbol="BTCUSDT", price=1.0, timestamp=NOW, source="fake"), []
        ),
        strategy=get_strategy("donchian_1d", timeframe="1d", trade_amount=100.0),
        store=NullProgressionStore(),
        now=lambda: NOW,
    )
    stop = asyncio.Event()
    await run.run_forever(
        poll_seconds=0.01, stop=stop, max_iterations=2, report_enabled=False
    )
    assert len(run.outcomes) >= 1  # the loop survived and recorded the failure


def test_run_module_has_no_live_order_path():
    from halaltrade.progression.state_machine import LIVE_EXECUTION_IMPLEMENTED

    assert LIVE_EXECUTION_IMPLEMENTED is False
    assert run_mod.DEFAULT_LIVE_REST_BASE.endswith("binance.vision")
