PATH: src/hooks/useWakeLock.ts

import { useCallback, useEffect, useRef } from "react";

type WakeLockSentinel = {
  released: boolean;
  release: () => Promise<void>;
  addEventListener: (
    type: "release",
    listener: () => void,
  ) => void;
};

type WakeLockNavigator = Navigator & {
  wakeLock?: {
    request: (type: "screen") => Promise<WakeLockSentinel>;
  };
};

export function useWakeLock(enabled: boolean) {
  const lockRef = useRef<WakeLockSentinel | null>(null);

  const release = useCallback(async () => {
    if (!lockRef.current) return;

    try {
      await lockRef.current.release();
    } catch {
      // The lock may already have been released by the browser.
    }

    lockRef.current = null;
  }, []);

  const request = useCallback(async () => {
    if (!enabled) return;

    const wakeLock = (navigator as WakeLockNavigator).wakeLock;
    if (!wakeLock || document.visibilityState !== "visible") return;

    try {
      await release();

      const lock = await wakeLock.request("screen");
      lockRef.current = lock;

      lock.addEventListener("release", () => {
        lockRef.current = null;
      });
    } catch {
      // Wake Lock is best-effort. Tracking must continue without it.
    }
  }, [enabled, release]);

  useEffect(() => {
    if (!enabled) {
      void release();
      return;
    }

    void request();

    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        void request();
      }
    };

    document.addEventListener(
      "visibilitychange",
      handleVisibilityChange,
    );

    return () => {
      document.removeEventListener(
        "visibilitychange",
        handleVisibilityChange,
      );
      void release();
    };
  }, [enabled, release, request]);

  return {
    supported:
      typeof navigator !== "undefined" &&
      "wakeLock" in navigator,
    request,
    release,
  };
}
