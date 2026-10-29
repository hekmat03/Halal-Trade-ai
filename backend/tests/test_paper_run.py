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
import json
import os
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

# --- Environment isolation (test-only) -------------------------------------
# ``Settings`` is configured with ``env_prefix="HALAL_"`` and ``env_file=".env"``,
# so its values depend on the ambient environment: a developer's local
# ``backend/.env`` (the live paper-run config sets ``HALAL_TELEGRAM_ENABLED=true``
# plus a real bot token/chat id) or exported ``HALAL_*`` shell variables leak into
# these tests. That is what made the "Telegram defaults are OFF" assertions fail in
# a working tree that carries a live ``.env``.
#
# Fix (tests only; production code is untouched): every ``Settings`` built in this
# module pins the Telegram block explicitly. pydantic-settings ranks init kwargs
# above env vars and above ``.env``, so such an object is hermetic regardless of
# what the ambient environment holds.
TELEGRAM_OFF = dict(
    telegram_enabled=False,
    telegram_bot_token="",
    telegram_chat_id="",
    telegram_alert_daily_pnl=False,
    telegram_alert_strategy_change=False,
    telegram_alert_emergency_stop=False,
    telegram_alert_risk_rejection=False,
    telegram_alert_system_error=False,
)


@pytest.fixture
def hermetic_env(monkeypatch):
    """Strip ambient ``HALAL_*`` vars and ignore any local ``.env`` file.

    Lets a test assert the *shipped* defaults of :class:`Settings` without the
    local ``.env`` / shell environment interfering. ``monkeypatch`` restores the
    environment afterwards.
    """
    for key in [k for k in os.environ if k.startswith("HALAL_")]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    yield monkeypatch




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
        # Telegram pinned OFF: keeps every PaperRun test hermetic (a local .env
        # must never make the notifier try to reach the network) and keeps the
        # "fail-safe without Telegram" path genuine.
        **TELEGRAM_OFF,
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


TELEGRAM_ALERT_FIELDS = (
    "telegram_alert_daily_pnl",
    "telegram_alert_strategy_change",
    "telegram_alert_emergency_stop",
    "telegram_alert_risk_rejection",
    "telegram_alert_system_error",
)


def test_telegram_settings_default_to_off(hermetic_env):
    # Hermetic: no HALAL_* env vars and no .env file, so this is the shipped default.
    s = Settings()
    assert s.telegram_enabled is False
    assert s.telegram_bot_token == ""
    assert s.telegram_chat_id == ""
    for field in TELEGRAM_ALERT_FIELDS:
        assert getattr(s, field) is False, field

    # Belt and braces: init kwargs outrank env vars and .env, so with Telegram
    # switched ON in the ambient environment an explicitly pinned OFF block still
    # comes back OFF. This is the property the three hermetic tests rely on.
    hermetic_env.setenv("HALAL_TELEGRAM_ENABLED", "true")
    hermetic_env.setenv("HALAL_TELEGRAM_BOT_TOKEN", "ambient-token")
    hermetic_env.setenv("HALAL_TELEGRAM_CHAT_ID", "ambient-chat")
    hermetic_env.setenv("HALAL_TELEGRAM_ALERT_DAILY_PNL", "true")
    pinned = Settings(**TELEGRAM_OFF)
    assert pinned.telegram_enabled is False
    assert pinned.telegram_bot_token == ""
    assert pinned.telegram_chat_id == ""
    for field in TELEGRAM_ALERT_FIELDS:
        assert getattr(pinned, field) is False, field


async def test_notifier_is_fail_safe_with_empty_settings(hermetic_env):
    notifier = TelegramNotifier(Settings())
    assert await notifier.send(AlertType.DAILY_PNL, "hello") is False
    # enabled but no token/chat => still False, still no exception
    # (token/chat pinned empty so a local .env can never trigger a real send)
    partial = Settings(telegram_enabled=True, telegram_alert_daily_pnl=True,
                       telegram_bot_token="", telegram_chat_id="")
    assert partial.telegram_bot_token == ""
    assert partial.telegram_chat_id == ""
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


async def test_report_send_is_fail_safe_without_telegram(hermetic_env):
    settings = _settings()  # Telegram pinned OFF (see TELEGRAM_OFF)
    assert settings.telegram_enabled is False
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
# ---------------------------------------------------------------------------
# 9. paper_start clock anchor survives the JSON-encased audit detail (regression)
# ---------------------------------------------------------------------------
ANCHOR = datetime(2026, 10, 9, 6, 0, 27, 721304, tzinfo=timezone.utc)
ANCHOR_ISO = "2026-10-09T06:00:27.721304+00:00"
# What the DB-backed progression store hands back: `detail` persisted as a JSON
# document, so the stored string ends with `"}` right after the timestamp.
DB_DETAIL = '{"stage": "BACKTEST", "live_enabled": false, "note": "paper_start=' + ANCHOR_ISO + '"}'
PLAIN_DETAIL = f"paper_start={ANCHOR_ISO}"


