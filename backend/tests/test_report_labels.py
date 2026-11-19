"""The written check: no field of a paper report can mislabel either run.

Two paper runs (A: ``donchian_1d`` on ``1d``, B: ``donchian_breakout`` on ``4h``)
post to one Telegram chat and differ only by ``--run-label``. This module walks
every field ``format_report`` renders and pins the guarantee that makes it
truthful:

* **mode** — always ``PAPER (simulated, live off)``. The report is produced by
  the paper runner; it is never allowed to claim BACKTEST or LIVE as its mode.
* **progression** — the ladder's own position, printed separately as
  ``progression: <stage>``. Reporting reads it and never advances it: a fresh
  database legitimately sits at BACKTEST and must stay there.
* **label / header** — Daily vs Weekly comes from the forced label
  (``format_daily_report`` / ``format_weekly_report``), so a weekly period can
  never carry a daily header. ``send_report`` rejects any other kind rather than
  borrowing one of the two headers.
* **run label** — rendered in the header only, and only when set; an unlabelled
  report carries no brackets at all and is byte-identical whether the label is
  absent or empty.
* **symbol, strategy, timeframe** — come from the run's own configuration, so
  the identity line names the run even if its label is wrong, stale or empty.
* **equity / period P&L / trades / closed / wins / losses / win rate /
  realized P&L / profit factor / max drawdown / fees / open position** — every
  one is rendered straight from :class:`PaperStats` (computed by
  ``compute_stats`` from simulated fills and the equity curve), never
  re-estimated in the formatter. Nothing is rendered for a value the stats do
  not hold.
* **paper day / window days** — the day count is only ever printed as
  ``paper day N of W`` when ``N <= W``; a run that outlives its declared
  reporting window says so in words instead of printing "day 400 of 30".
* **market data** — printed only when a source was actually observed, and it is
  the source string the run recorded, not a guess.
* **disclaimer** — always last, always present: simulated results, not a profit
  guarantee, no implication that live trading is on.

Everything here is offline and deterministic (pure formatters + one in-memory
run with a null progression store).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

from halaltrade.config import Settings
from halaltrade.notifications.reports import (
    PROFIT_FACTOR_CAP,
    FillRecord,
    PaperStats,
    PerformanceTracker,
    format_daily_report,
    format_report,
    format_weekly_report,
)
from halaltrade.progression import NullProgressionStore, ProgressionStage
from halaltrade.strategies import get_strategy

from run_paper_trading import PaperRun, machine_stage

NOW = datetime(2026, 11, 15, 18, 0, tzinfo=timezone.utc)
TELEGRAM_OFF = dict(
    telegram_enabled=False,
    telegram_bot_token="",
    telegram_chat_id="",
    telegram_alert_daily_pnl=False,
    telegram_alert_strategy_change=False,
    telegram_alert_emergency_stop=False,
)


def _stats(**over) -> PaperStats:
    """A complete paper report's worth of stats, with the real field defaults."""
    base = dict(
        label="DAILY",
        period_end=NOW,
        starting_equity=1000.0,
        ending_equity=1000.0,
        trades=0,
        closed_trades=0,
        wins=0,
        losses=0,
        realized_pnl=0.0,
        fees_paid=0.0,
        profit_factor=0.0,
        period_max_drawdown_pct=0.0,
        open_position_qty=0.0,
        period_start=NOW - timedelta(days=1),
        symbol="BTCUSDT",
        stage="BACKTEST",
        paper_days=3.4,
        data_source="binance",
        strategy="donchian_1d",
        timeframe="1d",
        run_label="1D-CH15",
        window_days=548,
    )
    base.update(over)
    return PaperStats(**base)


