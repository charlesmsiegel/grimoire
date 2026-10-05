// The vocabulary of the end-of-scene review: what a staged edit is once the
// panel has an opinion about it, which drawer it belongs in, and how each of
// the absorb's phases reads out loud.
//
// Pure, and deliberately free of React. Everything here was module-level in
// `CampaignView` before the review moved out of it (#378); it is separated from
// `useSceneReview` so the routing rules can be tested — and read — without
// mounting anything.
import type { AbsorbPhase, Dossiers, IdentityCheck, StagedEdit } from "../../api/client";

/** A staged edit plus the reviewer's standing verdict on it.
 *
 *  `approved` is what the save sends. `rejected` is the reviewer saying no
 *  out loud, which is NOT the same as leaving a row alone: an undecided row
 *  is one nobody has looked at yet, and the footer counts those so a save
 *  cannot quietly drop a proposal the reviewer never saw. Both false is
 *  undecided; both true is impossible (the controls are exclusive). */
export type EditRow = StagedEdit & {
  approved: boolean; rejected?: boolean; judged?: boolean;
};

// Reader-facing names for the absorb steps the API reports in `phases`. The
// wire names say where the work happens; these say what the reviewer lost.
export const PHASE_LABELS: Record<AbsorbPhase["name"], string> = {
  extraction: "the scene summary",
  identity: "the existing-record check",
  dossiers: "NPC dossiers",
  voice: "voice checks",
  audit: "mechanics audit",
};

/** Which drawer of the review a proposal belongs in.
 *
 *  Grouped by *store* rather than by edit kind, because that is the question a
 *  reviewer is actually asking — "what is this absorb claiming about her
 *  state", not "how many `bond` rows are there". Two kinds that write the same
 *  file are one group. */
export const EDIT_GROUPS: { key: string; label: string; kinds: StagedEdit["kind"][] }[] = [
  { key: "state", label: "Character state", kinds: ["character_state", "dossier"] },
  { key: "relationships", label: "Relationships", kinds: ["relationship", "bond"] },
  { key: "facts", label: "Facts", kinds: ["fact"] },
  { key: "plot", label: "Plot & commitments", kinds: ["plot", "commitment"] },
  { key: "new", label: "New records", kinds: ["new_character", "new_location", "new_lore"] },
  // "World records", not "Lore": a `lore` row is a body append onto any of the
  // five entity kinds (#224) — an item or a creature lands here too.
  { key: "records", label: "World records & cards", kinds: ["lore", "authored"] },
  { key: "sheets", label: "Sheets", kinds: ["sheet"] },
  { key: "voice", label: "Voice", kinds: ["voice_drift"] },
];

export function groupOf(e: StagedEdit): string {
  return EDIT_GROUPS.find((g) => g.kinds.includes(e.kind))?.key ?? "records";
}

/** A row nothing in the transcript was cited for. These are the ones the panel
 *  puts first and in `--alert`: an uncited proposal is not wrong, but it is the
 *  one kind of proposal a reviewer cannot check against anything, so it is the
 *  one that most needs a human. */
export function isUncited(e: StagedEdit): boolean {
  return !e.review || !e.review.quote.trim();
}

// Staged edit kinds whose payload stamps the scene the beat came from, and so
// have to follow a scene rename made while the review is open — see
// `useSceneReview`'s `sceneRenamed`.
export const SCENE_STAMPED: StagedEdit["kind"][] = ["plot", "commitment", "fact"];

// What the backend proved about a proposal's cited speaker (#112), said the way
// a reviewer would say it. The wire names are tiers; these are the reason the
// row is banded where it is, which is the only thing worth a chip.
export const AUTHORITY_LABELS: Record<NonNullable<StagedEdit["review"]>["authority"], string> = {
  narration: "narrated",
  self: "said of themself",
  other: "said by someone else",
  // Not "speaker not in this scene": the tier also covers a name TWO speakers
  // answer to, and telling a reviewer their model invented a citation it did
  // not invent is a worse error than the vaguer wording.
  unattributed: "no one speaker matches",
  uncited: "nothing cited",
};

/** How a contradicted row's later scene was established, in the reviewer's
 *  words. The three sources are not equally strong and the tooltip says which
 *  one answered: a quote is evidence a reader can go and check, a write-back
 *  is a record-level log entry, and a thread's last beat is neither. */
export const CONTRADICTION_SOURCES: Record<string, string> = {
  citation: "quoted",
  changes: "recorded",
  thread: "last beat",
};

