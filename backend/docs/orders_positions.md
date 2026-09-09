# HalalTrade AI — Order + Position Management (Delivery 5)

Modules: `halaltrade/orders/` (`OrderManager`, `validation.py`) and
`halaltrade/positions/` (`PositionTracker`). Both run on the simulated
(paper/backtest) account only — **no live Binance execution** (paper-first).

## 1. Order lifecycle

Binance-shaped states, tracked by `OrderManager`:

```
NEW → PARTIALLY_FILLED → FILLED
NEW → CANCELED | EXPIRED | REJECTED
PARTIALLY_FILLED → CANCELED   (remainder cancelled after partial fills)
```

`FILLED / CANCELED / EXPIRED / REJECTED` are terminal — a terminal order never
changes state again (`cancel`/`expire`/`apply_fill` on one returns the recorded
view as-is). An order is *complete* only at `FILLED` (full execution confirmed).

## 2. Idempotency semantics

- `submit()` assigns a fresh UUID4 `clientOrderId` (`ht-<hex>`) when the caller
  does not supply one. A supplied id that was already seen returns the
  **original recorded result** — an idempotent retry never creates a duplicate
  order and never produces a second fill (`_results` map).
- Cancel is idempotent: cancelling a terminal order returns its current view.
- Cancelling / filling / expiring an **unknown** `clientOrderId` raises
  `KeyError` — status is never invented for an id that was never seen.
- Every submit / fill / cancel / expire event is recorded to the DB `orders`
  table (`record_order_event`) when a session is given; audit failures are
  logged and never break order flow.

## 3. Partial fills

`apply_fill(client_order_id, qty, price, is_final=...)` accumulates
`executed_qty` / `cummulative_quote_qty`. The order stays `PARTIALLY_FILLED`
until executed quantity reaches the ordered quantity (float-dust snapped shut)
→ `FILLED`. `is_final=True` may only be set from a **confirmed** fill report
(e.g. an execution report with status FILLED); the manager never
self-transitions. Non-positive fill qty/price raises `ValueError`.

## 4. TIF / POST_ONLY behavior

- Limit orders support TIF `GTC / IOC / FOK` (anything else is `REJECTED`).
  Pre-submission validation (`validation.py`) enforces: side BUY/SELL only,
  LIMIT needs a positive limit price, quantity positive and a multiple of the
  LOT_SIZE step (`Settings.qty_step`), notional ≥ `Settings.min_notional`
  (MIN_NOTIONAL). Validation is pure arithmetic — no network calls.
- A `POST_ONLY` limit that would cross the reference `market_price`
  immediately (BUY limit ≥ ref, SELL limit ≤ ref) is `REJECTED` as maker-only.
  Without a reference price the order rests — a cross is never assumed.
- `expire()` marks a resting order `EXPIRED` (GTC expiry / IOC-FOK unfilled
  window); the paper broker routes resting-limit expiry through it, while the
  backtest engine (which drives the same `SimAccount` directly) enforces
  stop-first intra-bar stop-loss / take-profit exits on every bar.

## 5. Cancel semantics

`cancel(client_order_id)` confirms cancellation of a live (`NEW` /
`PARTIALLY_FILLED`) order → `CANCELED`. Terminal orders return their recorded
view unchanged; unknown ids raise `KeyError` (a rejection, never invented
status). Cancel-by-`clientOrderId` is the only cancel path — matching the
unique-id discipline required for live safety.

## 6. Position tracking

`PositionTracker` wraps the `SimAccount` (single source of fill/economics
truth) and layers the position policy on top:

- `apply_buy_fill` / `apply_sell_fill` update the account **and** the position
  record (avg entry, stop, TP, optional trailing state). A SELL larger than
  holdings raises — **never shorts**.
- `on_price(price)` is the monitor pump the caller drives each tick
  (the paper broker calls it on every price update; the backtest engine on
  every bar). **Trigger order is stop-loss first, then take-profit, then
  trailing stop** — the first trigger wins (stop has priority over TP on the
  same print). Each trigger flattens through the simulated account and returns
  a `CloseReport`.
- Trailing stop: `TrailingConfig(enabled, distance, activation_profit)` —
  ratchets below the running peak only after the optional activation profit is
  reached.
- Dust: a residual below `qty_step` that cannot be traded is closed in **one**
  final fill via `flatten_dust` (or marked dust-closed when untradeable)
  instead of retrying failing orders. Normal-size residuals take the regular
  close path.

## 7. How position state feeds the Risk gate

`PositionTracker.risk_account(price)` builds the position-aware `RiskAccount`
snapshot the Risk gate (`gates/risk.py`) decides on:

- `current_position_value` = open BTC × price → **exposure cap**
  (`resulting exposure > max_exposure` rejects a BUY).
- `open_position_count` = 1 when holding, else 0 → **position cap**
  (`open_position_count >= max_open_positions`, default **1**, rejects new
  entries — single-asset bot).
- `base_holdings` = owned BTC → never-short SELL check.
- `realized_pnl_today` (tracker-owned daily bucket, baselined at creation so
  pre-existing history never leaks in) → **daily-loss limit**.

The paper broker submits each order through the OrderManager (idempotent) and
records fills into the tracker; the backtest engine drives the same
`SimAccount` directly with stop-first intra-bar exits through the same Risk
gate, so fill economics stay consistent by construction.

## 8. Invariants (non-negotiable)

1. **Long-only 1× spot.** SELL beyond holdings raises; no short, no leverage,
   no margin, no futures/derivatives — enforced by the Shariah gate, the Risk
   gate, validation, and the simulation alike.
2. **Never assume order status.** Transitions happen only inside `apply_fill`,
   `cancel`, `expire`, or an explicit rejection from a confirmed report.
3. **User sizes, engine never does.** Orders need an explicit quantity (or
   amount + price); nothing auto-sizes.
4. **Mandatory stop-loss discipline** and position-aware risk caps
   (`max_open_positions`, exposure, daily loss) apply to every fill path.
5. **No live Binance execution — paper-first.** These modules trade the
   simulated account only. Live trading ships disabled by default and requires
   explicit user authorization.
