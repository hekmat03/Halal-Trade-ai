"""Market-data module (Delivery 2).

Provides a clean, testable interface to fresh BTC/USDT market data for a
Spot-only, Shariah-compliant agent. Delivery 2 does **not** place orders — it
only gets reliable data.

Layers
------
- :class:`Ticker` / :class:`Candle` — pure-data models, timestamp-stamped UTC.
- :class:`MarketDataSource` — abstract interface.
- :class:`BinanceSource` — primary (REST + WebSocket live stream).
- :class:`CoinGeckoSource` — keyless fallback.
- :class:`FallbackChain` — tries sources in order, reports the real producer.
- :class:`CachedSource` — last-known-value fallback, marked cached/stale.
- :func:`build_default_source` — assemble the standard stack from ``Settings``.
- :func:`is_stale` — the one canonical staleness check for consumers.

Nothing here ever invents a price: on any upstream failure/staleness it raises
:class:`DataUnavailableError` or serves clearly-marked cached data.
"""
from __future__ import annotations

from .base import (
    DEFAULT_MAX_AGE_SECONDS,
    DataFreshnessMixin,
    MarketDataSource,
    is_stale,
)
from .binance import BinanceSource
from .cached import CachedSource
from .chain import FallbackChain
from .coingecko import CoinGeckoSource
from .errors import DataUnavailableError, MarketDataError
from .factory import build_default_source
from .models import Candle, Ticker

__all__ = [
    "Ticker",
    "Candle",
    "MarketDataSource",
    "DataFreshnessMixin",
    "BinanceSource",
    "CoinGeckoSource",
    "FallbackChain",
    "CachedSource",
    "build_default_source",
    "is_stale",
    "DEFAULT_MAX_AGE_SECONDS",
    "DataUnavailableError",
    "MarketDataError",
]
