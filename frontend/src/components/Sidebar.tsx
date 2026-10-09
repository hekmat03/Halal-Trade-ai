import { useEffect, useState } from "react";
import { LiveDot, PulseDot, StatusPill } from "~/components/ui";

export interface NavSection {
  id: string;
  label: string;
  hint: string;
}

/**
 * Fixed sidebar on >=768px; on phones it collapses into a hamburger drawer.
 * Nav items are real in-page anchors to the dashboard sections, with a glowing
 * active indicator driven by scroll position.
 */
export function Sidebar({
  sections,
  killed,
  liveEnabled,
  operatingMode,
  connectionOk,
  tradeBusy,
  onRefresh,
}: {
  sections: NavSection[];
  killed: boolean;
  liveEnabled: boolean;
  operatingMode: string;
  connectionOk: boolean;
  tradeBusy: boolean;
  onRefresh: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(sections[0]?.id ?? "");

  // Highlight the section currently in view.
  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
        if (visible?.target.id) setActive(visible.target.id);
      },
      { rootMargin: "-25% 0px -60% 0px", threshold: [0.1, 0.4, 0.8] },
    );
    sections.forEach((s) => {
      const el = document.getElementById(s.id);
      if (el) observer.observe(el);
    });
    return () => observer.disconnect();
  }, [sections]);

  // Close the drawer when the viewport grows past the mobile breakpoint.
  useEffect(() => {
    const mq = window.matchMedia("(min-width: 768px)");
    const onChange = () => mq.matches && setOpen(false);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  return (
    <>
      {/* mobile top bar */}
      <div className="sticky top-0 z-40 flex items-center justify-between gap-3 border-b border-slate-800 bg-slate-950/85 px-4 py-3 backdrop-blur-md md:hidden">
        <div className="flex items-center gap-2.5">
          <BrandMark />
          <div className="leading-tight">
            <p className="text-sm font-bold tracking-tight text-slate-100">HalalTrade AI</p>
            <p className="text-[10px] uppercase tracking-[0.18em] text-slate-500">Control Dashboard</p>
          </div>
        </div>
        <button
          onClick={() => setOpen(true)}
          aria-label="Open navigation"
          aria-expanded={open}
          className="btn-ghost flex h-11 w-11 items-center justify-center rounded-xl"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <path d="M3 6h18M3 12h18M3 18h18" />
          </svg>
        </button>
      </div>

      {/* backdrop */}
      {open && (
        <div
          onClick={() => setOpen(false)}
          className="fixed inset-0 z-40 bg-slate-950/70 backdrop-blur-sm animate-fade-in md:hidden"
          aria-hidden="true"
        />
      )}

      <aside
        className={`fixed inset-y-0 left-0 z-50 flex w-[17rem] flex-col border-r border-slate-800 bg-slate-950/95 backdrop-blur-xl transition-transform duration-300 md:translate-x-0 md:bg-slate-950/70 ${
          open ? "translate-x-0 drawer-enter" : "-translate-x-full"
        }`}
      >
        <div className="flex items-center justify-between gap-2 border-b border-slate-800 px-5 py-5">
          <div className="flex items-center gap-3">
            <BrandMark />
            <div className="leading-tight">
              <p className="text-sm font-bold tracking-tight text-slate-100">HalalTrade AI</p>
              <p className="text-[10px] uppercase tracking-[0.18em] text-slate-500">Control Dashboard</p>
            </div>
          </div>
          <button
            onClick={() => setOpen(false)}
            aria-label="Close navigation"
            className="flex h-11 w-11 items-center justify-center rounded-xl text-slate-400 hover:bg-slate-800/70 hover:text-slate-100 md:hidden"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </div>

        <nav className="scroll-slim flex-1 space-y-1 overflow-y-auto px-3 py-4">
          <p className="px-2 pb-2 text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-500">Console</p>
          {sections.map((s) => (
            <a
              key={s.id}
              href={`#${s.id}`}
              data-nav
              data-active={active === s.id}
              onClick={() => setOpen(false)}
              className="nav-item flex min-h-[44px] items-center justify-between gap-3 rounded-xl px-3 py-2 text-sm text-slate-400"
            >
              <span className="font-medium">{s.label}</span>
              <span className="text-[10px] uppercase tracking-wider text-slate-600">{s.hint}</span>
            </a>
          ))}

          <div className="mt-5 rounded-xl border border-slate-800 bg-slate-900/50 p-3">
            <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-slate-500">Safety state</p>
            <div className="mt-2 flex flex-col gap-2">
              <div className="flex items-center justify-between">
                <span className="text-xs text-slate-400">Trading mode</span>
                <StatusPill tone={operatingMode === "PAPER" ? "info" : "warn"}>{operatingMode}</StatusPill>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-xs text-slate-400">Live trading</span>
                <StatusPill tone={liveEnabled ? "reject" : "pass"}>{liveEnabled ? "ENABLED" : "DISABLED"}</StatusPill>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-xs text-slate-400">Kill switch</span>
                <StatusPill tone={killed ? "reject" : "pass"}>{killed ? "ENGAGED" : "RELEASED"}</StatusPill>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-xs text-slate-400">Backend</span>
                <LiveDot live={connectionOk} label={connectionOk ? "online" : "offline"} />
              </div>
            </div>
          </div>

          <div className="mt-3 rounded-xl border border-slate-800 bg-slate-900/40 p-3">
            <div className="flex items-center gap-2">
              {tradeBusy ? <PulseDot tone="info" /> : <span className="h-2 w-2 rounded-full bg-slate-600" />}
              <p className="text-xs text-slate-400">{tradeBusy ? "Evaluating through the gates…" : "Pipeline idle"}</p>
            </div>
          </div>
        </nav>

        <div className="border-t border-slate-800 p-3">
          <button onClick={onRefresh} className="btn-ghost flex min-h-[44px] w-full items-center justify-center gap-2 rounded-xl px-3 text-sm font-medium">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M21 12a9 9 0 1 1-3-6.7M21 4v5h-5" />
            </svg>
            Refresh data
          </button>
          <p className="mt-3 px-1 text-[10px] leading-relaxed text-slate-600">
            Spot only · 1× · no leverage, margin, shorting or interest. Live trading ships disabled.
          </p>
        </div>
      </aside>
    </>
  );
}

function BrandMark() {
  return (
    <span className="relative flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-cyan-400/90 via-cyan-500/70 to-amber-400/70 shadow-[0_8px_24px_-10px_rgba(34,211,238,0.9)]">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#04060c" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M3 17l5-6 4 3 5-8 4 5" />
      </svg>
    </span>
  );
}
