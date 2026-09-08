"""CachedSource — last-known-value fallback with clear stale marking.

Wraps any upstream :class:`MarketDataSource`. When the upstream is reachable it
passes fresh data through (and caches a copy). When the upstream goes down it
serves the **last-known value**, clearly marked ``cached=True`` so consumers
know it is not live and therefore **not tradeable**.

Freshness rules
---------------
- Cached value fresher than/equal to ``max_data_age_seconds`` is served, marked
  ``cached=True``. Consumers must still treat it as non-tradeable.
- Cached value older than ``max_data_age_seconds`` is **refused**
  (``DataUnavailableError``) — the source refuses to stand behind it, satisfying
  the "refuse to serve a ticker older than an age limit" rule.
- ``is_stale(ticker)`` is the consumers' canonical tradeability check.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from ..models import utcnow
from .base import (
    DEFAULT_MAX_AGE_SECONDS,
    DataFreshnessMixin,
    MarketDataSource,
    is_stale,
)
from .errors import DataUnavailableError
from .models import Candle, Ticker

__all__ = ["CachedSource"]


class CachedSource(MarketDataSource, DataFreshnessMixin):
    """Last-known-value cache in front of another source."""

    name = "cache"

    def __init__(
        self,
        upstream: MarketDataSource,
        *,
        now: Callable[[], datetime] = utcnow,
        max_data_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
    ) -> None:
        MarketDataSource.__init__(self, now=now, max_data_age_seconds=max_data_age_seconds)
        self.upstream = upstream
        self._last_ticker: Ticker | None = None
        self._last_candles: dict[tuple[str, str], list[Candle]] = {}

    # -- seeding / cache control --------------------------------------------
    def seed(self, ticker: Ticker) -> None:
        """Seed the cache (used by tests to simulate a previously-observed value)."""
        self._last_ticker = ticker

    def last_ticker(self) -> Ticker | None:
        return self._last_ticker

    # -- MarketDataSource ----------------------------------------------------
    async def get_ticker(self, symbol: str = "BTCUSDT") -> Ticker:
        try:
            ticker = await self.upstream.get_ticker(symbol)
            self._last_ticker = ticker
            return ticker
        except DataUnavailableError:
            return self._serve_cached(symbol)

    def _serve_cached(self, symbol: str) -> Ticker:
        cached = self._last_ticker
        if cached is None:
            raise DataUnavailableError(
                "upstream down and no cached value available", source=self.name
            )
        # Refuse data older than the hard age limit (stale => untrustworthy).
        self._reject_if_stale(cached)
        # Serve the last-known value, clearly marked as cached (non-tradeable).
        return cached.model_copy(update={"cached": True, "source": self.name})

    async def get_candles(
        self, symbol: str = "BTCUSDT", timeframe: str = "1m", limit: int = 100
    ) -> list[Candle]:
        key = (symbol, timeframe)
        try:
            candles = await self.upstream.get_candles(symbol, timeframe, limit)
            self._last_candles[key] = candles
            return candles
        except DataUnavailableError:
            cached = self._last_candles.get(key)
            if cached is None:
                raise DataUnavailableError(
                    "upstream down and no cached candles available", source=self.name
                )
            # Candles do not carry an observation clock in the same way; we mark
            # them from the cache source so callers know they are not live.
            return [c.model_copy(update={"source": self.name}) for c in cached]
