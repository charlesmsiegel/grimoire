import { useEffect, useRef } from "react";

/**
 * Poll a callback on a fixed interval while the document is visible.
 * Pauses on `visibilitychange` to `hidden`, fires immediately on resume.
 *
 * - The callback is called once at mount.
 * - Errors from the callback are swallowed (the caller is expected to
 *   handle its own failure UX; we don't want a transient fetch error to
 *   tear down the polling loop).
 */
export function useObservabilityPolling(callback: () => Promise<void>, intervalMs: number): void {
  const cbRef = useRef(callback);
  cbRef.current = callback;

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setInterval> | null = null;

    const fire = async () => {
      if (cancelled) return;
      try {
        await cbRef.current();
      } catch {
        // intentional — see jsdoc
      }
    };

    const start = () => {
      if (timer !== null) return;
      void fire();
      timer = setInterval(() => void fire(), intervalMs);
    };
    const stop = () => {
      if (timer !== null) {
        clearInterval(timer);
        timer = null;
      }
    };
    const onVisibility = () => {
      if (document.visibilityState === "visible") {
        start();
      } else {
        stop();
      }
    };

    if (document.visibilityState === "visible") start();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      cancelled = true;
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [intervalMs]);
}
