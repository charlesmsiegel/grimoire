/**
 * useAuxInflight — tracks the campaign's in-flight auxiliary task results.
 *
 * "In-flight" here matches the backend's meaning: results that have finished
 * streaming and are parked in ``Orchestrator._inflight_aux`` awaiting accept
 * or discard. The hook fetches the current set on mount and refetches when
 * ``aux_complete`` or ``aux_error`` events arrive over the campaign WS.
 *
 * Accept/discard issued from the same client refetches via the returned
 * ``refresh`` callback — callers wire it into their UI flow when they
 * complete one of those actions.
 */

import { useCallback, useEffect, useState } from "react";

import { type AuxiliaryResult, auxiliaryApi } from "../../../api/auxiliary";
import { useCampaignEvent } from "../../../state/useCampaignEvent";

export interface AuxInflightState {
  loading: boolean;
  error: string | null;
  results: AuxiliaryResult[];
  refresh: () => void;
}

export function useAuxInflight(campaignId: string): AuxInflightState {
  const [results, setResults] = useState<AuxiliaryResult[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchInflight = useCallback(async () => {
    try {
      const rows = await auxiliaryApi.inFlight(campaignId);
      setResults(rows);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [campaignId]);

  useEffect(() => {
    void fetchInflight();
  }, [fetchInflight]);

  useCampaignEvent(
    ["aux_complete", "aux_error"],
    useCallback(() => {
      void fetchInflight();
    }, [fetchInflight]),
  );

  return {
    loading,
    error,
    results,
    refresh: () => void fetchInflight(),
  };
}
