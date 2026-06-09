/**
 * State machine for a ConfirmDestructiveDialog-backed action: call
 * `request(target)` from the triggering control, render the dialog while
 * `target` is set, and wire `confirm`/`cancel`/`busy`/`error` straight
 * through. Replaces the old inline `window.confirm` flows.
 */

import { useState } from "react";

import { errorMessage } from "../api/client";

export function useDestructiveConfirm<T>(action: (target: T) => Promise<void>) {
  const [target, setTarget] = useState<T | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const request = (next: T) => {
    setError(null);
    setTarget(next);
  };
  const cancel = () => {
    setTarget(null);
    setError(null);
  };
  const confirm = () => {
    if (target === null) return;
    const acting = target;
    setBusy(true);
    setError(null);
    void action(acting)
      // Only clear the confirmation this action belongs to — a later
      // request() must not be closed by an earlier action resolving.
      .then(() => setTarget((current) => (current === acting ? null : current)))
      .catch((err: unknown) => setError(errorMessage(err)))
      .finally(() => setBusy(false));
  };

  return { target, request, cancel, confirm, busy, error };
}
