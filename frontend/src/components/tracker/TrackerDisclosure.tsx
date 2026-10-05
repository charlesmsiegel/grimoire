import { useEffect, useMemo, useState } from "react";
import { api } from "../../api/client";
import type { TrackerEdits, TrackerEntry, TrackerRecord } from "../../api/client";
import { ErrorNote } from "../ErrorNote";
import { TrackerEditForm } from "./TrackerEditForm";
import { TrackerValues, valueText } from "./TrackerValues";

const EMPTY_SET: ReadonlySet<string> = new Set();

/** The status and changes shown inside the disclosure.
 *
 *  A key with NO entry reads "untracked", not "updating": the turn marks the
 *  post pending inside the lock hold that covers its frames, so a missing entry
 *  means it was never tracked. A `pending` entry is the live case, and the
 *  server reports a pending one that nothing is running as `failed`. */
export function summaryText(
  entry: TrackerEntry | undefined, names: Record<string, string>, labels: Record<string, string>,
): string {
  if (entry === undefined) return "Tracker · untracked";
  if (entry.status === "pending") return "Tracker · updating…";
  let text: string;
  if (entry.status === "failed") text = "Tracker · untracked";
  else if (entry.changed.length === 0) text = "Tracker · no change";
  else text = "Tracker · " + entry.changed.map(([ref, key, value]) =>
    `${names[ref] ?? ref}: ${labels[key] ?? key} → ${valueText(value)}`).join("; ");
  if (entry.flags.upstream_changed) text += " · earlier state changed";
  if (entry.flags.text_changed) text += " · text changed since tracked";
  return text;
}

/** What "Re-run tracker from here" asks before it spends anything: `count`
 *  tracked posts, this one included, each a paid model call. */
export function rerunPrompt(count: number): string {
  const posts = count === 1 ? "1 post" : `${count} posts`;
  const calls = count === 1 ? "1 model call" : `${count} model calls`;
  return `Re-run the tracker for ${posts} from here? That is ${calls}.`;
}

/** One post's tracked state: a compact label, and on first open the record
 *  behind it, read-only until Edit is pressed. Fetches only on expansion, like
 *  `SavedThinking`: a long transcript need not transfer every post's snapshot.
 *
 *  `enabled` is the campaign's switch. Off, a post with no entry shows nothing
 *  and one with an entry shows it read-only -- the server refuses an edit, a
 *  retry and a re-run with `tracker_off`, so the controls are not offered.
 *
 *  `rerunCount` is how many tracked posts a re-run from here covers (this one
 *  and every one after it): each is a paid call, so the button asks first. */
export function TrackerDisclosure({ cid, sid, trackerKey, entry, names, labels, enabled,
  rerunCount, onChanged }: {
  cid: string; sid: string; trackerKey: string;
  entry: TrackerEntry | undefined;
  rerunCount: number;
  names: Record<string, string>; labels: Record<string, string>;
  enabled: boolean;
  onChanged: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [record, setRecord] = useState<TrackerRecord | null>(null);
  const [loading, setLoading] = useState(false);
  // The record the open form was built from. Frozen at the click: a re-read
  // that lands while the form is up (a poll, a refresh) must not swap what its
  // drafts are diffed against, or an untouched value the server moved would be
  // sent back as the person's edit.
  const [editRecord, setEditRecord] = useState<TrackerRecord | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  // Read on open, and again whenever the entry this post is showing moves (a
  // retry landed, a re-run reached it, an edit flagged it) so an open
  // disclosure never keeps a record the summary has outgrown.
  useEffect(() => {
    if (!open) return;
    let live = true;
    setLoading(true); setError(null);
    api.getTrackerRecord(cid, sid, trackerKey)
      .then((r) => { if (live) setRecord(r); })
      .catch((err) => { if (live) setError(err); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [open, cid, sid, trackerKey, entry]);

  const changedBy = useMemo(() => {
    const out = new Map<string, Set<string>>();
    for (const [ref, key] of entry?.changed ?? []) {
      if (!out.has(ref)) out.set(ref, new Set());
      out.get(ref)!.add(key);
    }
    return out;
  }, [entry]);

  if (entry === undefined && !enabled) return null;

  const pending = entry?.status === "pending";
  const canRetry = enabled && (entry === undefined || entry.status === "failed");

  async function run(action: () => Promise<unknown>) {
    setBusy(true); setError(null);
    try { await action(); onChanged(); }
    catch (err) { setError(err); }
    finally { setBusy(false); }
  }

  async function save(edits: TrackerEdits) {
    const next = await api.editTrackerRecord(cid, sid, trackerKey, edits);
    setRecord(next); setEditRecord(null);
    onChanged();
  }

  const actors = record?.snapshot ? Object.entries(record.snapshot) : [];
  const ordered = [...actors.filter(([, a]) => a.present), ...actors.filter(([, a]) => !a.present)];

  return (
    <div className="tracker-wrap">
      <details className="tracker"
               onToggle={(event) => {
                 const now = event.currentTarget.open;
                 setOpen(now);
                 if (!now) setEditRecord(null);
               }}>
        <summary>Tracker</summary>
        <p className="field-hint">{summaryText(entry, names, labels)}</p>
        {loading && record === null && <p role="status">Loading tracker…</p>}
        {entry?.status === "failed" && entry.error && (
          <p className="field-hint">{entry.error === "interrupted"
            ? "Tracking was interrupted." : `Tracking failed: ${entry.error}`}</p>
        )}
        {record !== null && (editRecord !== null ? (
          <TrackerEditForm record={editRecord} onSave={save} onCancel={() => setEditRecord(null)} />
        ) : (
          <>
            {record.snapshot === null
              ? <p className="field-hint">No tracked state was recorded for this post.</p>
              : ordered.map(([ref, actor]) => (
                <TrackerValues key={ref} actorRef={ref} actor={actor} fields={record.fields}
                               names={record.names} changed={changedBy.get(ref) ?? EMPTY_SET} />
              ))}
            {enabled && (
              <div className="form-actions">
                {record.snapshot !== null && (
                  <button className="subtle" disabled={busy || pending}
                          onClick={() => setEditRecord(record)}>Edit</button>
                )}
                <button className="subtle" disabled={busy || pending}
                        onClick={() => {
                          if (!window.confirm(rerunPrompt(rerunCount))) return;
                          void run(() => api.rerunTrackerFrom(cid, sid, trackerKey));
                        }}>
                  Re-run tracker from here
                </button>
              </div>
            )}
          </>
        ))}
        {canRetry && (
          <button className="subtle" disabled={busy}
                  onClick={() => void run(() => api.retryTracker(cid, sid, trackerKey))}>Retry</button>
        )}
        {error !== null && <p role="alert" className="field-hint"><ErrorNote err={error} /></p>}
      </details>
    </div>
  );
}
