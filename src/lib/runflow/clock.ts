/**
 * Wall-clock stopwatch that only advances while a run is active.
 *
 * Counting `+1` per setInterval tick undercounts as soon as the browser throttles
 * timers (screen dimmed, tab backgrounded). This derives elapsed time from real
 * timestamps instead. Pure, so it can be unit tested without a browser.
 */
export type Clock = {
  /** accumulated moving time in milliseconds */
  movingMs: number;
  /** timestamp of the last advance, or null while paused / not started */
  lastAt: number | null;
};

/** Ignore single gaps longer than this (device clock jumped or was changed). */
export const MAX_TICK_GAP_MS = 6 * 60 * 60 * 1000;
/** A gap this long between ticks means the page was frozen (screen off / backgrounded). */
export const INTERRUPTION_MS = 15_000;

export function createClock(movingMs = 0): Clock {
  return { movingMs: Math.max(0, movingMs), lastAt: null };
}

/** Bank elapsed time up to `now` and keep running. First call just sets the reference time. */
export function advanceClock(c: Clock, now: number): Clock {
  if (c.lastAt == null) return { movingMs: c.movingMs, lastAt: now };
  const gap = now - c.lastAt;
  const add = gap > 0 && gap <= MAX_TICK_GAP_MS ? gap : 0;
  return { movingMs: c.movingMs + add, lastAt: now };
}

/** Bank elapsed time up to `now`, then stop counting until the next advance. */
export function pauseClock(c: Clock, now: number): Clock {
  const banked = c.lastAt == null ? c : advanceClock(c, now);
  return { movingMs: banked.movingMs, lastAt: null };
}

/** Milliseconds since the last advance (0 when not running or the clock went backwards). */
export function gapSince(c: Clock, now: number): number {
  return c.lastAt == null ? 0 : Math.max(0, now - c.lastAt);
}

export function clockSeconds(c: Clock): number {
  return Math.floor(c.movingMs / 1000);
}
