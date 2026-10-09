import type { ReactNode } from "react";
import { useCountUp } from "~/hooks/useCountUp";

/* ============================================================ primitives
   Presentation-only building blocks for the premium dashboard. They take the
   same data the dashboard already receives from src/lib/api.ts — no data is
   invented here.
   ====================================================================== */

export type Tone = "pass" | "reject" | "neutral" | "warn" | "info";

const toneClasses: Record<Tone, string> = {
  pass: "border-emerald-400/30 bg-emerald-500/12 text-emerald-300 shadow-[0_0_18px_-8px_rgba(16,185,129,0.8)]",
  reject: "border-rose-400/30 bg-rose-500/12 text-rose-300 shadow-[0_0_18px_-8px_rgba(244,63,94,0.8)]",
  warn: "border-amber-400/30 bg-amber-500/12 text-amber-300 shadow-[0_0_18px_-8px_rgba(251,191,36,0.8)]",
  info: "border-cyan-400/30 bg-cyan-500/12 text-cyan-300 shadow-[0_0_18px_-8px_rgba(34,211,238,0.85)]",
  neutral: "border-slate-600/50 bg-slate-500/10 text-slate-300",
};

export function StatusPill({ tone, children, className = "" }: { tone: Tone; children: ReactNode; className?: string }) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${toneClasses[tone]} ${className}`}
    >
      {children}
    </span>
  );
}

/** Animated "agent is thinking" dot: solid core + expanding ring. */
export function PulseDot({ tone = "info", className = "" }: { tone?: "info" | "pass" | "warn" | "reject"; className?: string }) {
  const core =
    tone === "pass" ? "bg-emerald-400" : tone === "warn" ? "bg-amber-400" : tone === "reject" ? "bg-rose-400" : "bg-cyan-400";
  return (
    <span className={`relative inline-flex h-2.5 w-2.5 shrink-0 items-center justify-center ${className}`} aria-hidden="true">
      <span className={`absolute inset-0 rounded-full animate-pulse-ring ${core} opacity-60`} />
      <span className={`relative h-2 w-2 rounded-full ${core}`} />
    </span>
  );
}

/** Small live/offline indicator with a pulsing halo when it is actually live. */
export function LiveDot({ live, label }: { live: boolean; label: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-[11px] font-medium uppercase tracking-wider text-slate-400">
      <span className="relative inline-flex h-2.5 w-2.5 items-center justify-center">
        {live && <span className="absolute inset-0 rounded-full bg-emerald-400/70 animate-pulse-ring" />}
        <span className={`relative h-2 w-2 rounded-full ${live ? "bg-emerald-400" : "bg-slate-600"}`} />
      </span>
      {label}
    </span>
  );
}

export function GlassCard({
  title,
  subtitle,
  actions,
  children,
  className = "",
  bodyClassName = "",
  id,
}: {
  title?: string;
  subtitle?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  id?: string;
}) {
  return (
    <section
      id={id}
      className={`glass gradient-border lift rounded-2xl p-4 sm:p-5 animate-raised ${className}`}
    >
      {(title || actions) && (
        <header className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div>
            {title && (
              <h2 className="fluid-h2 font-semibold tracking-tight text-slate-100">{title}</h2>
            )}
            {subtitle && <p className="mt-1 text-xs text-slate-400">{subtitle}</p>}
          </div>
          {actions}
        </header>
      )}
      <div className={bodyClassName}>{children}</div>
    </section>
  );
}

/** Big metric with count-up animation. `null` renders an explicit dash. */
export function AnimatedMetric({
  value,
  format,
  className = "",
}: {
  value: number | null | undefined;
  format: (n: number) => string;
  className?: string;
}) {
  const animated = useCountUp(value ?? null);
  return (
    <span className={`tabular ${className}`}>
      {animated === null || animated === undefined ? "—" : format(animated)}
    </span>
  );
}

export function MetricCard({
  label,
  value,
  format,
  hint,
  tone = "neutral",
  pulse = false,
}: {
  label: string;
  value: number | null | undefined;
  format: (n: number) => string;
  hint?: string;
  tone?: "pos" | "neg" | "neutral" | "accent";
  pulse?: boolean;
}) {
  const valueTone =
    tone === "pos"
      ? "text-emerald-300"
      : tone === "neg"
        ? "text-rose-300"
        : tone === "accent"
          ? "text-cyan-300"
          : "text-slate-100";
  return (
    <div className="glass gradient-border lift rounded-2xl p-4 animate-raised">
      <div className="flex items-center justify-between gap-2">
        <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-400">{label}</p>
        {pulse && <PulseDot tone={tone === "neg" ? "reject" : tone === "pos" ? "pass" : "info"} />}
      </div>
      <p className={`fluid-metric mt-2 font-bold ${valueTone}`}>
        <AnimatedMetric value={value ?? null} format={format} />
      </p>
      {hint && <p className="mt-1 text-[11px] text-slate-500">{hint}</p>}
    </div>
  );
}

export function KeyValue({
  label,
  value,
  tone,
}: {
  label: string;
  value: ReactNode;
  tone?: "pos" | "neg" | "neutral";
}) {
  return (
    <div className="flex items-center justify-between gap-3 border-b border-slate-800/70 py-2 last:border-0">
      <dt className="text-sm text-slate-400">{label}</dt>
      <dd
        className={`tabular text-sm font-medium ${
          tone === "pos" ? "text-emerald-300" : tone === "neg" ? "text-rose-300" : "text-slate-100"
        }`}
      >
        {value}
      </dd>
    </div>
  );
}

/* ------------------------------------------------------------- charts --
   Hand-rolled SVG area chart (no chart dependency installed). Renders only
   real samples; with fewer than 2 points it shows an explicit empty state.
   ---------------------------------------------------------------------- */

export function AreaSparkline({
  points,
  height = 96,
  stroke = "#22d3ee",
  gradientId,
  label,
  emptyLabel = "Awaiting data",
  format,
}: {
  points: number[];
  height?: number;
  stroke?: string;
  gradientId: string;
  label?: string;
  emptyLabel?: string;
  format?: (n: number) => string;
}) {
  if (!points || points.length < 2) {
    return (
      <div
        className="flex items-center justify-center rounded-xl border border-dashed border-slate-700/70 bg-slate-950/40 text-xs text-slate-500"
        style={{ height }}
      >
        {emptyLabel}
      </div>
    );
  }

  const w = 320;
  const h = height;
  const pad = 8;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || Math.abs(max) * 0.001 || 1;

  const coords = points.map((p, i) => {
    const x = pad + (i / (points.length - 1)) * (w - pad * 2);
    const y = h - pad - ((p - min) / span) * (h - pad * 2);
    return [x, y] as const;
  });

  const line = coords.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`).join(" ");
  const area = `${line} L${coords[coords.length - 1][0].toFixed(2)},${h - pad} L${coords[0][0].toFixed(2)},${h - pad} Z`;
  const last = points[points.length - 1];
  const first = points[0];
  const rising = last >= first;

  return (
    <div className="relative animate-fade-in">
      <svg viewBox={`0 0 ${w} ${h}`} width="100%" height={h} preserveAspectRatio="none" role="img" aria-label={label ?? "Session sample trend"}>
        <defs>
          <linearGradient id={`${gradientId}-fill`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={stroke} stopOpacity="0.42" />
            <stop offset="55%" stopColor={stroke} stopOpacity="0.12" />
            <stop offset="100%" stopColor={stroke} stopOpacity="0" />
          </linearGradient>
          <linearGradient id={`${gradientId}-line`} x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor={stroke} stopOpacity="0.65" />
            <stop offset="100%" stopColor={stroke} stopOpacity="1" />
          </linearGradient>
        </defs>

        {[0.25, 0.5, 0.75].map((f) => (
          <line key={f} x1={0} x2={w} y1={h * f} y2={h * f} stroke="rgba(51,65,85,0.45)" strokeWidth="1" strokeDasharray="3 6" />
        ))}

        <path d={area} fill={`url(#${gradientId}-fill)`} className="animate-fade-in" />
        <path
          d={line}
          fill="none"
          stroke={`url(#${gradientId}-line)`}
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          className="draw-line glow-line"
          style={{ "--dash": 1200 } as React.CSSProperties}
        />
        <circle cx={coords[coords.length - 1][0]} cy={coords[coords.length - 1][1]} r="3.4" fill={stroke} className="animate-breathe" />
      </svg>

      <div className="mt-2 flex items-center justify-between text-[11px] text-slate-400">
        <span>{format ? format(min) : min.toFixed(2)}</span>
        <span className={rising ? "text-emerald-300" : "text-rose-300"}>
          last {format ? format(last) : last.toFixed(2)}
        </span>
        <span>{format ? format(max) : max.toFixed(2)}</span>
      </div>
    </div>
  );
}

/** 24-point bar sparkline for audit-event density (real audit rows only). */
export function MiniBars({ values, tone = "cyan" }: { values: number[]; tone?: "cyan" | "amber" }) {
  const max = Math.max(1, ...values);
  const color = tone === "amber" ? "from-amber-400/80 to-amber-500/20" : "from-cyan-400/80 to-cyan-500/10";
  return (
    <div className="flex h-12 items-end gap-[3px]">
      {values.map((v, i) => (
        <div
          key={i}
          className={`w-full rounded-sm bg-gradient-to-t ${color} transition-[height] duration-500`}
          style={{ height: `${Math.max(4, (v / max) * 100)}%` }}
        />
      ))}
    </div>
  );
}
