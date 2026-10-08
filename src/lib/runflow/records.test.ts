PATH: src/lib/runflow/records.test.ts

import { describe, expect, it } from "vitest";
import {
  estimateCalories,
  MIN_PACE_RECORD_M,
  paceSecPerKm,
  personalRecords,
  recordPace,
} from "./calc";
import { formatDuration, formatPace, toGoalPercent } from "./format";
import type { Run } from "./types";

const run = (
  id: string,
  distanceM: number,
  durationSec: number,
  startedAt = 0,
): Run => ({
  id,
  startedAt,
  endedAt: startedAt + durationSec * 1000,
  durationSec,
  distanceM,
  calories: 0,
  points: [],
  splits: [],
  title: "Run",
});

describe("pace", () => {
  it("never reports impossible paces", () => {
    expect(paceSecPerKm(0, 100)).toBe(0);
    expect(paceSecPerKm(40, 100)).toBe(0);
    expect(paceSecPerKm(1000, 30)).toBe(0);
    expect(paceSecPerKm(100, 3600)).toBe(0);
    expect(Math.round(paceSecPerKm(5000, 1500))).toBe(300);
  });

  it("formats unusable pace as a placeholder, never 0:01", () => {
    expect(formatPace(0, "km")).toBe("--:--");
    expect(formatPace(Number.NaN, "km")).toBe("--:--");
    expect(formatPace(300, "km")).toBe("5:00");
  });
});

describe("records", () => {
  it("short jogs cannot set the fastest-pace record", () => {
    expect(recordPace(run("a", MIN_PACE_RECORD_M - 1, 200))).toBe(0);
    expect(recordPace(run("b", MIN_PACE_RECORD_M, 300))).toBe(300);
  });

  it("tracks longest distance and duration from saved runs", () => {
    const r = personalRecords([
      run("a", 3000, 1000),
      run("b", 8000, 2800),
      run("c", 5000, 3000),
    ]);

    expect(r.longestM).toBe(8000);
    expect(r.longestDurationSec).toBe(3000);
  });

  it("is empty for no runs", () => {
    const r = personalRecords([]);

    expect(r.longestM).toBe(0);
    expect(r.bestPace5k).toBe(0);
  });
});

describe("goal + calories + format", () => {
  it("caps weekly progress at 100%", () => {
    expect(toGoalPercent(12_400, 20_000)).toBe(62);
    expect(toGoalPercent(30_000, 20_000)).toBe(100);
    expect(toGoalPercent(1000, 0)).toBe(0);
  });

  it("estimates calories only for real movement", () => {
    expect(estimateCalories(0, 600, 70)).toBe(0);
    expect(estimateCalories(5000, 1500, 70)).toBeGreaterThan(250);
    expect(estimateCalories(5000, 1500, 70)).toBeLessThan(600);
  });

  it("formats durations", () => {
    expect(formatDuration(1421)).toBe("23:41");
    expect(formatDuration(3725)).toBe("1:02:05");
  });
});
