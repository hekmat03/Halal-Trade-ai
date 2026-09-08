"""FallbackChain — try sources in order, return the first that succeeds.

If Binance is down the chain transparently tries the next source (CoinGecko,
then a cache, ...). Every value returned carries its ``source`` so callers and
the audit log know exactly which provider produced it. If *every* source fails
the chain raises ``DataUnavailableError`` — it never fabricates a price.
"""
from __future__ import annotations

import logging

from .errors import DataUnavailableError
from .models import Candle, Ticker

logger = logging.getLogger(__name__)

__all__ = ["FallbackChain"]


class FallbackChain:
    """Ordered list of :class:`MarketDataSource` tried in priority order."""

    def __init__(self, sources: list, *, name: str = "fallback_chain") -> None:
        if not sources:
            raise ValueError("FallbackChain needs at least one source")
        self.sources = sources
        self.name = name

    async def get_ticker(self, symbol: str = "BTCUSDT") -> Ticker:
        failures: list[str] = []
        for source in self.sources:
            try:
                ticker = await source.get_ticker(symbol)
                logger.info("ticker served by %s for %s", ticker.source, symbol)
                return ticker
            except DataUnavailableError as exc:
                failures.append(f"{getattr(source, 'name', source)}: {exc}")
                logger.warning("source failed for %s: %s", symbol, exc)
            except Exception as exc:  # defensive: any source error => next source
                failures.append(f"{getattr(source, 'name', source)}: {exc!r}")
                logger.exception("unexpected error in source for %s", symbol)
        raise DataUnavailableError(
            "all sources failed: " + "; ".join(failures), source=self.name
        )

    async def get_candles(
        self, symbol: str = "BTCUSDT", timeframe: str = "1m", limit: int = 100
    ) -> list[Candle]:
        failures: list[str] = []
        for source in self.sources:
            try:
                candles = await source.get_candles(symbol, timeframe, limit)
                if candles:
                    return candles
                failures.append(f"{getattr(source, 'name', source)}: empty")
            except Exception as exc:
                failures.append(f"{getattr(source, 'name', source)}: {exc}")
        raise DataUnavailableError(
            "all sources failed for candles: " + "; ".join(failures), source=self.name
        )

    async def stream_ticker(self, symbol: str = "BTCUSDT"):
        """Stream from the first source that offers a live stream."""
        for source in self.sources:
            stream = getattr(source, "stream_ticker", None)
            if stream is None:
                continue
            try:
                async for ticker in stream(symbol):
                    yield ticker
                return
            except NotImplementedError:
                continue
            except Exception:  # noqa: BLE001 - fall through to next live source
                continue
        raise DataUnavailableError("no live stream source available", source=self.name)
