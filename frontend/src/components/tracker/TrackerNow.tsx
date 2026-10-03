import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { TrackerEdits, TrackerEntry, TrackerRecord } from "../../api/client";
import { ErrorNote } from "../ErrorNote";
import { TrackerEditForm } from "./TrackerEditForm";
import { TrackerValues } from "./TrackerValues";

const EMPTY_SET: ReadonlySet<string> = new Set();

/** What the dossier needs to show one actor's current tracked state. */
export type NowTracker = {
  cid: string; sid: string;
  /** The latest post whose tracking landed -- a pending or failed tail still
   *  shows the last good state. Null when no post has one. */
  key: string | null;
  /** The open actor, `"<kind>:<id>"`, as the tracker names it. */
  ref: string;
  /** The campaign's switch. Off, the server refuses an edit (`tracker_off`). */
  enabled: boolean;
  /** That key's index entry. Its identity is stable between polls that found
   *  nothing new (`shareSummary`), so it moves exactly when the record behind
   *  the key was rewritten -- a retry, a re-run, an edit from the transcript. */
  entry: TrackerEntry | undefined;
  /** A post after `key` is still being tracked. Its update was built on the
   *  record `key` names as it stood before, so an edit made now would land
   *  under a result that never saw it: Edit waits until the tail settles. */
  updating: boolean;
};

/** The latest key whose tracking landed, or null. */
export function lastOkKey(
  keys: { index: number; key: string }[], entries: Record<string, TrackerEntry>,
): string | null {
  for (let i = keys.length - 1; i >= 0; i--)
    if (entries[keys[i].key]?.status === "ok") return keys[i].key;
  return null;
}

/** Whether any key after `key` is still `pending`. */
export function pendingAfter(
  keys: { index: number; key: string }[], entries: Record<string, TrackerEntry>,
  key: string | null,
): boolean {
  const at = key === null ? -1 : keys.findIndex((k) => k.key === key);
  return keys.slice(at + 1).some((k) => entries[k.key]?.status === "pending");
}

/** One actor's current tracked values, in the dossier. Read-only until Edit is
 *  pressed; the form is limited to this actor and is built from the record the
 *  click found, which a re-read must not swap out from under its drafts.
 *
 *  Renders nothing until the record lands, and nothing when the actor is not
 *  present in it: "Now" is a claim about this scene, an actor the tracker never
 *  saw has no state to give, and one who left keeps only their last values --
 *  which describe when they left, not now. */
export function TrackerNow({ tracker, onChanged }: {
  tracker: NowTracker & { key: string };
  onChanged: () => void;
}) {
  const { cid, sid, key, ref, enabled, entry, updating } = tracker;
  const [record, setRecord] = useState<TrackerRecord | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [reload, setReload] = useState(0);
  const [editRecord, setEditRecord] = useState<TrackerRecord | null>(null);

  // Read whenever the key moves, and again when its entry does or a save lands.
  // A response that arrives after any of those moved again is dropped (`live`).
  useEffect(() => {
    let live = true;
    setError(null);
    api.getTrackerRecord(cid, sid, key)
      .then((r) => { if (live) setRecord(r); })
      .catch((err) => { if (live) setError(err); });
    return () => { live = false; };
  }, [cid, sid, key, ref, entry, reload]);

  // When the key advances the previous record stays up until the new one lands,
  // rather than the section blinking out each turn. The parent remounts this per
  // scene and actor, so what is held here is always this actor's last good read.
  const stored = record?.snapshot?.[ref];
  const actor = stored?.present ? stored : undefined;

  async function save(edits: TrackerEdits) {
    if (editRecord === null) return;
    await api.editTrackerRecord(cid, sid, editRecord.key, edits);
    setEditRecord(null);
    setReload((n) => n + 1);
    onChanged();
  }

  if (record === null ? error === null : (!actor && editRecord === null)) return null;

  return (
    <div className="column-section">
      <div className="column-section-head">
        <span className="section-label">Now</span>
        <span className="column-count">scene state</span>
      </div>
      <div className="tracker-now">
        {error !== null && <p role="alert" className="field-hint"><ErrorNote err={error} /></p>}
        {editRecord !== null ? (
          <TrackerEditForm record={editRecord} only={ref} onSave={save}
                           onCancel={() => setEditRecord(null)} />
        ) : record !== null && actor && (
          <>
            <TrackerValues actorRef={ref} actor={actor} fields={record.fields}
                           names={record.names} changed={EMPTY_SET} />
            {enabled && (
              <div className="form-actions">
                <button className="subtle" disabled={updating}
                        onClick={() => setEditRecord(record)}>Edit</button>
                {updating && <span className="field-hint">updating…</span>}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