class _TrailStore(NullProgressionStore):
    """A read-only audit trail: whatever events the test wants history() to show."""

    def __init__(self, events: list[dict]) -> None:
        super().__init__()
        self._trail = events

    def history(self, limit: int = 50) -> list[dict]:  # newest first, like the real store
        return list(self._trail)[:limit]


def _run_with_trail(detail: str, *, timestamp: str | None = None) -> PaperRun:
    event: dict = {"event_type": "paper_run_start", "detail": detail}
    if timestamp is not None:
        event["timestamp"] = timestamp
    store = _TrailStore([event])
    return PaperRun(
        _settings(),
        source=_breakout_source(),
        strategy=get_strategy("donchian_1d", timeframe="1d", trade_amount=100.0),
        store=store,
        machine=ProgressionMachine(settings=_settings(), store=store),
        now=lambda: NOW,
    )


@pytest.mark.parametrize(
    "detail",
    [DB_DETAIL, PLAIN_DETAIL],
    ids=["db-json-encased", "legacy-plain"],
)
def test_paper_start_regex_captures_only_the_iso_stamp(detail: str):
    match = run_mod._PAPER_START_RE.search(detail)
    assert match is not None
    assert match.group(1) == ANCHOR_ISO
    # the captured group must be directly parseable (this is what used to blow up)
    assert datetime.fromisoformat(match.group(1)) == ANCHOR


@pytest.mark.parametrize(
    "detail",
    [DB_DETAIL, PLAIN_DETAIL],
    ids=["db-json-encased", "legacy-plain"],
)
def test_load_or_create_paper_start_recovers_anchor_not_now(detail: str):
    run = _run_with_trail(detail)
    recovered = run._load_or_create_paper_start()
    assert recovered == ANCHOR
    assert recovered != NOW
    # and the restarted run measures its 30-day clock from the original anchor,
    # not from the restart moment (which is what the bug silently did)
    run.paper_start = recovered
    restart = ANCHOR + timedelta(days=31, hours=2)
    assert run.paper_days(now=restart) == pytest.approx(31.083, abs=0.01)
    assert run.paper_days(now=restart) != pytest.approx(0.0, abs=0.01)


def test_load_or_create_paper_start_skips_unreadable_stamp_for_an_older_good_one():
    store = _TrailStore(
        [
            {"event_type": "paper_run_resume", "detail": "paper_start=2026-10-09T06:00:27.721304"},
            {"event_type": "paper_run_resume", "detail": DB_DETAIL},
        ]
    )
    run = PaperRun(
        _settings(),
        source=_breakout_source(),
        strategy=get_strategy("donchian_1d", timeframe="1d", trade_amount=100.0),
        store=store,
        machine=ProgressionMachine(settings=_settings(), store=store),
        now=lambda: NOW,
    )
    assert run._load_or_create_paper_start() == ANCHOR


def test_load_or_create_paper_start_falls_back_to_now_when_nothing_parseable():
    store = _TrailStore(
        [
            {"event_type": "paper_run_start", "detail": '{"note": "no stamp here"}'},
            {"event_type": "stage_change", "detail": "paper_start=2026-10-09T06:00:27+00:00"},
        ]
    )
    run = PaperRun(
        _settings(),
        source=_breakout_source(),
        strategy=get_strategy("donchian_1d", timeframe="1d", trade_amount=100.0),
        store=store,
        machine=ProgressionMachine(settings=_settings(), store=store),
        now=lambda: NOW,
    )
    assert run._load_or_create_paper_start() == NOW


# ---------------------------------------------------------------------------
# 10. Report-scheduler survival across restarts (platform machine swaps)
# ---------------------------------------------------------------------------
# The platform has replaced this machine repeatedly, killing the run and
# restarting its clock forward. In-memory scheduler state meant the 18:00 UTC
# deadline was silently dropped; the state below is persisted, so a restart
# delivers exactly one missed daily + one missed weekly and never re-sends a
# slot it already reported. Everything here is offline (synthetic candles, fake
# notifier, tmp state file).
UTC = timezone.utc


class _Clock:
    """A settable clock: emulates a restart on a box whose clock jumped."""

    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def __call__(self) -> datetime:
        return self.moment

    def set(self, moment: datetime) -> None:
        self.moment = moment


