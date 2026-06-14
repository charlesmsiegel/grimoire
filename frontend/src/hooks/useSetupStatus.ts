import { useCallback, useEffect, useRef, useState } from "react";

import { useResource } from "../api/useResource";
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
  const loader = useCallback(() => setupApi.status(), []);
  const { data, error, loading, reload } = useResource(loader);
  // ``setLocal`` writes an optimistic override; a fresh fetch overwrites it,
  // mirroring the original (single status slot mutated by both load + setLocal).
  const [local, setLocal] = useState<SetupStatus | null>(null);
  const lastDataRef = useRef<SetupStatus | null>(null);
  useEffect(() => {
    if (data !== lastDataRef.current) {
      lastDataRef.current = data;
      setLocal(null);
    }
  }, [data]);

  return {
    status: local ?? data,
    loading,
    error: error?.message ?? null,
    reload,
    setLocal,
  };
}
