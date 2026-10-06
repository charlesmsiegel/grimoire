/** The continuity review's words, in one table (capstone Decision 25, §30).
 *
 *  A raw decision word never reaches the reader: a model's `duplicate` is a
 *  suggestion about two records, not a verdict, so every proposal is shown
 *  hedged -- "Suggested: …" -- or as "No clear suggestion". The server keeps
 *  the same strings in `store/continuity/pending.DECISION_LABELS` for Todo's
 *  items; each side pins its copy with a test, so the two cannot drift
 *  without one of them going red.
 *
 *  A leaf: types only, no React.
 */
import type { CandidateKind, ContinuityGroup } from "../../api/types";

/** The groups, as the column and the main pane head them. */
export const GROUP_LABELS: Record<ContinuityGroup, string> = {
  overlaps: "Possible overlaps",
  closures: "Possible closures",
  resolutions: "Possible commitment resolutions",
  reviewed: "Reviewed links / merges",
  dismissed: "Dismissed findings",
};

/** What a finding is, in the words §30 prefers. The same phrases Todo's
 *  continuity items lead with (`routes/todo._CONTINUITY_KIND`). */
export const KIND_PHRASES: Record<CandidateKind, string> = {
  possible_duplicate: "Possible overlap",
  possible_relation: "Possible relation",
  possible_thread_closure: "May be finished",
  possible_commitment_resolution: "Needs resolution review",
};

/** Every decision word the reconcile prompt may answer with, hedged. */
export const DECISION_LABELS: Record<string, string> = {
  duplicate: "Suggested: same business (merge)",
  continuation: "Suggested: continuation of",
  subthread: "Suggested: subthread of",
  related: "Suggested: related",
  pays_off: "Suggested: pays off",
  distinct: "Suggested: different business",
  close: "Suggested: may be finished",
  fulfilled: "Suggested: fulfilled",
  broken: "Suggested: broken",
  expired: "Suggested: expired",
  keep_open: "Suggested: keep open",
  before: "Suggested: before",
  on: "Suggested: on",
  after: "Suggested: after",
  by: "Suggested: by",
  unrelated: "No clear suggestion",
  uncertain: "No clear suggestion",
};

/** The hedged label for a proposal's decision, or null -- for no proposal,
 *  and for a word this table does not hold, which is omitted rather than
 *  shown raw. An own-property check, so a word like `constructor` is not an
 *  entry. */
export function proposalLabel(decision: string | null | undefined): string | null {
  if (!decision || !Object.prototype.hasOwnProperty.call(DECISION_LABELS, decision)) {
    return null;
  }
  return DECISION_LABELS[decision];
}

/** A stale finding's marker: visible text, never a glyph with a tooltip. */
export const STALE_TEXT: Record<"records" | "evidence", string> = {
  records: "Records changed",
  evidence: "Evidence moved",
};

/** What the review will do on Refresh, by matching mode (§12.1). */
export const MATCHING_LINES: Record<"basic" | "semantic", string> = {
  basic: "Basic matching active — semantic matching not configured",
  semantic: "Semantic matching active",
};

/** How a stored link reads, `a <phrase> b` (§30: "Continuation of"). A
 *  relation this table does not hold reads as itself, since a link's relation
 *  is a store word the reader chose rather than a model's proposal. */
export const RELATION_PHRASES: Record<string, string> = {
  continues: "Continuation of",
  subthread_of: "Subthread of",
  pays_off: "Pays off",
  related_to: "Related to",
  before: "Before",
  on: "On",
  after: "After",
  by: "By",
};

/** What a dismissal decided, for the Dismissed findings group (§12.7). */
export const SUPPRESSION_LABELS: Record<string, string> = {
  dismiss: "Dismissed",
  keep_open: "Kept open",
};

/** Why a finding's actions are off (§12.2): its records moved since the sweep
 *  found it, or a scene its proposal cites was renamed or removed. Either way
 *  a Refresh is the way forward. */
export const STALE_SENTENCES: Record<"records" | "evidence", string> = {
  records: "Records have changed since this was found.",
  evidence: "An evidence scene has been renamed or removed since this was found.",
};

/** What a merge across liveness would hide (Decision 21), by the type of the
 *  two records and by which side is still live: `source` is the record merged
 *  away, and it is the one that disappears behind the kept one. */
export const LIVENESS_SENTENCES: Record<"thread" | "commitment",
                                       { sourceLive: string; canonicalLive: string }> = {
  thread: {
    sourceLive: "Merging will hide an open thread behind a closed one",
    canonicalLive: "Merging will hide a closed thread behind an open one",
  },
  commitment: {
    sourceLive: "Merging will hide an unresolved commitment behind a resolved one",
    canonicalLive: "Merging will hide a resolved commitment behind an unresolved one",
  },
};

/** The statuses a commitment is resolved by (`store/commitments.RESOLVED`). */
const RESOLVED = ["fulfilled", "broken", "expired"];

/** Still open / still owed -- `effective.is_live`, the predicate the server's
 *  liveness check compares. */
export function isLive(type: "thread" | "commitment", status: string): boolean {
  const s = status.toLowerCase();
  return type === "thread" ? s !== "closed" : !RESOLVED.includes(s);
}
