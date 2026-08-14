import { useCallback, useEffect, useState } from "react";
import { ApiError, api, type StoreConflicts } from "../api/client";

/** Files and folders a sync client left behind, shown on the Configuration
 *  page's Storage section (#35).
 *
 *  When two devices write the same record, Syncthing and Dropbox keep both by
 *  renaming the loser — to a name no record id will ever resolve to. Nothing
 *  in the app reads that file, so without this the user's edit looks like it
 *  simply vanished. This says where it went.
 *
 *  It never offers to delete or merge one: which side to keep is a question
 *  only the person who made both edits can answer, so the report ends at the
 *  path, and the file manager takes it from there. Same posture as the
 *  world→campaign sync screen, which flags and waits.
 *
 *  A clean store says so rather than rendering nothing — "no conflicts" and
 *  "this panel never ran" have to look different, which is also why a scan
 *  that failed reports the failure instead of quietly showing an empty list. */
export function StoreConflictNotice() {
  const [state, setState] = useState<StoreConflicts | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);

  const scan = useCallback(async () => {
    setScanning(true);
    try {
      setState(await api.getStoreConflicts());
      setError(null);
    } catch (e) {
      setState(null);
      setError(e instanceof ApiError ? e.detail : "Could not scan the library");
    } finally {
      setScanning(false);
    }
  }, []);

  useEffect(() => { scan(); }, [scan]);

  const rescan = (
    <button className="link" onClick={scan} disabled={scanning}>
      {scanning ? "Scanning…" : "Scan again"}
    </button>
  );

  if (error) {
    return (
      <p className="config-msg err">
        {error} — {rescan}
      </p>
    );
  }
  if (!state) return <p className="field-hint">Checking for sync conflicts…</p>;
  if (state.conflicts.length === 0) {
    return (
      <p className="field-hint">
        No conflicted copies in the library. {rescan}
      </p>
    );
  }

  return (
    <div className="banner store-conflicts">
      <p>
        {/* "copies", not "files": Dropbox conflicts a whole folder as readily as
            one record, and both land in this list. "At least" once truncated:
            the count is what the scan listed, which is a floor rather than a
            total the moment it stopped early. */}
        <strong>{state.truncated ? "At least " : ""}{state.conflicts.length} conflicted
        cop{state.conflicts.length === 1 ? "y" : "ies"} in the library.</strong>{" "}
        Your sync client kept both sides of an edit made on two devices. Grimoire
        reads none of these, and changes none of them — open them yourself, keep
        the version you want, and delete the rest.
      </p>
      <ul>
        {state.conflicts.map((c) => (
          <li key={c.path}>
            <code>{c.path}</code>
            <span className="field-hint">
              {" "}{c.tool} · {c.kind === "directory" ? "folder" : `${c.size} bytes`} · {c.modified}
            </span>
          </li>
        ))}
      </ul>
      {state.truncated && (
        <p className="field-hint">
          The list stops here — there are more, or the library was too large to
          finish walking.
        </p>
      )}
      <p>{rescan}</p>
    </div>
  );
}
