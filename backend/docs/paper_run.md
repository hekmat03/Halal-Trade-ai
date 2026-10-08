# Delivery 8 — 30-day paper run on live market data + Telegram reports

**Simulated money only. Live trading stays OFF. There is no real-order path in
this script, or anywhere else in the repo** (`LIVE_EXECUTION_IMPLEMENTED` is
`False`, `HALAL_LIVE_ENABLED` defaults to `false`, and the run never touches a
Binance account endpoint or an API secret). If a run ever looks like it traded,
it traded paper USDT against live *public* market data.

`backend/run_paper_trading.py` is a long-running asyncio loop that:

1. reads a **live, non-cached** BTC/USDT ticker (stale or cached data ⇒ NO TRADE);
2. reads **closed** candles only — the still-forming bar is dropped, and bars
   older than `HALAL_PAPER_CANDLE_MAX_AGE_BARS` bar-durations are refused;
3. calls the configured strategy-library strategy on the closed bars
   (default `donchian_1d`, the Delivery 6 research winner);
4. stamps the owner's explicit USDT size + a mandatory stop-loss on the signal,
   then routes it through the unmodified four-gate pipeline
   (`Shariah → Risk → Security → Execution`) and the existing
   `halaltrade.paper.broker.PaperBroker` simulated fill path;
5. records progression evidence (paper days + trade P&L stats) so a later
   `LiveReadinessCheck` sees a real paper track record;
6. sends a daily and a weekly report when Telegram is configured.

The gates are never weakened or bypassed. Any refusal is logged with the
engine's own reason and the run takes NO TRADE.

## Starting the run (detached, survives the shell)

```bash
cd /home/team/shared/halaltrade/backend

export HALAL_TRADING_MODE=paper          # paper is the default; live stays OFF
export HALAL_LIVE_ENABLED=false
export HALAL_PAPER_STRATEGY=donchian_1d
export HALAL_PAPER_TIMEFRAME=1d
export HALAL_PAPER_TRADE_AMOUNT_USDT=100   # the owner's size per entry
export HALAL_PAPER_STARTING_USDT=1000      # simulated balance
export HALAL_PAPER_POLL_SECONDS=3600
# optional Telegram reports (all OFF unless these four are set):
export HALAL_TELEGRAM_ENABLED=false
export HALAL_TELEGRAM_BOT_TOKEN=
export HALAL_TELEGRAM_CHAT_ID=
export HALAL_TELEGRAM_ALERT_DAILY_PNL=false

setsid nohup .venv/bin/python run_paper_trading.py \
  --strategy donchian_1d \
  --symbol BTCUSDT \
  --timeframe 1d \
  --trade-usdt 100 \
  --starting-usdt 1000 \
  --poll-seconds 3600 \
  > /tmp/paper_run.log 2>&1 &
```

Follow it with `tail -f /tmp/paper_run.log`. `SIGINT`/`SIGTERM` shut the loop
down cleanly (`Ctrl-C` in the foreground; `kill <pid>` when detached) and write a
`paper_run_stop` audit event.

Useful flags: `--once` (one poll, then exit), `--check-data` (probe the data
sources and exit), `--max-iterations N` (stop after N polls), `--no-reports`,
`--no-db`, `--log-level DEBUG`, `--list-strategies`.

## Environment variables

All settings come from `halaltrade/config.py`, prefix `HALAL_`. Secrets live
only in env vars — never in code, never in the repo.

| Env var | Default | Meaning |
| --- | --- | --- |
| `HALAL_TRADING_MODE` | `paper` | `paper` only in scope; live is disabled |
| `HALAL_LIVE_ENABLED` | `false` | master switch for live; must stay false |
| `HALAL_PAPER_SYMBOL` | `BTCUSDT` | instrument |
| `HALAL_PAPER_STARTING_USDT` | `1000.0` | simulated starting balance |
| `HALAL_PAPER_STRATEGY` | `donchian_1d` | strategy registry name |
| `HALAL_PAPER_TIMEFRAME` | `1d` | candle timeframe for the paper clock |
| `HALAL_PAPER_TRADE_AMOUNT_USDT` | `100.0` | **the owner's explicit size** per entry |
| `HALAL_PAPER_STOP_LOSS_PCT` | `0.02` | mandatory stop when a signal carries none |
| `HALAL_PAPER_POLL_SECONDS` | `3600.0` | how often the market is re-read |
| `HALAL_PAPER_CANDLE_MAX_AGE_BARS` | `2.0` | refuse bars older than N bar-durations |
| `HALAL_PAPER_DAILY_REPORT_UTC` | `18:00` | daily report time (UTC) |
| `HALAL_PAPER_WEEKLY_REPORT_UTC` | `12:00` | weekly report time (UTC) |
| `HALAL_PAPER_WEEKLY_REPORT_DAY` | `SUN` | weekly report weekday (UTC) |
| `HALAL_TELEGRAM_ENABLED` | `false` | notifications master switch |
| `HALAL_TELEGRAM_BOT_TOKEN` | `""` | bot token (env only, never committed) |
| `HALAL_TELEGRAM_CHAT_ID` | `""` | destination chat |
| `HALAL_TELEGRAM_ALERT_DAILY_PNL` | `false` | gate for the daily/weekly report alerts |
| `HALAL_TELEGRAM_ALERT_STRATEGY_CHANGE` | `false` | alert toggle |
| `HALAL_TELEGRAM_ALERT_EMERGENCY_STOP` | `false` | alert toggle |
| `HALAL_TELEGRAM_ALERT_RISK_REJECTION` | `false` | alert toggle |
| `HALAL_TELEGRAM_ALERT_SYSTEM_ERROR` | `false` | alert toggle |
| `HALAL_QTY_STEP` | `0.00001` | BTCUSDT LOT_SIZE step used to align order size |
| `HALAL_MIN_NOTIONAL` | `5.0` | Binance minimum order notional (USDT) |
| `HALAL_MAX_DATA_AGE_SECONDS` | `5` | execution gate: reject signals older than this |
| `HALAL_MARKET_DATA_MAX_AGE_SECONDS` | `5` | refuse market data older than this |
| `HALAL_BINANCE_REST_BASE` | `https://api.binance.com` | public REST base (probed/overridable) |
| `HALAL_BINANCE_WS_BASE` | `wss://stream.binance.com:9443` | public WS base |
| `HALAL_DATABASE_URL` | `sqlite:///halaltrade.db` | audit + progression persistence |

