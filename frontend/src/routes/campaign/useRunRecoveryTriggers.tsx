import { useEffect } from "react";

export function useRunRecoveryTriggers({ activeId, adoptPendingRun, cid, selected, fail }: {
  fail: (err: unknown, retryable?: boolean, from?: string) => void;
  activeId: string | null;
  adoptPendingRun: (cid: string, sid: string) => Promise<void>;
  cid: string;
  selected: () => Readonly<{ cid: string; sid: string; }> | null;
}) {

  useEffect(() => {
    if (!activeId) return;
    adoptPendingRun(cid, activeId).catch((err: unknown) => fail(err, false, "scene"));
    const onVisible = () => {
      const scope = selected();
      if (document.visibilityState === "visible" && scope) {
        adoptPendingRun(cid, scope.sid).catch((err: unknown) => fail(err, false, "scene"));
      }
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [cid, activeId, adoptPendingRun, selected, fail]);
  return {};
}
