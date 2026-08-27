// The vocabulary of the end-of-scene review: what a staged edit is once the
// panel has an opinion about it, which drawer it belongs in, and how each of
// the absorb's phases reads out loud.
//
// Pure, and deliberately free of React. Everything here was module-level in
// `CampaignView` before the review moved out of it (#378); it is separated from
// `useSceneReview` so the routing rules can be tested — and read — without
// mounting anything.
import type { AbsorbPhase, Dossiers, StagedEdit } from "../../api/client";

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
