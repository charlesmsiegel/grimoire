/**
 * Tiny async-resource hook: load once on mount (or when the memoized
 * ``loader`` identity changes / reload tick fires), exposes
 * ``{ data, error, loading, reload }``.
 *
 * Callers MUST wrap ``loader`` in ``useCallback`` with its real
 * dependencies. See the note on ``useApi`` for the rationale — same fix
 * for the same BUGS.md HIGH item.
 */

import { useCallback, useEffect, useRef, useState } from "react";

export interface Resource<T> {
  data: T | null;
  error: Error | null;
  loading: boolean;
  reload: () => void;
}

export function useResource<T>(loader: () => Promise<T>): Resource<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  // Track whether we've ever resolved data, so paginated / filtered
  // re-fetches keep the prior data visible (no AsyncBoundary "Loading…"
  // flash) while the next page loads. Initial load still shows loading.
  const hasResolvedRef = useRef(false);

  const reload = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    let cancelled = false;
    if (!hasResolvedRef.current) setLoading(true);
    setError(null);
    loader()
      .then((value) => {
        if (!cancelled) {
          setData(value);
          hasResolvedRef.current = true;
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err : new Error(String(err)));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [loader, tick]);

  return { data, error, loading, reload };
}
