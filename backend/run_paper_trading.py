#!/usr/bin/env python3
"""30-day PAPER-trading run on live market data — simulated money only.

What this script does
---------------------
Long-running asyncio loop that, on every poll:

1. reads a **live, non-cached** BTC/USDT ticker (stale/cached data => NO TRADE);
2. reads **closed** candles, dropping the still-forming bar, and refuses bars
   that are older than ``paper_candle_max_age_bars`` of age;
3. calls the configured strategy library strategy on the closed bars
   (default ``donchian_1d`` — the Delivery 6 research winner);
4. stamps the owner's explicit size (``--trade-usdt``) and a mandatory
   stop-loss on the signal, then routes it through the four-gate pipeline and
   the existing :class:`halaltrade.paper.broker.PaperBroker` fill path;
5. records progression evidence (paper days + trade P&L stats) so a later
   ``LiveReadinessCheck`` sees a real paper track record;
6. sends a daily (default 18:00 UTC) and a weekly (default Sunday 12:00 UTC)
   Telegram report when Telegram is configured.

Hard limits (unchanged from the rest of the repo)
-------------------------------------------------
* Spot, 1x, long-only. No futures/leverage/margin/shorting/riba.
* No live order path exists: ``LIVE_EXECUTION_IMPLEMENTED`` stays False and
  this script never touches a Binance account endpoint or an API secret.
* The user's explicit size is what gets traded — the strategy never sizes.
* Nothing is invented: if data is stale, cached or unavailable the run logs
  DATA UNAVAILABLE and takes NO TRADE.

Exact start commands and the env-var table live in ``docs/paper_run.md``.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import signal
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402

from halaltrade.config import Settings  # noqa: E402
from halaltrade.logging_config import *  # noqa: F401,E402  (sets up logging)
from halaltrade.marketdata import DataUnavailableError  # noqa: E402
from halaltrade.marketdata.binance import BinanceSource  # noqa: E402
from halaltrade.marketdata.coingecko import CoinGeckoSource  # noqa: E402
from halaltrade.marketdata.factory import build_default_source  # noqa: E402
from halaltrade.marketdata.models import Candle  # noqa: E402
from halaltrade.models import Side, Signal, utcnow  # noqa: E402
from halaltrade.notifications.reports import (  # noqa: E402
    PaperStats,
    PerformanceTracker,
    format_daily_report,
    format_weekly_report,
)
from halaltrade.notifications.telegram import AlertType, TelegramNotifier  # noqa: E402
from halaltrade.paper.broker import PaperBroker, PaperResult  # noqa: E402
from halaltrade.progression import (  # noqa: E402
    DbProgressionStore,
    NullProgressionStore,
    ProgressionMachine,
    ProgressionStage,
    StageEvidence,
)
from halaltrade.strategies import get_strategy, list_strategies  # noqa: E402

logger = logging.getLogger("halaltrade.paper_run")

#: Binance's public market-data mirror. The mainnet REST hosts
#: (api.binance.com, api1..api4) answer HTTP 451 from some regions/countries,
#: while ``data-api.binance.vision`` serves the *same* public klines/ticker
#: data and is the endpoint this repo's earlier scripts already used.
DEFAULT_LIVE_REST_BASE = "https://data-api.binance.vision"

BAR_SECONDS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "6h": 21600,
    "8h": 28800,
    "12h": 43200,
    "1d": 86400,
    "3d": 259200,
    "1w": 604800,
}

WEEKDAYS: dict[str, int] = {
    "MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6,
}

_PAPER_START_RE = re.compile(r"paper_start=(\S+)")


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested, no network)
# ---------------------------------------------------------------------------


def bar_seconds(timeframe: str) -> int:
    try:
        return BAR_SECONDS[timeframe]
    except KeyError:
        raise ValueError(
            f"unsupported timeframe {timeframe!r}; supported: {sorted(BAR_SECONDS)}"
        ) from None


def parse_hhmm(value: str) -> dtime:
    """Parse ``"HH:MM"`` (UTC) into a :class:`datetime.time`."""
    try:
        hour, minute = value.strip().split(":")
        parsed = dtime(int(hour), int(minute), tzinfo=timezone.utc)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid HH:MM time {value!r}") from exc
    return parsed


def _close_time(candle: Candle, timeframe: str) -> datetime:
    """Bar close time: Binance's own ``close_time`` when present, else derived."""
    raw = getattr(candle, "raw", None) or {}
    kline = raw.get("kline") if isinstance(raw, dict) else None
    if isinstance(kline, (list, tuple)) and len(kline) > 6:
        try:
            return datetime.fromtimestamp(float(kline[6]) / 1000.0, tz=timezone.utc)
        except (TypeError, ValueError):
            pass
    return candle.timestamp + timedelta(seconds=bar_seconds(timeframe))


