"""Abstract market-data interface + shared staleness machinery.

Design principles
-----------------
- Real sources (Binance, CoinGecko, Kraken) implement :class:`MarketDataSource`.
- Every source carries an injectable clock (``now``) so tests are deterministic
  and offline. Default clock is :func:`halaltrade.models.utcnow`.
- ``is_stale`` is the one canonical freshness check. Consumers use it to refuse
  old data; sources use it (via :class:`DataFreshnessMixin`) to *refuse to serve*
  data older than their age limit.
- Sources NEVER fabricate. On any upstream failure/malformed/stale condition they
  raise :class:`DataUnavailableError`.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timezone

from ..models import utcnow
from .errors import DataUnavailableError
from .models import Candle, Ticker

__all__ = [
    "MarketDataSource",
    "DataFreshnessMixin",
    "is_stale",
    "DEFAULT_MAX_AGE_SECONDS",
]

DEFAULT_MAX_AGE_SECONDS = 5.0


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def is_stale(
    ticker: Ticker,
    max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
    now: datetime | None = None,
) -> bool:
    """True if *ticker* was observed more than ``max_age_seconds`` ago.

    This is the single freshness check consumers call before using a price:
    data older than the threshold must not be used to make a decision. Naive
    timestamps are treated as UTC.
    """
    now = now or utcnow()
    return ticker.age_seconds(now) > max_age_seconds


class MarketDataSource(ABC):
    """Abstract source of market data. Subclass and implement per provider."""

    #: Stable identifier reported on every Ticker/Candle (e.g. ``"binance"``).
    name: str = "source"

    def __init__(
        self,
        *,
        now: Callable[[], datetime] = utcnow,
        max_data_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
    ) -> None:
        self._now = now
        self._max_data_age_seconds = max_data_age_seconds

    @abstractmethod
    async def get_ticker(self, symbol: str = "BTCUSDT") -> Ticker:
        """Return the current ticker or raise ``DataUnavailableError``."""

    @abstractmethod
    async def get_candles(
        self, symbol: str = "BTCUSDT", timeframe: str = "1m", limit: int = 100
    ) -> list[Candle]:
        """Return recent OHLCV candles (oldest first) or raise.

        ``limit`` is an upper bound, never an exact guarantee.
        """

    # WebSocket live stream support is optional; base raises so only sources
    # that actually implement it advertise it.
    async def stream_ticker(self, symbol: str = "BTCUSDT") -> AsyncIterator[Ticker]:
        """Yield live ticker updates.

        Not implemented by default — only sources with a WebSocket feed
        override this.
        """
        raise NotImplementedError(f"{type(self).__name__} has no live stream")
        yield  # pragma: no cover - makes this a proper async generator

    def _build_ticker(
        self,
        *,
        symbol: str,
        price: float,
        source: str,
        raw: dict,
        cached: bool = False,
    ) -> Ticker:
        """Stamp a ticker with the observation time and the source id."""
        return Ticker(
            symbol=symbol,
            price=price,
            timestamp=self._now(),
            source=source,
            raw=raw,
            cached=cached,
        )


class DataFreshnessMixin:
    """Give a source a *refuse old data* rule.

    A source that may hold onto historical/delayed values (e.g. a cache) calls
    :meth:`_reject_if_stale` before serving it. If the observation is older than
    the source's age limit it raises ``DataUnavailableError`` — the source
    refuses to serve data it cannot stand behind.
    """

    _max_data_age_seconds: float = DEFAULT_MAX_AGE_SECONDS
    _now: Callable[[], datetime] = utcnow

    def _reject_if_stale(self, ticker: Ticker) -> None:
        if is_stale(ticker, self._max_data_age_seconds, now=self._now()):
            raise DataUnavailableError(
                f"stale data (age {ticker.age_seconds(self._now()):.1f}s "
                f"> limit {self._max_data_age_seconds:.1f}s)",
                source=ticker.source,
            )
