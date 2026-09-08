"""Binance market-data source (public market data, no API key required).

Implements the :class:`MarketDataSource` interface against Binance public REST
endpoints and its WebSocket trade stream. Public market data needs no key, but
everything is environment-configurable (see ``Settings``) — no secrets are
hardcoded.

Testing note: Binance is geo-restricted from some regions (HTTP 451) and the
REST endpoints may be rate-limited. All network IO is injectable so tests run
fully offline and deterministically:

- REST calls go through an injectable ``httpx.AsyncClient`` (tests pass one
  backed by ``httpx.MockTransport``).
- The live stream goes through an injectable ``ws_factory`` (tests pass a fake
  that yields canned messages) — never a real socket.
- The clock (``now``) is injectable for deterministic timestamps.

The source retries transient failures with exponential backoff and is
rate-limit aware (honours ``429``/``418`` by backing off). If it still cannot
return fresh data it raises ``DataUnavailableError`` — it never fabricates.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

import httpx

from ..models import utcnow
from .base import DEFAULT_MAX_AGE_SECONDS, MarketDataSource
from .errors import DataUnavailableError
from .io import WSConnection, backoff_delays, build_ws_factory, sleep
from .models import Candle, Ticker

__all__ = ["BinanceSource"]

REST_BASE = "https://api.binance.com"
WS_BASE = "wss://stream.binance.com:9443"

# Intervals we honour on the REST calls; any other value from a caller is
# rejected up front (never silently mislabeled).
INTERVALS = ("1m", "5m", "15m", "1h", "4h", "1d")

_MAX_RETRIES = 3


class BinanceSource(MarketDataSource):
    """Binance Spot (BTC/USDT) public market data via REST + WebSocket."""

    name = "binance"

    def __init__(
        self,
        *,
        now: Callable[[], datetime] = utcnow,
        max_data_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
        rest_base: str = REST_BASE,
        ws_base: str = WS_BASE,
        client: httpx.AsyncClient | None = None,
        ws_factory: Callable[[str], Awaitable[Any]] | None = None,
        max_retries: int = _MAX_RETRIES,
    ) -> None:
        super().__init__(now=now, max_data_age_seconds=max_data_age_seconds)
        self.rest_base = rest_base.rstrip("/")
        self.ws_base = ws_base.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=10.0)
        self._owns_client = client is None
        self._ws_factory = ws_factory
        self._max_retries = max_retries

    # -- lifecycle -----------------------------------------------------------
    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # -- REST helpers --------------------------------------------------------
    async def _get_json(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        """GET *path* with retry + exponential backoff; returns parsed JSON.

        Raises ``DataUnavailableError`` on final failure (network error,
        non-2xx, rate-limit exhaustion, or malformed JSON). A ``429``/``418``
        (rate limit) is retried; other 4xx are immediate failures.
        """
        last_err: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                resp = await self._client.get(
                    f"{self.rest_base}{path}", params=params
                )
            except httpx.HTTPError as exc:  # network-level failure
                last_err = exc
                if attempt + 1 < self._max_retries:
                    await sleep(backoff_delays(attempt))
                continue

            if resp.status_code in (429, 418):
                # Rate-limited — back off and retry.
                last_err = DataUnavailableError(
                    f"rate limited (HTTP {resp.status_code})", source=self.name
                )
                if attempt + 1 < self._max_retries:
                    await sleep(backoff_delays(attempt, base=1.0))
                continue
            if resp.status_code != 200:
                raise DataUnavailableError(
                    f"HTTP {resp.status_code}: {resp.text[:120]}", source=self.name
                )
            try:
                data = resp.json()
            except ValueError as exc:  # malformed body
                raise DataUnavailableError("malformed JSON body", source=self.name) from exc
            return data

        raise DataUnavailableError(
            f"unreachable after {self._max_retries} attempts: {last_err or ''}",
            source=self.name,
        )

    # -- MarketDataSource ----------------------------------------------------
    async def get_ticker(self, symbol: str = "BTCUSDT") -> Ticker:
        data = await self._get_json("/api/v3/ticker/price", {"symbol": symbol})
        price = data.get("price")
        if price is None:
            raise DataUnavailableError("no 'price' field in response", source=self.name)
        try:
            price = float(price)
        except (TypeError, ValueError) as exc:
            raise DataUnavailableError(f"bad price value {price!r}", source=self.name) from exc
        return self._build_ticker(
            symbol=symbol, price=price, source=self.name, raw=data
        )

    @staticmethod
    def _interval(timeframe: str) -> str:
        if timeframe not in INTERVALS:
            raise ValueError(
                f"unsupported timeframe {timeframe!r}; supported: {INTERVALS}"
            )
        return timeframe

    async def get_candles(
        self, symbol: str = "BTCUSDT", timeframe: str = "1m", limit: int = 100
    ) -> list[Candle]:
        interval = self._interval(timeframe)
        data = await self._get_json(
            "/api/v3/klines",
            {"symbol": symbol, "interval": interval, "limit": max(1, limit)},
        )
        if not isinstance(data, list):
            raise DataUnavailableError("klines response is not a list", source=self.name)
        candles: list[Candle] = []
        for row in data:
            try:
                candles.append(
                    Candle(
                        symbol=symbol,
                        timeframe=interval,
                        open=float(row[1]),
                        high=float(row[2]),
                        low=float(row[3]),
                        close=float(row[4]),
                        volume=float(row[5]),
                        timestamp=datetime.fromtimestamp(row[0] / 1000, tz=_UTC),
                        source=self.name,
                        raw={"kline": row},
                    )
                )
            except (IndexError, TypeError, ValueError) as exc:
                raise DataUnavailableError(
                    f"malformed kline row: {row!r}", source=self.name
                ) from exc
        return candles

    # -- live WebSocket stream ----------------------------------------------
    async def stream_ticker(self, symbol: str = "BTCUSDT") -> AsyncIterator[Ticker]:
        """Yield live trade prices from Binance's WebSocket, with reconnection.

        On a dropped/disconnected socket it reconnects with exponential backoff.
        Corrupted or unparseable messages are skipped (logged), never invented.
        The injected ``ws_factory`` makes the live socket fully mockable.
        """
        stream = f"{symbol.lower()}@trade"
        url = f"{self.ws_base}/ws/{stream}"
        factory = self._ws_factory or self._default_ws_connect
        attempt = 0
        while True:
            try:
                async with await factory(url) as conn:  # type: ignore[misc]
                    attempt = 0  # connected — reset backoff
                    async for raw in _arecv(conn):
                        try:
                            msg = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
                            price = msg.get("p")
                            if price is None:
                                continue
                            yield self._build_ticker(
                                symbol=symbol,
                                price=float(price),
                                source=self.name,
                                raw=msg,
                            )
                        except (TypeError, ValueError):
                            # Unparseable message — skip, never fabricate.
                            continue
            except Exception:
                # Socket dropped / connect failed. A live stream is expected to
                # resume, so we reconnect with capped exponential backoff and
                # keep going. We never fabricate a price here.
                await sleep(backoff_delays(attempt))
                attempt += 1

    async def _default_ws_connect(self, url: str):
        import websockets

        return await websockets.connect(url)


async def _arecv(conn: Any):
    """Iterate messages from a WSConnection-like object."""
    while True:
        yield await conn.recv()


_UTC = timezone.utc
