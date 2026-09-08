"""Tests for the market-data module (Delivery 2).

All network IO is mocked — no real socket, no real HTTP. We inject:
- ``httpx`` clients backed by ``httpx.MockTransport``,
- a fake WebSocket factory for the live stream,
- an injectable, manually-advanceable clock for deterministic timestamps.

Nothing here touches a live Binance/CoinGecko endpoint.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from halaltrade.marketdata import (
    BinanceSource,
    CachedSource,
    CoinGeckoSource,
    DataUnavailableError,
    FallbackChain,
    MarketDataSource,
    Ticker,
    is_stale,
)
from halaltrade.marketdata.models import Candle
from halaltrade.models import utcnow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
class MovableClock:
    """Deterministic, mutable wall clock (UTC)."""

    def __init__(self, t: datetime | None = None) -> None:
        self.t = t or datetime(2026, 8, 30, 12, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t = self.t + timedelta(seconds=seconds)


class TickerSource(MarketDataSource):
    """Fake source used to test the fallback chain and caching."""

    def __init__(
        self,
        name: str,
        price: float,
        *,
        fail: bool = False,
        now=utcnow,
        candle_count: int = 3,
    ) -> None:
        super().__init__(now=now)
        self._name = name
        self.name = name
        self._price = price
        self._fail = fail
        self._candle_count = candle_count

    async def get_ticker(self, symbol: str = "BTCUSDT") -> Ticker:
        if self._fail:
            raise DataUnavailableError("down", source=self._name)
        return self._build_ticker(
            symbol=symbol, price=self._price, source=self._name, raw={}
        )

    async def get_candles(
        self, symbol: str = "BTCUSDT", timeframe: str = "1m", limit: int = 100
    ) -> list[Candle]:
        if self._fail:
            raise DataUnavailableError("down", source=self._name)
        t = self._now()
        return [
            Candle(
                symbol=symbol,
                timeframe=timeframe,
                open=1.0, high=1.0, low=1.0, close=1.0, volume=10.0,
                timestamp=t - timedelta(minutes=self._candle_count - i),
                source=self._name,
            )
            for i in range(self._candle_count)
        ]


class FlakySource(TickerSource):
    """Raises on get_ticker always — used as a 'down' upstream."""

    async def get_ticker(self, symbol: str = "BTCUSDT") -> Ticker:
        raise DataUnavailableError("down", source=self._name)


def binance_mock_client(*, ticker_price="65000.0", fail=False):
    """An httpx.AsyncClient backed by a deterministic mock transport."""

    def handler(request: httpx.Request) -> httpx.Response:
        if fail:
            return httpx.Response(451, json={"code": 0, "msg": "restricted"})
        if request.url.path.endswith("/ticker/price"):
            return httpx.Response(200, json={"symbol": "BTCUSDT", "price": ticker_price})
        if request.url.path.endswith("/klines"):
            return httpx.Response(
                200,
                json=[
                    [1700000000000, "100", "110", "90", "105", "1000", 1700000059999,
                     "100000", 500, "50000", 0, "0"],
                ],
            )
        return httpx.Response(404, json={"code": -1121, "msg": "bad symbol"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class FakeWS:
    """Deterministic fake websocket used to test the live stream."""

    def __init__(self, messages: list) -> None:
        self._messages = list(messages)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def recv(self):
        if self._messages:
            return self._messages.pop(0)
        raise ConnectionError("socket dropped")

    async def close(self):
        return None


# ---------------------------------------------------------------------------
# 1. Binance normal path (REST, mocked)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_binance_get_ticker_normal_path() -> None:
    clock = MovableClock()
    src = BinanceSource(now=clock, client=binance_mock_client(), max_retries=1)
    ticker = await src.get_ticker("BTCUSDT")
    assert ticker.price == 65000.0
    assert ticker.symbol == "BTCUSDT"
    assert ticker.source == "binance"
    assert ticker.cached is False
    assert ticker.timestamp == clock()                       # observed when fetched
    assert ticker.timestamp.tzinfo is not None               # UTC-aware
    assert is_stale(ticker, now=clock()) is False
    await src.aclose()


@pytest.mark.asyncio
async def test_binance_get_candles_normal_path() -> None:
    clock = MovableClock()
    src = BinanceSource(now=clock, client=binance_mock_client(), max_retries=1)
    candles = await src.get_candles("BTCUSDT", "1m", limit=10)
    assert len(candles) == 1
    c = candles[0]
    assert c.open == 100.0 and c.high == 110.0 and c.low == 90.0 and c.close == 105.0
    assert c.volume == 1000.0
    assert c.timeframe == "1m"
    assert c.source == "binance"
    assert c.timestamp.tzinfo is not None
    await src.aclose()


@pytest.mark.asyncio
async def test_binance_get_candles_rejects_unknown_timeframe() -> None:
    src = BinanceSource(client=binance_mock_client(), max_retries=1)
    with pytest.raises(ValueError):
        await src.get_candles("BTCUSDT", "3m")
    await src.aclose()


# ---------------------------------------------------------------------------
# 1b. Binance failure -> DataUnavailableError (never fabricates)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_binance_failure_raises_data_unavailable() -> None:
    src = BinanceSource(client=binance_mock_client(fail=True), max_retries=1)
    with pytest.raises(DataUnavailableError):
        await src.get_ticker("BTCUSDT")
    await src.aclose()


# ---------------------------------------------------------------------------
# 2. Fallback: primary down -> next source serves
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_fallback_chain_serves_from_next_when_primary_fails() -> None:
    clock = MovableClock()
    primary = TickerSource("binance", 70000.0, fail=True, now=clock)
    backup = TickerSource("coingecko", 69000.0, now=clock)
    chain = FallbackChain([primary, backup])

    ticker = await chain.get_ticker("BTCUSDT")
    assert ticker.price == 69000.0
    assert ticker.source == "coingecko"      # transparent: reports real producer


@pytest.mark.asyncio
async def test_fallback_chain_uses_primary_when_it_works() -> None:
    clock = MovableClock()
    primary = TickerSource("binance", 70000.0, now=clock)
    backup = TickerSource("coingecko", 69000.0, now=clock)
    chain = FallbackChain([primary, backup])
    ticker = await chain.get_ticker("BTCUSDT")
    assert ticker.price == 70000.0
    assert ticker.source == "binance"


@pytest.mark.asyncio
async def test_fallback_chain_raises_when_all_down() -> None:
    a = TickerSource("a", 1.0, fail=True)
    b = TickerSource("b", 2.0, fail=True)
    chain = FallbackChain([a, b])
    with pytest.raises(DataUnavailableError):
        await chain.get_ticker("BTCUSDT")


@pytest.mark.asyncio
async def test_fallback_chain_candles() -> None:
    clock = MovableClock()
    primary = TickerSource("binance", 1.0, fail=True, now=clock)
    backup = TickerSource("coingecko", 2.0, now=clock)
    chain = FallbackChain([primary, backup])
    candles = await chain.get_candles("BTCUSDT", "1m", 5)
    assert len(candles) == 3
    assert candles[0].source == "coingecko"


# ---------------------------------------------------------------------------
# 3. Stale detection
# ---------------------------------------------------------------------------
def test_is_stale_detects_old_ticker() -> None:
    clock = MovableClock()
    fresh = Ticker(symbol="BTCUSDT", price=65000.0, timestamp=clock(), source="binance")
    assert is_stale(fresh, now=clock()) is False

    clock.advance(6.0)                                    # past the 5s default
    assert is_stale(fresh, now=clock()) is True


def test_is_stale_respects_custom_max_age_and_naive_ts() -> None:
    clock = MovableClock()
    ticker = Ticker(
        symbol="BTCUSDT", price=1.0,
        timestamp=clock().replace(tzinfo=None),  # naive -> treated as UTC
        source="binance",
    )
    assert is_stale(ticker, max_age_seconds=5, now=clock()) is False
    clock.advance(5.1)
    assert is_stale(ticker, max_age_seconds=5, now=clock()) is True


# ---------------------------------------------------------------------------
# 4. Source refuses to serve data older than the age limit
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cached_source_refuses_old_cached_value() -> None:
    clock = MovableClock()
    upstream = FlakySource("binance", 1.0, now=clock)
    cached = CachedSource(upstream, now=clock, max_data_age_seconds=5)

    # Seed the cache with a value observed at t0.
    old = Ticker(symbol="BTCUSDT", price=64000.0, timestamp=clock(), source="binance")
    cached.seed(old)

    clock.advance(10.0)  # now the cached value is 10s old > limit 5s
    with pytest.raises(DataUnavailableError):   # refused — not served
        await cached.get_ticker("BTCUSDT")

    # The consumer-facing check agrees the value is stale.
    assert is_stale(old, max_age_seconds=5, now=clock()) is True


# ---------------------------------------------------------------------------
# 5. Cached fallback serves last-known, marked cached, when upstream is down
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cached_fallback_serves_last_known_marked_cached() -> None:
    clock = MovableClock()
    upstream = FlakySource("binance", 1.0, now=clock)
    cached = CachedSource(upstream, now=clock, max_data_age_seconds=5)

    seen = Ticker(symbol="BTCUSDT", price=64000.0, timestamp=clock(), source="binance")
    cached.seed(seen)

    # Upstream is down; within the age limit the last-known value is served.
    ticker = await cached.get_ticker("BTCUSDT")
    assert ticker.price == 64000.0
    assert ticker.cached is True                      # clearly marked cached
    assert ticker.source == "cache"
    # A consumer must treat it as non-tradeable / stale-capable.
    assert is_stale(ticker, now=clock()) is False     # young enough, but cached


@pytest.mark.asyncio
async def test_cached_fallback_no_cache_raises() -> None:
    clock = MovableClock()
    upstream = FlakySource("binance", 1.0, now=clock)
    cached = CachedSource(upstream, now=clock)
    with pytest.raises(DataUnavailableError):       # nothing cached to serve
        await cached.get_ticker("BTCUSDT")


@pytest.mark.asyncio
async def test_cached_fallback_passes_fresh_through_when_up() -> None:
    clock = MovableClock()
    upstream = TickerSource("binance", 72000.0, now=clock)
    cached = CachedSource(upstream, now=clock)
    ticker = await cached.get_ticker("BTCUSDT")
    assert ticker.price == 72000.0
    assert ticker.cached is False
    assert ticker.source == "binance"
    # Now the cache remembers it for later fallback.
    assert cached.last_ticker() is not None


# ---------------------------------------------------------------------------
# Live WebSocket stream (mocked socket)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_binance_ws_stream_yields_prices() -> None:
    clock = MovableClock()
    messages = [
        '{"e":"trade","s":"BTCUSDT","p":"65500.00"}',
        '{"e":"trade","s":"BTCUSDT","p":"65600.00"}',
        'garbage-not-json',                       # must be skipped, not invented
    ]
    ws = FakeWS(messages)

    async def factory(url: str):
        return ws

    src = BinanceSource(now=clock, ws_factory=factory, max_retries=1)
    collected = []
    async for ticker in src.stream_ticker("BTCUSDT"):
        collected.append(ticker)
        if len(collected) >= 2:
            break
    assert [t.price for t in collected] == [65500.0, 65600.0]
    assert all(t.source == "binance" for t in collected)
    assert all(t.timestamp.tzinfo is not None for t in collected)


# ---------------------------------------------------------------------------
# Realistic toolbar: the default stack built from Settings
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_build_default_source_stack_offline() -> None:
    from halaltrade.config import Settings
    from halaltrade.marketdata.factory import build_default_source

    clock = MovableClock()
    client = binance_mock_client()
    source = build_default_source(
        Settings(_env_file=None), now=clock, binance_client=client, coingecko_client=client
    )
    ticker = await source.get_ticker("BTCUSDT")
    assert ticker.price == 65000.0
    assert ticker.source == "binance"   # primary used
    assert ticker.timestamp.tzinfo is not None


@pytest.mark.asyncio
async def test_build_default_source_falls_back_to_coingecko_and_cache() -> None:
    from halaltrade.config import Settings
    from halaltrade.marketdata.factory import build_default_source

    clock = MovableClock()

    # Binance fails (451), CoinGecko succeeds via its own mock client.
    binance_bad = binance_mock_client(fail=True)
    cg_client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200, json={"bitcoin": {"usd": 77777.0}}
            )
        )
    )
    source = build_default_source(
        Settings(_env_file=None), now=clock,
        binance_client=binance_bad, coingecko_client=cg_client,
    )
    ticker = await source.get_ticker("BTCUSDT")
    assert ticker.price == 77777.0
    assert ticker.source == "coingecko"
    assert ticker.cached is False


@pytest.mark.asyncio
async def test_default_stack_serves_cached_when_everything_down() -> None:
    from halaltrade.config import Settings
    from halaltrade.marketdata.factory import build_default_source

    clock = MovableClock()
    bad = binance_mock_client(fail=True)
    bad_cg = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(500, json={})
        )
    )
    source = build_default_source(
        Settings(_env_file=None), now=clock,
        binance_client=bad, coingecko_client=bad_cg,
    )

    # Seed the cache with a fresh observed value, then everything goes down.
    from halaltrade.marketdata import Ticker
    source.seed(Ticker(symbol="BTCUSDT", price=63000.0, timestamp=clock(), source="binance"))

    ticker = await source.get_ticker("BTCUSDT")
    assert ticker.price == 63000.0
    assert ticker.cached is True        # served from cache, non-tradeable
    assert ticker.source == "cache"
