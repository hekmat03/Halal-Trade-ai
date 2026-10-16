import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";

import { Sidebar, type NavSection } from "~/components/Sidebar";
import {
  AnimatedMetric,
  AreaSparkline,
  GlassCard,
  KeyValue,
  MetricCard,
  MiniBars,
  PulseDot,
  StatusPill,
} from "~/components/ui";
import { useSampleHistory } from "~/hooks/useSampleHistory";
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

/* ------------------------------------------------------------------ types
   Unchanged data shapes: the presentation layer renders exactly what
   src/lib/api.ts returns. Nothing is fabricated anywhere below — unknown
   values render as "—" (DATA UNAVAILABLE), never as invented numbers.
   ---------------------------------------------------------------------- */

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

/* -------------------------------------------------------------- helpers */

const fmt = (n: number | null | undefined, digits = 2): string =>
  n === null || n === undefined || !Number.isFinite(Number(n))
    ? "—"
    : Number(n).toLocaleString("en-US", { maximumFractionDigits: digits });

const asNum = (n: number | string | null | undefined): number | null => {
  if (n === null || n === undefined || n === "") return null;
  const v = typeof n === "number" ? n : Number(n);
  return Number.isFinite(v) ? v : null;
};

const fmtMoney = (n: number | string | null | undefined, digits = 2): string => {
  const v = asNum(n);
  if (v !== null) return `$${fmt(v, digits)}`;
  if (n === null || n === undefined || n === "") return "—";
  return String(n);
};

