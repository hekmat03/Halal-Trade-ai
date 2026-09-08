"""FastAPI application wiring the HalalTrade engine to HTTP/JSON.

Design notes
------------
* ``create_app`` is a factory so tests can build a fully-offline instance with an
  injected fake data source, an in-memory/temp SQLite session factory, an
  injectable clock and a disposable paper broker. The module-level ``app`` is the
  production instance built from ``Settings`` (env only).
* A session is opened fresh per request from the configured session factory
  ("any endpoint that needs a DB session opens one from the existing SQLAlchemy
  setup").
* The PaperBroker is a long-lived singleton so paper equity/positions persist
  across requests; its per-request session is swapped in for each call.
* The emergency kill switch is held in memory AND persisted (emergency_events +
  audit_log). When it is ON, ``POST /trade`` is refused with NO TRADE.
* Nothing here fabricates data. If market data is unavailable/stale the API
  returns DATA UNAVAILABLE rather than inventing a price.
* PAPER only. ``Settings.live_enabled`` defaults to False; no live order code
  exists anywhere in this module.
"""
from __future__ import annotations

import logging
import random
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from fastapi import FastAPI, Request
from pydantic import BaseModel, Field

from ..backtest import BacktestConfig, BacktestEngine, make_sma_cross
from ..config import Settings
from ..db import AuditLog, EmergencyEvent, create_session, make_engine
from ..db.recorder import record_pipeline
from ..marketdata import (
    DataUnavailableError,
    MarketDataSource,
    Ticker,
    build_default_source,
    is_stale,
)
from ..marketdata.models import Candle
from ..models import InstrumentType, Side, Signal
from ..paper import PaperBroker

logger = logging.getLogger(__name__)

__all__ = ["app", "create_app", "TradeRequest", "TradeResponse", "KillRequest"]


# --------------------------------------------------------------------------------------
# Request / response schemas
# --------------------------------------------------------------------------------------


class TradeRequest(BaseModel):
    """A user-supplied signal. Size is always the exact USDT amount the user
    wants to deploy — the agent never sizes a trade on its own."""

    side: Side
    amount: float = Field(gt=0, description="Exact USDT notional to deploy.")
    stop_loss: Optional[float] = None
    instrument_type: InstrumentType = InstrumentType.SPOT
    leverage: float = 1.0
    symbol: str = "BTCUSDT"
    reason: str = ""
    client_order_id: Optional[str] = None


class TradeResponse(BaseModel):
    """Outcome of a /trade submission. ``executed`` is the single source of truth."""

    executed: bool
    reason: str
    decision: str
    signal_id: str = "n/a"
    order_id: str = ""
    kill_switch_on: bool
    gate_results: dict[str, Any] = Field(default_factory=dict)
    fill: Optional[dict[str, Any]] = None
    equity_after: Optional[float] = None
    realized_pnl: Optional[float] = None


class KillRequest(BaseModel):
    enabled: bool = True


# --------------------------------------------------------------------------------------
# Factory
# --------------------------------------------------------------------------------------