def drop_unclosed_candles(
    candles: list[Candle], timeframe: str, now: datetime
) -> list[Candle]:
    """Return only bars whose close time has already passed.

    Binance's kline endpoint includes the still-forming bar. Trading a
    half-formed bar would be look-ahead bias, so it is dropped.
    """
    return [c for c in candles if _close_time(c, timeframe) <= now]


def newest_bar_age_seconds(
    candles: list[Candle], timeframe: str, now: datetime
) -> Optional[float]:
    """Seconds since the newest closed bar closed, or None when empty."""
    if not candles:
        return None
    return max(0.0, (now - _close_time(candles[-1], timeframe)).total_seconds())


def candles_are_stale(
    candles: list[Candle], timeframe: str, now: datetime, max_age_bars: float
) -> tuple[bool, str]:
    """Tradeability check for the candle feed (mirrors the ticker stale rule)."""
    if not candles:
        return True, "no closed candles available"
    if candles[-1].source == "cache":
        return True, "candles served from cache (not live) — not tradeable"
    age = newest_bar_age_seconds(candles, timeframe, now)
    limit = max_age_bars * bar_seconds(timeframe)
    if age is not None and age > limit:
        return True, (
            f"stale candles (newest closed bar is {age:.0f}s old, limit {limit:.0f}s)"
        )
    return False, ""


def next_daily_utc(now: datetime, at: str = "18:00") -> datetime:
    """Next occurrence of ``at`` (UTC HH:MM), strictly after ``now``."""
    today = now.astimezone(timezone.utc).replace(
        hour=parse_hhmm(at).hour, minute=parse_hhmm(at).minute, second=0, microsecond=0
    )
    if today <= now:
        today += timedelta(days=1)
    return today


def next_weekly_utc(now: datetime, at: str = "12:00", day: str = "SUN") -> datetime:
    """Next occurrence of ``day`` at ``at`` UTC, strictly after ``now``."""
    target = WEEKDAYS.get(day.strip().upper())
    if target is None:
        raise ValueError(f"unknown weekday {day!r}; use one of {sorted(WEEKDAYS)}")
    candidate = next_daily_utc(now, at)
    delta = (target - candidate.weekday()) % 7
    if delta == 0 and candidate <= now:
        delta = 7
    return candidate + timedelta(days=delta)


def select_live_rest_base(probes: list[dict[str, Any]]) -> Optional[str]:
    """First probe that served *both* a ticker and candles, in probe order."""
    for probe in probes:
        if probe.get("ticker_ok") and probe.get("candles_ok"):
            return probe.get("rest_base") or None
    return None


def prepare_signal(
    signal: Signal,
    *,
    symbol: str,
    bar_timestamp: datetime,
    price: float,
    trade_amount: float,
    stop_loss_pct: float,
    timeframe: str = "1d",
) -> Signal:
    """Stamp the owner's explicit size, a mandatory stop and a unique id.

    The strategy recommends direction; **the size is the owner's**. The four
    gates reject any non-HOLD signal without an explicit amount and a
    stop-loss, so both are always present here. SELL signals from library
    strategies already carry ``amount`` + ``stop_loss``; anything missing is
    filled in from the run configuration (never silently zero).
    """
    if signal.side == Side.HOLD:
        return signal
    amount = signal.amount if signal.amount else float(trade_amount)
    stop = signal.stop_loss if signal.stop_loss else price * (1.0 - stop_loss_pct)
    client_order_id = signal.client_order_id or (
        f"paper-{symbol}-{bar_timestamp.strftime('%Y%m%d%H%M')}-{uuid.uuid4().hex[:8]}"
    )
    return signal.model_copy(
        update={
            "symbol": symbol,
            "amount": amount,
            "stop_loss": stop,
            "client_order_id": client_order_id,
            "price": signal.price or price,
            "data_timestamp": bar_timestamp,
            "metadata": {
                **dict(signal.metadata),
                "paper_run": True,
                "timeframe": timeframe,
                "size_source": "user_configured" if not signal.amount else "strategy",
            },
        }
    )


def machine_stage(machine: Any) -> str:
    """Current progression stage as a string (tolerates property or method)."""
    value = getattr(machine, "current_stage", None)
    if callable(value):
        value = value()
    return str(getattr(value, "value", value))


def machine_next_stage(machine: Any) -> Any:
    """Next progression stage (tolerates property or method)."""
    value = getattr(machine, "next_stage", None)
    return value() if callable(value) else value


def _run_key(kind: str, when: datetime) -> str:
    return f"{kind}:{when.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M')}"


# ---------------------------------------------------------------------------
# Live market-data probe
# ---------------------------------------------------------------------------