class _CountingNotifier:
    """Records what would have gone to Telegram. No network."""

    def __init__(self) -> None:
        self.sent: list[tuple[object, str]] = []

    async def send(self, alert_type, text: str) -> bool:  # noqa: ANN001 - mirrors TelegramNotifier
        self.sent.append((alert_type, text))
        return True


def _survivor_run(state_path, clock, *, notifier=None) -> PaperRun:
    return PaperRun(
        _settings(),
        source=_breakout_source(),
        strategy=get_strategy("donchian_1d", timeframe="1d", trade_amount=100.0),
        store=NullProgressionStore(),
        notifier=notifier,
        now=clock,
        state_path=state_path,
    )


def _state_json(state_path) -> dict:
    return json.loads(state_path.read_text(encoding="utf-8"))


def _watermark(state_path, kind: str):
    stamp = _state_json(state_path)["watermarks"].get(kind)
    return datetime.fromisoformat(stamp) if stamp else None


def _kinds(run: PaperRun) -> list[str]:
    return [record["kind"] for record in run.report_log]


async def test_restart_after_deadline_delivers_exactly_one_catch_up_daily(tmp_path):
    """(1) Deadline passed while the run was down => one caught-up daily, marked."""
    state = tmp_path / "paper_run_state.json"
    clock = _Clock(datetime(2026, 10, 20, 12, 0, tzinfo=UTC))

    first_notifier = _CountingNotifier()
    first = _survivor_run(state, clock, notifier=first_notifier)
    await first.start()
    await first._maybe_send_reports()
    assert first_notifier.sent == []                      # 12:00 — nothing is due yet
    assert state.exists()                                  # ...but the baseline is on disk
    assert _watermark(state, "daily") == datetime(2026, 10, 19, 18, 0, tzinfo=UTC)

    # machine swap: the process dies at ~17:00 and the new box is already at 19:05
    clock.set(datetime(2026, 10, 20, 19, 5, tzinfo=UTC))
    second_notifier = _CountingNotifier()
    second = _survivor_run(state, clock, notifier=second_notifier)
    await second.start()
    await second._maybe_send_reports()

    assert len(second_notifier.sent) == 1                  # exactly one report
    assert _kinds(second) == ["DAILY"]
    assert "Paper Report" in second_notifier.sent[0][1]
    assert _watermark(state, "daily") == datetime(2026, 10, 20, 18, 0, tzinfo=UTC)
    # the catch-up slot is in the persisted key set too
    assert "daily:2026-10-20T18:00" in _state_json(state)["sent_keys"]["daily"]
    # and it is a *report of today's data*, produced by the normal report path
    assert second.report_log[0]["delivered"] is True


async def test_no_catch_up_when_slot_already_marked_across_restarts(tmp_path):
    """(2) A marked slot is never sent again — not even by a later restart."""
    state = tmp_path / "paper_run_state.json"
    clock = _Clock(datetime(2026, 10, 20, 12, 0, tzinfo=UTC))

    first = _survivor_run(state, clock, notifier=_CountingNotifier())
    await first.start()
    await first._maybe_send_reports()

    # 18:00 comes and goes *while the process is alive*: one normal report
    clock.set(datetime(2026, 10, 20, 18, 5, tzinfo=UTC))
    await first._maybe_send_reports()
    assert _kinds(first) == ["DAILY"]

    # restart 1: same slot, already marked => nothing
    clock.set(datetime(2026, 10, 20, 20, 0, tzinfo=UTC))
    second_notifier = _CountingNotifier()
    second = _survivor_run(state, clock, notifier=second_notifier)
    await second.start()
    await second._maybe_send_reports()
    assert second_notifier.sent == []
    assert second.report_log == []

    # restart 2: next morning, still before the next deadline => nothing
    clock.set(datetime(2026, 10, 21, 9, 0, tzinfo=UTC))
    third_notifier = _CountingNotifier()
    third = _survivor_run(state, clock, notifier=third_notifier)
    await third.start()
    await third._maybe_send_reports()
    assert third_notifier.sent == []
    # the persisted key set stayed stable across both restarts
    assert _state_json(state)["sent_keys"]["daily"].count("daily:2026-10-20T18:00") == 1