def create_app(
    settings: Settings | None = None,
    *,
    data_source: MarketDataSource | None = None,
    session_factory: Callable[[], Any] | None = None,
    starting_usdt: float = 5000.0,
    now: Callable[[], datetime] | None = None,
) -> FastAPI:
    settings = settings or Settings(trading_mode="paper", live_enabled=False)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Tables are created when the session factory is built (create_session).
        sf = session_factory or create_session(database_url=settings.database_url)
        source = data_source or build_default_source(settings)
        app.state.settings = settings
        app.state.session_factory = sf
        app.state.data_source = source
        app.state.last_quote: Optional[dict[str, Any]] = None
        app.state.broker = PaperBroker(
            settings,
            data_source=source,
            starting_usdt=starting_usdt,
            session=sf(),
            now=now,
        )
        app.state.kill_switch = _restore_kill_switch(sf)
        logger.info(
            "HalalTrade API up: mode=%s live_enabled=%s kill_switch=%s",
            settings.trading_mode, settings.live_enabled, app.state.kill_switch,
        )
        yield

    app = FastAPI(title="HalalTrade AI API", version="0.1.0", lifespan=lifespan)

    # ------------------------------------------------------------------ health
    @app.get("/health")
    async def health(request: Request) -> dict[str, Any]:
        state = request.app.state
        quote = await _refresh_quote(state)
        return {
            "status": "ok",
            "trading_mode": state.settings.trading_mode,
            "live_enabled": state.settings.live_enabled,
            "kill_switch": state.kill_switch,
            "data_source": quote["source"] if quote else "DATA UNAVAILABLE",
            "last_price": quote["price"] if quote else None,
            "last_data_timestamp": quote["timestamp"] if quote else None,
            "data_fresh": quote["fresh"] if quote else None,
        }

    # ------------------------------------------------------------------ status
    @app.get("/status")
    async def status(request: Request) -> dict[str, Any]:
        state = request.app.state
        broker: PaperBroker = state.broker
        quote = await _refresh_quote(state)
        price = quote["price"] if quote else None
        positions = []
        if broker.account.has_position:
            positions.append(
                {
                    "symbol": "BTCUSDT",
                    "quantity": broker.account.btc,
                    "avg_entry_price": broker.account.avg_entry_price,
                    "stop_loss": broker.account.stop_loss,
                    "take_profit": broker.account.take_profit,
                    "unrealized_pnl": (
                        broker.account.unrealized_pnl(price) if price else None
                    ),
                }
            )
        return {
            "operating_mode": state.settings.trading_mode.upper(),
            "live_enabled": state.settings.live_enabled,
            "kill_switch": state.kill_switch,
            "paper": {
                "starting_balance": broker.account.starting_usdt,
                "usdt_balance": broker.account.usdt,
                "btc_holdings": broker.account.btc,
                "equity": (
                    broker.equity(price) if price is not None else "DATA UNAVAILABLE"
                ),
                "realized_pnl": broker.account.realized_pnl,
                "fees_paid": broker.account.fees_paid,
            },
            "positions": positions,
            "data": _public_quote(quote),
        }

    # ------------------------------------------------------------------ trade
    @app.post("/trade")
    async def submit_trade(req: TradeRequest, request: Request) -> TradeResponse:
        state = request.app.state
        if state.kill_switch:
            return TradeResponse(
                executed=False,
                reason="NO TRADE: emergency kill switch is ON — trading halted.",
                decision="NO_TRADE",
                kill_switch_on=True,
                order_id=req.client_order_id or "",
            )

        signal = Signal(
            side=req.side,
            symbol=req.symbol,
            instrument_type=req.instrument_type,
            leverage=req.leverage,
            amount=req.amount,
            stop_loss=req.stop_loss,
            reason=req.reason,
            client_order_id=req.client_order_id or f"api-{uuid.uuid4().hex[:12]}",
        )

        broker: PaperBroker = state.broker
        session = state.session_factory()
        try:
            broker.session = session
            result = await broker.execute(signal)
            # Persist the full gate evaluation (pass or reject) so every decision
            # has an audit trail.
            if result.pipeline is not None:
                record_pipeline(session, result.signal, result.pipeline)
            if result.executed:
                session.add(
                    AuditLog(
                        event_type="TRADE_ORDER",
                        signal_id=result.signal.client_order_id or "",
                        gate="pipeline",
                        passed=True,
                        detail=(
                            f"paper {req.side.value} {req.amount:.4f} USDT "
                            f"filled on {req.symbol}."
                        ),
                    )
                )
                session.commit()

            return TradeResponse(
                executed=result.executed,
                reason=result.reason,
                decision=(
                    result.pipeline.decision.value
                    if result.pipeline
                    else ("TRADE" if result.executed else "NO_TRADE")
                ),
                signal_id=result.pipeline.signal_id if result.pipeline else "n/a",
                order_id=result.order_id or signal.client_order_id or "",
                kill_switch_on=False,
                gate_results=(
                    {
                        name: {
                            "gate_name": g.gate_name,
                            "passed": g.passed,
                            "reasons": g.reasons,
                            "stopped": g.stopped,
                        }
                        for name, g in result.pipeline.gates.items()
                    }
                    if result.pipeline
                    else {}
                ),
                fill=(
                    {
                        "side": result.fill.side,
                        "price": result.fill.price,
                        "quantity": result.fill.quantity,
                        "notional": result.fill.notional,
                        "fee": result.fill.fee,
                    }
                    if result.fill
                    else None
                ),
                equity_after=result.equity_after,
                realized_pnl=result.realized_pnl,
            )
        finally:
            session.close()

    # ------------------------------------------------------------------ kill
    @app.post("/kill")
    async def set_kill(req: KillRequest, request: Request) -> dict[str, Any]:
        state = request.app.state
        state.kill_switch = req.enabled
        detail = (
            f"emergency kill switch {'ENGAGED' if req.enabled else 'RELEASED'}"
        )
        session = state.session_factory()
        try:
            session.add(
                EmergencyEvent(event_type="KILL_SWITCH", detail=detail)
            )
            session.add(
                AuditLog(
                    event_type="KILL_SWITCH",
                    passed=req.enabled,
                    detail=detail,
                )
            )
            session.commit()
        finally:
            session.close()
        logger.warning("kill switch = %s", state.kill_switch)
        return {"kill_switch": req.enabled, "detail": detail, "trading_halted": req.enabled}

    # ------------------------------------------------------------------ audit
    @app.get("/audit")
    async def audit(request: Request, limit: int = 30) -> list[dict[str, Any]]:
        state = request.app.state
        session = state.session_factory()
        try:
            rows = (
                session.query(AuditLog)
                .order_by(AuditLog.id.desc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "id": r.id,
                    "timestamp": r.timestamp,
                    "event_type": r.event_type,
                    "signal_id": r.signal_id,
                    "gate": r.gate,
                    "passed": r.passed,
                    "detail": r.detail,
                }
                for r in rows
            ]
        finally:
            session.close()

    # --------------------------------------------------------------- backtest
    @app.get("/backtest")
    async def backtest(request: Request, candles: int = 240) -> dict[str, Any]:
        """Run the existing backtest engine on synthetic candles (example SMA
        cross strategy). Returns a PerformanceReport — explicitly a SIMULATION,
        never live."""
        state = request.app.state
        session = state.session_factory()
        try:
            engine = BacktestEngine(state.settings, session=session)
            strategy = make_sma_cross()
            synth = _synthetic_candles(n=candles)
            result = engine.run(
                strategy,
                synth,
                config=BacktestConfig(symbol="BTCUSDT", timeframe="1h"),
            )
            return {
                "simulation": True,
                "mode": "backtest",
                "symbol": "BTCUSDT",
                "timeframe": "1h",
                "candles_used": len(synth),
                "run_id": result.run_id,
                "strategy": result.strategy_name,
                "report": result.report.model_dump(),
            }
        finally:
            session.close()

    return app


