import { useEffect, useRef, useState } from "react";

/**
 * Animate a number up/down to `target` with requestAnimationFrame.
 *
 * Hand-rolled on purpose: the owner asked for count-up animations without
 * adding a dependency. Behaviour notes:
 *  - Returns the *target* immediately when there is nothing to animate, so it is
 *    safe during SSR / hydration (no server/client markup mismatch).
 *  - `null` / `undefined` / non-finite input stays `null` — the UI renders "—"
 *    rather than inventing a number (DATA UNAVAILABLE, never fabricated).
 *  - Respects prefers-reduced-motion by snapping straight to the target.
 */
export function useCountUp(target: number | null | undefined, duration = 900): number | null {
  const initial = typeof target === "number" && Number.isFinite(target) ? target : null;
  const [display, setDisplay] = useState<number | null>(initial);
  const fromRef = useRef<number>(initial ?? 0);
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    if (typeof target !== "number" || !Number.isFinite(target)) {
      fromRef.current = 0;
      setDisplay(null);
      return;
    }

    const reduced =
      typeof window !== "undefined" &&
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    const from = fromRef.current;
    const to = target;

    if (reduced || from === to) {
      fromRef.current = to;
      setDisplay(to);
      return;
    }

    const start = performance.now();
    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3); // easeOutCubic
      const value = from + (to - from) * eased;
      fromRef.current = value;
      setDisplay(value);
      if (t < 1) rafRef.current = requestAnimationFrame(tick);
      else {
        fromRef.current = to;
        setDisplay(to);
      }
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    };
  }, [target, duration]);

  return display;
}
