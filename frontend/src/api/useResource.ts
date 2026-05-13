/**
 * Tiny async-resource hook: load once on mount (or when `key` changes),
 * exposes `{ data, error, loading, reload }`.
 */

import { useCallback, useEffect, useRef, useState } from "react";

export interface Resource<T> {
  data: T | null;
  error: Error | null;
  loading: boolean;
  reload: () => void;
}

export function useResource<T>(loader: () => Promise<T>, deps: unknown[]): Resource<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const cancelled = useRef(false);

  const reload = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    cancelled.current = false;
    setLoading(true);
    setError(null);
    loader()
      .then((value) => {
        if (cancelled.current) return;
        setData(value);
      })
      .catch((err: unknown) => {
        if (cancelled.current) return;
        setError(err instanceof Error ? err : new Error(String(err)));
      })
      .finally(() => {
        if (cancelled.current) return;
        setLoading(false);
      });
    return () => {
      cancelled.current = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick]);

  return { data, error, loading, reload };
}
