"""Build the default market-data source stack from :class:`Settings`.

Topology (cleanest fallback, no duplicate network calls):

    CacheSource (wraps the live chain)
        └─ FallbackChain [ BinanceSource, CoinGeckoSource ]
                (Binance primary → CoinGecko keyless fallback)
        └─ on total live failure → serves last-known value, marked cached/stale

The returned object is a single :class:`MarketDataSource`. Every ticker reports
the *actual* producing source (``"binance"`` / ``"coingecko"`` / ``"cache"``),
so fallback is transparent to the audit log.

All IO is injectable (``client``, ``ws_factory``, ``now``) so tests can run
fully offline without touching a real socket or public endpoint.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any, Awaitable

from ..config import Settings
from ..models import utcnow
from .binance import BinanceSource
from .cached import CachedSource
from .chain import FallbackChain
from .coingecko import CoinGeckoSource

__all__ = ["build_default_source"]

from .errors import DataUnavailableError  # noqa: E402  (re-export for convenience)
__all__ += ["DataUnavailableError"]


def build_default_source(
    settings: Settings | None = None,
    *,
    now: Callable[[], datetime] = utcnow,
    binance_client: Any = None,
    coingecko_client: Any = None,
    ws_factory: Callable[[str], Awaitable[Any]] | None = None,
) -> CachedSource:
    """Assemble and return the default keyless BTC/USDT market-data source.

    Pass ``binance_client``/``coingecko_client`` (httpx clients, e.g. backed by
    ``httpx.MockTransport``) and/or ``ws_factory`` to run offline in tests.
    """
    settings = settings or Settings()
    max_age = settings.market_data_max_age_seconds

    binance = BinanceSource(
        now=now,
        max_data_age_seconds=max_age,
        rest_base=settings.binance_rest_base,
        ws_base=settings.binance_ws_base,
        client=binance_client,
        ws_factory=ws_factory,
    )
    coingecko = CoinGeckoSource(
        now=now,
        max_data_age_seconds=max_age,
        client=coingecko_client or binance_client,
    )

    live_chain = FallbackChain([binance, coingecko], name="live_primary")
    return CachedSource(
        upstream=live_chain, now=now, max_data_age_seconds=max_age
    )
