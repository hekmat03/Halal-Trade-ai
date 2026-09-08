import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useState } from "react";

import {
  getAudit,
  getHealth,
  getStatus,
  runBacktest,
  setKill,
  submitTrade,
  type TradeInput,
} from "~/lib/api";

export const Route = createFileRoute("/")({
  component: Dashboard,
});

/* ------------------------------------------------------------------ types */

interface HealthBody {
  status: string;
  trading_mode: string;
  live_enabled: boolean;
  kill_switch: boolean;
  data_source: string;
  last_price: number | null;
  last_data_timestamp: string | null;
  data_fresh: boolean | null;
}

interface StatusBody {
  operating_mode: string;
  live_enabled: boolean;
  kill_switch: boolean;
  paper: {
    starting_balance: number;
    usdt_balance: number;
    btc_holdings: number;
    equity: number | string;
    realized_pnl: number;
    fees_paid: number;
  };
  positions: Array<{
    symbol: string;
    quantity: number;
    avg_entry_price: number;
    stop_loss: number | null;
    take_profit: number | null;
    unrealized_pnl: number | null;
  }>;
  data: { price: number | null; timestamp: string | null; source: string; fresh: boolean | null };
}

interface AuditRow {
  id: number;
  timestamp: string;
  event_type: string;
  signal_id: string | null;
  gate: string | null;
  passed: boolean | null;
  detail: string | null;
}

interface BacktestBody {
  simulation: boolean;
  mode: string;
  symbol: string;
  timeframe: string;
  candles_used: number;
  strategy: string;
  run_id: string;
  report: {
    starting_equity: number;
    ending_equity: number;
    total_return: number;
    pnl: number;
    trade_count: number;
    win_rate: number;
    profit_factor: number;
    max_drawdown: number;
    sharpe: number;
    fees_paid: number;
  } | null;
}

const fmt = (n: number | null | undefined, digits = 2): string =>
  n === null || n === undefined ? "—" : n.toLocaleString("en-US", { maximumFractionDigits: digits });

const fmtMoney = (n: number | string | null | undefined, digits = 2): string =>
  typeof n === "number" ? `$${fmt(n, digits)}` : n === null || n === undefined ? "—" : String(n);

