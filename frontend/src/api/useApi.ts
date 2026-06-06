/**
 * Tiny async-load hook used by the per-campaign views.
 *
 * The store reducer (spec 14 §State management) owns durable state. Per-view
 * fetches that don't need to live in the store (lists pulled fresh whenever
 * the route mounts) use this hook instead.
 *
 * The hook drives off ``fetcher`` identity: callers MUST wrap the fetcher
 * in ``useCallback`` with its real dependencies, so ``react-hooks/
 * exhaustive-deps`` lints the dep list at the call site. Without that, a
 * fresh arrow on every render would re-fetch endlessly — a loud failure
 * mode that's preferable to the old silent ``stale data forever`` bug
 * where the hook took an explicit ``deps`` array with exhaustive-deps
 * disabled.
 */

import { useCallback, useEffect, useState } from "react";

export type Loadable<T> =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "error"; error: Error }
  | { status: "ok"; data: T };

export function useApi<T>(fetcher: () => Promise<T>): Loadable<T> & { reload: () => void } {
  const [state, setState] = useState<Loadable<T>>({ status: "idle" });
  const [nonce, setNonce] = useState(0);

  const reload = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });
    fetcher()
      .then((data) => {
        if (!cancelled) setState({ status: "ok", data });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const error = err instanceof Error ? err : new Error(String(err));
          setState({ status: "error", error });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [fetcher, nonce]);

  return { ...state, reload };
}
