# HalalTrade AI — Paper & Backtest Engines

This document describes the two simulated trading engines in Delivery 3:

- **Paper engine** — `halaltrade/paper/broker.py` (`PaperBroker`)
- **Backtest engine** — `halaltrade/backtest/engine.py` (`BacktestEngine`)

Both trade the **same** simulated Spot account (`halaltrade/simulation/`), so the
fill, fee, slippage and P&L math is identical by construction. Neither touches
real money, neither places a live order, and neither is "live trading."

---

## 1. Paper trading is the DEFAULT mode

Live trading ships **disabled by default**. The configured trading mode is
`paper` (see `Settings.trading_mode`, default `"paper"`) and `live_enabled`
defaults to `False`. The progression is enforced as **Backtest → Paper →
Extended Paper → Small Live → Scale**, and only an explicit, deliberate opt-in
with valid restricted credentials ever moves towards live.

`PaperBroker` simulates Spot BUY/SELL fills against **live** market data from the
marketdata sources (REST + WebSocket with fallbacks and stale-data protection).
Every paper order is routed through the four-gate pipeline and an explicit
fill/confirmation step before the simulated account changes.

## 2. The manual-size rule — the engine never sizes, the user always does

Both engines enforce a single, hard sizing principle:

> **The user supplies the exact USDT amount.** The engine/strategy never decides
> how much to trade.

- In **paper**, a signal without an exact `amount` (or a valid `quantity × price`)
  is rejected by the Risk gate → `executed=False`, NO TRADE.
- In **backtest**, the run is given a fixed `BacktestConfig.trade_amount` (default
  `1000.0` USDT) and the engine applies that exact figure on each BUY. The
  strategy never sizes; the engine only executes the user-supplied amount.

Every simulated trade therefore spends exactly the user's chosen USDT figure
(plus the fee) and never auto-sizes above it.

## 3. Fees and slippage

Fees and slippage are **always a cost** and are applied to every fill. They live
in `halaltrade/simulation/fees.py` (`FeeConfig`) and are shared by both engines.

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `taker_bps` | `10` (0.10%) | Taker fee, mirrors Binance spot taker fee |
| `maker_bps` | `10` (0.10%) | Maker fee (reserved for future limit orders) |
| `slippage_bps` | `0` | Slippage budget; BUY fills at `quote*(1+slip)`, SELL at `quote*(1-slip)` |
| `bnb_discount_pct` | `0` | Optional fee discount as a % of the fee |

- A BUY of `amount` USDT fills at `buy_price(price)` and costs `amount + fee`.
- A SELL fills at `sell_price(price)`, and the fee is charged on the notional.
- `fees_paid` on the account and in every report is the sum of all per-fill fees.

## 4. Stop-loss and take-profit enforcement

A **mandatory stop-loss** is required on every non-HOLD trade — if the
user/strategy does not supply one, the Risk gate rejects the trade (NO TRADE).
In backtest, if a BUY signal arrives without a stop, the engine fills it in:
`stop = price * (1 - stop_loss_pct)` (default `2%`). An optional take-profit is
added as `price * (1 + take_profit_pct)` (default `5%`).

Exits are checked **intra-bar** on every candle after a position is open:

- If `candle.low <= stop_loss` → the position closes **at the stop price**
  (`STOP_LOSS`).
- Else if `candle.high >= take_profit` → the position closes **at the TP price**
  (`TAKE_PROFIT`).

A strategy-signal SELL (closing an owned position) is labeled `SIGNAL`, and a
position left open at the last bar is flattened at the final close (`FLAT_CLOSE`).

## 5. The four-gate pipeline guards every trade

Both engines run every (non-HOLD) signal through the same ordered, non-bypassable
pipeline — `Signal → Shariah → Risk → Security → Execution` — before any simulated
fill. **If any gate fails, the result is NO TRADE.**

- **Shariah** — Spot only, 1x, no futures/leverage/margin/short/derivatives/interest.
- **Risk** — explicit size, mandatory stop-loss, position/exposure/loss-drawdown caps.
- **Security** — restricted keys, no withdrawals, system healthy.
- **Execution** — fresh data, min notional, unique `clientOrderId`, confirmed status.

Because backtest candles carry a timestamp that is used both as the "data
timestamp" and as the gate clock, freshness always passes inside a backtest; gate
rejections there come from genuine policy violations (e.g. a FUTURES instrument,
or an order exceeding a risk cap).

## 6. Backtest determinism

`BacktestEngine.run(strategy, candles, config)` is **deterministic**:

- It runs a strategy forward over a supplied list of OHLCV `Candle`s.
- There is **no network** and **no randomness** in the core path — given the same
  candles + config, the same result is reproduced exactly.
- Candles come from the marketdata module or injected fixtures (in tests, always
  synthetic and offline).
- Fill prices, fees, equity, P&L and every report metric are computed from the
  candles and config only — nothing is invented.

The strategy is a pure callable `f(candles, position, equity) -> Signal` (see
`halaltrade/backtest/base.py`). Example strategies `make_sma_cross` and
`make_hold` live in `halaltrade/backtest/strategies.py` — they are clearly
labelled illustrative examples for testing only, not validated trading advice.

## 7. Performance report

`Build_report` in `halaltrade/backtest/performance.py` returns a
`PerformanceReport` with:

```
starting_equity, ending_equity, total_return, pnl,
trade_count, win_rate, profit_factor, max_drawdown, max_drawdown_usd,
sharpe, gross_profit, gross_loss, wins, losses, fees_paid,
closed_trades[], equity_curve[]
```

Metrics are computed solely from simulated fills and the mark-to-market equity
curve. When there are no closed trades, win rate and profit factor are reported
as `0` (DATA UNAVAILABLE-style) — never fabricated.

## 8. Hard, non-negotiable rules in both engines

- **No shorting.** A SELL can only ever dispose of an owned position; nothing can
  ever create a negative position.
- **No leverage / margin / futures** — Spot 1x only, enforced by the Shariah gate.
- **No trading on stale or unavailable data** — paper refuses stale source feeds.
- **Idempotent `clientOrderId`** — a duplicate cannot create a second fill.
- **No withdrawals, ever** — enforced by the Security gate.
- **Never assume a fill** — order status must be confirmed.
