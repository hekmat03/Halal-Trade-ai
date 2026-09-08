# HalalTrade AI

A Shariah-compliant, SPOT-only BTC/USDT trading-agent foundation for **one owner**.

**Governing principle:** *AI recommends, policies decide, risk controls, execution acts,
logs remember, testing validates — and the user keeps full control of trade size and
final authorization.* The AI is NEVER given unrestricted authority over money.

This repository is **Delivery 1 (MVP foundation)** — a clean, tested Python library
that implements the four non-bypassable policy gates and the `Signal → Shariah → Risk →
Security → Execution` pipeline. Live trading ships **disabled by default**; only
backtest and paper modes are in scope.

## Layout

```
halaltrade/
├── backend/            Python 3.11+ library (FastAPI-ready), pytest suite
│   └── halaltrade/     the package: models, gates, pipeline, db
├── frontend/           placeholder only — dashboard is a LATER delivery
└── docs/               architecture + pipeline notes
```

## Quick start (backend)

```bash
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill real values ONLY via environment variables
python -m pytest            # run the full test suite
```

## Safety invariants (non-negotiable)

- **Shariah Gate is FORCED** — there is no disable/bypass flag and none can be added.
- No futures, leverage, margin, short-selling, options, derivatives, staking, lending,
  or interest/riba. Spot BUY/SELL only.
- **Risk Gate** enforces hard user-configurable caps and a mandatory stop-loss on every
  trade. No size → **NO TRADE**.
- **Security Gate** — secrets come from environment variables only (never hardcoded),
  API keys are withdrawal-restricted, system health must be valid. Invalid → reject and STOP.
- **Execution Validation** — order status is NEVER assumed; it must be confirmed from the
  source. Stale data (>5s) and below-min-notional orders are rejected.
- If any gate fails → **NO TRADE**. No gate can be skipped.
