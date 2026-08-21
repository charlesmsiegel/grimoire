import { useCallback, useEffect, useState } from "react";
import { ApiError, api, type LibraryDependent, type LibraryKind } from "../api/client";

/** The world-scope detail sidebar's "take this out of the library" action (#52).
 *
 *  Deliberately two steps, and the first one is the point: under the overlay a
 *  campaign that never copied a record is the one depending on it MOST, since
 *  it has nothing else. So nothing is demoted until the dependents have been
 *  named and the user has decided what happens to them.
 *
 *  Copy-down is the default and the destructive option is the one you have to
 *  choose, which is the same shape the route uses.
 */
export function DemotePanel({ wid, kind, id, onDemoted }: {
  wid: string;
  kind: LibraryKind;
  id: string;
  onDemoted?: () => void;
}) {
  const [deps, setDeps] = useState<LibraryDependent[] | null>(null);
  const [open, setOpen] = useState(false);
  const [copyDown, setCopyDown] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    api.libraryDependents(wid, kind, id).then(setDeps).catch(() => setDeps(null));
  }, [wid, kind, id]);

  useEffect(load, [load]);

  async function demote() {
    setBusy(true);
    setError(null);
    try {
      await api.demoteFromLibrary(wid, kind, id, { copy_down: copyDown });
      onDemoted?.();
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (deps === null) return null;

  return (
    <div className="side-section">
      <h4>Library</h4>
      {!open ? (
        <button className="subtle" onClick={() => setOpen(true)}>Remove from library…</button>
      ) : (
        <>
          <div className="field-hint">
            {deps.length === 0
              ? "No campaign uses this world yet."
              : `${deps.length} campaign${deps.length === 1 ? "" : "s"} read this record:`}
          </div>
          {deps.length > 0 && (
            <div className="chips">
              {deps.map((d) => (
                <span key={d.id} className="chip on">
                  {d.name}{d.has_copy ? " (own copy)" : ""}
                </span>
              ))}
            </div>
          )}
          <label className="field-hint">
            <input type="checkbox" checked={copyDown}
                   onChange={(e) => setCopyDown(e.target.checked)} />
            {" "}Leave each campaign its own copy
          </label>
          <div className="field-hint">
            {copyDown
              ? "The record and its images become campaign-local, one copy each."
              : "The record is deleted everywhere. Campaigns that had no copy lose it."}
          </div>
          <div className="form-actions">
            <button className="subtle" disabled={busy}
                    onClick={() => { void demote(); }}>
              {copyDown ? "Remove and copy down" : "Remove everywhere"}
            </button>
            <button className="subtle" disabled={busy}
                    onClick={() => { setOpen(false); setError(null); }}>Cancel</button>
          </div>
        </>
      )}
      {error && <div className="field-hint error">{error}</div>}
    </div>
  );
}

export default DemotePanel;