// A row's band, with the fallback that keeps the pre-#110 behaviour intact:
// dossier, voice and sheet proposals are staged after the extraction and rest
// on no citation, so they route as `medium` — shown, and pre-approved.
export function editBand(e: StagedEdit): NonNullable<StagedEdit["review"]>["band"] {
  return e.review?.band ?? "medium";
}

// Only `low` starts unticked, and that is now a *display* verdict rather than
// a gate: the save sends everything the reviewer did not reject, so an unticked
// row still lands unless it is rejected. What the tick buys is visibility --
// the rows the model was least sure about arrive looking unfinished and are
// collected in the two NEEDS YOU drawers, so "what did nobody look at" is one
// glance rather than a diff.
//
// The older rule was the reverse (nothing applied without a tick). It was
// changed because its failure mode was silent in the worse direction: closing a
// review without reading it discarded exactly the model's least confident work
// while looking identical to accepting it. The footer now states which way it
// goes and counts it on the button.
export function approvedByDefault(e: StagedEdit): boolean {
  return editBand(e) !== "low";
}

/** Which drawer a row belongs in. The two NEEDS YOU drawers cut across the
 *  stores on purpose: they hold exactly the rows that did NOT arrive
 *  pre-approved, which is the only question a reviewer has to answer before
 *  saving. A row is uncited *or* low, never filed in both.
 *
 *  This is also what retired the Show/Hide low-confidence disclosure: a
 *  drawer with a live count in the column says "these were withheld" more
 *  plainly than a collapsed section nested inside another drawer did, which
 *  is exactly what that disclosure existed to say.
 */
export function drawerKey(e: StagedEdit): string {
  return isUncited(e) ? "uncited" : editBand(e) === "low" ? "low" : groupOf(e);
}

// The dossier phase has five distinguishable bad endings and the wording has to
// match the edit list beside it: "prepared", never "refreshed" (a dossier is
// staged here and only written on save, #235), and never "failed" for a phase
// that produced something. Ordered most-specific first.
export function dossierNotice(d: Dossiers): string {
  if (d.budget_exhausted && !d.attempted) return `No NPC dossier was prepared: ${d.reason}`;
  if (d.failed.length > 0) {
    return d.status === "failed" ? "No NPC dossier could be prepared"
                                 : "Some NPC dossiers could not be prepared";
  }
  // Nothing went wrong per-NPC, so the reason is the whole phase's story: a
  // partial run (some prepared, the rest dropped) or a phase that never got off
  // the ground at all (an unreadable cast).
  return d.status === "degraded" ? `Some NPC dossiers were not prepared: ${d.reason}`
                                 : `NPC dossier refresh failed: ${d.reason}`;
}

// ---- the existing-record check (spec §10.3) -------------------------------

/** What an uncertain row says when there is a record to switch it to. */
export const IDENTITY_UNCERTAIN_HINT =
  "Possible existing record — reject this row, or switch it to the existing record, "
  + "if it is the same business.";

/** ...and when there is not: its only candidate is closed, or another row in
 *  the batch already moves it, so the sentence above would point at a switch
 *  the row does not offer. */
export const IDENTITY_NO_ALTERNATIVE_HINT =
  "The matching record is closed or already used by another row in this scene; "
  + "this stays a new record unless you reject it.";

/** One row as the save sends it. `approved` is the panel's display verdict and
 *  never the server's business; the alternatives are complete staged rows the
 *  server rendered for the swap, and sending them back would only make the
 *  body heavier. Everything else -- `rejected` and `judged` included -- goes
 *  exactly as it always did, so a row with no identity check is unchanged. */
export function wireEdit(row: EditRow): StagedEdit {
  const { approved: _approved, ...rest } = row;
  if (!rest.identity_check) return rest;
  const { alternatives: _alternatives, ...check } = rest.identity_check;
  return { ...rest, identity_check: check };
}

/** The chip a row the check examined wears, or null for none.
 *
 *  Read off the record the row writes NOW, not off the stored verdict alone:
 *  a swap keeps the server's check (decision, status, reason) and changes only
 *  the target, so a verdict about the row as staged can misstate the row as it
 *  will save. As staged, only an accepted `existing` targets a candidate.
 *
 *  - On a candidate: an accepted retarget wears "Matched an existing record"
 *    so its escape hatch is discoverable ("matched" says what was staged, not
 *    that it is true); a row the reviewer switched there wears "Switched to an
 *    existing record", which says who decided it.
 *  - Off every candidate, a row the check did not settle wears "Possible
 *    existing record" -- WITH or without alternatives, because the ones
 *    without are the downgrades (the only candidate is closed, or another row
 *    already holds it), and those are the rows most in need of a second look.
 *    An accepted `new` wears nothing, and neither does an accepted retarget
 *    the reviewer kept as new: either way there is nothing left to flag,
 *    though the block still lists what was compared. */
