import { useState } from "react";
import { api, type Notice } from "../api/client";

/** `in N days`, pluralized — a warn window of 1 must not say "1 days". */
function lead(n: number): string {
  return n === 1 ? "tomorrow" : `in ${n} days`;
}

/** The pre-notice banner (#106): what is about to happen, said once.
 *
 *  Every other surface that mentions an upcoming event mentions it *every*
 *  time — the prompt's "Upcoming:" line goes to the model on every turn, and
 *  the When section names today's observances whenever it is open. This one is
 *  addressed to the reader, and a warning the reader has already read is a nag.
 *  So the only state the feature has is the acknowledgement, and Dismiss is
 *  what writes it.
 *
 *  Dismiss, not first render. Marking on display would consume the warning
 *  whether or not anybody saw it — a panel that painted behind a modal, a page
 *  left open on another tab — and the failure modes are not symmetrical: showing
 *  a banner twice costs a second glance, swallowing one costs the holiday.
 *
 *  The dismissal is campaign-wide and keyed to the OCCURRENCE, so it silences
 *  this notice everywhere (the scene panel and the scene-planning list share one
 *  ledger) and next year's instance of the same holiday still warns.
 *
 *  **Dismissing is undoable, and has to be.** A dismissal is permanent for that
 *  occurrence — the store refuses to overwrite an acknowledgement — so a
 *  misclick would otherwise silence a holiday until its day had gone by, with
 *  no way back short of editing notices.json. The dismissed row is therefore
 *  kept on screen as an Undo rather than vanishing. It holds its own copy of the
 *  `Notice`, because the owner refetches on the callback below and the row is
 *  gone from `notices` by the time the Undo is there to be clicked.
 *
 *  Optimistic on both sides: the row changes state on click and the write
 *  follows. A failed write puts it back, because the alternative — a banner that
 *  looks dismissed and is not — is the one outcome a reader cannot tell from
 *  success.
 */
export function NoticeBanner({ cid, notices, scene = "", onChanged }: {
  cid: string;
  notices: Notice[];
  /** Where the dismissal happened. Recorded in the ledger for the reader's
   *  benefit; once-ness is campaign-wide, so nothing reads it back. */
  scene?: string;
  /** Called after a dismissal or an undo lands, so an owner holding its own
   *  copy of the list can refetch. */
  onChanged?: () => void;
}) {
  const [dismissed, setDismissed] = useState<Notice[]>([]);

  // Reset when the campaign changes. This component stays mounted across a
  // `cid` navigation (the inspector's rail and the new-scene chooser both do),
  // and an occurrence key is NOT unique across campaigns -- the same built-in
  // holiday on the same day generates the same key everywhere. Without this,
  // dismissing Midwinter in one campaign hides the legitimate, unacknowledged
  // Midwinter in the next one the reader opens. Adjusting state during render
  // is the documented React pattern for "reset when a prop changes", and unlike
  // an effect it never lets the stale list paint against the new campaign.
  const [seenCid, setSeenCid] = useState(cid);
  if (cid !== seenCid) {
    setSeenCid(cid);
    setDismissed([]);
  }

  const shown = notices.filter((n) => !dismissed.some((d) => d.key === n.key));
  if (shown.length === 0 && dismissed.length === 0) return null;

  async function dismiss(notice: Notice) {
    setDismissed((rows) => [...rows, notice]);
    try {
      await api.dismissNotices(cid, [notice.key], scene);
      onChanged?.();
    } catch {
      setDismissed((rows) => rows.filter((r) => r.key !== notice.key));
    }
  }

  async function undo(notice: Notice) {
    setDismissed((rows) => rows.filter((r) => r.key !== notice.key));
    try {
      await api.restoreNotices(cid, [notice.key]);
      onChanged?.();
    } catch {
      setDismissed((rows) => [...rows, notice]);
    }
  }

  return (
    <div className="notice-banner" role="status" aria-label="Coming up">
      {shown.map((n) => (
        <div className="notice-row" key={n.key}>
          <span className="notice-text">
            <strong>{n.name}</strong> {lead(n.in_days)}
            {n.friendly ? ` — ${n.friendly}` : ""}
          </span>
          <button className="chip-clear" aria-label={`Dismiss ${n.name}`}
                  title="Don't warn me about this again"
                  onClick={() => void dismiss(n)}>✕</button>
        </div>
      ))}
      {dismissed.map((n) => (
        <div className="notice-row notice-done" key={n.key}>
          <span className="notice-text">{n.name} dismissed.</span>
          <button className="notice-undo" aria-label={`Undo dismissing ${n.name}`}
                  onClick={() => void undo(n)}>Undo</button>
        </div>
      ))}
    </div>
  );
}
