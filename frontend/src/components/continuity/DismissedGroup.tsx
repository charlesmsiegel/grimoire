/** Dismissed findings (capstone §12.7): what the reader set aside, and the way
 *  to take that back.
 *
 *  Only dismissals that still name their records as they are (`live`): one
 *  whose records have since changed no longer suppresses anything -- its
 *  finding, if it is still one, comes back on its own -- so listing it would
 *  offer a Restore that restores nothing. A restored finding a sweep finds
 *  reappears at the next refresh; a model-only one (a touched re-check, a
 *  temporal pair), which no sweep re-finds, stayed cached while it was set
 *  aside and is back at once.
 */
import type { ContinuityState } from "../../api/client";
import { dismissedUnreadable, SUPPRESSION_LABELS } from "./labels";

export function DismissedGroup(
  { state, busy, onRestore }: {
    state: ContinuityState | null | "failed";
    busy: boolean;
    onRestore: (fingerprint: string) => void;
  },
) {
  if (state === null) return <p className="column-empty">Reading the dismissed findings…</p>;
  if (state === "failed") {
    return <p className="empty-state">The dismissed findings could not be read.</p>;
  }
  const live = state.suppressions.filter((s) => s.live);
  // A section that did not read is empty here, never "nothing dismissed" (§26).
  const unread = dismissedUnreadable(state.malformed);
  if (unread) return <p className="continuity-note" role="status">{unread}</p>;
  if (!live.length) {
    return (
      <p className="empty-state">
        <span className="empty-what">Nothing dismissed.</span> A finding you dismiss or keep
        open is listed here until its records change, and can be restored.
      </p>
    );
  }
  return (
    <ul className="continuity-list">
      {live.map((s) => {
        const label = (s.titles.length ? s.titles : s.refs).join(" / ") || s.fingerprint;
        return (
          <li key={s.fingerprint} className="continuity-item" aria-label={label}>
            <div className="continuity-item-text">
              <span className="continuity-item-title">{label}</span>
              <span className="chip on">{SUPPRESSION_LABELS[s.decision] ?? "Dismissed"}</span>
            </div>
            <button type="button" className="subtle" disabled={busy}
                    aria-label={`Restore ${label}`} onClick={() => onRestore(s.fingerprint)}>
              Restore
            </button>
          </li>
        );
      })}
    </ul>
  );
}

export default DismissedGroup;
