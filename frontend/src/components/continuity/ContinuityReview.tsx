/** The continuity review's main pane (capstone §12.1): one group's findings,
 *  or what was already decided.
 *
 *  The column lists the groups and holds Refresh; this pane carries the same
 *  Refresh in its head as well, and not as decoration. On a phone (≤720px)
 *  `PageShell` puts the column behind "‹ Ledger sections", and a stale finding
 *  -- one whose records moved since the sweep -- has exactly one way forward,
 *  which is a Refresh. Leaving it only in the hidden column would leave the
 *  reader looking at a finding they can neither act on nor update.
 *
 *  A finding is a button that addresses it; nothing here acts on one. The
 *  detail and its explicit actions are the next pane in.
 */
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { errorText } from "../../api/errors";
import type { ContinuityCandidate, ContinuityGroup } from "../../api/types";
import { ledgerHref } from "../../ledgerPaths";
import { DismissedGroup } from "./DismissedGroup";
import {
  GROUP_LABELS, KIND_PHRASES, MATCHING_LINES, proposalLabel, STALE_TEXT,
} from "./labels";
import { ReviewedGroup } from "./ReviewedGroup";
import type { ContinuityReview as Review } from "./useContinuityReview";
import { api } from "../../api/client";

/** What an empty findings group says: where it would fill from. */
const NOTHING: Record<"overlaps" | "closures" | "resolutions", string> = {
  overlaps: "No possible overlaps. A refresh, or ending a scene, looks for records that "
    + "may be the same business.",
  closures: "Nothing here may be finished. A thread that has gone quiet, or that a scene "
    + "just moved, is offered here for a look.",
  resolutions: "No commitments need a resolution review. One that is past due, or that a "
    + "scene just moved, is offered here.",
};

/** The records a finding names, the way Todo and the server label them. */
const titlesOf = (c: ContinuityCandidate) =>
  c.records.map((r) => r.title || r.ref).join(" / ");

/** Refresh, and what it is doing. Rendered in the column and in this pane's
 *  head, from one hook, so both show the same sweep. */
export function RefreshControl({ review }: { review: Review }) {
  return (
    <div className="continuity-refresh">
      <button type="button" className="subtle" disabled={review.refreshing}
              onClick={() => { void review.refresh(); }}>
        Refresh continuity review
      </button>
      {review.refreshing && (
        <p className="field-hint" role="status">
          {review.following ? "A continuity sweep is running." : "Refreshing…"}
        </p>
      )}
      {!review.refreshing && review.refreshNote && (
        <p className="field-hint">{review.refreshNote}</p>
      )}
    </div>
  );
}

/** Which matching the next sweep will use (§12.1), from whichever read has
 *  answered. Nothing while neither has. */
export function matchingLine(review: Review): string | null {
  const c = review.candidates;
  const s = review.state;
  const mode = c && c !== "failed" ? c.matching : s && s !== "failed" ? s.matching : null;
  return mode ? MATCHING_LINES[mode] ?? null : null;
}

function FindingList(
  { cid, group, review }: {
    cid: string; group: "overlaps" | "closures" | "resolutions"; review: Review;
  },
) {
  const navigate = useNavigate();
  const { candidates } = review;
  if (candidates === null) return <p className="column-empty">Reading the findings…</p>;
  if (candidates === "failed") {
    return (
      <p className="empty-state">
        The continuity review could not be read. The rest of the ledger is unaffected.
      </p>
    );
  }
  const found = candidates.candidates.filter((c) => c.group === group);
  if (!found.length) {
    return <p className="empty-state"><span className="empty-what">{NOTHING[group]}</span></p>;
  }
  return (
    <ul className="continuity-list">
      {found.map((c) => {
        const suggested = proposalLabel(c.proposal?.decision);
        return (
          <li key={c.id}>
            <button type="button" className="continuity-finding"
                    onClick={() => navigate(ledgerHref(cid, {
                      section: "continuity", group, candidate: c.id }))}>
              <span className="continuity-item-title">{titlesOf(c)}</span>
              <span className="continuity-item-meta">
                {KIND_PHRASES[c.kind]}{suggested && ` · ${suggested}`}
              </span>
              {c.stale && (
                <span className="continuity-stale">
                  {STALE_TEXT[c.stale_reason ?? "records"]}
                </span>
              )}
            </button>
          </li>
        );
      })}
    </ul>
  );
}

export function ContinuityReview(
  { cid, group, review }: { cid: string; group: ContinuityGroup; review: Review },
) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /** One reviewed or dismissed write, then the shared re-read (one epoch). */
  async function perform(write: () => Promise<unknown>) {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await write();
      review.wrote();
    } catch (err: unknown) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="shelf-head">
        <div>
          <div className="eyebrow">Continuity review</div>
          <h1 className="screen-title">{GROUP_LABELS[group]}</h1>
        </div>
        <RefreshControl review={review} />
      </div>
      {error && <p className="continuity-error" role="alert">{error}</p>}
      {group === "reviewed" ? (
        <ReviewedGroup state={review.state} busy={busy}
                       onUnmerge={(ref) => { void perform(() => api.removeAlias(cid, ref)); }}
                       onRemoveLink={(id) => { void perform(() => api.removeLink(cid, id)); }} />
      ) : group === "dismissed" ? (
        <DismissedGroup state={review.state} busy={busy}
                        onRestore={(fp) => {
                          void perform(() => api.restoreSuppression(cid, fp));
                        }} />
      ) : (
        <FindingList cid={cid} group={group} review={review} />
      )}
    </>
  );
}

export default ContinuityReview;