function Badge({ on, label, offLabel }: { on: boolean; label: string; offLabel?: string }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold ${
        on ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/60 dark:text-emerald-200" : "bg-amber-100 text-amber-800 dark:bg-amber-900/60 dark:text-amber-200"
      }`}
    >
      <span className={`h-2 w-2 rounded-full ${on ? "bg-emerald-500" : "bg-amber-500"}`} />
      {on ? label : offLabel ?? label}
    </span>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-700 dark:bg-slate-900">
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500 dark:text-slate-400">{title}</h2>
      {children}
    </section>
  );
}

/* -------------------------------------------------------------- component */

function Dashboard() {
  const [health, setHealth] = useState<HealthBody | null>(null);
  const [status, setStatus] = useState<StatusBody | null>(null);
  const [audit, setAudit] = useState<AuditRow[]>([]);
  const [backtest, setBacktest] = useState<BacktestBody | null>(null);
  const [connectionNote, setConnectionNote] = useState<string | null>(null);

  const [killBusy, setKillBusy] = useState(false);
  const [btBusy, setBtBusy] = useState(false);
  const [tradeBusy, setTradeBusy] = useState(false);

  // trade form
  const [side, setSide] = useState<"BUY" | "SELL">("BUY");
  const [amount, setAmount] = useState("300");
  const [stopLoss, setStopLoss] = useState("");
  const [tradeResult, setTradeResult] = useState<{
    executed: boolean;
    decision: string;
    reason: string;
    gateResults: Record<string, { passed: boolean; reasons: string[] }>;
    fill: unknown;
    equityAfter: number | null;
  } | null>(null);

  async function refreshAll() {
    const [h, s, a] = await Promise.all([getHealth(), getStatus(), getAudit({ data: { limit: 25 } })]);
    const messages: string[] = [];
    if (!h.ok) messages.push(`health: ${(h.body as { reason?: string }).reason ?? "error"}`);
    if (!s.ok) messages.push(`status: ${(s.body as { reason?: string }).reason ?? "error"}`);
    if (!a.ok) messages.push(`audit: ${(a.body as { reason?: string }).reason ?? "error"}`);
    setConnectionNote(messages.length ? messages.join(" · ") : null);
    if (h.ok) setHealth(h.body as HealthBody);
    if (s.ok) setStatus(s.body as StatusBody);
    if (a.ok) setAudit(a.body as AuditRow[]);
  }

  // Load on mount and keep polling so status/freshness stay current.
  useEffect(() => {
    void refreshAll();
    const id = setInterval(() => void refreshAll(), 10000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function toggleKill() {
    setKillBusy(true);
    const next = !(status?.kill_switch ?? false);
    const r = await setKill({ data: { enabled: next } });
    if (r.ok) {
      await refreshAll();
    } else {
      setConnectionNote(`kill switch failed: ${(r.body as { reason?: string }).reason ?? "error"}`);
    }
    setKillBusy(false);
  }

  async function doTrade(e: React.FormEvent) {
    e.preventDefault();
    setTradeBusy(true);
    const input: TradeInput = {
      side,
      amount: Number(amount),
      instrument_type: "SPOT",
      leverage: 1,
      reason: "manual submission from dashboard",
    };
    if (stopLoss !== "") input.stop_loss = Number(stopLoss);
    const r = await submitTrade({ data: input });
    if (r.ok && r.body) {
      const b = r.body as {
        executed: boolean;
        decision: string;
        reason: string;
        gate_results: Record<string, { passed: boolean; reasons: string[] }>;
        fill: unknown;
        equity_after: number | null;
      };
      setTradeResult({
        executed: b.executed,
        decision: b.decision,
        reason: b.reason,
        gateResults: b.gate_results ?? {},
        fill: b.fill,
        equityAfter: b.equity_after,
      });
      await refreshAll();
    } else {
      setTradeResult({
        executed: false,
        decision: "NO_TRADE",
        reason: r.reason ?? "submission failed",
        gateResults: {},
        fill: null,
        equityAfter: null,
      });
    }
    setTradeBusy(false);
  }

  async function doBacktest() {
    setBtBusy(true);
    const r = await runBacktest({ data: { candles: 240 } });
    if (r.ok) setBacktest(r.body as BacktestBody);
    else setConnectionNote(`backtest failed: ${(r.body as { reason?: string }).reason ?? "error"}`);
    setBtBusy(false);
  }

  const killed = status?.kill_switch ?? health?.kill_switch ?? false;
  const liveEnabled = status?.live_enabled ?? health?.live_enabled ?? false;

  return (
    <div className="min-h-dvh bg-slate-50 text-slate-900 dark:bg-slate-950 dark:text-slate-100">
      <div className="mx-auto max-w-5xl px-4 py-8">
        {/* header */}
        <header className="mb-6 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-2xl font-bold">HalalTrade AI — Control Dashboard</h1>
            <p className="text-sm text-slate-500 dark:text-slate-400">
              Shariah-compliant BTC/USDT Spot · AI recommends, policies decide, you authorize
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Badge on={!liveEnabled} label="LIVE DISABLED" offLabel="LIVE DISABLED" />
            <Badge
              on={(status?.operating_mode ?? "PAPER") === "PAPER"}
              label="PAPER MODE"
              offLabel="PAPER MODE"
            />
            {connectionNote ? (
              <span className="rounded-full bg-rose-100 px-3 py-1 text-xs font-medium text-rose-700 dark:bg-rose-900/60 dark:text-rose-200">
                ⚠ backend unreachable
              </span>
            ) : (
              <button
                onClick={refreshAll}
                className="rounded-full border border-slate-300 px-3 py-1 text-xs font-medium hover:bg-slate-100 dark:border-slate-600 dark:hover:bg-slate-800"
              >
                ↻ Refresh
              </button>
            )}
          </div>
        </header>

        {connectionNote && (
          <div className="mb-6 rounded-xl border border-rose-300 bg-rose-50 p-4 text-sm text-rose-700 dark:border-rose-800 dark:bg-rose-950 dark:text-rose-200">
            <p className="font-semibold">FastAPI backend is not reachable.</p>
            <p className="mt-1">{connectionNote}. Start it with: <code className="rounded bg-rose-100 px-1 dark:bg-rose-900">.venv/bin/uvicorn halaltrade.api.main:app --host 127.0.0.1 --port 8000</code></p>
          </div>
        )}

        {/* kill switch */}
        <Card title="Emergency Kill Switch">
          <div className="flex flex-col items-start gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm text-slate-600 dark:text-slate-300">
                When engaged, <span className="font-semibold">all trading is halted — NO TRADE is enforced end-to-end.</span>
              </p>
              <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                State: {killed ? "ENGAGED — trading halted" : "released — paper trading allowed"}
              </p>
            </div>
            <button
              onClick={toggleKill}
              disabled={killBusy}
              className={`shrink-0 rounded-xl px-6 py-3 text-base font-bold text-white shadow transition ${
                killed
                  ? "bg-emerald-600 hover:bg-emerald-700"
                  : "bg-rose-600 hover:bg-rose-700 focus:ring-4 focus:ring-rose-300"
              } disabled:opacity-50 ${killed ? "" : "animate-pulse"}`}
            >
              {killBusy ? "…" : killed ? "RELEASE KILL SWITCH" : "⛔ ENGAGE KILL SWITCH"}
            </button>
          </div>
        </Card>

        {/* status grid */}
        <div className="mt-6 grid gap-4 sm:grid-cols-2">
          <Card title="Account Status">
            <dl className="space-y-2 text-sm">
              {[
                ["Operating mode", status?.operating_mode ?? "—"],
                ["Paper USDT balance", fmtMoney(status?.paper?.usdt_balance)],
                ["BTC holdings", fmt(status?.paper?.btc_holdings, 8)],
                ["Equity", fmtMoney(status?.paper?.equity)],
                ["Realized P&L", fmtMoney(status?.paper?.realized_pnl)],
                ["Fees paid", fmtMoney(status?.paper?.fees_paid)],
              ].map(([k, v]) => (
                <div key={k} className="flex justify-between border-b border-slate-100 pb-1 dark:border-slate-800">
                  <dt className="text-slate-500 dark:text-slate-400">{k}</dt>
                  <dd className="font-medium">{v}</dd>
                </div>
              ))}
            </dl>
          </Card>

          <Card title="Market data">
            <dl className="space-y-2 text-sm">
              {[
                ["BTC price", `$${fmt(status?.data?.price ?? health?.last_price ?? null, 2)}`],
                ["Source", status?.data?.source ?? health?.data_source ?? "—"],
                ["Fresh", status?.data?.fresh === null || status?.data?.fresh === undefined ? "—" : status.data.fresh ? "yes (≤5s)" : "STALE"],
                ["Observed at", status?.data?.timestamp ?? health?.last_data_timestamp ?? "—"],
              ].map(([k, v]) => (
                <div key={k} className="flex justify-between border-b border-slate-100 pb-1 dark:border-slate-800">
                  <dt className="text-slate-500 dark:text-slate-400">{k}</dt>
                  <dd className="font-medium">{v}</dd>
                </div>
              ))}
            </dl>
            <div className="mt-3">
              <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">Open position</p>
              {!status?.positions?.length ? (
                <p className="text-sm text-slate-400">No open positions.</p>
              ) : (
                status.positions.map((p, i) => (
                  <div key={i} className="rounded-lg bg-slate-50 p-2 text-xs dark:bg-slate-800">
                    <p>{p.symbol} · {fmt(p.quantity, 6)} BTC @ {fmtMoney(p.avg_entry_price, 0)}</p>
                    <p className="text-slate-500">stop {fmtMoney(p.stop_loss, 0)} · tp {fmtMoney(p.take_profit, 0)} · uPnL {fmtMoney(p.unrealized_pnl)}</p>
                  </div>
                ))
              )}
            </div>
          </Card>
        </div>

        {/* trade form */}
        <div className="mt-6 grid gap-4 lg:grid-cols-2">
          <Card title="Submit paper trade signal">
            <form onSubmit={doTrade} className="space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <label className="block text-sm">
                  <span className="text-slate-500 dark:text-slate-400">Side</span>
                  <select
                    value={side}
                    onChange={(e) => setSide(e.target.value as "BUY" | "SELL")}
                    className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-800"
                  >
                    <option value="BUY">BUY (long)</option>
                    <option value="SELL">SELL (close position)</option>
                  </select>
                </label>
                <label className="block text-sm">
                  <span className="text-slate-500 dark:text-slate-400">Amount (USDT)</span>
                  <input
                    type="number"
                    min="5"
                    step="any"
                    value={amount}
                    onChange={(e) => setAmount(e.target.value)}
                    className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-800"
                  />
                </label>
              </div>
              <label className="block text-sm">
                <span className="text-slate-500 dark:text-slate-400">Stop-loss (USDT price) — mandatory</span>
                <input
                  type="number"
                  step="any"
                  value={stopLoss}
                  onChange={(e) => setStopLoss(e.target.value)}
                  placeholder="e.g. 70000"
                  className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm dark:border-slate-600 dark:bg-slate-800"
                />
              </label>
              <button
                type="submit"
                disabled={tradeBusy || killed}
                className="w-full rounded-lg bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
              >
                {tradeBusy ? "Evaluating through the gates…" : killed ? "Trading halted (kill switch)" : "Run pipeline & paper fill"}
              </button>
              <p className="text-xs text-slate-400">
                Spot only, 1× leverage, no shorting. Live trading is disabled — this always executes on the paper broker.
              </p>
            </form>
          </Card>

          <Card title="Pipeline result">
            {tradeResult === null ? (
              <p className="text-sm text-slate-400">Submit a signal to see the full gate-by-gate result.</p>
            ) : (
              <div className="space-y-3">
                <div
                  className={`rounded-lg px-3 py-2 text-sm font-semibold ${
                    tradeResult.executed
                      ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/60 dark:text-emerald-200"
                      : "bg-rose-100 text-rose-800 dark:bg-rose-900/60 dark:text-rose-200"
                  }`}
                >
                  {tradeResult.executed ? "✓ EXECUTED (paper fill)" : "✗ NO TRADE"} — {tradeResult.decision}
                </div>
                {tradeResult.equityAfter !== null && (
                  <p className="text-sm">Equity after: <span className="font-semibold">{fmtMoney(tradeResult.equityAfter)}</span></p>
                )}
                <p className="text-sm text-slate-600 dark:text-slate-300">{tradeResult.reason}</p>
                {Object.entries(tradeResult.gateResults).map(([name, g]) => (
                  <div key={name} className="rounded-lg border border-slate-200 p-2 text-sm dark:border-slate-700">
                    <p className="font-semibold">
                      {name}{" "}
                      <span className={g.passed ? "text-emerald-600" : "text-rose-600"}>
                        {g.passed ? "PASS" : "REJECT"}
                      </span>
                    </p>
                    <ul className="mt-1 list-inside list-disc text-xs text-slate-500 dark:text-slate-400">
                      {g.reasons.map((r, i) => (
                        <li key={i}>{r}</li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </div>

        {/* backtest + audit */}
        <div className="mt-6 grid gap-4 lg:grid-cols-2">
          <Card title="Backtest (simulation)">
            <p className="mb-2 text-xs text-slate-400">
              Example SMA-crossover strategy on synthetic candles. <span className="font-semibold">Simulation only — not live, not a recommendation.</span>
            </p>
            <button
              onClick={doBacktest}
              disabled={btBusy}
              className="rounded-lg bg-slate-800 px-4 py-2 text-sm font-semibold text-white hover:bg-slate-700 disabled:opacity-50 dark:bg-slate-200 dark:text-slate-900 dark:hover:bg-white"
            >
              {btBusy ? "Running…" : "Run backtest"}
            </button>
            {backtest?.report && (
              <dl className="mt-3 space-y-1 text-sm">
                {[
                  ["Strategy", backtest.strategy],
                  ["Return", `${(backtest.report.total_return * 100).toFixed(2)}%`],
                  ["P&L", fmtMoney(backtest.report.pnl, 0)],
                  ["Trades", String(backtest.report.trade_count)],
                  ["Win rate", `${(backtest.report.win_rate * 100).toFixed(1)}%`],
                  ["Profit factor", fmt(backtest.report.profit_factor, 3)],
                  ["Max drawdown", `${(backtest.report.max_drawdown * 100).toFixed(2)}%`],
                  ["Sharpe", fmt(backtest.report.sharpe, 2)],
                ].map(([k, v]) => (
                  <div key={k} className="flex justify-between border-b border-slate-100 pb-1 dark:border-slate-800">
                    <dt className="text-slate-500 dark:text-slate-400">{k}</dt>
                    <dd className="font-medium">{v}</dd>
                  </div>
                ))}
              </dl>
            )}
          </Card>

          <Card title="Audit log">
            <div className="max-h-80 overflow-auto">
              {audit.length === 0 ? (
                <p className="text-sm text-slate-400">No audit entries yet.</p>
              ) : (
                <ul className="space-y-1">
                  {audit.map((row) => (
                    <li key={row.id} className="border-b border-slate-100 py-1 text-xs dark:border-slate-800">
                      <div className="flex items-center justify-between">
                        <span className="font-mono text-slate-500">{row.timestamp}</span>
                        <span
                          className={`rounded px-1.5 py-0.5 font-semibold ${
                            row.passed === false
                              ? "bg-rose-100 text-rose-700 dark:bg-rose-900/60 dark:text-rose-200"
                              : "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-300"
                          }`}
                        >
                          {row.event_type}
                          {row.gate ? ` · ${row.gate}` : ""}
                          {row.passed === null ? "" : row.passed ? " PASS" : " REJECT"}
                        </span>
                      </div>
                      {row.detail && <p className="mt-0.5 break-words text-slate-500 dark:text-slate-400">{row.detail}</p>}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </Card>
        </div>

        <footer className="mt-8 text-center text-xs text-slate-400">
          HalalTrade AI · Delivery 4 · Spot-only trading agent. No profits guaranteed — the owner keeps full control and final authorization.
        </footer>
      </div>
    </div>
  );
}

export default Dashboard;
