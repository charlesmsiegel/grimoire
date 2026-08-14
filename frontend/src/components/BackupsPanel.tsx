import { useCallback, useEffect, useState } from "react";
import { ApiError, api, type BackupEntry } from "../api/client";

/** Bytes as something a person can weigh a disk against. Binary units, because
 *  that is what a file manager will show for the same archive. */
export function formatSize(bytes: number): string {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let n = bytes;
  let unit = 0;
  while (n >= 1024 && unit < units.length - 1) {
    n /= 1024;
    unit += 1;
  }
  return `${unit === 0 ? n : n.toFixed(1)} ${units[unit]}`;
}

/** The restore points that exist, and a way to make one now (#32).
 *
 *  Deliberately *not* part of the Configuration page's draft/Save cycle: the
 *  four backup settings are, but this is a list of files on disk and a button
 *  that writes one. A "Back up now" held back until Save would be the only
 *  action on the page whose effect is a file, waiting on a form.
 *
 *  `dir` is the saved setting, passed in rather than read from the listing, so
 *  the panel re-reads the moment a new backup directory is actually stored —
 *  a listing of the old directory sitting under a changed field is the one way
 *  this block can lie about where the archives are.
 */
export function BackupsPanel({ dir }: { dir: string }) {
  const [where, setWhere] = useState("");
  const [rows, setRows] = useState<BackupEntry[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const body = await api.listBackups();
      setWhere(body.dir);
      setRows(body.backups);
    } catch (e) {
      // Never an empty list on failure: "no restore points" and "could not
      // look" are opposite answers to the only question this block is asked.
      setRows(null);
      setMsg({
        kind: "err",
        text: e instanceof ApiError ? e.detail : "Could not read the backups folder",
      });
    }
  }, []);

  useEffect(() => { load(); }, [load, dir]);

  async function backUpNow() {
    if (busy) return;
    setBusy(true);
    setMsg(null);
    try {
      const run = await api.createBackup();
      setWhere(run.dir);
      setRows(run.backups);
      setMsg({
        kind: "ok",
        text: run.swept.length
          ? `Backed up to ${run.created} — ${run.swept.length} older ${
              run.swept.length === 1 ? "archive" : "archives"} removed`
          : `Backed up to ${run.created}`,
      });
    } catch (e) {
      setMsg({
        kind: "err",
        text: e instanceof ApiError ? e.detail : "Could not write a backup",
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="joined">
        <input className="mono-input" aria-label="Current backup folder" readOnly value={where} />
        <button className="btn-accent" onClick={backUpNow} disabled={busy}>
          {busy ? "Backing up…" : "Back up now"}
        </button>
      </div>
      {msg && (
        <p className={msg.kind === "err" ? "config-msg err" : "config-msg save-flash"}>
          {msg.text}
        </p>
      )}
      {rows !== null && rows.length === 0 && (
        <p className="field-hint">No backups yet.</p>
      )}
      {rows !== null && rows.length > 0 && (
        <ul className="backup-list">
          {rows.map((b) => (
            <li key={b.name} className="backup-row">
              <span className="backup-name">{b.name}</span>
              <span className="backup-meta">
                {new Date(b.created).toLocaleString()} · {formatSize(b.size)}
              </span>
            </li>
          ))}
        </ul>
      )}
      <p className="field-hint">
        Restoring is manual: unzip an archive into an empty folder and point the
        storage location above at it. Nothing is restored in place while the app
        is running — that would be a race against whatever it is serving.
      </p>
    </>
  );
}
