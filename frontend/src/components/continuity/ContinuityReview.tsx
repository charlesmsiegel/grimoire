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
 *  A finding in the list is a button that addresses it; nothing in the list
 *  acts on one. Its address opens `CandidateDetail`, read-only, whose explicit
 *  actions land here: this pane sends them, reads what a refusal means
 *  (`ApiError.kind`), and on a 2xx returns to the group with one line saying
 *  what was done (Decision 24).
 *
 *  AN ANSWER BELONGS TO WHAT WAS ASKED. Nothing holds the reader still while
 *  an action is in flight -- "‹ All findings", the list, the rail and a
 *  campaign switch all stay live -- so an answer may land on a page that is
 *  showing something else. What it says about the campaign it was sent to
 *  still holds (the write landed, so the ledger re-reads; a 409's current
 *  records are that finding's); what it says to the reader -- a confirmation,
 *  a refusal, "Merge anyway", the move back to the group -- is shown only if
 *  the finding it was about is still the one open. An answer for a campaign
 *  the reader has left touches nothing: the review hook already belongs to
 *  the new one, and a fork shares candidate ids.
 */
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { errorText } from "../../api/errors";
import type {
  ContinuityApply, ContinuityCandidate, ContinuityGroup,
} from "../../api/types";
import { ledgerHref } from "../../ledgerPaths";
import { CandidateDetail, formKey, type DetailError } from "./CandidateDetail";
import { DismissedGroup } from "./DismissedGroup";
import {
  GROUP_LABELS, isLive, KIND_PHRASES, LIVENESS_SENTENCES, MATCHING_LINES, proposalLabel,
  RELATION_PHRASES, STALE_TEXT,
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

/** What a refusal carries, read structurally (`api/errors.ts`'s rule): the
 *  view meets an `ApiError`, but nothing here needs the class. */
function refusal(err: unknown): { kind?: string; body: Record<string, unknown> } {
  if (typeof err !== "object" || err === null) return { body: {} };
  const { kind, body } = err as { kind?: unknown; body?: unknown };
  return {
    kind: typeof kind === "string" ? kind : undefined,
    body: typeof body === "object" && body !== null ? body as Record<string, unknown> : {},
  };
}

/** Decision 21's sentence for a `409 liveness_mismatch`, from the standings
 *  its body carries; null when the body does not say. */
function livenessSentence(body: Record<string, unknown>): string | null {
  const { source, canonical } = body as {
    source?: { status?: unknown }; canonical?: { ref?: unknown; status?: unknown };
  };
  const ref = typeof canonical?.ref === "string" ? canonical.ref : "";
  const type = ref.slice(0, Math.max(ref.indexOf(":"), 0));
  if (type !== "thread" && type !== "commitment") return null;
  if (typeof source?.status !== "string" || typeof canonical?.status !== "string") return null;
  const mine = isLive(type, source.status);
  if (mine === isLive(type, canonical.status)) return null;
  return LIVENESS_SENTENCES[type][mine ? "sourceLive" : "canonicalLive"];
}

/** The one line a landed action leaves behind (Decision 24). */
function confirmation(c: ContinuityCandidate, body: ContinuityApply | "dismiss" | "keep_open"):
    string {
  const title = (ref?: string) =>
    c.records.find((r) => r.ref === ref)?.title || "the record";
  const first = c.records[0]?.title || "The record";
  if (body === "dismiss") return "Dismissed the finding.";
  if (body === "keep_open") return `Kept ${first} open.`;
  if (body.op === "alias") {
    const source = c.records.find((r) => r.ref !== body.canonical);
    return `Merged ${title(source?.ref)} into ${title(body.canonical)}.`;
  }
  if (body.op === "link") {
    const phrase = (RELATION_PHRASES[body.relation ?? ""] ?? body.relation ?? "").toLowerCase();
    return `Linked ${title(body.from)}: ${phrase} ${title(body.to)}.`;
  }
  if (body.op === "close") return `Closed ${first}.`;
  return `Marked ${first} ${body.status ?? "resolved"}.`;
}

export function ContinuityReview(
  { cid, group, candidate, review }: {
    cid: string; group: ContinuityGroup; candidate?: string; review: Review;
  },
) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  /** A refused reviewed/dismissed action, held with the campaign it was for. */
  const [error, setError] = useState<{ cid: string; text: string } | null>(null);
  /** A refusal of an action on the open finding. */
  const [detailError, setDetailError] = useState<DetailError | null>(null);
  /** Findings an action was told are no longer pending (404), held with the
   *  campaign they came from; a re-read may not have caught up yet. */
  const [gone, setGone] = useState<{ cid: string; ids: string[] }>({ cid, ids: [] });
  /** The line a landed action leaves on its group's list. */
  const [done, setDone] = useState<{ cid: string; group: ContinuityGroup; text: string } | null>(
    null);
  /** What is open now, read by an answer that lands after the reader moved. */
  const openRef = useRef({ cid, candidate });
  openRef.current = { cid, candidate };

  // Opening a finding (or another one) starts it clean.
  useEffect(() => {
    setDetailError(null);
    if (candidate) { setDone(null); setError(null); }
  }, [candidate]);

  /** One reviewed or dismissed write, then the shared re-read (one epoch).
   *  An answer that lands after a campaign switch is the old campaign's: it
   *  neither re-reads the new one nor lays its refusal over it. */
  async function perform(write: () => Promise<unknown>) {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await write();
      if (openRef.current.cid === cid) review.wrote();
    } catch (err: unknown) {
      if (openRef.current.cid === cid) setError({ cid, text: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  /** One action on the open finding: on a 2xx, back to its group with a
   *  confirmation; otherwise whatever the refusal means. */
  async function decide(c: ContinuityCandidate, write: () => Promise<unknown>,
                        said: ContinuityApply | "dismiss" | "keep_open") {
    if (busy) return;
    setBusy(true);
    setDetailError(null);
    try {
      await write();
      if (openRef.current.cid !== cid) return;
      review.wrote();
      if (openRef.current.candidate !== c.id) return;
      setDone({ cid, group: c.group, text: confirmation(c, said) });
      navigate(ledgerHref(cid, { section: "continuity", group: c.group }), { replace: true });
    } catch (err: unknown) {
      if (openRef.current.cid === cid) refused(c, err, said);
    } finally {
      setBusy(false);
    }
  }

  function apply(c: ContinuityCandidate, body: ContinuityApply) {
    void decide(c, () => api.applyCandidate(cid, c.id, body), body);
  }

  /** A refusal for this campaign. What it says about the finding is kept;
   *  what it says to the reader waits on that finding still being open. */
  function refused(c: ContinuityCandidate, err: unknown,
                   said: ContinuityApply | "dismiss" | "keep_open") {
    const { kind, body } = refusal(err);
    const here = openRef.current.candidate === c.id;
    if (kind === "liveness_mismatch" && typeof said === "object") {
      if (!here) return;
      setDetailError({
        text: `${livenessSentence(body) ?? errorText(err)}.`,
        action: { label: "Merge anyway",
                  run: () => apply(c, { ...said, accept_status_change: true }) },
      });
    } else if (kind === "stale_candidate") {
      // Never a run (§12.9): a re-read, and the records as they are now.
      if (body.reason === "evidence") {
        review.evidenceMoved(c.id);
      } else {
        const current = api.staleCurrent(err);
        if (current) {
          review.applyCurrent(c.id, current);
          if (here) setDetailError({ text: "Records have changed since this was found. They are "
            + "shown as they are now; act again if the finding still holds." });
        }
      }
      review.reread();
    } else if (kind === "not_found") {
      setGone((g) => ({ cid, ids: [...(g.cid === cid ? g.ids : []), c.id] }));
      review.reread();
    } else if (kind === "partial_apply" || kind === "partial_dismiss") {
      // §22: a 500 that names parts that landed is a write. Drawing the
      // finding from the old read would leave it actionable after the thread
      // closed or the suppression landed, and a second click would only draw
      // a 409; so re-read as a 2xx does, back to the group, and keep the
      // server's sentence about what did not land where the list shows it.
      review.wrote();
      if (!here) return;
      setError({ cid, text: errorText(err) });
      navigate(ledgerHref(cid, { section: "continuity", group: c.group }), { replace: true });
    } else if (kind === "bad_scene") {
      // The beat's scene was renamed or removed under the open form (§12.9):
      // re-read, so the select offers the scenes as they are now rather than
      // an id the server will only refuse again.
      review.reread();
      if (here) setDetailError({ text: errorText(err) });
    } else if (here) {
      setDetailError({ text: errorText(err) });
    }
  }

  const findings = group === "overlaps" || group === "closures" || group === "resolutions"
    ? group : null;
  const read = review.candidates && review.candidates !== "failed" ? review.candidates : null;
  const goneHere = gone.cid === cid ? gone.ids : [];
  const open = findings && candidate && read && !goneHere.includes(candidate)
    ? read.candidates.find((c) => c.id === candidate) ?? null
    : null;
  // Only once a read has settled: a deep link from Todo never flashes it.
  const missing = !!(findings && candidate && read && !open);
  const said = done && done.cid === cid && done.group === group && !candidate ? done.text : null;

  return (
    <>
      <div className="shelf-head">
        <div>
          <div className="eyebrow">Continuity review</div>
          <h1 className="screen-title">{GROUP_LABELS[group]}</h1>
        </div>
        <RefreshControl review={review} />
      </div>
      {error && error.cid === cid
        && <p className="continuity-error" role="alert">{error.text}</p>}
      {group === "reviewed" ? (
        <ReviewedGroup state={review.state} busy={busy}
                       onUnmerge={(ref) => { void perform(() => api.removeAlias(cid, ref)); }}
                       onRemoveLink={(id) => { void perform(() => api.removeLink(cid, id)); }} />
      ) : group === "dismissed" ? (
        <DismissedGroup state={review.state} busy={busy}
                        onRestore={(fp) => {
                          void perform(() => api.restoreSuppression(cid, fp));
                        }} />
      ) : open && read ? (
        <CandidateDetail key={formKey(open)} cid={cid} candidate={open} names={read.names}
                         scenes={read.scenes} busy={busy} refreshing={review.refreshing}
                         error={detailError}
                         onApply={(body) => apply(open, body)}
                         onDismiss={(decision) => {
                           void decide(open, () => api.dismissCandidate(
                             cid, open.id, decision, open.fingerprint), decision);
                         }}
                         onBack={() => navigate(ledgerHref(cid, {
                           section: "continuity", group }))}
                         onRefresh={() => { void review.refresh(); }} />
      ) : (
        <>
          {missing && <p className="continuity-note" role="status">
            This finding is no longer pending.
          </p>}
          {said && <p className="continuity-confirm" role="status">{said}</p>}
          <FindingList cid={cid} group={group} review={review} />
        </>
      )}
    </>
  );
}

export default ContinuityReview;
