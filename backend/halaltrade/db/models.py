"""Database layer for HalalTrade AI.

SQLite for development, schema written portably so it migrates to PostgreSQL in
production (generic SQLAlchemy types, no SQLite-only features, ISO-8601 UTC
datetimes stored as strings to avoid naive/aware drift).

Tables:
    audit_log        — append-only record of every gate decision & key action
    signals          — every signal submitted to the pipeline
    shariah_checks   — shariah gate outcomes (every rejection is recorded)
    risk_events      — risk gate outcomes and triggered limit events
    trades           — approved trades (paper/backtest) with full metadata
    orders           — orders placed / attempted with their lifecycle
    emergency_events — kill-switch / fatal security events
    system_events    — health, startup/shutdown, credential-state changes
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def utc_str(dt: datetime | None = None) -> str:
    """ISO-8601 UTC string — the portable timestamp format used across tables."""
    dt = dt or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


class Base(DeclarativeBase):
    pass


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    timestamp: Mapped[str] = mapped_column(String(32), default=utc_str, index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    signal_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    gate: Mapped[str | None] = mapped_column(String(32), default=None)
    passed: Mapped[bool | None] = mapped_column(Boolean, default=None)
    detail: Mapped[str | None] = mapped_column(Text, default=None)


class SignalRecord(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    signal_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    timestamp: Mapped[str] = mapped_column(String(32), default=utc_str)
    side: Mapped[str] = mapped_column(String(16))
    symbol: Mapped[str] = mapped_column(String(32))
    instrument_type: Mapped[str] = mapped_column(String(32))
    price: Mapped[float | None] = mapped_column(Float, default=None)
    quantity: Mapped[float | None] = mapped_column(Float, default=None)
    amount: Mapped[float | None] = mapped_column(Float, default=None)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[str | None] = mapped_column(Text, default=None)
    stop_loss: Mapped[float | None] = mapped_column(Float, default=None)
    decision: Mapped[str] = mapped_column(String(16))  # TRADE | NO_TRADE


class ShariahCheck(Base):
    __tablename__ = "shariah_checks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    timestamp: Mapped[str] = mapped_column(String(32), default=utc_str)
    signal_id: Mapped[str] = mapped_column(String(64), index=True)
    passed: Mapped[bool] = mapped_column(Boolean)
    instrument_type: Mapped[str] = mapped_column(String(32))
    leverage: Mapped[float] = mapped_column(Float)
    reasons: Mapped[str | None] = mapped_column(Text, default=None)  # JSON list


class RiskEvent(Base):
    __tablename__ = "risk_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    timestamp: Mapped[str] = mapped_column(String(32), default=utc_str)
    signal_id: Mapped[str] = mapped_column(String(64), index=True)
    passed: Mapped[bool] = mapped_column(Boolean)
    limit_hit: Mapped[str | None] = mapped_column(String(64), default=None)
    reasons: Mapped[str | None] = mapped_column(Text, default=None)


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    timestamp: Mapped[str] = mapped_column(String(32), default=utc_str)
    signal_id: Mapped[str] = mapped_column(String(64), index=True)
    client_order_id: Mapped[str | None] = mapped_column(String(64), unique=True, default=None)
    symbol: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(16))
    quantity: Mapped[float | None] = mapped_column(Float, default=None)
    price: Mapped[float | None] = mapped_column(Float, default=None)
    notional: Mapped[float | None] = mapped_column(Float, default=None)
    status: Mapped[str] = mapped_column(String(32))  # confirmed lifecycle state


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    timestamp: Mapped[str] = mapped_column(String(32), default=utc_str)
    signal_id: Mapped[str] = mapped_column(String(64), index=True)
    client_order_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(16))
    order_type: Mapped[str] = mapped_column(String(32))
    quantity: Mapped[float | None] = mapped_column(Float, default=None)
    price: Mapped[float | None] = mapped_column(Float, default=None)
    status: Mapped[str] = mapped_column(String(32))  # CONFIRMED from source, never assumed


class Position(Base):
    """A simulated spot position (paper/backtest). Long-only, 1x leverage.

    Superset of the position lifecycle the engines manage: opened at an average
    entry with a mandatory stop-loss, eventually closed (SELL / stop / TP).
    """

    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    timestamp: Mapped[str] = mapped_column(String(32), default=utc_str)
    symbol: Mapped[str] = mapped_column(String(32))
    mode: Mapped[str] = mapped_column(String(16))      # paper | backtest
    run_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    client_order_id: Mapped[str | None] = mapped_column(String(64), default=None)
    quantity: Mapped[float] = mapped_column(Float)
    avg_entry_price: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[float | None] = mapped_column(Float, default=None)
    take_profit: Mapped[float | None] = mapped_column(Float, default=None)
    status: Mapped[str] = mapped_column(String(16))    # OPEN | CLOSED
    realized_pnl: Mapped[float | None] = mapped_column(Float, default=None)
    close_price: Mapped[float | None] = mapped_column(Float, default=None)
    close_reason: Mapped[str | None] = mapped_column(String(32), default=None)


class BacktestRun(Base):
    """One persisted backtest run with its final Performance report."""

    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    timestamp: Mapped[str] = mapped_column(String(32), default=utc_str)
    strategy: Mapped[str] = mapped_column(String(64))
    symbol: Mapped[str] = mapped_column(String(32))
    timeframe: Mapped[str] = mapped_column(String(8))
    candles_count: Mapped[int] = mapped_column(default=0)
    starting_equity: Mapped[float] = mapped_column(Float)
    ending_equity: Mapped[float] = mapped_column(Float)
    total_return: Mapped[float] = mapped_column(Float)
    trade_count: Mapped[int] = mapped_column(default=0)
    win_rate: Mapped[float] = mapped_column(Float)
    profit_factor: Mapped[float] = mapped_column(Float)
    max_drawdown: Mapped[float] = mapped_column(Float)
    sharpe: Mapped[float] = mapped_column(Float)
    pnl: Mapped[float] = mapped_column(Float)
    detail: Mapped[str | None] = mapped_column(Text, default=None)  # JSON of full report


class EmergencyEvent(Base):
    __tablename__ = "emergency_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    timestamp: Mapped[str] = mapped_column(String(32), default=utc_str)
    event_type: Mapped[str] = mapped_column(String(64), index=True)  # e.g. KILL_SWITCH
    detail: Mapped[str | None] = mapped_column(Text, default=None)


class SystemEvent(Base):
    __tablename__ = "system_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    timestamp: Mapped[str] = mapped_column(String(32), default=utc_str)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    healthy: Mapped[bool] = mapped_column(Boolean, default=True)
    detail: Mapped[str | None] = mapped_column(Text, default=None)


# ------------------------------------------------------------------------------------
# Engine / session helpers (SQLite for dev)
# ------------------------------------------------------------------------------------


def make_engine(database_url: str = "sqlite:///halaltrade.db"):
    """Create an engine for the given URL. SQLite default for development."""
    return create_engine(database_url, echo=False)


def create_session(engine=None, database_url: str | None = None):
    """Return a configured sessionmaker bound to the given engine/URL."""
    engine = engine or make_engine(database_url or "sqlite:///halaltrade.db")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def create_all(engine) -> None:
    Base.metadata.create_all(engine)


def drop_all(engine) -> None:
    Base.metadata.drop_all(engine)