async def test_multi_day_clock_jump_catches_up_at_most_one_each(tmp_path):
    """(3) A huge forward jump => one daily + one weekly, never a backfill burst."""
    state = tmp_path / "paper_run_state.json"
    # Sunday 2026-10-18 09:00 UTC: last daily slot 10-17 18:00, last weekly 10-11 12:00
    clock = _Clock(datetime(2026, 10, 18, 9, 0, tzinfo=UTC))
    before = _survivor_run(state, clock, notifier=_CountingNotifier())
    await before.start()
    await before._maybe_send_reports()
    assert before.report_log == []
    assert _watermark(state, "daily") == datetime(2026, 10, 17, 18, 0, tzinfo=UTC)
    assert _watermark(state, "weekly") == datetime(2026, 10, 11, 12, 0, tzinfo=UTC)

    # the box comes back eleven days later (Oct 16 -> Oct 29 is what really happened)
    clock.set(datetime(2026, 10, 29, 6, 0, tzinfo=UTC))
    notifier = _CountingNotifier()
    after = _survivor_run(state, clock, notifier=notifier)
    await after.start()
    await after._maybe_send_reports()

    assert len(notifier.sent) == 2                          # bounded: 1 daily + 1 weekly
    assert sorted(_kinds(after)) == ["DAILY", "WEEKLY"]
    assert _watermark(state, "daily") == datetime(2026, 10, 28, 18, 0, tzinfo=UTC)
    assert _watermark(state, "weekly") == datetime(2026, 10, 25, 12, 0, tzinfo=UTC)

    # a further check in the same process sends nothing more
    await after._maybe_send_reports()
    assert len(notifier.sent) == 2
    # and a restart right afterwards sends nothing either (no backfill spam)
    restart_notifier = _CountingNotifier()
    clock.set(datetime(2026, 10, 29, 9, 0, tzinfo=UTC))
    again = _survivor_run(state, clock, notifier=restart_notifier)
    await again.start()
    await again._maybe_send_reports()
    assert restart_notifier.sent == []


async def test_missing_state_file_is_a_clean_start(tmp_path):
    """(4a) No state file => no crash, baseline seeded, file created, no send."""
    state = tmp_path / "paper_run_state.json"
    assert not state.exists()
    clock = _Clock(datetime(2026, 10, 20, 12, 0, tzinfo=UTC))
    notifier = _CountingNotifier()
    run = _survivor_run(state, clock, notifier=notifier)
    await run.start()
    await run._maybe_send_reports()
    assert notifier.sent == []
    assert state.exists()
    payload = _state_json(state)
    assert payload["version"] == 1
    assert payload["restarts"] == 1
    assert payload["watermarks"]["daily"] == "2026-10-19T18:00:00+00:00"


@pytest.mark.parametrize(
    "garbage",
    [
        "{\"watermarks\": {\"daily\": ",           # truncated (crash mid-write)
        "not json at all",
        "[]",                                       # valid JSON, wrong shape
        "{}",                                       # valid JSON, no fields
        json.dumps({"watermarks": "nope", "sent_keys": 5, "restarts": "many"}),
    ],
    ids=["truncated", "plain-text", "json-list", "empty-object", "wrong-types"],
)
async def test_corrupt_state_file_starts_empty_and_never_raises(tmp_path, garbage):
    """(4b) Corrupt/unreadable state => warning + empty state; the run continues."""
    state = tmp_path / "paper_run_state.json"
    state.write_text(garbage, encoding="utf-8")
    clock = _Clock(datetime(2026, 10, 20, 12, 0, tzinfo=UTC))
    notifier = _CountingNotifier()
    run = _survivor_run(state, clock, notifier=notifier)
    await run.start()                     # must not raise
    await run._maybe_send_reports()       # must not raise, must not spam
    assert notifier.sent == []
    payload = _state_json(state)          # the file was rewritten in a valid form
    assert payload["watermarks"]["daily"] == "2026-10-19T18:00:00+00:00"
    assert payload["version"] == 1


