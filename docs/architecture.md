# HalalTrade AI — Architecture (Delivery 1)

## Principles

1. **AI recommends, policies decide.** Any AI/strategy output is just a `Signal`
   — a *recommendation*. It becomes a trade only after it clears every gate.
2. **Risk controls, execution acts, logs remember.** Gates constrain; execution
   confirms; every decision is recorded for audit.
3. **User keeps full control** of trade size and final authorization. Size must
   be explicit; there is no mechanism for the AI to size trades on its own.
4. **No fabrication.** Unknown state is "DATA UNAVAILABLE", never invented.
5. **Live trading disabled by default.** Only backtest and paper are in scope
   for Delivery 1.

## The four non-bypassable gates

Fixed order, nothing can be skipped, first failure => **NO TRADE**:

```
Signal -> ShariahGate -> RiskGate -> SecurityGate -> ExecutionValidationGate
```

| Gate | Enforces |
|------|----------|
| **Shariah** (FORCED) | Spot-only. Rejects futures/perpetual/leverage/margin/short/options/derivatives/staking/lending/borrowed. Any leverage != 1.0 rejected. No disable or bypass flag exists. |
| **Risk** | Hard user caps: position size, exposure, loss/trade, daily loss, drawdown, min balance. Mandatory stop-loss. Missing/exact size => reject (NO TRADE). |
| **Security** | Secrets from env only; API key withdrawal-restricted; system health valid. Invalid => reject and STOP (halts pipeline). |
| **ExecutionValidation** | Stale data (>5s) reject; BTCUSDT min notional; unique clientOrderId idempotency; order status must be CONFIRMED from source (never assumed). |

## Modules

```
backend/halaltrade/
├── config.py      Settings (pydantic-settings), env prefix HALAL_
├── models.py      pydantic v2 Signal + per-gate Result + PipelineResult
├── pipeline.py    fixed-order short-circuit runner
├── gates/
│   ├── base.py    Gate ABC, Context (account, idempotency, clock)
│   ├── shariah.py ShariahGate (FORCED)
│   ├── risk.py    RiskGate
│   ├── security.py SecurityGate
│   └── execution.py ExecutionValidationGate
└── db/
    ├── models.py  SQLAlchemy schema (audit_log, signals, shariah_checks,
    │              risk_events, trades, orders, emergency_events, system_events)
    └── recorder.py Optional persistence of pipeline results
```

## Notes / decisions

- **HOLD** signals intentionally fail the Risk gate (no explicit size) and so
  resolve to NO_TRADE — which is exactly the desired "do nothing" behavior.
- **Instrument types are an explicit enum** (allowlist semantics: only SPOT
  passes) so the Shariah rule is mechanical, not a keyword guess.
- **Idempotency**: a clientOrderId is registered only after the whole Execution
  gate passes, so a failed attempt doesn't poison a legitimate retry.
- **DB portability**: timestamps are ISO-8601 UTC strings and only generic
  SQLAlchemy types are used, so the schema migrates to PostgreSQL cleanly.

## Later deliveries (out of scope now)

FastAPI endpoints; market-data connector (REST+WS, fallbacks, stale protection);
backtest & paper engine; dashboard; notifications; live-safety progression
(Backtest -> Paper -> Extended Paper -> Small Live -> Scale).
