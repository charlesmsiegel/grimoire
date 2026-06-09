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
  // Track whether the current loader has ever resolved, so reload() keeps
  // the prior data visible (no "Loading…" flash) while the refetch runs.
  // Initial load — and the first load after a loader change — shows loading.
  const hasResolvedRef = useRef(false);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  const prevLoaderRef = useRef(loader);

  useEffect(() => {
    let cancelled = false;
    if (prevLoaderRef.current !== loader) {
      // A new loader identity is a different resource (other campaign, other
      // kind, …): drop the previous result so consumers never render — or
      // seed editors from — another query's data. Explicit reload() of the
      // same loader keeps data visible (stale-while-revalidate).
      prevLoaderRef.current = loader;
      hasResolvedRef.current = false;
      setData(null);
    }
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