async def test_clock_jumping_backward_does_not_resend(tmp_path):
    """(5) Watermark-based dedupe also holds when the clock goes backwards."""
    state = tmp_path / "paper_run_state.json"
    clock = _Clock(datetime(2026, 10, 20, 12, 0, tzinfo=UTC))
    seeded = _survivor_run(state, clock, notifier=_CountingNotifier())
    await seeded.start()                       # baseline: 10-19 18:00 / 10-18 12:00
    await seeded._maybe_send_reports()
    assert seeded.report_log == []

    clock.set(datetime(2026, 10, 29, 20, 0, tzinfo=UTC))
    await seeded._maybe_send_reports()
    # 20:00 on the 29th: the 29th's 18:00 daily and the 25th's weekly are due
    assert sorted(_kinds(seeded)) == ["DAILY", "WEEKLY"]
    assert _watermark(state, "daily") == datetime(2026, 10, 29, 18, 0, tzinfo=UTC)
    assert _watermark(state, "weekly") == datetime(2026, 10, 25, 12, 0, tzinfo=UTC)

    # the new box comes up with a clock that is a week BEHIND
    clock.set(datetime(2026, 10, 22, 12, 0, tzinfo=UTC))
    backward_notifier = _CountingNotifier()
    backward = _survivor_run(state, clock, notifier=backward_notifier)
    await backward.start()
    await backward._maybe_send_reports()
    assert backward_notifier.sent == []            # nothing is "new" going backwards
    assert backward.report_log == []

    # the clock catches up again to a slot that was already reported: still silent
    clock.set(datetime(2026, 10, 29, 21, 0, tzinfo=UTC))
    await backward._maybe_send_reports()
    assert backward_notifier.sent == []
    assert _watermark(state, "daily") == datetime(2026, 10, 29, 18, 0, tzinfo=UTC)

    # ...but a genuinely new slot is still delivered afterwards
    clock.set(datetime(2026, 10, 30, 19, 0, tzinfo=UTC))
    await backward._maybe_send_reports()
    assert _kinds(backward) == ["DAILY"]
    assert _watermark(state, "daily") == datetime(2026, 10, 30, 18, 0, tzinfo=UTC)


async def test_normal_in_process_scheduling_is_unchanged(tmp_path):
    """(6) 18:00 UTC daily and SUN 12:00 UTC weekly still fire exactly once each."""
    state = tmp_path / "paper_run_state.json"
    clock = _Clock(datetime(2026, 10, 20, 12, 0, tzinfo=UTC))
    notifier = _CountingNotifier()
    run = _survivor_run(state, clock, notifier=notifier)
    await run.start()

    await run._maybe_send_reports()                       # 12:00 — nothing due
    assert _kinds(run) == []

    clock.set(datetime(2026, 10, 20, 18, 5, tzinfo=UTC))  # 18:05 — daily
    await run._maybe_send_reports()
    assert _kinds(run) == ["DAILY"]

    clock.set(datetime(2026, 10, 20, 23, 0, tzinfo=UTC))  # same slot, later poll
    await run._maybe_send_reports()
    assert _kinds(run) == ["DAILY"]

    clock.set(datetime(2026, 10, 21, 18, 5, tzinfo=UTC))  # next day's daily
    await run._maybe_send_reports()
    assert _kinds(run) == ["DAILY", "DAILY"]

    clock.set(datetime(2026, 10, 25, 12, 5, tzinfo=UTC))  # Sunday 12:05 — weekly (+that day's daily)
    await run._maybe_send_reports()
    assert _kinds(run) == ["DAILY", "DAILY", "DAILY", "WEEKLY"]
    assert _watermark(state, "weekly") == datetime(2026, 10, 25, 12, 0, tzinfo=UTC)

    clock.set(datetime(2026, 10, 25, 18, 5, tzinfo=UTC))  # same Sunday, daily due again
    await run._maybe_send_reports()
    assert _kinds(run) == ["DAILY", "DAILY", "DAILY", "WEEKLY", "DAILY"]


def test_state_writes_are_atomic_and_leave_no_temp_file(tmp_path):
    store = run_mod.ReportStateStore(tmp_path / "paper_run_state.json")
    state = {
        "watermarks": {kind: datetime(2026, 10, 20, 18, 0, tzinfo=UTC) for kind in run_mod.REPORT_KINDS},
        "sent_keys": {"daily": ["daily:2026-10-20T18:00"], "weekly": []},
        "last_report": {"kind": "DAILY"},
        "restarts": 3,
    }
    assert store.save(state, now=datetime(2026, 10, 20, 18, 1, tzinfo=UTC)) is True
    assert not list(tmp_path.glob("*.tmp"))                    # temp file was renamed away
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    assert payload["restarts"] == 3
    assert payload["sent_keys"]["daily"] == ["daily:2026-10-20T18:00"]
    # a stale temp file from a previous crash is ignored by load()
    (tmp_path / "paper_run_state.json.tmp").write_text("{ half written", encoding="utf-8")
    loaded = store.load()
    assert loaded["watermarks"]["daily"] == datetime(2026, 10, 20, 18, 0, tzinfo=UTC)
    assert loaded["found"] is True


def test_unwritable_state_path_is_fail_safe(tmp_path):
    """Persistence failures must degrade to "no state", never to a dead run."""
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("", encoding="utf-8")
    store = run_mod.ReportStateStore(blocker / "paper_run_state.json")
    assert store.save({"watermarks": {}, "sent_keys": {}, "last_report": {}, "restarts": 0}) is False
    assert store.load()["found"] is False
