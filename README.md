# HalalTrade AI

A Shariah-compliant, SPOT-only BTC/USDT trading-agent foundation for **one owner**.

**Governing principle:** *AI recommends, policies decide, risk controls, execution acts,
logs remember, testing validates — and the user keeps full control of trade size and
final authorization.* The AI is NEVER given unrestricted authority over money.

This repository is the **MVP foundation (Deliveries 1–4)** — a clean, tested Python
library plus FastAPI endpoints and a live control dashboard. It implements the four
non-bypassable policy gates and the `Signal → Shariah → Risk → Security → Execution`
pipeline. Live trading ships **disabled by default**; only backtest and paper modes are
in scope.

## Layout

```
halaltrade/
├── backend/            Python 3.11+ library (FastAPI-ready), pytest suite
│   └── halaltrade/     the package: models, gates, pipeline, db, marketdata, paper, backtest, api
├── frontend/           control dashboard (TanStack Start / React / Tailwind), mirrors /home/team/shared/site
└── docs/               architecture + pipeline notes
```

## Frontend / dashboard

The control dashboard is a TanStack Start app served on **port 3000** (the team's public
site surface) and published to the live URL. It polls the FastAPI backend
(`http://127.0.0.1:8000`, overridable via `VITE_API_URL`) and provides:

- **Paper / Live indicator** (Live is DISABLED by default)
- **Emergency kill switch** — halts all trading end-to-end
- **Account status** — paper balance, BTC holdings, equity, realized P&L, fees
- **Market data** — BTC price, source, freshness (≤5s)
- **Trade form** — manual spot BUY/SELL with exact USDT amount + optional stop-loss
- **Backtest runner** — SMA-crossover simulation (clearly labeled, not a recommendation)
- **Audit log viewer** — every gate decision recorded

```
cd frontend
bun install
bun run publish   # builds + serves on port 3000 (re-publishes the live site)
```
The dashboard source in this repo mirrors `/home/team/shared/site` — when it changes,
re-run `bun run publish` there and mirror the source back into `frontend/`.

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
