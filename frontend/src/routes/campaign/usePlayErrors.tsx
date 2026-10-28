import { useCallback, useRef, useState } from "react";
import {
  invalidateConfigCache
} from "../../api/client";
import { errorText, isProviderFailure, isSetupFailure } from "../../api/errors";
import { configChanged } from "../../appEvents";
import type { PlayError } from "../../components/play/PlayBanners";

export function usePlayErrors() {
  // The banner's failure, and whether its Retry -- which generates -- is the
  // recovery for it: `PlayError` says why each field is there.
  const [error, setError] = useState<PlayError | null>(null);
  /** Whether the dot has been told this connection is failing, so that the
   *  next turn which works can tell it otherwise. Recovery is only visible if
   *  something announces it, and the shell has no other way to hear. */
  const sawProviderFailure = useRef(false);
  const fail = useCallback((e: unknown, retryable = true, from?: string) => {
    // A refusal of the model settings is not retried: the same request meets
    // the same refusal. A missing installation may need repairing outside the
    // app, so whether a settings page can fix it does not decide retryability.
    setError({ text: errorText(e), retryable: retryable && !isSetupFailure(e), from, err: e });
    // A turn that failed is the freshest thing anyone knows about that
    // connection, and it arrives as an SSE frame — so it never passes through
    // the api client's rejection path, where the same signal is raised for
    // every other provider call (#146). Without this the status dot keeps
    // yesterday's verdict until the reader navigates.
    if (isProviderFailure(e)) {
      sawProviderFailure.current = true;
      invalidateConfigCache();
      configChanged();
    }
  }, []);
  const providerSucceeded = useCallback(() => {
    if (!sawProviderFailure.current) return;
    sawProviderFailure.current = false;
    invalidateConfigCache();
    configChanged();
  }, []);
  return { fail, setError, providerSucceeded, error };
}