### Order size and the LOT_SIZE step

The owner sizes every trade in USDT (`--trade-usdt`), but Binance requires the
*derived* quantity (`amount / price`) to be an exact multiple of
`HALAL_QTY_STEP`, and the repo enforces that in
`halaltrade/orders/validation.py` before any order is submitted. At a real BTC
price not every USDT amount lands on a step boundary, so the run rounds the
amount **down** to the largest step-aligned quantity — never up, so the user's
size cap is never breached. The requested amount is always recorded in the
signal metadata (`requested_amount_usdt`, `aligned_quantity`, `qty_step`), so
the rounding is visible and never silent. If the alignment cannot produce a
valid order the raw amount is passed through and the engine's own validation
refuses it with its own reason.

## How the 30-day clock accumulates

* On start the run writes a `paper_run_start` audit event carrying
  `paper_start=<UTC timestamp>` into the progression store.
* `paper_days` = `now − paper_start`, in UTC days.
* On every restart the run **recovers** `paper_start` from the audit trail
  (`paper_run_start` / `paper_run_resume` events) rather than resetting it, so
  days accumulate across restarts, crashes and redeploys. Only if the trail has
  no usable stamp (for example `--no-db`) does the clock start at "now".
* Once `paper_days >= 30` the run asks the progression machine for
  `EXTENDED_PAPER_30D`. **The machine decides**: it applies the real limits
  (≥30 days, ≥30 paper trades, profit factor ≥ 1.0, max drawdown ≤ 20%) and the
  result is logged as `EXTENDED_PAPER_30D transition: ...`. A time-based request
  that the machine refuses changes nothing.
* `HALAL_LIVE_ENABLED`/live credentials are irrelevant here: nothing in this
  path can enable live trading, and the progression machine still requires the
  owner's explicit confirmations for any later stage.

## Report schedule

* Daily report: `HALAL_PAPER_DAILY_REPORT_UTC` (default **18:00 UTC**).
* Weekly report: `HALAL_PAPER_WEEKLY_REPORT_DAY` + `HALAL_PAPER_WEEKLY_REPORT_UTC`
  (default **Sunday 12:00 UTC**).
* Each report is built from the simulated-equity tracker (trades, win rate,
  profit factor, drawdown, equity, open position, stage, paper days, data
  source) and covers the trailing 1 day / 7 days.
* A missed slot is caught up once the loop next runs; already-sent slots are
  de-duplicated, so restarts never double-send.

### Telegram caveat

Reports are delivered **only** when all of `HALAL_TELEGRAM_ENABLED=true`,
`HALAL_TELEGRAM_BOT_TOKEN`, `HALAL_TELEGRAM_CHAT_ID` and
`HALAL_TELEGRAM_ALERT_DAILY_PNL=true` are set. Otherwise the report is still
built in full and logged with `delivered: false` in the run's report log — the
run never blocks, never retries forever, and never fails because Telegram is
missing or unreachable. The token is a secret: env var only.

## Verification commands

```bash
cd /home/team/shared/halaltrade/backend

# 1. Which public data sources work from this host? (no trading, exits after)
.venv/bin/python run_paper_trading.py --check-data

# 2. One real evaluation of the latest closed bar against live data, then exit
.venv/bin/python run_paper_trading.py --once --log-level INFO

# 3. Full offline test suite (no network required)
.venv/bin/python -m pytest -q -p no:warnings
```

`--check-data` prints each candidate REST base with its real error. At the time
of writing, from this host: `data-api.binance.vision` serves BTCUSDT klines
(HTTP 200, ~0.4 s) and is used for market data; `api.binance.com` is
geo-blocked here (HTTP 451); CoinGecko serves a ticker but no OHLCV, so it is a
partial fallback only. A source that cannot serve **both** a ticker and candles
is never selected for a trade decision.

## Proving it is paper

* No live order path exists in the script: fills come from
  `PaperBroker` + `OrderManager` simulation, and every fill is logged as
  `PAPER FILL ...`.
* Balances/equity in the logs and reports are `PAPER` (simulated) USDT.
* Staleness protection is never relaxed: a cached ticker, an unclosed bar, or a
  bar older than the configured max age produces `DATA_UNAVAILABLE` / NO TRADE.
* The strategy recommends, the gates decide, the owner sizes — see
  `docs/backtest_paper.md`, `docs/live_safety_progression.md` and
  `docs/orders_positions.md` for the surrounding machinery.
