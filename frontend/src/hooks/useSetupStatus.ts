import { useCallback, useEffect, useState } from "react";

import { type SetupStatus, setupApi } from "../api/setup";

/**
 * Tracks the first-run setup state from the backend sentinel file
 * (``{data_root}/.setup-complete``). Components can override the value
 * via ``setLocal`` after marking complete to avoid a redundant round-trip
 * for the next render.
 */
export function useSetupStatus(): {
  status: SetupStatus | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
  setLocal: (next: SetupStatus) => void;
} {
  const [status, setStatus] = useState<SetupStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    void (async () => {
      try {
        const data = await setupApi.status();
        if (!cancelled) setStatus(data);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { status, loading, error, reload, setLocal: setStatus };
}
