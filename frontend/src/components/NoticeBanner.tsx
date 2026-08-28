import { useRef, useState } from "react";
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
 *  `Notice`, because the owning surface refetches as soon as the write lands and
 *  the row is gone from `notices` by the time the Undo is there to be clicked.
 *
 *  Optimistic on both sides: the row changes state on click and the write
 *  follows. A failed write puts it back, because the alternative — a banner that
 *  looks dismissed and is not — is the one outcome a reader cannot tell from
 *  success. Optimistic, but not concurrent: Undo is disabled until its own
 *  dismissal has landed, or the two writes race for the campaign lock and the
 *  loser decides.
 *
 *  Nothing is passed back to the owner. `dismissNotices` and `restoreNotices`
 *  emit on the `notices` app-event channel, and every surface showing this
 *  ledger listens — which is what a callback could not do, since two of those
 *  surfaces are mounted as siblings and neither owns the other.
 */
export function NoticeBanner({ cid, notices, scene = "" }: {
  cid: string;
  notices: Notice[];
  /** Where the dismissal happened. Recorded in the ledger for the reader's
   *  benefit; once-ness is campaign-wide, so nothing reads it back. */
  scene?: string;
}) {
  const [dismissed, setDismissed] = useState<Notice[]>([]);
  // Keys whose write is still in flight. Undo is disabled while its own
  // dismissal is one: both requests take the campaign lock, so they serialize
  // in whichever order they arrive, and a forget that wins finds nothing to
  // forget -- then the mark it beat lands, leaving the occurrence dismissed
  // while the banner has already reported it restored. Serializing at the
  // control is what keeps the two from being in flight together at all.
  const [writing, setWriting] = useState<string[]>([]);

  // See the handshake below `shown`. A ref and not state because nothing renders
  // from it: it only decides whether a key reappearing in `notices` is a
  // retirement or the ordinary gap before the owner refetches.
  const retired = useRef<Set<string>>(new Set());

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
    setWriting([]);
    retired.current.clear();
  }

  // The campaign this render belongs to, readable from a settled promise. The
  // reset above clears state when `cid` changes, but a request already in
  // flight closes over the OLD campaign and its completion would still land on
  // the new one's state -- an A rejection removing a row B optimistically
  // hid, or an A `finally` clearing B's guard early and re-opening the
  // mark/forget race the guard exists to close. Every completion below checks
  // that it is still answering the campaign it was started for.
  const current = useRef(cid);
  current.current = cid;

  // Dismissed keys the owner has since refetched away -- the ledger has the
  // acknowledgement and `pending` no longer offers the notice. That is the
  // handshake, and it is what lets the row below come BACK: the store retires
  // acknowledgements of its own accord (deleting an event drops every notice
  // keyed to its id, so that a recreation of the same id is not born already
  // dismissed), and when it does, `pending` offers the key again. Without this,
  // the optimistic list still holds it and the banner filters out a live warning
  // until something happens to unmount it -- the one failure this whole feature
  // exists to avoid.
  //
  // Two states rather than one, because "in `notices` again" alone is not
  // evidence of a retirement: between a dismissal landing and the owner's
  // refetch arriving, the key is still in `notices` for perfectly ordinary
  // reasons, and treating that as a return would un-dismiss every row a moment
  // after it was dismissed.
  for (const row of dismissed) {
    if (!notices.some((n) => n.key === row.key)) retired.current.add(row.key);
  }
  // Never while this instance's OWN write is still out. Two banners can be
  // mounted over one ledger (the scene panel and the new-scene chooser), so a
  // key can leave `notices` and come back for reasons that are not this
  // banner's dismissal at all -- the other instance's undo, landing between
  // this one's mark going out and coming back. Read as a retirement, that
  // drops the receipt of a dismissal that then lands anyway, leaving the
  // occurrence acknowledged with the Undo it needs gone from the screen. Same
  // principle as the guard on Undo below: a key with a write in flight has no
  // settled state to reason about yet.
  const back = dismissed.filter(
    (d) => retired.current.has(d.key) && !writing.includes(d.key)
           && notices.some((n) => n.key === d.key));
  if (back.length > 0) {
    for (const row of back) retired.current.delete(row.key);
    setDismissed((rows) => rows.filter((r) => !back.some((b) => b.key === r.key)));
  }

  const shown = notices.filter((n) => !dismissed.some((d) => d.key === n.key));
  if (shown.length === 0 && dismissed.length === 0) return null;

  async function dismiss(notice: Notice) {
    const startedIn = cid;
    setDismissed((rows) => [...rows, notice]);
    setWriting((keys) => [...keys, notice.key]);
    try {
      await api.dismissNotices(startedIn, [notice.key], scene);
    } catch {
      if (current.current === startedIn) {
        setDismissed((rows) => rows.filter((r) => r.key !== notice.key));
      }
    } finally {
      if (current.current === startedIn) {
        setWriting((keys) => keys.filter((k) => k !== notice.key));
      }
    }
  }

  async function undo(notice: Notice) {
    // Guarded here and not only by the button's `disabled`: the attribute is
    // the affordance a reader sees, this is the serialization. They are not the
    // same thing -- a programmatic click, or a second one racing the re-render
    // that disables it, reaches the handler with the attribute set.
    if (writing.includes(notice.key)) return;
    const startedIn = cid;
    setDismissed((rows) => rows.filter((r) => r.key !== notice.key));
    setWriting((keys) => [...keys, notice.key]);
    try {
      await api.restoreNotices(startedIn, [notice.key]);
    } catch {
      if (current.current === startedIn) setDismissed((rows) => [...rows, notice]);
    } finally {
      if (current.current === startedIn) {
        setWriting((keys) => keys.filter((k) => k !== notice.key));
      }
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
                  disabled={writing.includes(n.key)}
                  onClick={() => void undo(n)}>Undo</button>
        </div>
      ))}
    </div>
  );
}
