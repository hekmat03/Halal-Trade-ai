PATH: src/lib/runflow/clock.test.ts

import { describe, expect, it } from "vitest";
import {
  advanceClock,
  clockSeconds,
  createClock,
  gapSince,
  INTERRUPTION_MS,
  MAX_TICK_GAP_MS,
  pauseClock,
} from "./clock";

describe("clock", () => {
  it("first advance only sets the reference time", () => {
    const c = advanceClock(createClock(), 1_000);
    expect(c.movingMs).toBe(0);
    expect(c.lastAt).toBe(1_000);
  });

  it("counts real elapsed time even when ticks are throttled", () => {
    let c = advanceClock(createClock(), 0);
    c = advanceClock(c, 1_000);
    c = advanceClock(c, 61_000); // the browser froze timers for 60 s
    expect(clockSeconds(c)).toBe(61);
  });

  it("does not count time while paused", () => {
    let c = advanceClock(createClock(), 0);
    c = pauseClock(c, 10_000);
    expect(c.lastAt).toBeNull();
    c = advanceClock(c, 500_000); // resume much later: sets reference only
    c = advanceClock(c, 505_000);
    expect(clockSeconds(c)).toBe(15);
  });

  it("continues from a restored run", () => {
    let c = advanceClock(createClock(90_000), 0);
    c = advanceClock(c, 5_000);
    expect(clockSeconds(c)).toBe(95);
  });

  it("ignores backwards or absurd clock jumps", () => {
    let c = advanceClock(createClock(), 100_000);
    c = advanceClock(c, 50_000);
    expect(c.movingMs).toBe(0);
    c = advanceClock(c, 50_000 + MAX_TICK_GAP_MS + 1);
    expect(c.movingMs).toBe(0);
  });

  it("reports how long the page was frozen", () => {
    const c = advanceClock(createClock(), 0);
    expect(gapSince(c, 3_000)).toBe(3_000);
    expect(gapSince(c, 40_000)).toBeGreaterThan(INTERRUPTION_MS);
    expect(gapSince(createClock(), 40_000)).toBe(0);
  });
});
