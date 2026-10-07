# Live-trading safety progression, human confirmation and notifications (Delivery 7)

**Nothing in this delivery enables live trading.** It builds the machinery and the
gates that must be satisfied *before* a human is even asked to decide. Live
trading remains disabled by default, and this repository contains **no code path
that can place a real order on Binance mainnet** (`halaltrade.progression.LIVE_EXECUTION_IMPLEMENTED`
is a hard `False`; `live_execution_implemented()` always returns `False`).

The governing principle is unchanged: *AI recommends, policies decide, risk
controls, execution acts, logs remember, testing validates — and the user keeps
full control of size and final authorization.*

## The ladder

`BACKTEST -> PAPER -> EXTENDED_PAPER_30D -> SMALL_LIVE -> SCALE`

* A fresh or empty database means `BACKTEST` with live **off**.
* Stages can never be skipped, repeated or reversed: a request may only target
  the immediate next stage, and every stage entry is judged on its own evidence.
* Every refusal returns a **structured list of reasons** (`failed`), in the same
  style as `LiveReadinessResult`.

## Evidence required per stage

| Transition | Evidence that must be recorded first | Extra human step |
|---|---|---|
| → `PAPER` | backtest `run_id`, trades ≥ 20, profit factor > 1.0, max drawdown ≤ 30 %, walk-forward passed | confirmation token |
| → `EXTENDED_PAPER_30D` | paper ≥ 30 days, ≥ 30 trades, profit factor > 1.0, max drawdown ≤ 20 % | confirmation token |
| → `SMALL_LIVE` | extended-paper ≥ 30 days + ≥ 30 trades, walk-forward passed, **and** `LiveReadinessCheck.evaluate().ready` (all 8 spec conditions, reused verbatim) | confirmation token |
| → `SCALE` | small-live track record ≥ 30 days, ≥ 20 trades, profit factor > 1.0, max drawdown ≤ 20 %, plus an explicit clip inside the hard cap (default 250 USDT) | confirmation token **and** a separate `human_authorized_scale=true` authorization |
| live authorization | reach `SMALL_LIVE`/`SCALE` **and** `LiveReadinessCheck` ready (which itself requires `HALAL_LIVE_ENABLED=true`, both risk and Shariah acknowledgements, healthy system, withdrawal-restricted key) | confirmation token |

All numbers are bounded by `ProgressionLimits` and are shown to the user read-only
via `GET /progression`.

## How a human authorizes each step

1. `GET /progression` — current stage, evidence checklist, limits, readiness
   detail (every condition by name) and any pending confirmations.
2. `POST /progression/evidence` — record what was actually done (backtest/paper
   results, the two acknowledgements). Nothing is inferred or guessed.
3. `POST /progression/transition {target_stage, clip_usdt?}` — checked against the
   stage's rule. Answer: `REFUSED` + reasons, or `AWAITING_CONFIRMATION` + a
   single-use token.
4. `POST /progression/confirm {token, clip_usdt?, human_authorized_scale?}` —
   evidence is **re-checked** (an earlier pass is never trusted), then the stage
   is applied, persisted and notified. Tokens are single-use and expire
   (900 s default). A failed confirmation leaves the token unused so it can be
   retried after the missing evidence is recorded.
5. `POST /progression/live {enabled}` — `true` can only ever return `REFUSED`
   with reasons or `AWAITING_CONFIRMATION`; it never turns live on by itself.
   `false` is always allowed (the safe direction), and a kill-switch event
   withdraws live authorization automatically.

## Per-trade size and confirmation

The agent never chooses a size and never auto-executes:

1. `POST /trade/authorize {side, size, client_order_id}` — the user states the
   exact size; a single-use token bound to that size and `clientOrderId` is
   returned. A missing or non-positive size is refused, never defaulted.
2. `POST /trade {side, amount, stop_loss, client_order_id, human_confirmation_token}`
   — the token must exist, be unused, unexpired, for the `trade` purpose, and
   match both the size and the `clientOrderId`, or the trade is refused.

`/trade` stays paper-only and its behavior is unchanged when no token is
supplied. Confirmation becomes mandatory on every trade when
`HALAL_REQUIRE_TRADE_CONFIRMATION=true` (Settings field
`require_trade_confirmation`) or once live has been authorized.

## Notifications

`halaltrade.progression.notifications.ProgressionNotifier` delegates to the
existing fail-safe `TelegramNotifier` and swallows any exception, so a
notification can never block or crash a transition, a trade or a kill switch.
Alerts are sent for: stage transitions, pending confirmation requests,
live-enable attempts (success **and** refusal with reasons), live withdrawal and
kill-switch events. Alert types reuse existing `AlertType` members
(transitions → `STRATEGY_CHANGE`, refusals → `RISK_REJECTION`, confirmations →
`SYSTEM_ERROR`, safety stops → `EMERGENCY_STOP`).

## Persistence

`ProgressionEvent` (`progression_events`) mirrors the kill-switch pattern: an
append-only log where the latest row wins, so stage, live-authorization flag,
clip and the full evidence record survive a restart. An empty table = `BACKTEST`,
live off. Each write also appends an `audit_log` row (`PROGRESSION_*`), so the
dashboard's audit view shows every progression decision.
