"""Pure-data pydantic models for market data.

These models carry *observed* market data plus provenance. Every ticker/candle
is timestamp-stamped with **when the agent observed it** (UTC) so consumers can
detect staleness. ``source`` records which provider produced the value and
``cached`` records whether it came from a live feed or a fall-back cache — both
matter for tradeability decisions.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from ..models import utcnow

__all__ = ["Ticker", "Candle"]

# Timeframes supported for OHLCV candles (Binance interval strings).
SUPPORTED_TIMEFRAMES = ("1m", "5m", "15m", "1h", "4h", "1d")


class Ticker(BaseModel):
    """One point-in-time price observation for a symbol.

    Attributes
    ----------
    symbol:
        Trading pair, e.g. ``"BTCUSDT"``.
    price:
        Price in quote currency. Present and real, or the source raises
        ``DataUnavailableError`` — never fabricated.
    timestamp:
        UTC datetime of **when the agent observed** the price.
    source:
        Which provider produced this value (``"binance"``, ``"coingecko"``,
        ``"kraken"``, ``"cache"``, ...).
    raw:
        The unmodified upstream response for audit/debug (json-able).
    cached:
        True when this value was served from a cache rather than a live feed.
        Consumers must treat cached values as NOT tradeable.
    """

    symbol: str
    price: float
    timestamp: datetime = Field(description="UTC, time the agent observed the price.")
    source: str
    raw: dict[str, Any] = Field(default_factory=dict)
    cached: bool = False

    def age_seconds(self, now: datetime | None = None) -> float:
        """Age of this observation in seconds (>= 0) against *now* (UTC)."""
        now = now or utcnow()
        ts = self.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return max(0.0, (now - ts).total_seconds())


class Candle(BaseModel):
    """One OHLCV candle for a symbol/timeframe.

    ``timestamp`` is the candle's open time in UTC. ``source`` records the
    provider, ``raw`` keeps the upstream row for audit.
    """

    symbol: str
    timeframe: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    timestamp: datetime = Field(description="Candle open time, UTC.")
    source: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)