# --------------------------------------------------------------------------------------
# Internal helpers
# --------------------------------------------------------------------------------------


def _restore_kill_switch(session_factory) -> bool:
    """Read the most recent persisted KILL_SWITCH event; ENGAGED => True.

    This keeps the kill switch latched across restarts (single-owner safety)."""
    try:
        session = session_factory()
    except Exception:  # pragma: no cover - no DB available yet
        return False
    try:
        event = (
            session.query(EmergencyEvent)
            .filter_by(event_type="KILL_SWITCH")
            .order_by(EmergencyEvent.id.desc())
            .first()
        )
        if event is None:
            return False
        return "ENGAGED" in (event.detail or "")
    finally:
        session.close()


async def _refresh_quote(state, force: bool = False) -> Optional[dict[str, Any]]:
    """Return a fresh market quote with a 2s app-level cache. Never fabricates:
    returns None (=> DATA UNAVAILABLE) if the source is down or stale."""
    now = datetime.now(timezone.utc)
    cached = state.last_quote
    if cached and not force and (now - cached["fetched"]).total_seconds() < 2.0:
        return cached
    try:
        ticker: Ticker = await state.data_source.get_ticker("BTCUSDT")
    except DataUnavailableError:
        state.last_quote = None
        return None
    fresh = not is_stale(ticker, state.settings.market_data_max_age_seconds, now=now)
    quote = {
        "price": ticker.price,
        "timestamp": ticker.timestamp.isoformat(),
        "source": ticker.source,
        "cached": ticker.cached,
        "fresh": fresh,
        "fetched": now,
    }
    state.last_quote = quote
    return quote


def _public_quote(quote: Optional[dict[str, Any]]) -> dict[str, Any]:
    if quote is None:
        return {
            "price": None,
            "timestamp": None,
            "source": "DATA UNAVAILABLE",
            "cached": None,
            "fresh": None,
        }
    return {
        "price": quote["price"],
        "timestamp": quote["timestamp"],
        "source": quote["source"],
        "cached": quote["cached"],
        "fresh": quote["fresh"],
    }


def _synthetic_candles(n: int = 240, start: float = 50000.0, seed: int = 7) -> list[Candle]:
    """Deterministic synthetic OHLCV series (upward drift + noise) so the example
    SMA-cross strategy produces fillable round-trips. Clearly simulation data."""
    rng = random.Random(seed)
    ts = datetime(2024, 1, 1, tzinfo=timezone.utc)
    candles: list[Candle] = []
    price = start
    for _ in range(n):
        price = max(500.0, price + 120.0 + rng.uniform(-500.0, 500.0))
        open_ = price + rng.uniform(-60.0, 60.0)
        high = max(open_, price) * (1.0 + rng.uniform(0.0004, 0.006))
        low = min(open_, price) * (1.0 - rng.uniform(0.0004, 0.006))
        candles.append(
            Candle(
                symbol="BTCUSDT",
                timeframe="1h",
                open=round(open_, 2),
                high=round(high, 2),
                low=round(low, 2),
                close=round(price, 2),
                volume=round(rng.uniform(10.0, 120.0), 2),
                timestamp=ts,
                source="synthetic",
            )
        )
        ts += timedelta(hours=1)
    return candles


app: FastAPI = create_app()