def _settings(**over) -> Settings:
    base = dict(
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


def _run(*, strategy_name="donchian_1d", timeframe="1d", run_label="1D-CH15",
         window_days=548) -> PaperRun:
    return PaperRun(
        _settings(),
        source=None,  # reports never touch market data
        strategy=get_strategy(strategy_name, timeframe=timeframe, trade_amount=100.0),
        store=NullProgressionStore(),
        now=lambda: NOW,
        paper_start=NOW - timedelta(days=12),
        timeframe=timeframe,
        window_days=window_days,
        run_label=run_label,
        strategy_name=strategy_name,
    )


def _line(text: str, prefix: str) -> str:
    return next(l for l in text.splitlines() if l.startswith(prefix))


# ---------------------------------------------------------------------------
# 1. The mode line: PAPER, with the ladder position kept separate and honest
# ---------------------------------------------------------------------------


def test_mode_line_says_paper_and_never_claims_backtest_as_its_mode():
    text = format_report(_stats())
    mode = _line(text, "Mode: ")
    assert mode.startswith("Mode: PAPER (simulated, live off)")
    assert "progression: BACKTEST" in mode
    # the old, misleading rendering is gone for good
    assert "Stage: BACKTEST" not in text
    # BACKTEST may appear only as the progression position, never as the mode
    assert "Mode: BACKTEST" not in text
    assert "PAPER" in mode.split("progression:")[0]


def test_progression_position_is_printed_whenever_the_ladder_reports_one():
    for stage in ("BACKTEST", "PAPER", "EXTENDED_PAPER_30D", "SMALL_LIVE"):
        mode = _line(format_report(_stats(stage=stage)), "Mode: ")
        assert mode.startswith("Mode: PAPER (simulated, live off)")
        assert f"progression: {stage}" in mode

    # no stage recorded => no progression claim at all (never invented)
    mode = _line(format_report(_stats(stage="")), "Mode: ")
    assert "progression:" not in mode
    assert mode.startswith("Mode: PAPER (simulated, live off)")


async def test_reporting_never_advances_the_progression_ladder():
    run = _run()
    await run.start()
    assert machine_stage(run.machine) == ProgressionStage.BACKTEST.value
    record = await run.send_report("daily")
    assert "progression: BACKTEST" in record["text"]
    assert record["stats"]["stage"] == "BACKTEST"
    # the gate is untouched by rendering a report
    assert machine_stage(run.machine) == ProgressionStage.BACKTEST.value
    await run.stop()


# ---------------------------------------------------------------------------
# 2. Identity: symbol, strategy, timeframe, label
# ---------------------------------------------------------------------------


def test_identity_line_names_the_strategy_and_timeframe_of_each_run():
    one_d = format_report(_stats(strategy="donchian_1d", timeframe="1d"))
    four_h = format_report(
        _stats(strategy="donchian_breakout", timeframe="4h", run_label="4H-CH20")
    )
    assert "BTCUSDT — strategy donchian_1d @ 1d — simulated money" in one_d
    assert "BTCUSDT — strategy donchian_breakout @ 4h — simulated money" in four_h
    assert _line(one_d, "BTCUSDT") != _line(four_h, "BTCUSDT")


def test_identity_survives_a_wrong_or_empty_label():
    # same (wrong) label on both runs: the identity line still separates them
    a = format_report(_stats(run_label=""))
    b = format_report(_stats(run_label="", strategy="donchian_breakout", timeframe="4h"))
    assert "strategy donchian_1d @ 1d" in a
    assert "strategy donchian_breakout @ 4h" in b
    assert _line(a, "BTCUSDT") != _line(b, "BTCUSDT")


def test_identity_line_degrades_without_stray_separators():
    plain = format_report(_stats(strategy="", timeframe="", run_label=""))
    assert "BTCUSDT — simulated money (no live order path)" in plain
    assert "strategy" not in plain and "@" not in plain
    # partial identity is still honest: never an invented timeframe
    only_strategy = _line(format_report(_stats(timeframe="")), "BTCUSDT")
    assert only_strategy == "BTCUSDT — strategy donchian_1d — simulated money (no live order path)"
    only_tf = _line(format_report(_stats(strategy="")), "BTCUSDT")
    assert only_tf == "BTCUSDT — timeframe 1d — simulated money (no live order path)"


def test_empty_run_label_renders_exactly_like_an_absent_label():
    labelled = format_report(_stats())
    empty = format_report(_stats(run_label=""))
    assert "[" not in empty and "]" not in empty
    # the only difference is the header suffix; every other line is identical
    assert empty.splitlines()[1:] == labelled.splitlines()[1:]
    assert empty.splitlines()[0] == labelled.splitlines()[0].replace(" [1D-CH15]", "")
    assert empty.splitlines()[0].startswith("\U0001F4C5 Daily Paper Report — ")


# ---------------------------------------------------------------------------
# 3. Daily vs weekly headers
# ---------------------------------------------------------------------------


def test_daily_and_weekly_headers_cannot_be_swapped():
    stats = _stats(label="WEEKLY")
    assert format_daily_report(stats).splitlines()[0].startswith("\U0001F4C5 Daily Paper Report")
    assert format_weekly_report(stats).splitlines()[0].startswith("\U0001F5D3 Weekly Paper Report")
    # a daily-labelled stats cannot produce a weekly header and vice versa
    assert "Weekly" not in format_daily_report(_stats(label="DAILY")).splitlines()[0]
    assert "Daily" not in format_weekly_report(_stats(label="WEEKLY")).splitlines()[0]


async def test_send_report_refuses_a_kind_it_cannot_label_honestly():
    run = _run()
    await run.start()
    with pytest.raises(ValueError):
        await run.send_report("monthly")
    daily = await run.send_report("daily")
    weekly = await run.send_report("weekly")
    assert "Daily Paper Report" in daily["text"].splitlines()[0]
    assert "Weekly Paper Report" in weekly["text"].splitlines()[0]
    assert daily["kind"] == "DAILY" and weekly["kind"] == "WEEKLY"
    await run.stop()


# ---------------------------------------------------------------------------
# 4. Paper day vs window days
# ---------------------------------------------------------------------------


def test_paper_day_never_prints_a_day_count_past_its_window():
    inside = _line(format_report(_stats(paper_days=3.4, window_days=548)), "Mode: ")
    assert inside.endswith("— paper day 3.4 of 548")

    boundary = _line(format_report(_stats(paper_days=548.0, window_days=548)), "Mode: ")
    assert boundary.endswith("— paper day 548.0 of 548")

    beyond = format_report(_stats(paper_days=400.0, window_days=30))
    mode = _line(beyond, "Mode: ")
    assert "of 30" not in mode
    assert "beyond the declared 30-day reporting window" in mode
    assert not re.search(r"paper day (\d+(?:\.\d+)?) of (\d+)", mode)

    unknown = _line(format_report(_stats(paper_days=12.0, window_days=0)), "Mode: ")
    assert "no reporting window declared" in unknown


def test_paper_day_line_is_omitted_when_there_is_no_day_count():
    mode = _line(format_report(_stats(paper_days=0.0)), "Mode: ")
    assert "paper day" not in mode


# ---------------------------------------------------------------------------
# 5. Every numeric field is rendered from the stats, not re-derived
# ---------------------------------------------------------------------------


def test_every_number_in_the_report_comes_from_the_stats():
    tracker = PerformanceTracker(1000.0)
    tracker.record_equity(1000.0, NOW - timedelta(hours=6))
    tracker.record_equity(1012.34, NOW)
    tracker._fills.append(
        FillRecord(
            timestamp=NOW - timedelta(hours=2),
            side="SELL",
            quantity=0.001,
            price=85000.0,
            notional=85.0,
            fee=0.05,
            realized_pnl=12.34,
        )
    )
    stats = tracker.stats(
        label="DAILY",
        period_start=NOW - timedelta(days=1),
        open_position_qty=0.0,
        stage="BACKTEST",
        paper_days=3.4,
        data_source="binance",
        strategy="donchian_1d",
        timeframe="1d",
        run_label="1D-CH15",
        window_days=548,
    )
    text = format_report(stats)
    assert f"Equity: $1,000.00 -> $1,012.34 ({stats.pnl_pct:+.2f}%)" in text
    assert f"Period P&L: {stats.pnl:+,.2f} USDT" in text
    assert (
        f"Trades: {stats.trades} (closed: {stats.closed_trades}; "
        f"wins: {stats.wins}, losses: {stats.losses}, "
        f"win rate: {stats.win_rate_pct:.1f}%)"
    ) in text
    assert f"Realized P&L: {stats.realized_pnl:+,.2f} USDT" in text
    # one win and no loss => the capped wording, stated plainly
    assert "Profit factor: >= 1,000,000 (no losing trades yet)" in text
    assert f"Max drawdown (period): -{stats.period_max_drawdown_pct:.2f}%" in text
    assert f"Fees paid: ${stats.fees_paid:,.2f}" in text
    assert f"Open position: {stats.open_position_qty:.8f} BTC" in text
    assert "Market data: binance (freshness checked per bar)" in text
    assert text.splitlines()[-1] == "Simulated paper results — not a profit guarantee."


def test_no_losing_trades_is_stated_as_a_capped_profit_factor():
    text = format_report(_stats(profit_factor=PROFIT_FACTOR_CAP))
    assert "no losing trades yet" in text


def test_market_data_line_is_absent_when_no_source_was_observed():
    text = format_report(_stats(data_source=""))
    assert "Market data:" not in text
    assert "unknown" not in text


def test_disclaimer_is_always_the_last_line():
    for stats in (
        _stats(),
        _stats(stage="", data_source="", run_label="", strategy="", timeframe=""),
        _stats(label="WEEKLY", run_label="4H-CH20", strategy="donchian_breakout",
               timeframe="4h"),
    ):
        assert format_report(stats).splitlines()[-1] == (
            "Simulated paper results — not a profit guarantee."
        )
