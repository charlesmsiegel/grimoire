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
 *  Optimistic: the row leaves on click and the write follows. A failed write
 *  puts it back, because the alternative — a banner that looks dismissed and is
 *  not — is the one outcome a reader cannot tell from success.
 */
export function NoticeBanner({ cid, notices, scene = "", onDismissed }: {
  cid: string;
  notices: Notice[];
  /** Where the dismissal happened. Recorded in the ledger for the reader's
   *  benefit; once-ness is campaign-wide, so nothing reads it back. */
  scene?: string;
  /** Called after a dismissal lands, so an owner holding its own copy of the
   *  list can refetch. Optional: `dismissed` below already hides the row. */
  onDismissed?: (key: string) => void;
}) {
  const [dismissed, setDismissed] = useState<string[]>([]);
  const shown = notices.filter((n) => !dismissed.includes(n.key));
  if (shown.length === 0) return null;

  async function dismiss(key: string) {
    setDismissed((keys) => [...keys, key]);
    try {
      await api.dismissNotices(cid, [key], scene);
      onDismissed?.(key);
    } catch {
      setDismissed((keys) => keys.filter((k) => k !== key));
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
                  onClick={() => void dismiss(n.key)}>✕</button>
        </div>
      ))}
    </div>
  );
}
