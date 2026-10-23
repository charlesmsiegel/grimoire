import { useCallback, useEffect, useRef, useState } from "react";
import { api, type ExpressionCollection, type ExpressionMutation, type ExpressionTarget } from "../../api/client";
import { errorText } from "../../api/errors";

/** Mounted per target. A retired editor can finish a write to its original
 * actor, but can never install that response in the next actor's editor. */
export function useExpressions(target: ExpressionTarget) {
  const [collection, setCollection] = useState<ExpressionCollection | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [cleaned, setCleaned] = useState<number | null>(null);
  const live = useRef(false);
  const writing = useRef(false);
  const sequence = useRef(0);
  const reload = useCallback(async () => {
    const request = ++sequence.current;
    try {
      const result = await api.getExpressions(target);
      if (live.current && request === sequence.current) { setCollection(result); setError(null); }
    } catch (err) { if (live.current && request === sequence.current) setError(errorText(err)); }
  }, [target.scope, target.owner_id, target.kind, target.actor_id, target.version_id]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { live.current = true; void reload(); return () => { live.current = false; }; }, [reload]);
  async function save(action: (revision: string) => Promise<ExpressionCollection>) {
    if (!collection || writing.current || !live.current) return;
    writing.current = true; setBusy(true); setError(null); ++sequence.current;
    try { const result = await action(collection.effective_revision); if (live.current) setCollection(result); }
    catch (err) { if (live.current) setError(errorText(err)); }
    finally { writing.current = false; if (live.current) setBusy(false); }
  }
  return { collection, error, busy, reload, cleaned,
    cleanup: () => save(async () => {
      const result = await api.cleanupExpressionRevisions(target);
      if (live.current) setCleaned(result.removed_revisions);
      return api.getExpressions(target);
    }),
    mutate: (mutations: ExpressionMutation[]) => save(revision => api.mutateExpressions(target, revision, mutations)),
    upload: (outfit: string, expression: string, file: File) => save(revision => api.uploadExpression(target, revision, outfit, expression, file)),
  };
}
