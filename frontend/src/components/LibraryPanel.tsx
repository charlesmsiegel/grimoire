import { useCallback, useEffect, useState } from "react";
import { ApiError, api, type LibraryKind, type LibraryStatus } from "../api/client";

/** The campaign-scope detail sidebar's one library action (#52, #53).
 *
 *  Which action that is — publish a campaign-local record, or save an override
 *  back over the library's — is decided by the server (`libraryStatus`), not
 *  recomputed here. Two copies of that rule would drift, and the visible
 *  symptom of the drift would be a button that always 409s.
 *
 *  Nothing renders in world scope: the world IS the library, so neither move
 *  means anything there.
 */
export function LibraryPanel({ cid, kind, id, onMoved }: {
  cid: string;
  kind: LibraryKind;
  id: string;
  onMoved?: () => void;
}) {
  const [status, setStatus] = useState<LibraryStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The library moved under this record since it was copied. Holding the
  // refusal rather than clearing it is what makes the overwrite a second,
  // deliberate click — the same shape as the stale-record banner on save.
  const [conflict, setConflict] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    setConflict(null);
    api.libraryStatus(cid, kind, id).then(setStatus).catch(() => setStatus(null));
  }, [cid, kind, id]);

  useEffect(load, [load]);

  async function run(move: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await move();
      setConflict(null);
      load();
      onMoved?.();
    } catch (err) {
      // `push_conflict` is the one refusal the user can resolve, so it gets the
      // overwrite affordance; everything else is a plain message.
      if (err instanceof ApiError && err.kind === "push_conflict") setConflict(err.detail);
      else setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (!status) return null;
  if (!status.can_promote && !status.can_push && !conflict) {
    if (!status.in_library) return null;
    // Two different states, and saying the wrong one is worse than saying
    // nothing: an actor this campaign has rewritten IS diverged, it simply
    // cannot be pushed (#53 option B). Calling that "in sync with the library"
    // is a claim about two records, and a false one.
    return (
      <div className="side-section">
        <h4>Library</h4>
        <div className="field-hint">
          {status.diverged
            ? "this campaign's own version; the library keeps its own, and this kind cannot be saved back"
            : "in sync with the library"}
        </div>
      </div>
    );
  }

  return (
    <div className="side-section">
      <h4>Library</h4>
      {status.can_promote && (
        <>
          <button className="subtle" disabled={busy}
                  onClick={() => { void run(() => api.promoteToLibrary(cid, kind, id)); }}>
            Publish to library
          </button>
          <div className="field-hint">
            this record exists only in this campaign; publishing puts it in the world,
            where every campaign can use it
          </div>
        </>
      )}
      {status.can_push && !conflict && (
        <>
          <button className="subtle" disabled={busy}
                  onClick={() => { void run(() => api.pushToLibrary(cid, kind, id)); }}>
            Save to library
          </button>
          <div className="field-hint">
            your version differs from the library's; saving replaces the library's
            and clears the override
          </div>
        </>
      )}
      {conflict && (
        <>
          <div className="field-hint error">{conflict}</div>
          <button className="subtle" disabled={busy}
                  onClick={() => { void run(() => api.pushToLibrary(cid, kind, id, true)); }}>
            Overwrite the library anyway
          </button>
        </>
      )}
      {error && <div className="field-hint error">{error}</div>}
    </div>
  );
}

export default LibraryPanel;
