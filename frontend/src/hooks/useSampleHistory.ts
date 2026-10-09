import { useEffect, useRef, useState } from "react";

/**
 * Rolling buffer of REAL samples observed from the backend during this browser
 * session (each 10s poll appends the value the API actually returned).
 *
 * This is deliberately not a fabricated market series: if the backend has never
 * returned a value, the buffer is empty and the chart renders an explicit
 * "awaiting data" state instead of a made-up curve.
 */
export function useSampleHistory(maxPoints = 60) {
  const [points, setPoints] = useState<number[]>([]);
  const lastRef = useRef<number | null>(null);

  const push = (value: number | null | undefined) => {
    if (typeof value !== "number" || !Number.isFinite(value)) return;
    if (lastRef.current === value) return; // don't pad the buffer with duplicate ticks
    lastRef.current = value;
    setPoints((prev) => {
      const next = [...prev, value];
      return next.length > maxPoints ? next.slice(next.length - maxPoints) : next;
    });
  };

  return { points, push, reset: () => { lastRef.current = null; setPoints([]); } };
}

/** Ticking clock for "last updated" labels — avoids hydration mismatch. */
export function useMounted() {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  return mounted;
}
