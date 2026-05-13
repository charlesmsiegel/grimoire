/**
 * Tiny async-load hook used by the per-campaign views.
 *
 * The store reducer (spec 14 §State management) owns durable state. Per-view
 * fetches that don't need to live in the store (lists pulled fresh whenever
 * the route mounts) use this hook instead.
 */

import { useEffect, useState, useCallback } from "react";

export type Loadable<T> =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "error"; error: Error }
  | { status: "ok"; data: T };

export function useApi<T>(
  fetcher: () => Promise<T>,
  deps: readonly unknown[],
): Loadable<T> & { reload: () => void } {
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce]);

  return { ...state, reload };
}