async def probe_market_sources(
    settings: Settings,
    symbol: str = "BTCUSDT",
    *,
    candidates: Optional[list[tuple[str, str]]] = None,
    client: Optional[httpx.AsyncClient] = None,
    now: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    """Probe each candidate source and report exactly what it returned.

    Never fabricates: a source that fails is reported as failed with the real
    error. The caller decides what to do with the results.
    """
    now = now or utcnow()
    candidates = candidates or [
        ("binance-vision-mirror", DEFAULT_LIVE_REST_BASE),
        ("binance-mainnet", settings.binance_rest_base),
        ("coingecko", "coingecko"),
    ]
    results: list[dict[str, Any]] = []
    for name, base in candidates:
        probe: dict[str, Any] = {
            "name": name,
            "rest_base": base,
            "ticker_ok": False,
            "candles_ok": False,
            "detail": "",
        }
        if base == "coingecko":
            source = CoinGeckoSource(now=lambda: now, client=client)
        else:
            source = BinanceSource(
                now=lambda: now,
                max_data_age_seconds=settings.market_data_max_age_seconds,
                rest_base=base,
                client=client,
            )
        try:
            ticker = await source.get_ticker(symbol)
            probe["ticker_ok"] = True
            probe["price"] = ticker.price
            probe["ticker_source"] = ticker.source
            probe["ticker_age_seconds"] = ticker.age_seconds(now)
        except Exception as exc:  # noqa: BLE001 - probe must never raise
            probe["detail"] += f"ticker: {type(exc).__name__}: {exc} "
        try:
            raw = await source.get_candles(symbol, "1d", 3)
            closed = drop_unclosed_candles(raw, "1d", now)
            probe["candles_ok"] = bool(closed)
            if closed:
                probe["newest_closed_bar"] = closed[-1].timestamp.isoformat()
                probe["newest_closed_bar_age_seconds"] = newest_bar_age_seconds(
                    closed, "1d", now
                )
                probe["candles_returned"] = len(closed)
        except Exception as exc:  # noqa: BLE001 - probe must never raise
            probe["detail"] += f"candles: {type(exc).__name__}: {exc} "
        results.append(probe)
    return results


def format_probe_table(probes: list[dict[str, Any]]) -> str:
    """Human-readable probe output (also used by ``--check-data``)."""
    lines = ["market-data probe (this host, just now):"]
    for probe in probes:
        lines.append(
            f"  - {probe['name']:<24} ticker={'OK' if probe['ticker_ok'] else 'FAIL'}"
            f" candles={'OK' if probe['candles_ok'] else 'FAIL'}"
        )
        if probe.get("price") is not None:
            lines.append(f"      price={probe['price']} source={probe.get('ticker_source')}")
        if probe.get("newest_closed_bar"):
            lines.append(
                f"      newest closed 1d bar={probe['newest_closed_bar']}"
                f" (age {probe['newest_closed_bar_age_seconds']:.0f}s)"
            )
        if probe.get("detail"):
            lines.append(f"      {probe['detail'].strip()}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


@dataclass
class BarOutcome:
    """Result of one poll/bar evaluation. ``executed`` is the single truth."""

    bar_timestamp: Optional[datetime]
    action: str
    executed: bool = False
    reason: str = ""
    price: Optional[float] = None
    equity: Optional[float] = None
    source: str = ""
    side: str = ""
    realized_pnl: Optional[float] = None
    checked_at: Optional[datetime] = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("bar_timestamp", "checked_at"):
            if isinstance(data.get(key), datetime):
                data[key] = data[key].isoformat()
        return data


class PaperRun:
    """The paper-trading run: one strategy, one simulated account, one audit trail."""

    def __init__(
        self,
        settings: Settings,
        *,
        source: Any,
        strategy: Any,
        session: Any = None,
        notifier: Optional[TelegramNotifier] = None,
        store: Any = None,
        machine: Optional[ProgressionMachine] = None,
        tracker: Optional[PerformanceTracker] = None,
        now: Callable[[], datetime] = utcnow,
        symbol: Optional[str] = None,
        timeframe: Optional[str] = None,
        trade_amount: Optional[float] = None,
        stop_loss_pct: Optional[float] = None,
        daily_at: str = "18:00",
        weekly_at: str = "12:00",
        weekly_day: str = "SUN",
        paper_start: Optional[datetime] = None,
    ) -> None:
        self.settings = settings
        self.source = source
        self.strategy = strategy
        self.session = session
        self.notifier = notifier
        self.store = store or NullProgressionStore()
        self.machine = machine or ProgressionMachine(
            settings=settings, store=self.store, notifier=notifier
        )
        self.symbol = symbol or settings.paper_symbol
        self.timeframe = timeframe or settings.paper_timeframe
        self.trade_amount = (
            float(trade_amount)
            if trade_amount is not None
            else float(settings.paper_trade_amount_usdt)
        )
        self.stop_loss_pct = (
            float(stop_loss_pct)
            if stop_loss_pct is not None
            else float(settings.paper_stop_loss_pct)
        )
        self.daily_at = daily_at
        self.weekly_at = weekly_at
        self.weekly_day = weekly_day
        self._now = now
        self.log = logger

        self.broker = PaperBroker(
            settings,
            data_source=source,
            starting_usdt=settings.paper_starting_usdt,
            session=session,
            notifier=notifier,
            now=now,
        )
        self.tracker = tracker or PerformanceTracker(
            settings.paper_starting_usdt, symbol=self.symbol
        )
        self.paper_start: Optional[datetime] = paper_start
        self.last_candle_source: str = ""
        self.last_ticker_source: str = ""
        self.outcomes: list[BarOutcome] = []
        self.report_log: list[dict[str, Any]] = []
        self._last_bar_ts: Optional[datetime] = None
        self._sent_reports: set[str] = set()
        self._evidence_error_logged = False
        self._next_daily: Optional[datetime] = None
        self._next_weekly: Optional[datetime] = None

    # -- lifecycle -----------------------------------------------------------
    async def start(self) -> None:
        """Resolve the paper start date, audit the start, log the config."""
        if self.paper_start is None:
            self.paper_start = self._load_or_create_paper_start()
        self._audit(
            "paper_run_start",
            f"paper_start={self.paper_start.astimezone(timezone.utc).isoformat()}",
        )
        self.log.info(
            "paper run started | strategy=%s timeframe=%s symbol=%s "
            "starting_balance=%.2f USDT size=%.2f USDT stop=%.2f%% paper_start=%s",
            getattr(self.strategy, "name", type(self.strategy).__name__),
            self.timeframe,
            self.symbol,
            self.settings.paper_starting_usdt,
            self.trade_amount,
            self.stop_loss_pct * 100.0,
            self.paper_start.isoformat(),
        )
        now = self._now()
        self._next_daily = next_daily_utc(now, self.daily_at)
        self._next_weekly = next_weekly_utc(now, self.weekly_at, self.weekly_day)
        self.log.info(
            "report schedule | daily at %s UTC (next %s) | weekly %s %s UTC (next %s) "
            "| telegram_enabled=%s",
            self.daily_at,
            self._next_daily.isoformat(),
            self.weekly_day,
            self.weekly_at,
            self._next_weekly.isoformat(),
            getattr(self.settings, "telegram_enabled", False),
        )

    async def stop(self, *, reason: str = "shutdown") -> None:
        self._audit("paper_run_stop", f"reason={reason}")
        self.log.info(
            "paper run stopped | %s | equity=%.2f USDT realized=%.2f USDT trades=%d",
            reason,
            self.tracker.last_equity(),
            self.broker.account.realized_pnl,
            len(self.tracker.fills),
        )

    def _load_or_create_paper_start(self) -> datetime:
        """Recover the paper-run start from the audit trail (survives restarts)."""
        try:
            for event in self.store.history(limit=200):
                if event.get("event_type") not in ("paper_run_start", "paper_run_resume"):
                    continue
                match = _PAPER_START_RE.search(str(event.get("detail", "")))
                stamp = match.group(1) if match else event.get("timestamp")
                if not stamp:
                    continue
                parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed
        except Exception:  # noqa: BLE001 - a broken trail must not stop the run
            self.log.warning("could not read paper start from the audit trail", exc_info=True)
        return self._now()

    def _audit(self, event_type: str, detail: str) -> None:
        """Append to the audit trail (progression store). Never raises."""
        try:
            self.store.append(
                event_type=event_type,
                snapshot=self.machine._snapshot()
                if hasattr(self.machine, "_snapshot")
                else {"stage": str(self.machine.current_stage.value)},
                detail=detail,
            )
        except Exception:  # noqa: BLE001
            self.log.warning("audit append failed (%s)", event_type, exc_info=True)

    def paper_days(self, now: Optional[datetime] = None) -> float:
        if self.paper_start is None:
            return 0.0
        now = now or self._now()
        return max(0.0, (now - self.paper_start).total_seconds() / 86400.0)

    # -- market data ---------------------------------------------------------
    async def _live_ticker(self) -> Optional[Any]:
        """Fresh, non-cached ticker or None (NO TRADE on anything else)."""
        try:
            ticker = await self.source.get_ticker(self.symbol)
        except DataUnavailableError as exc:
            self.log.warning("ticker unavailable: %s", exc)
            return None
        except Exception as exc:  # noqa: BLE001
            self.log.warning("ticker error: %s", exc)
            return None
        if getattr(ticker, "cached", False):
            self.log.warning("ticker came from cache (not live) — NO TRADE")
            return None
        return ticker

    async def _closed_candles(self, now: datetime) -> Optional[list[Candle]]:
        limit = max(60, int(self.settings.paper_candle_max_age_bars) + 40)
        try:
            raw = await self.source.get_candles(
                self.symbol, self.timeframe, max(limit, 120)
            )
        except DataUnavailableError as exc:
            self.log.warning("candles unavailable: %s", exc)
            return None
        except Exception as exc:  # noqa: BLE001
            self.log.warning("candle error: %s", exc)
            return None
        closed = drop_unclosed_candles(list(raw or []), self.timeframe, now)
        stale, why = candles_are_stale(
            closed, self.timeframe, now, self.settings.paper_candle_max_age_bars
        )
        if stale:
            self.log.warning("candle feed not tradeable: %s — NO TRADE", why)
            return None
        self.last_candle_source = closed[-1].source or "unknown"
        return closed

    # -- one poll ------------------------------------------------------------
    async def tick(self) -> BarOutcome:
        """Evaluate the latest closed bar once (and monitor open stops)."""
        now = self._now()
        ticker = await self._live_ticker()
        if ticker is None:
            return self._record(
                BarOutcome(
                    bar_timestamp=None,
                    action="DATA_UNAVAILABLE",
                    reason="no fresh, non-cached ticker — NO TRADE",
                    checked_at=now,
                )
            )
        self.last_ticker_source = ticker.source
        equity = self.broker.equity(ticker.price)
        self.tracker.record_equity(equity, now)
        await self._monitor_positions(ticker, now)

        candles = await self._closed_candles(now)
        if candles is None:
            return self._record(
                BarOutcome(
                    bar_timestamp=None,
                    action="DATA_UNAVAILABLE",
                    reason="candles unavailable, stale or cached — NO TRADE",
                    price=ticker.price,
                    equity=self.broker.equity(ticker.price),
                    source=ticker.source,
                    checked_at=now,
                )
            )

        bar = candles[-1]
        if self._last_bar_ts is not None and bar.timestamp <= self._last_bar_ts:
            return self._record(
                BarOutcome(
                    bar_timestamp=bar.timestamp,
                    action="NO_NEW_BAR",
                    reason="latest closed bar already evaluated",
                    price=ticker.price,
                    equity=self.broker.equity(ticker.price),
                    source=f"{self.last_candle_source}/{ticker.source}",
                    checked_at=now,
                )
            )
        self._last_bar_ts = bar.timestamp

        signal = self.strategy(
            candles, self.broker.btc_holdings, self.broker.equity(ticker.price)
        )
        if signal is None or signal.side == Side.HOLD:
            return self._record(
                BarOutcome(
                    bar_timestamp=bar.timestamp,
                    action="HOLD",
                    reason="strategy recommends HOLD (no trade)",
                    price=ticker.price,
                    equity=self.broker.equity(ticker.price),
                    source=f"{self.last_candle_source}/{ticker.source}",
                    checked_at=now,
                )
            )

        prepared = prepare_signal(
            signal,
            symbol=self.symbol,
            bar_timestamp=bar.timestamp,
            price=ticker.price,
            trade_amount=self.trade_amount,
            stop_loss_pct=self.stop_loss_pct,
            timeframe=self.timeframe,
        )
        result = await self.broker.execute(prepared)
        self._record_pipeline(prepared, result)

        if result.executed and result.fill is not None:
            self.tracker.record_fill(
                result.fill,
                realized_pnl_total=self.broker.account.realized_pnl,
                ts=now,
            )
            self.log.info(
                "PAPER FILL %s %s qty=%.8f price=%.2f equity=%.2f realized=%.2f",
                prepared.side.value,
                prepared.symbol,
                result.fill.quantity,
                result.fill.price,
                self.broker.equity(ticker.price),
                self.broker.account.realized_pnl,
            )
        else:
            self.log.info(
                "NO TRADE on bar %s: %s", bar.timestamp.isoformat(), result.reason
            )

        self._update_evidence(now)
        await self.maybe_request_extended_paper()

        return self._record(
            BarOutcome(
                bar_timestamp=bar.timestamp,
                action=prepared.side.value if result.executed else "REJECTED",
                executed=result.executed,
                reason=result.reason,
                price=ticker.price,
                equity=self.broker.equity(ticker.price),
                source=f"{self.last_candle_source}/{ticker.source}",
                side=prepared.side.value,
                realized_pnl=self.broker.account.realized_pnl,
                checked_at=now,
            )
        )

    async def _monitor_positions(self, ticker: Any, now: datetime) -> None:
        """Let the existing position monitor run stop-loss / take-profit."""
        if not self.broker.account.has_position:
            return
        before = self.broker.account.realized_pnl
        try:
            report = self.broker.monitor_price(ticker.price)
        except Exception as exc:  # noqa: BLE001 - monitoring must never kill the run
            self.log.warning("position monitor failed: %s", exc)
            return
        if not getattr(report, "closed", False):
            return
        delta = self.broker.account.realized_pnl - before
        fill = getattr(report, "fill", None)
        if fill is not None:
            self.tracker.record_fill(
                fill, realized_pnl_total=self.broker.account.realized_pnl, ts=now
            )
        else:
            # No fill object exposed: record the *real* realized delta with the
            # observed exit price. Never a fabricated quantity or fee.
            self.tracker.record_exit(
                realized_pnl=delta,
                price=float(getattr(report, "price", None) or ticker.price),
                fee=float(getattr(report, "fee", 0.0) or 0.0),
                ts=now,
                reason=str(getattr(report, "reason", "")),
            )
        self.log.info(
            "paper position closed by %s at %.2f (realized %+.2f USDT)",
            getattr(report, "reason", "?"),
            getattr(report, "price", None) or ticker.price,
            delta,
        )

    def _record_pipeline(self, signal: Signal, result: PaperResult) -> None:
        """Persist every pipeline outcome for audit (best effort)."""
        if self.session is None or result.pipeline is None:
            return
        try:
            from halaltrade.db.recorder import record_pipeline

            record_pipeline(self.session, signal, result.pipeline)
        except Exception:  # noqa: BLE001
            self.log.warning("pipeline audit record failed", exc_info=True)

    # -- progression evidence ------------------------------------------------
    def _update_evidence(self, now: datetime) -> Optional[StageEvidence]:
        """Record the paper track record so a later readiness check sees it."""
        try:
            stats = self.current_stats()
            days = self.paper_days(now)
            evidence = self.machine.record_evidence(
                paper_days=days,
                paper_trades=stats.trades,
                paper_profit_factor=stats.profit_factor,
                paper_max_drawdown=stats.period_max_drawdown_pct / 100.0,
            )
            return evidence
        except Exception:  # noqa: BLE001 - evidence must never kill the run
            if not self._evidence_error_logged:
                self.log.warning("progression evidence update failed", exc_info=True)
                self._evidence_error_logged = True
            return None

    async def maybe_request_extended_paper(self) -> None:
        """Ask for EXTENDED_PAPER_30D once the >=30-day clock is genuinely met.

        The decision itself is the progression machine's (it applies the real
        limits); this only decides *when to ask*. Never raises.
        """
        try:
            days = self.paper_days()
            if days < 30.0:
                return
            if self.machine.current_stage != ProgressionStage.PAPER:
                return
            if self.machine.next_stage != ProgressionStage.EXTENDED_PAPER_30D:
                return
            key = f"extended_paper_request:{int(days)}"
            if key in self._sent_reports:
                return
            self._sent_reports.add(key)
            stats = self.current_stats()
            result = await self._request_extended_paper(days, stats)
            self.log.info("EXTENDED_PAPER_30D transition: %s", result.summary)
        except Exception:  # noqa: BLE001
            self.log.warning("EXTENDED_PAPER_30D request failed", exc_info=True)

    async def _request_extended_paper(self, days: float, stats: PaperStats):
        return await self.machine.request_transition(
            ProgressionStage.EXTENDED_PAPER_30D,
            evidence_updates={
                "extended_paper_days": days,
                "extended_paper_trades": stats.trades,
                "extended_paper_profit_factor": stats.profit_factor,
                "extended_paper_max_drawdown": stats.period_max_drawdown_pct / 100.0,
            },
        )

    # -- reports -------------------------------------------------------------
    def current_stats(
        self,
        *,
        label: str = "DAILY",
        period_start: Optional[datetime] = None,
        now: Optional[datetime] = None,
    ) -> PaperStats:
        now = now or self._now()
        return self.tracker.stats(
            label=label,
            period_start=period_start,
            ending_equity=self.tracker.last_equity(),
            open_position_qty=self.broker.account.btc,
            stage=machine_stage(self.machine),
            paper_days=self.paper_days(now),
            data_source=self.last_candle_source or "unknown",
        )

    async def send_report(self, kind: str, *, now: Optional[datetime] = None) -> dict[str, Any]:
        """Build and send one report. Fail-safe: never raises, never blocks."""
        now = now or self._now()
        label = kind.upper()
        period_start = now - timedelta(days=1 if label == "DAILY" else 7)
        stats = self.current_stats(label=label, period_start=period_start, now=now)
        text = (
            format_daily_report(stats)
            if label == "DAILY"
            else format_weekly_report(stats)
        )
        delivered = False
        if self.notifier is not None:
            try:
                delivered = await self.notifier.send(AlertType.DAILY_PNL, text)
            except Exception:  # noqa: BLE001 - notifier is already fail-safe
                self.log.warning("report send raised", exc_info=True)
                delivered = False
        else:
            self.log.warning("no notifier configured — report not sent")
        record = {
            "kind": label,
            "sent_at": now.isoformat(),
            "delivered": bool(delivered),
            "stats": stats.to_dict(),
            "text": text,
        }
        self.report_log.append(record)
        if delivered:
            self.log.info("%s report delivered via Telegram", label)
        else:
            self.log.warning(
                "%s report NOT delivered (Telegram disabled or unconfigured: "
                "set HALAL_TELEGRAM_ENABLED, HALAL_TELEGRAM_BOT_TOKEN, "
                "HALAL_TELEGRAM_CHAT_ID, HALAL_TELEGRAM_ALERT_DAILY_PNL). "
                "The report itself is complete and logged.",
                label,
            )
        return record

    async def _maybe_send_reports(self, now: Optional[datetime] = None) -> None:
        now = now or self._now()
        if self._next_daily is None:
            self._next_daily = next_daily_utc(now, self.daily_at)
        if self._next_weekly is None:
            self._next_weekly = next_weekly_utc(now, self.weekly_at, self.weekly_day)
        while now >= self._next_daily:
            key = _run_key("daily", self._next_daily)
            if key not in self._sent_reports:
                self._sent_reports.add(key)
                await self.send_report("daily", now=now)
            self._next_daily = next_daily_utc(self._next_daily, self.daily_at)
        while now >= self._next_weekly:
            key = _run_key("weekly", self._next_weekly)
            if key not in self._sent_reports:
                self._sent_reports.add(key)
                await self.send_report("weekly", now=now)
            self._next_weekly = next_weekly_utc(self._next_weekly, self.weekly_at, self.weekly_day)

    def _record(self, outcome: BarOutcome) -> BarOutcome:
        if outcome.equity is None:
            outcome.equity = self.tracker.last_equity()
        self.outcomes.append(outcome)
        return outcome

    # -- the loop ------------------------------------------------------------
    async def run_forever(
        self,
        *,
        poll_seconds: float,
        stop: Optional[asyncio.Event] = None,
        max_iterations: Optional[int] = None,
        report_enabled: bool = True,
    ) -> None:
        """Poll forever (until ``stop`` is set). Never dies silently."""
        stop = stop or asyncio.Event()
        await self.start()
        iterations = 0
        errors = 0
        while not stop.is_set():
            iterations += 1
            try:
                outcome = await self.tick()
                if outcome.action not in ("NO_NEW_BAR",):
                    self.log.info(
                        "poll %d: %s | %s | equity=%.2f",
                        iterations,
                        outcome.action,
                        outcome.reason or "bar " + str(outcome.bar_timestamp),
                        outcome.equity or 0.0,
                    )
                self._update_evidence(self._now())
                await self.maybe_request_extended_paper()
            except Exception:  # noqa: BLE001 - catch-all per iteration
                errors += 1
                self.log.exception(
                    "iteration %d failed (error %d) — continuing, NO TRADE assumed",
                    iterations,
                    errors,
                )
            if report_enabled:
                try:
                    await self._maybe_send_reports()
                except Exception:  # noqa: BLE001
                    self.log.exception("report scheduling failed — continuing")
            if max_iterations is not None and iterations >= max_iterations:
                self.log.info("max_iterations=%d reached — stopping", max_iterations)
                break
            try:
                await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
            except asyncio.TimeoutError:
                continue
            except Exception:  # noqa: BLE001
                self.log.exception("wait failed — continuing")
        await self.stop(reason="stopped" if not stop.is_set() else "signal/shutdown")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _install_signal_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):  # pragma: no cover - platform
            signal.signal(sig, lambda *_: stop.set())


def build_arg_parser() -> argparse.ArgumentParser:
    settings = Settings()
    parser = argparse.ArgumentParser(
        prog="run_paper_trading.py",
        description=(
            "30-day PAPER-trading run on live BTC/USDT market data with simulated "
            "money and daily/weekly Telegram reports. Live trading is OFF."
        ),
    )
    parser.add_argument("--strategy", default=settings.paper_strategy)
    parser.add_argument("--symbol", default=settings.paper_symbol)
    parser.add_argument(
        "--timeframe",
        default=settings.paper_timeframe,
        help="candle timeframe for the paper clock (default 1d)",
    )
    parser.add_argument(
        "--starting-usdt",
        type=float,
        default=settings.paper_starting_usdt,
        help="simulated starting balance in USDT (default 1000)",
    )
    parser.add_argument(
        "--trade-usdt",
        type=float,
        default=settings.paper_trade_amount_usdt,
        help="the owner's explicit size per entry, USDT (default 100)",
    )
    parser.add_argument(
        "--stop-loss-pct",
        type=float,
        default=settings.paper_stop_loss_pct,
        help="mandatory stop-loss when a signal carries none (default 0.02)",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=settings.paper_poll_seconds,
        help="how often to re-read the market (default 3600)",
    )
    parser.add_argument("--daily-report-utc", default=settings.paper_daily_report_utc)
    parser.add_argument("--weekly-report-utc", default=settings.paper_weekly_report_utc)
    parser.add_argument("--weekly-report-day", default=settings.paper_weekly_report_day)
    parser.add_argument(
        "--rest-base",
        default=None,
        help=(
            "Binance REST base for public market data "
            f"(default: probe first, then {DEFAULT_LIVE_REST_BASE})"
        ),
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="evaluate the latest closed bar once, then exit (verification)",
    )
    parser.add_argument(
        "--check-data",
        action="store_true",
        help="probe the market-data sources from this host and exit",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=None,
        help="stop after N polls (testing/verification)",
    )
    parser.add_argument("--no-reports", action="store_true", help="do not send reports")
    parser.add_argument(
        "--no-db",
        action="store_true",
        help="do not persist audit/progression rows (in-memory only)",
    )
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--list-strategies", action="store_true")
    return parser


async def _resolve_rest_base(
    settings: Settings, symbol: str, explicit: Optional[str]
) -> tuple[Optional[str], list[dict[str, Any]]]:
    candidates = [(f"explicit:{explicit}", explicit)] if explicit else None
    probes = await probe_market_sources(settings, symbol, candidates=candidates)
    print(format_probe_table(probes))
    return select_live_rest_base(probes), probes


def _make_store(settings: Settings, args: argparse.Namespace):
    """DB-backed progression store, or an in-memory one if the DB is unusable."""
    if args.no_db:
        logger.warning("--no-db: audit/progression rows are NOT persisted")
        return NullProgressionStore(), None
    try:
        from halaltrade.db.models import create_session

        factory = create_session(database_url=settings.database_url)
        store = DbProgressionStore(session_factory=factory)
        session = factory()
        return store, session
    except Exception:  # noqa: BLE001
        logger.warning("database unavailable — using in-memory store", exc_info=True)
        return NullProgressionStore(), None


async def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.getLogger().setLevel(getattr(logging, str(args.log_level).upper(), logging.INFO))

    if args.list_strategies:
        print("\n".join(list_strategies()))
        return 0

    settings = Settings().model_copy(
        update={
            "trading_mode": "paper",
            "live_enabled": False,
            "paper_symbol": args.symbol,
            "paper_strategy": args.strategy,
            "paper_timeframe": args.timeframe,
            "paper_starting_usdt": args.starting_usdt,
        }
    )
    try:
        bar_seconds(args.timeframe)
    except ValueError as exc:
        logger.error("%s", exc)
        return 2

    if args.check_data:
        base, probes = await _resolve_rest_base(settings, args.symbol, args.rest_base)
        print(
            f"\nselected live REST base: {base}"
            if base
            else "\nNO live candle source from this host — DATA UNAVAILABLE"
        )
        return 0 if base else 2

    base, _probes = await _resolve_rest_base(settings, args.symbol, args.rest_base)
    if base is None:
        logger.error(
            "DATA UNAVAILABLE: no market-data source on this host returned both a "
            "fresh ticker and closed candles. NO TRADE. Nothing is fabricated — "
            "re-run with --check-data to see each source's real error."
        )
        return 2

    settings = settings.model_copy(update={"binance_rest_base": base})
    source = build_default_source(settings)
    try:
        strategy = get_strategy(
            args.strategy, timeframe=args.timeframe, trade_amount=args.trade_usdt
        )
    except KeyError as exc:
        logger.error("%s", exc)
        return 2

    store, session = _make_store(settings, args)
    notifier = TelegramNotifier(settings)
    run = PaperRun(
        settings,
        source=source,
        strategy=strategy,
        session=session,
        notifier=notifier,
        store=store,
        daily_at=args.daily_report_utc,
        weekly_at=args.weekly_report_utc,
        weekly_day=args.weekly_report_day,
        trade_amount=args.trade_usdt,
        stop_loss_pct=args.stop_loss_pct,
    )

    if args.once:
        await run.start()
        outcome = await run.tick()
        print(json.dumps(outcome.to_dict(), indent=2, default=str))
        print(
            json.dumps(
                {
                    "equity": run.broker.equity(outcome.price)
                    if outcome.price
                    else None,
                    "usdt": run.broker.usdt_balance,
                    "btc": run.broker.btc_holdings,
                    "realized_pnl": run.broker.account.realized_pnl,
                    "paper_days": run.paper_days(),
                    "stage": machine_stage(run.machine),
                },
                indent=2,
            )
        )
        if not args.no_reports:
            await run.send_report("daily")
        await run.stop(reason="--once")
        return 0

    stop = asyncio.Event()
    _install_signal_handlers(stop)
    await run.run_forever(
        poll_seconds=args.poll_seconds,
        stop=stop,
        max_iterations=args.max_iterations,
        report_enabled=not args.no_reports,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(130)