export function identityChip(e: StagedEdit): string | null {
  const ic = e.identity_check;
  if (!ic) return null;
  const matched = ic.decision === "existing" && ic.status === "accepted";
  if (isCandidateAlternative(e, ic)) {
    return matched ? "Matched an existing record" : "Switched to an existing record";
  }
  if (matched) return null;
  if (ic.decision === "uncertain" || ic.decision === "unchecked" || ic.status === "hint_only") {
    return "Possible existing record";
  }
  return null;
}

/** The hint under an uncertain row, or null for none. Like the chip it reads
 *  the current target: a row already switched onto a candidate has taken the
 *  hint's advice, so repeating it would ask for a switch that is done. The
 *  spec's hint names a switch; a row with nothing to switch to (its only
 *  candidate is closed, or another row holds it) gets the sentence that is
 *  true of it instead. */
export function identityHint(e: StagedEdit): string | null {
  const ic = e.identity_check;
  if (!ic || ic.decision !== "uncertain" || isCandidateAlternative(e, ic)) return null;
  return (ic.alternatives ?? []).length > 0 ? IDENTITY_UNCERTAIN_HINT : IDENTITY_NO_ALTERNATIVE_HINT;
}

/** What the extraction itself proposed, as one visible line, or null when the
 *  check carries no title. A matched or switched row is labelled from the
 *  STORED record, so without this the reviewer weighing whether the match is
 *  wrong cannot see the record the model meant to open -- the escape hatch's
 *  own text is a bare "Keep as a new record". `distinguished_from` holds bare
 *  ids of stored records the model said this is not; each is named by its
 *  candidate's title where the candidates list it, by its id otherwise. */
export function identityProposal(ic: IdentityCheck): string | null {
  const title = ic.proposed?.title?.trim();
  if (!title) return null;
  let line = `Proposed as new: “${title}”`;
  const why = ic.proposed.why_new?.trim();
  if (why) line += ` — ${why}`;
  const apart = (ic.proposed.distinguished_from ?? []).map((id) =>
    ic.candidates.find((c) => c.ref.slice(c.ref.indexOf(":") + 1) === id)?.title || id);
  if (apart.length > 0) line += ` (set apart from ${apart.join(", ")})`;
  return line;
}

/** Whether an alternative stages onto an existing record, as against being
 *  the as-new variant. Read off the alternative itself first: a non-empty
 *  `before` is the stored record it writes, and the as-new variant's is
 *  always empty -- so a row onto a record the candidates do not name (one
 *  merged into another while the review was prepared, in a review stored
 *  before the server re-resolved its candidates) is never offered as new.
 *  Otherwise it is matched against the candidates, whose refs are PREFIXED
 *  canonical refs (`thread:find-the-ledger`) while a staged target is a bare
 *  id under the store's own kind (`plot` / `commitments`), so the comparison
 *  spells the prefix out. It also classifies the original row pushed back
 *  after a swap: an accepted retarget's original targets a candidate, an
 *  as-new one does not. */
export function isCandidateAlternative(alt: StagedEdit, ic: IdentityCheck): boolean {
  if (alt.before !== "") return true;
  const ref = `${alt.target.kind === "plot" ? "thread" : "commitment"}:${alt.target.id}`;
  return ic.candidates.some((c) => c.ref === ref);
}

/** The kinds two rows of which may not write the same record in one save. */
const TARGETED: StagedEdit["kind"][] = ["plot", "commitment"];

/** Whether another live row already writes the record `alt` would. A swap
 *  must not make two rows move one thread: `check_conflicts` judges every
 *  row against the pre-write store, so both would pass and the later status
 *  would win. `skip` is the row being swapped, which is about to stop
 *  targeting what it targets now. */
export function targetTaken(rows: readonly EditRow[], alt: StagedEdit, skip: number): boolean {
  return rows.some((r, j) => j !== skip && !r.rejected && r.kind === alt.kind
                             && r.target.id === alt.target.id);
}

/** Every set of two or more non-rejected plot / commitment rows writing one
 *  record, as row indices. Empty when the batch is clean. */
export function targetClashes(rows: readonly EditRow[]): number[][] {
  const byTarget = new Map<string, number[]>();
  rows.forEach((r, i) => {
    if (r.rejected || !TARGETED.includes(r.kind)) return;
    const key = `${r.kind}:${r.target.id}`;
    byTarget.set(key, [...(byTarget.get(key) ?? []), i]);
  });
  return [...byTarget.values()].filter((idx) => idx.length > 1);
}
