# Market Data — HalalTrade AI (Delivery 2)

The market-data module (`backend/halaltrade/marketdata/`) gets **fresh, reliable,
never-fabricated** BTC/USDT market data for the Spot-only, Shariah-compliant agent.
It places **no orders**. Live trading remains disabled.

## Interface

Every source implements `MarketDataSource` (in `base.py`):

```python
class MarketDataSource(ABC):
    async def get_ticker(self, symbol: str = "BTCUSDT") -> Ticker
    async def get_candles(self, symbol: str = "BTCUSDT",
                          timeframe: str = "1m", limit: int = 100) -> list[Candle]
    async def stream_ticker(self, symbol: str = "BTCUSDT") -> AsyncIterator[Ticker]  # optional
```

Returned models:

- `Ticker(symbol, price, timestamp, source, raw, cached)`
- `Candle(symbol, timeframe, open, high, low, close, volume, timestamp, source, raw)`

`timestamp` is **UTC and set to when the agent observed the data** (via an
injectable clock), so freshness is always checkable.

## Sources (in priority order)

| Order | Source                | Type                         | Key                  |
|-------|-----------------------|------------------------------|----------------------|
| 1     | `BinanceSource`       | REST `/api/v3` + WebSocket   | none (public)        |
| 2     | `CoinGeckoSource`     | REST `/api/v3` (keyless)     | none (public)        |
| 3     | `CachedSource`        | last-known value             | none                 |

WebSocket live price: `wss://stream.binance.com:9443/ws/btcusdt@trade`, reconnects
with capped exponential backoff on drop.

## Fallback chain

Built by `build_default_source(settings)` in `factory.py`:

```
CachedSource                       <- final responder (serves last-known, marked cached)
└─ FallbackChain [Binance, CoinGecko]   <- live primary, tries in order
```

If Binance throws (network, 451 geo-block, rate-limit, malformed), the chain
transparently tries CoinGecko, then the last-known cached value. **Every Ticker
carries its real `source`** (`"binance"`, `"coingecko"`, `"cache"`), so fallback
is visible to consumers and the audit log.

## Stale-data rules

- Every point is stamped with its observation time (UTC).
- `is_stale(ticker, max_age_seconds=5, now=None) -> bool` is the canonical
  consumer check: data older than the threshold must be refused.
- Real sources **never serve** data they cannot stand behind — on any failure or
  staleness they raise `DataUnavailableError`, never inventing a number.
- `CachedSource` serves its last-known value only while it is fresher than the
  age limit, and marks it `cached=True` / `source="cache"` so consumers know it
  is **not tradeable**. If the cached value is older than the limit it **refuses**
  (`DataUnavailableError`).

## Configuration (env-only, `HALAL_` prefix)

| Variable                         | Default                             | Purpose                          |
|----------------------------------|-------------------------------------|----------------------------------|
| `HALAL_MARKET_DATA_MAX_AGE_SECONDS` | `5`                             | refuse data older than this      |
| `HALAL_BINANCE_REST_BASE`        | `https://api.binance.com`           | Binance REST base                |
| `HALAL_BINANCE_WS_BASE`          | `wss://stream.binance.com:9443`     | Binance WebSocket base           |

Public market data needs **no API key**. Secrets are never hardcoded.

## Testing & offline guarantees

Every test in `tests/test_marketdata.py` mocks the network:

- REST via injected `httpx.AsyncClient` backed by `httpx.MockTransport`.
- WebSocket via an injected fake socket factory (no real socket).
- An injectable, manually-advanceable clock for deterministic timestamps.

Run everything: `cd backend && .venv/bin/python -m pytest` (all deliveries together).