const fmtUsdt = (v: number) => `$${v.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const fmtInt = (v: number) => Math.round(v).toLocaleString("en-US");
const fmtPct = (v: number) => `${v.toFixed(2)}%`;
const fmtRatio = (v: number) => v.toFixed(2);
const fmtBtc = (v: number) => v.toFixed(6);

const pnlTone = (n: number | null | undefined): "pos" | "neg" | "neutral" =>
  n === null || n === undefined ? "neutral" : n > 0 ? "pos" : n < 0 ? "neg" : "neutral";

const SECTIONS: NavSection[] = [
  { id: "overview", label: "Overview", hint: "KPIs" },
  { id: "safety", label: "Kill switch", hint: "halt" },
  { id: "market", label: "Market & account", hint: "live" },
  { id: "trade", label: "Submit signal", hint: "gates" },
  { id: "pipeline", label: "Pipeline result", hint: "audit" },
  { id: "backtest", label: "Backtest", hint: "sim" },
  { id: "audit", label: "Audit log", hint: "trail" },
];

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
  const [refreshing, setRefreshing] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);

  const equitySamples = useSampleHistory(60);
  const priceSamples = useSampleHistory(60);

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
    if (s.ok) {
      const body = s.body as StatusBody;
      setStatus(body);
      // Record only values the backend actually returned, for the session chart.
      equitySamples.push(asNum(body.paper?.equity) ?? asNum(body.paper?.usdt_balance));
      priceSamples.push(asNum(body.data?.price));
    } else if (h.ok) {
      priceSamples.push(asNum((h.body as unknown as HealthBody).last_price));
    }
    if (a.ok) setAudit(a.body as AuditRow[]);
    setLastUpdated(new Date().toLocaleTimeString("en-US", { hour12: false }));
  }

  // Load on mount and keep polling so status/freshness stay current.
  useEffect(() => {
    void refreshAll();
    const id = setInterval(() => void refreshAll(), 10000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function manualRefresh() {
    setRefreshing(true);
    await refreshAll();
    setRefreshing(false);
  }

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
  const operatingMode = status?.operating_mode ?? "PAPER";
  const connected = connectionNote === null;

  const equity = asNum(status?.paper?.equity);
  const realized = asNum(status?.paper?.realized_pnl);
  const usdt = asNum(status?.paper?.usdt_balance);
  const btc = asNum(status?.paper?.btc_holdings);
  const price = asNum(status?.data?.price ?? health?.last_price);
  const fresh = status?.data?.fresh ?? health?.data_fresh ?? null;
  const report = backtest?.report ?? null;

  const auditStats = useMemo(() => {
    const passes = audit.filter((r) => r.passed === true).length;
    const rejects = audit.filter((r) => r.passed === false).length;
    const byGate = new Map<string, number>();
    for (const row of audit) {
      const key = row.gate ?? row.event_type ?? "other";
      byGate.set(key, (byGate.get(key) ?? 0) + 1);
    }
    return {
      total: audit.length,
      passes,
      rejects,
      gates: [...byGate.entries()].sort((a, b) => b[1] - a[1]).slice(0, 6),
    };
  }, [audit]);

  return (
    <div className="min-h-dvh bg-slate-950 text-slate-100">
      <Sidebar
        sections={SECTIONS}
        killed={killed}
        liveEnabled={liveEnabled}
        operatingMode={operatingMode}
        connectionOk={connected}
        tradeBusy={tradeBusy || refreshing}
        onRefresh={() => void manualRefresh()}
      />

      <main className="md:pl-[17rem]">
        <div className="page-enter mx-auto max-w-[95rem] px-4 pb-16 pt-6 sm:px-6 lg:px-8">
          {/* ------------------------------------------------------ header */}
          <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
            <div className="min-w-0">
              <p className="text-[11px] font-semibold uppercase tracking-[0.22em] text-cyan-400/80">
                HalalTrade AI · Delivery 4
              </p>
              <h1 className="fluid-h1 mt-1 font-extrabold text-white">Control Dashboard</h1>
              <p className="fluid-body mt-1.5 max-w-2xl text-slate-400">
                Shariah-compliant BTC/USDT <span className="text-slate-200">spot</span> trading agent — AI recommends,
                policies decide, risk controls, <span className="text-slate-200">you authorize</span>.
              </p>
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <StatusPill tone={operatingMode === "PAPER" ? "info" : "warn"}>
                {operatingMode === "PAPER" && <PulseDot tone="info" />}
                {operatingMode} MODE
              </StatusPill>
              <StatusPill tone={liveEnabled ? "reject" : "pass"}>
                {liveEnabled ? "LIVE ENABLED" : "LIVE DISABLED"}
              </StatusPill>
              <StatusPill tone={fresh === true ? "pass" : fresh === false ? "reject" : "neutral"}>
                DATA {fresh === true ? "FRESH" : fresh === false ? "STALE" : "UNKNOWN"}
              </StatusPill>
              <span className="hidden text-[11px] text-slate-500 sm:inline">
                {lastUpdated ? `updated ${lastUpdated} · polls every 10s` : "polling every 10s"}
              </span>
              <button onClick={() => void manualRefresh()} disabled={refreshing} className="btn-ghost min-h-[44px] rounded-xl px-4 text-sm font-medium disabled:opacity-60">
                {refreshing ? "Refreshing…" : "↻ Refresh"}
              </button>
            </div>
          </header>

          {/* --------------------------------------------- connection note */}
          {connectionNote && (
            <div className="gradient-border mb-6 flex flex-wrap items-start gap-3 rounded-2xl border border-rose-800/60 bg-rose-950/40 p-4 backdrop-blur-md animate-fade-up">
              <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-rose-500/15 text-rose-300">
                <PulseDot tone="reject" />
              </span>
              <div className="min-w-0 text-sm">
                <p className="font-semibold text-rose-200">FastAPI backend is not reachable.</p>
                <p className="mt-1 break-words text-rose-200/80">{connectionNote}</p>
                <p className="mt-2 text-xs text-rose-200/70">
                  Start it with:{" "}
                  <code className="rounded bg-rose-900/60 px-1.5 py-0.5 font-mono text-[11px] text-rose-100">
                    .venv/bin/uvicorn halaltrade.api.main:app --host 127.0.0.1 --port 8000
                  </code>
                </p>
              </div>
            </div>
          )}

          {/* ------------------------------------------------------ KPIs */}
          <section id="overview" className="scroll-mt-24">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
              <MetricCard label="Paper equity" value={equity} format={fmtUsdt} tone="accent" pulse={connected} hint={status ? "from /status" : "awaiting backend"} />
              <MetricCard
                label="Realized P&L"
                value={realized}
                format={(v) => `${v < 0 ? "−" : "+"}${fmtUsdt(Math.abs(v))}`}
                tone={pnlTone(realized)}
                hint="closed trades, paper broker"
              />
              <MetricCard label="Paper USDT" value={usdt} format={fmtUsdt} hint="available balance" />
              <MetricCard label="BTC holdings" value={btc} format={fmtBtc} hint={status?.positions?.length ? `${status.positions.length} open position(s)` : "no open positions"} />
            </div>
          </section>

          {/* -------------------------------------------- kill switch ---- */}
          <section id="safety" className="mt-6 scroll-mt-24">
            <GlassCard
              title="Emergency Kill Switch"
              subtitle="When engaged, all trading is halted — NO TRADE is enforced end-to-end."
              actions={
                <StatusPill tone={killed ? "reject" : "pass"}>
                  {killed && <PulseDot tone="reject" />}
                  {killed ? "ENGAGED" : "RELEASED"}
                </StatusPill>
              }
            >
              <div className="flex flex-col items-start gap-4 sm:flex-row sm:items-center sm:justify-between">
                <div className="space-y-1">
                  <p className="text-sm text-slate-300">
                    State:{" "}
                    <span className={killed ? "font-semibold text-rose-300" : "font-semibold text-emerald-300"}>
                      {killed ? "ENGAGED — trading halted" : "released — paper trading allowed"}
                    </span>
                  </p>
                  <p className="text-xs text-slate-500">
                    POST /kill · the paper agent keeps running while halted; no signal can pass the security gate.
                  </p>
                </div>
                <button
                  onClick={() => void toggleKill()}
                  disabled={killBusy}
                  className={`min-h-[52px] shrink-0 rounded-xl px-7 text-base font-bold transition disabled:opacity-50 ${
                    killed ? "btn-safe" : "btn-danger"
                  }`}
                >
                  {killBusy ? (
                    <span className="flex items-center gap-2">
                      <PulseDot tone="warn" />
                      {killed ? "Releasing…" : "Engaging…"}
                    </span>
                  ) : killed ? (
                    "RELEASE KILL SWITCH"
                  ) : (
                    "⛔ ENGAGE KILL SWITCH"
                  )}
                </button>
              </div>
            </GlassCard>
          </section>

          {/* ------------------------------------------ market & account -- */}
          <section id="market" className="mt-6 scroll-mt-24">
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
              <GlassCard title="Account status" subtitle="Paper broker snapshot" className="lg:col-span-1">
                <dl>
                  <KeyValue label="Operating mode" value={status?.operating_mode ?? "—"} />
                  <KeyValue label="Starting balance" value={fmtMoney(status?.paper?.starting_balance)} />
                  <KeyValue label="Paper USDT balance" value={fmtMoney(status?.paper?.usdt_balance)} />
                  <KeyValue label="BTC holdings" value={fmt(status?.paper?.btc_holdings, 8)} />
                  <KeyValue label="Equity" value={fmtMoney(status?.paper?.equity)} />
                  <KeyValue
                    label="Realized P&L"
                    value={fmtMoney(status?.paper?.realized_pnl)}
                    tone={pnlTone(realized)}
                  />
                  <KeyValue label="Fees paid" value={fmtMoney(status?.paper?.fees_paid)} />
                </dl>
              </GlassCard>

              <GlassCard
                title="Market data"
                subtitle="BTC/USDT spot · stale data (>5s) blocks trading"
                className="lg:col-span-1"
                actions={
                  <StatusPill tone={fresh === true ? "pass" : fresh === false ? "reject" : "neutral"}>
                    {fresh === true ? "≤5s FRESH" : fresh === false ? "STALE" : "UNKNOWN"}
                  </StatusPill>
                }
              >
                <p className="fluid-metric font-bold text-white">
                  <AnimatedMetric value={price} format={(v) => `$${v.toLocaleString("en-US", { maximumFractionDigits: 2 })}`} />
                </p>
                <dl className="mt-3">
                  <KeyValue label="Source" value={status?.data?.source ?? health?.data_source ?? "—"} />
                  <KeyValue label="Observed at" value={status?.data?.timestamp ?? health?.last_data_timestamp ?? "—"} />
                  <KeyValue label="Trading mode" value={health?.trading_mode ?? "—"} />
                </dl>
                <div className="mt-4">
                  <p className="mb-2 text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">
                    BTC price — this session's polls
                  </p>
                  <AreaSparkline
                    points={priceSamples.points}
                    gradientId="price"
                    stroke="#22d3ee"
                    label="BTC price samples observed this session"
                    emptyLabel="Awaiting live samples — no price returned yet"
                    format={(v) => `$${v.toLocaleString("en-US", { maximumFractionDigits: 0 })}`}
                  />
                </div>
              </GlassCard>

              <GlassCard
                title="Paper equity — this session"
                subtitle="Sampled from /status on every poll. No historical curve is invented."
                className="lg:col-span-1"
              >
                <AreaSparkline
                  points={equitySamples.points}
                  gradientId="equity"
                  stroke="#fbbf24"
                  label="Paper equity samples observed this session"
                  emptyLabel="Awaiting live samples — no equity returned yet"
                  format={(v) => `$${v.toLocaleString("en-US", { maximumFractionDigits: 2 })}`}
                />
                <div className="mt-4 space-y-2">
                  <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">Open position</p>
                  {!status?.positions?.length ? (
                    <p className="rounded-xl border border-dashed border-slate-700/70 bg-slate-950/40 px-3 py-3 text-sm text-slate-500">
                      No open positions.
                    </p>
                  ) : (
                    status.positions.map((p, i) => (
                      <div key={i} className="rounded-xl border border-slate-800 bg-slate-900/60 p-3 text-xs">
                        <div className="flex items-center justify-between">
                          <span className="font-semibold text-slate-100">{p.symbol}</span>
                          <StatusPill tone={(p.unrealized_pnl ?? 0) >= 0 ? "pass" : "reject"}>
                            uPnL {fmtMoney(p.unrealized_pnl)}
                          </StatusPill>
                        </div>
                        <p className="mt-2 text-slate-400">
                          {fmt(p.quantity, 6)} BTC @ {fmtMoney(p.avg_entry_price, 2)}
                        </p>
                        <p className="mt-1 text-slate-500">
                          stop {fmtMoney(p.stop_loss, 2)} · tp {fmtMoney(p.take_profit, 2)}
                        </p>
                      </div>
                    ))
                  )}
                </div>
              </GlassCard>
            </div>
          </section>

          {/* ------------------------------------------------- trade form -- */}
          <section id="trade" className="mt-6 scroll-mt-24">
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <GlassCard
                title="Submit paper trade signal"
                subtitle="Spot only · 1× leverage · no shorting · always fills on the paper broker"
              >
                <form onSubmit={doTrade} className="space-y-4">
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                    <label className="block text-sm">
                      <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-400">Side</span>
                      <select
                        value={side}
                        onChange={(e) => setSide(e.target.value as "BUY" | "SELL")}
                        className="mt-1.5 min-h-[44px] w-full rounded-xl border border-slate-700 bg-slate-950/70 px-3 text-sm text-slate-100 transition focus:border-cyan-500/70 focus:ring-2 focus:ring-cyan-500/25"
                      >
                        <option value="BUY">BUY (open long)</option>
                        <option value="SELL">SELL (close position)</option>
                      </select>
                    </label>
                    <label className="block text-sm">
                      <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-400">
                        Amount (USDT)
                      </span>
                      <input
                        type="number"
                        min="5"
                        step="any"
                        value={amount}
                        onChange={(e) => setAmount(e.target.value)}
                        className="tabular mt-1.5 min-h-[44px] w-full rounded-xl border border-slate-700 bg-slate-950/70 px-3 text-sm text-slate-100 transition focus:border-cyan-500/70 focus:ring-2 focus:ring-cyan-500/25"
                      />
                    </label>
                  </div>

                  <label className="block text-sm">
                    <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-400">
                      Stop-loss (USDT price) — mandatory
                    </span>
                    <input
                      type="number"
                      step="any"
                      value={stopLoss}
                      onChange={(e) => setStopLoss(e.target.value)}
                      placeholder="e.g. 70000"
                      className="tabular mt-1.5 min-h-[44px] w-full rounded-xl border border-slate-700 bg-slate-950/70 px-3 text-sm text-slate-100 transition placeholder:text-slate-600 focus:border-cyan-500/70 focus:ring-2 focus:ring-cyan-500/25"
                    />
                  </label>

                  <div className="flex flex-wrap items-center gap-2 rounded-xl border border-slate-800 bg-slate-950/50 px-3 py-2 text-[11px] text-slate-400">
                    <StatusPill tone="info">SPOT</StatusPill>
                    <StatusPill tone="neutral">LEVERAGE 1×</StatusPill>
                    <StatusPill tone="pass">NO SHORTING</StatusPill>
                    <span className="ml-auto">Shariah gate: halal instruments only</span>
                  </div>

                  <button
                    type="submit"
                    disabled={tradeBusy || killed}
                    className="btn-primary min-h-[52px] w-full rounded-xl px-4 text-sm font-bold disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {tradeBusy ? (
                      <span className="flex items-center justify-center gap-2">
                        <PulseDot tone="info" />
                        Evaluating through the gates…
                      </span>
                    ) : killed ? (
                      "Trading halted (kill switch engaged)"
                    ) : (
                      "Run pipeline & paper fill"
                    )}
                  </button>

                  <p className="text-[11px] leading-relaxed text-slate-500">
                    Live trading is disabled — this always executes on the paper broker. The owner keeps full control of
                    size and final authorization.
                  </p>
                </form>
              </GlassCard>

              <GlassCard
                id="pipeline"
                title="Pipeline result"
                subtitle="Signal → Shariah → Risk → Security → Execution, gate by gate"
                className="scroll-mt-24"
                actions={
                  tradeBusy ? (
                    <StatusPill tone="info">
                      <PulseDot tone="info" /> RUNNING
                    </StatusPill>
                  ) : tradeResult ? (
                    <StatusPill tone={tradeResult.executed ? "pass" : "reject"}>
                      {tradeResult.executed ? "EXECUTED" : "NO_TRADE"}
                    </StatusPill>
                  ) : (
                    <StatusPill tone="neutral">IDLE</StatusPill>
                  )
                }
              >
                {tradeResult === null ? (
                  <div className="rounded-xl border border-dashed border-slate-700/70 bg-slate-950/40 px-4 py-8 text-center text-sm text-slate-500">
                    Submit a signal to see the full gate-by-gate result.
                  </div>
                ) : (
                  <div className="space-y-4 animate-fade-up">
                    <div
                      className={`flex flex-wrap items-center gap-3 rounded-xl border px-4 py-3 ${
                        tradeResult.executed
                          ? "border-emerald-500/40 bg-emerald-500/10"
                          : "border-rose-500/40 bg-rose-500/10"
                      }`}
                    >
                      <StatusPill tone={tradeResult.executed ? "pass" : "reject"}>
                        {tradeResult.executed ? "✓ EXECUTED (paper fill)" : "✗ NO TRADE"}
                      </StatusPill>
                      <span className="font-mono text-xs text-slate-300">{tradeResult.decision}</span>
                      {tradeResult.equityAfter !== null && (
                        <span className="ml-auto text-sm text-slate-300">
                          Equity after{" "}
                          <span className="font-semibold text-white">
                            <AnimatedMetric value={tradeResult.equityAfter} format={fmtUsdt} />
                          </span>
                        </span>
                      )}
                    </div>

                    <p className="text-sm text-slate-300">{tradeResult.reason}</p>

                    <div className="space-y-2">
                      {Object.entries(tradeResult.gateResults).length === 0 && (
                        <p className="text-xs text-slate-500">No gate results were returned for this submission.</p>
                      )}
                      {Object.entries(tradeResult.gateResults).map(([name, g], i) => (
                        <div
                          key={name}
                          className="rounded-xl border border-slate-800 bg-slate-950/50 p-3 animate-fade-up"
                          style={{ animationDelay: `${i * 70}ms` }}
                        >
                          <div className="flex items-center justify-between gap-2">
                            <span className="text-sm font-semibold capitalize text-slate-100">
                              {name.replace(/_/g, " ")}
                            </span>
                            <StatusPill tone={g.passed ? "pass" : "reject"}>{g.passed ? "PASS" : "REJECT"}</StatusPill>
                          </div>
                          {g.reasons.length > 0 && (
                            <ul className="mt-2 space-y-1">
                              {g.reasons.map((r, j) => (
                                <li key={j} className="flex gap-2 text-xs text-slate-400">
                                  <span className={g.passed ? "text-emerald-400" : "text-rose-400"}>•</span>
                                  <span>{r}</span>
                                </li>
                              ))}
                            </ul>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </GlassCard>
            </div>
          </section>

          {/* --------------------------------------------------- backtest -- */}
          <section id="backtest" className="mt-6 scroll-mt-24">
            <GlassCard
              title="Backtest (simulation)"
              subtitle="Example SMA-crossover strategy on synthetic candles — simulation only, not live, not a recommendation."
              actions={
                <button
                  onClick={() => void doBacktest()}
                  disabled={btBusy}
                  className="btn-ghost min-h-[44px] rounded-xl px-4 text-sm font-semibold disabled:opacity-60"
                >
                  {btBusy ? (
                    <span className="flex items-center gap-2">
                      <PulseDot tone="warn" /> Running…
                    </span>
                  ) : (
                    "Run backtest (240 candles)"
                  )}
                </button>
              }
            >
              {!report ? (
                <div className="rounded-xl border border-dashed border-slate-700/70 bg-slate-950/40 px-4 py-8 text-center text-sm text-slate-500">
                  {btBusy ? "Running the simulation…" : "No backtest report yet — run the simulation to populate metrics."}
                </div>
              ) : (
                <>
                  <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
                    <MetricCard
                      label="Total return"
                      value={report.total_return * 100}
                      format={fmtPct}
                      tone={pnlTone(report.total_return)}
                    />
                    <MetricCard label="P&L" value={report.pnl} format={(v) => fmtMoney(v, 0) ?? "—"} tone={pnlTone(report.pnl)} />
                    <MetricCard label="Trades" value={report.trade_count} format={fmtInt} hint={`${backtest?.candles_used ?? "—"} candles`} />
                    <MetricCard label="Win rate" value={report.win_rate * 100} format={(v) => `${v.toFixed(1)}%`} />
                    <MetricCard label="Profit factor" value={report.profit_factor} format={fmtRatio} />
                    <MetricCard label="Max drawdown" value={report.max_drawdown * 100} format={fmtPct} tone="neg" />
                  </div>

                  <div className="mt-4 overflow-hidden rounded-xl border border-slate-800">
                    <table className="w-full text-left text-sm">
                      <tbody>
                        {[
                          ["Strategy", report && backtest ? backtest.strategy : "—"],
                          ["Symbol / timeframe", backtest ? `${backtest.symbol} · ${backtest.timeframe}` : "—"],
                          ["Mode", backtest ? `${backtest.mode} (simulation=${String(backtest.simulation)})` : "—"],
                          ["Starting equity", fmtMoney(report.starting_equity)],
                          ["Ending equity", fmtMoney(report.ending_equity)],
                          ["Sharpe", fmt(report.sharpe, 2)],
                          ["Fees paid", fmtMoney(report.fees_paid)],
                          ["Run id", backtest?.run_id ?? "—"],
                        ].map(([k, v], i) => (
                          <tr key={k as string} className={i % 2 ? "bg-slate-900/40" : "bg-slate-950/30"}>
                            <th scope="row" className="w-1/2 px-4 py-2.5 font-normal text-slate-400">
                              {k}
                            </th>
                            <td className="tabular break-all px-4 py-2.5 font-medium text-slate-100">{v}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}
            </GlassCard>
          </section>

          {/* ------------------------------------------------------ audit -- */}
          <section id="audit" className="mt-6 scroll-mt-24">
            <GlassCard
              title="Audit log"
              subtitle="Every gate decision is recorded — logs remember."
              actions={
                <div className="flex flex-wrap items-center gap-2">
                  <StatusPill tone="neutral">{auditStats.total} events</StatusPill>
                  <StatusPill tone="pass">{auditStats.passes} PASS</StatusPill>
                  <StatusPill tone="reject">{auditStats.rejects} REJECT</StatusPill>
                </div>
              }
            >
              {audit.length === 0 ? (
                <div className="rounded-xl border border-dashed border-slate-700/70 bg-slate-950/40 px-4 py-8 text-center text-sm text-slate-500">
                  No audit entries yet.
                </div>
              ) : (
                <>
                  <div className="scroll-slim max-h-[26rem] overflow-auto rounded-xl border border-slate-800">
                    <table className="w-full min-w-[46rem] border-collapse text-left text-sm">
                      <thead className="sticky-head">
                        <tr className="border-b border-slate-800 text-[11px] uppercase tracking-[0.12em] text-slate-400">
                          <th className="px-3 py-3 font-semibold">Time</th>
                          <th className="px-3 py-3 font-semibold">Event</th>
                          <th className="px-3 py-3 font-semibold">Gate</th>
                          <th className="px-3 py-3 font-semibold">Status</th>
                          <th className="px-3 py-3 font-semibold">Detail</th>
                        </tr>
                      </thead>
                      <tbody>
                        {audit.map((row, i) => (
                          <tr
                            key={row.id}
                            className={`border-b border-slate-800/60 transition-colors hover:bg-cyan-500/5 ${
                              i % 2 ? "bg-slate-900/35" : "bg-slate-950/25"
                            }`}
                          >
                            <td className="tabular whitespace-nowrap px-3 py-2.5 font-mono text-[11px] text-slate-400">
                              {row.timestamp}
                            </td>
                            <td className="whitespace-nowrap px-3 py-2.5 text-slate-200">{row.event_type}</td>
                            <td className="whitespace-nowrap px-3 py-2.5 text-slate-400">{row.gate ?? "—"}</td>
                            <td className="whitespace-nowrap px-3 py-2.5">
                              {row.passed === null ? (
                                <StatusPill tone="neutral">LOGGED</StatusPill>
                              ) : row.passed ? (
                                <StatusPill tone="pass">PASS</StatusPill>
                              ) : (
                                <StatusPill tone="reject">REJECT</StatusPill>
                              )}
                            </td>
                            <td className="max-w-[26rem] px-3 py-2.5 text-xs text-slate-400">
                              <span className="line-clamp-2 break-words">{row.detail ?? "—"}</span>
                              {row.signal_id && (
                                <span className="mt-1 block font-mono text-[10px] text-slate-600">
                                  signal {row.signal_id}
                                </span>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>

                  {auditStats.gates.length > 0 && (
                    <div className="mt-4 rounded-xl border border-slate-800 bg-slate-950/40 p-4">
                      <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-500">
                        Audit events per gate (this page of results)
                      </p>
                      <div className="mt-3">
                        <MiniBars values={auditStats.gates.map(([, c]) => c)} />
                      </div>
                      <div className="mt-3 flex flex-wrap gap-2">
                        {auditStats.gates.map(([gate, count]) => (
                          <StatusPill key={gate} tone="neutral">
                            {gate} · {count}
                          </StatusPill>
                        ))}
                      </div>
                    </div>
                  )}
                </>
              )}
            </GlassCard>
          </section>

          <footer className="mt-10 flex flex-col items-center gap-2 border-t border-slate-800/70 pt-6 text-center text-xs text-slate-500">
            <p className="flex flex-wrap items-center justify-center gap-2">
              <StatusPill tone="info">SPOT ONLY</StatusPill>
              <StatusPill tone="pass">NO INTEREST / RIBA</StatusPill>
              <StatusPill tone="neutral">LIVE TRADING DISABLED BY DEFAULT</StatusPill>
            </p>
            <p>
              HalalTrade AI · Delivery 4 · Shariah-compliant BTC/USDT spot trading agent. No profits guaranteed — the
              owner keeps full control and final authorization.
            </p>
          </footer>
        </div>
      </main>
    </div>
  );
}

export default Dashboard;
