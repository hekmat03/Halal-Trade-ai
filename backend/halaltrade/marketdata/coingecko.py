"""CoinGecko market-data source (public, no API key).

A no-auth fallback in the chain when Binance is unavailable. CoinGecko tracks
BTC in USD rather than BTC/USDT; for the BTC/USDT agent we treat USD as the
quote (USDT is USD-pegged) and label the value honestly in ``raw``.

Only the ticker is supported (CoinGecko's free tier has no reliable public
OHLCV endpoint of the shape the agent needs); ``get_candles`` raises
``DataUnavailableError`` rather than fabricating candles.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

import httpx

from ..models import utcnow
from .base import DEFAULT_MAX_AGE_SECONDS, MarketDataSource
from .errors import DataUnavailableError
from .io import backoff_delays, sleep
from .models import Candle, Ticker

__all__ = ["CoinGeckoSource"]

REST_BASE = "https://api.coingecko.com/api/v3"

# symbol (BTCUSDT) -> (coingecko id, quote currency)
_COIN_MAP = {
    "BTCUSDT": ("bitcoin", "usd"),
    "BTCUSDC": ("bitcoin", "usd"),
}

_MAX_RETRIES = 3


class CoinGeckoSource(MarketDataSource):
    """CoinGecko public price for BTC. Keyless fallback source."""

    name = "coingecko"

    def __init__(
        self,
        *,
        now: Callable[[], datetime] = utcnow,
        max_data_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
        rest_base: str = REST_BASE,
        client: httpx.AsyncClient | None = None,
        max_retries: int = _MAX_RETRIES,
    ) -> None:
        super().__init__(now=now, max_data_age_seconds=max_data_age_seconds)
        self.rest_base = rest_base.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=10.0)
        self._owns_client = client is None
        self._max_retries = max_retries

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _coin(self, symbol: str) -> tuple[str, str]:
        try:
            return _COIN_MAP[symbol]
        except KeyError:
            raise DataUnavailableError(
                f"symbol {symbol!r} not known by CoinGecko", source=self.name
            )

    async def _get_json(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        last_err: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                resp = await self._client.get(f"{self.rest_base}{path}", params=params)
            except httpx.HTTPError as exc:
                last_err = exc
                if attempt + 1 < self._max_retries:
                    await sleep(backoff_delays(attempt))
                continue
            if resp.status_code == 429:
                last_err = DataUnavailableError("rate limited (HTTP 429)", source=self.name)
                if attempt + 1 < self._max_retries:
                    await sleep(backoff_delays(attempt, base=1.0))
                continue
            if resp.status_code != 200:
                raise DataUnavailableError(
                    f"HTTP {resp.status_code}: {resp.text[:120]}", source=self.name
                )
            try:
                return resp.json()
            except ValueError as exc:
                raise DataUnavailableError("malformed JSON body", source=self.name) from exc
        raise DataUnavailableError(
            f"unreachable after {self._max_retries} attempts: {last_err or ''}",
            source=self.name,
        )

    async def get_ticker(self, symbol: str = "BTCUSDT") -> Ticker:
        coin_id, quote = self._coin(symbol)
        data = await self._get_json(
            "/simple/price", {"ids": coin_id, "vs_currencies": quote}
        )
        try:
            price = float(data[coin_id][quote])
        except (KeyError, TypeError, ValueError) as exc:
            raise DataUnavailableError(
                f"unexpected response shape: {data!r}", source=self.name
            ) from exc
        if price <= 0:
            raise DataUnavailableError(f"non-positive price {price!r}", source=self.name)
        return self._build_ticker(symbol=symbol, price=price, source=self.name, raw=data)

    async def get_candles(
        self, symbol: str = "BTCUSDT", timeframe: str = "1m", limit: int = 100
    ) -> list[Candle]:
        raise DataUnavailableError(
            "CoinGecko free tier does not provide OHLCV in this shape — "
            "no candles served rather than fabricating them.",
            source=self.name,
        )
