import { createServerFn } from "@tanstack/react-start";

/**
 * Server-side proxy to the HalalTrade FastAPI backend.
 *
 * The site (port 3000) keeps a SINGLE public origin. The browser never talks to
 * the backend directly — these server functions run on the site's server (Node)
 * and forward to the FastAPI backend (default http://127.0.0.1:8000), then return
 * the JSON to the client. This works whether the site is served locally or on the
 * published URL as long as the backend is reachable from the server.
 *
 * Configure the backend address with VITE_API_URL (defaults to localhost:8000).
 */

const API_BASE = process.env.VITE_API_URL || "http://127.0.0.1:8000";

export interface GateResult {
  gate_name: string;
  passed: boolean;
  reasons: string[];
  stopped: boolean;
}

export interface TradeResponse {
  executed: boolean;
  reason: string;
  decision: string;
  signal_id: string;
  order_id: string;
  kill_switch_on: boolean;
  gate_results: Record<string, GateResult>;
  fill: { side: string; price: number; quantity: number; notional: number; fee: number } | null;
  equity_after: number | null;
  realized_pnl: number | null;
}

export interface TradeInput {
  side: "BUY" | "SELL";
  amount: number;
  stop_loss?: number | null;
  instrument_type?: "SPOT";
  leverage?: number;
  symbol?: string;
  reason?: string;
}

const jsonHeaders: Record<string, string> = { "Content-Type": "application/json" };

/** GET /health */
export const getHealth = createServerFn().handler(async () => {
  try {
    const res = await fetch(`${API_BASE}/health`, { cache: "no-store" });
    return { ok: res.ok, status: res.status, body: (await res.json()) as Record<string, unknown> };
  } catch (e) {
    return { ok: false, status: 0, body: { error: String(e), reason: "backend unreachable" } as Record<string, unknown> };
  }
});

/** GET /status */
export const getStatus = createServerFn().handler(async () => {
  try {
    const res = await fetch(`${API_BASE}/status`, { cache: "no-store" });
    return { ok: res.ok, status: res.status, body: (await res.json()) as Record<string, unknown> };
  } catch (e) {
    return { ok: false, status: 0, body: { error: String(e), reason: "backend unreachable" } as Record<string, unknown> };
  }
});

/** GET /audit?limit=N */
export const getAudit = createServerFn()
  .validator((d: { limit: number }) => d)
  .handler(async ({ data }) => {
    try {
      const res = await fetch(`${API_BASE}/audit?limit=${data.limit}`, { cache: "no-store" });
      return { ok: res.ok, status: res.status, body: (await res.json()) as unknown[] };
    } catch (e) {
      return { ok: false, status: 0, body: [{ error: String(e), reason: "backend unreachable" }] as unknown[] };
    }
  });

/** POST /trade */
export const submitTrade = createServerFn({ method: "POST" })
  .validator((d: TradeInput) => d)
  .handler(async ({ data }) => {
    try {
      const res = await fetch(`${API_BASE}/trade`, {
        method: "POST",
        headers: jsonHeaders,
        body: JSON.stringify(data),
        cache: "no-store",
      });
      if (!res.ok) {
        try {
          const err = (await res.json()) as Record<string, unknown>;
          return { ok: false, status: res.status, executed: false, body: err, reason: `validation error (${res.status})` };
        } catch {
          return { ok: false, status: res.status, executed: false, body: {}, reason: `HTTP ${res.status}` };
        }
      }
      const body = (await res.json()) as TradeResponse;
      return { ok: true, status: res.status, executed: body.executed, body, reason: body.reason };
    } catch (e) {
      return { ok: false, status: 0, executed: false, body: null, reason: `backend unreachable: ${String(e)}` };
    }
  });

/** POST /kill */
export const setKill = createServerFn({ method: "POST" })
  .validator((d: { enabled: boolean }) => d)
  .handler(async ({ data }) => {
    try {
      const res = await fetch(`${API_BASE}/kill`, {
        method: "POST",
        headers: jsonHeaders,
        body: JSON.stringify({ enabled: data.enabled }),
        cache: "no-store",
      });
      return { ok: res.ok, status: res.status, body: (await res.json()) as Record<string, unknown> };
    } catch (e) {
      return { ok: false, status: 0, body: { error: String(e), reason: "backend unreachable" } as Record<string, unknown> };
    }
  });

/** GET /backtest */
export const runBacktest = createServerFn()
  .validator((d: { candles: number }) => d)
  .handler(async ({ data }) => {
    try {
      const res = await fetch(`${API_BASE}/backtest?candles=${data.candles}`, { cache: "no-store" });
      return { ok: res.ok, status: res.status, body: (await res.json()) as Record<string, unknown> };
    } catch (e) {
      return { ok: false, status: 0, body: { error: String(e), reason: "backend unreachable" } as Record<string, unknown> };
    }
  });
