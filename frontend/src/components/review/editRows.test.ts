// The review's routing rules, tested where they are: pure functions over one
// staged edit. `SceneReview.test.tsx` proves the panel obeys them by ending a
// scene and reading the screen, which is the right test for the panel and a
// slow, indirect one for the rules themselves — a band that routes wrongly
// shows up there as a row missing from a drawer, three hundred lines from the
// line that decided it.
import {
  approvedByDefault, dossierNotice, drawerKey, editBand, groupOf, identityChip,
  isCandidateAlternative, isUncited, wireEdit,
} from "./editRows";
import type { Dossiers, IdentityCheck, StagedEdit } from "../../api/client";

const edit = (over: Partial<StagedEdit> = {}) => ({
  id: "lore:the-pact", kind: "lore", target: { kind: "lore", id: "the-pact" },
  label: "The Pact — lore", field: "body", before: "", after: "x", authored: false,
  ...over,
} as StagedEdit);

const cited = (over: Partial<NonNullable<StagedEdit["review"]>> = {}) => ({
  certainty: 0.9, quote: "They broke it by morning.", speaker: "Mara",
  authority: "other" as const, score: 0.3, band: "medium" as const, ...over,
});

test("a row with no review, and one whose quote is blank, are both uncited", () => {
  expect(isUncited(edit())).toBe(true);
  expect(isUncited(edit({ review: cited({ quote: "   " }) }))).toBe(true);
  expect(isUncited(edit({ review: cited() }))).toBe(false);
});

test("two kinds that write the same file are one drawer", () => {
  expect(groupOf(edit({ kind: "relationship" }))).toBe("relationships");
  expect(groupOf(edit({ kind: "bond" }))).toBe("relationships");
  expect(groupOf(edit({ kind: "character_state" }))).toBe("state");
  expect(groupOf(edit({ kind: "dossier" }))).toBe("state");
});

test("a kind no group claims falls back to World records & cards rather than vanishing", () => {
  // The drawers are the only way to reach a row, so a kind added to the API and
  // not to EDIT_GROUPS must still be reviewable — silently unroutable is the
  // one outcome that would let an unreviewed edit reach a save.
  expect(groupOf(edit({ kind: "something_new" as StagedEdit["kind"] }))).toBe("records");
});

test("a row the extraction never banded reads as medium, and so is pre-approved", () => {
  // dossier, voice and sheet proposals are staged after the extraction and rest
  // on no citation: the fallback is what keeps them shown and ticked.
  expect(editBand(edit({ kind: "dossier" }))).toBe("medium");
  expect(approvedByDefault(edit({ kind: "dossier" }))).toBe(true);
});

test("only a low band arrives unticked", () => {
  expect(approvedByDefault(edit({ review: cited({ band: "high" }) }))).toBe(true);
  expect(approvedByDefault(edit({ review: cited({ band: "medium" }) }))).toBe(true);
  expect(approvedByDefault(edit({ review: cited({ band: "low" }) }))).toBe(false);
});

test("uncited beats low, and low beats the row's own store", () => {
  // A row is filed in exactly one drawer, and the two NEEDS YOU drawers cut
  // across the stores: whichever question the reviewer must answer wins.
  expect(drawerKey(edit({ kind: "fact", review: cited({ quote: "", band: "low" }) })))
    .toBe("uncited");
  expect(drawerKey(edit({ kind: "fact", review: cited({ band: "low" }) }))).toBe("low");
  expect(drawerKey(edit({ kind: "fact", review: cited({ band: "high" }) }))).toBe("facts");
});

const dossiers = (over: Partial<Dossiers> = {}) => ({
  status: "ok", reason: "", proposed: [], failed: [], skipped: [],
  attempted: true, budget_exhausted: false, ...over,
} as Dossiers);

test("a dossier phase the clock never started says so before anything else", () => {
  // Most specific first: a budget skip that also carries per-NPC failures is
  // still "never prepared", because nothing was asked of the model.
  expect(dossierNotice(dossiers({
    status: "failed", reason: "the absorb time budget ran out",
    attempted: false, budget_exhausted: true,
  }))).toBe("No NPC dossier was prepared: the absorb time budget ran out");
});

test("per-NPC failures are counted, never called a whole-phase failure", () => {
  expect(dossierNotice(dossiers({ status: "failed", failed: [{ id: "mara", reason: "x" }] })))
    .toBe("No NPC dossier could be prepared");
  expect(dossierNotice(dossiers({ status: "degraded", failed: [{ id: "mara", reason: "x" }] })))
    .toBe("Some NPC dossiers could not be prepared");
});

test("a phase that produced something is never reported as failed", () => {
  // "prepared", never "refreshed": a dossier is staged here and only written on
  // save (#235), and a partial run is degraded rather than a failure.
  expect(dossierNotice(dossiers({ status: "degraded", reason: "ran out of time" })))
    .toBe("Some NPC dossiers were not prepared: ran out of time");
  expect(dossierNotice(dossiers({ status: "failed", reason: "the cast is unreadable" })))
    .toBe("NPC dossier refresh failed: the cast is unreadable");
});

// ---- the existing-record check (continuity identity) ----------------------

const ledgerCandidate = (status = "open") => ({
  ref: "thread:find-the-ledger", title: "Find the ledger", status,
  latest_beat: "Winifred learned the harbour ledger exists.",
  signals: { title_equal: false, slug_equal: false, tokens: 0.4, chars: 0.5, cosine: null,
             actors: [], scenes: [], anchors: [], via: "lexical" as const },
});

const asNew = edit({
  id: "plot:recover-the-harbour-ledger", kind: "plot",
  target: { kind: "plot", id: "recover-the-harbour-ledger" },
  label: "Recover the harbour ledger — open", field: "beat", before: "",
  after: "Winifred went looking for the harbour ledger.",
  payload: { id: "recover-the-harbour-ledger", title: "Recover the harbour ledger",
             status: "open", scene: "s1" },
});
const ontoLedger = edit({
  id: "plot:find-the-ledger", kind: "plot", target: { kind: "plot", id: "find-the-ledger" },
  label: "Find the ledger — advanced", field: "beat",
  before: "Find the ledger — open — Winifred learned the harbour ledger exists.",
  after: "Winifred went looking for the harbour ledger.",
  payload: { id: "find-the-ledger", title: "Find the ledger", status: "advanced", scene: "s1" },
});

const check = (over: Partial<IdentityCheck> = {}): IdentityCheck => ({
  decision: "uncertain", status: "accepted", reason: "the two may be the same search",
  proposed: { title: "Recover the harbour ledger", why_new: "", distinguished_from: [] },
  candidates: [ledgerCandidate()], alternatives: [ontoLedger], ...over,
});

test("wireEdit strips approved and alternatives and keeps the rest", () => {
  const row = { ...asNew, review: cited({ band: "low" }), identity_check: check(),
                approved: false, rejected: false, judged: true, resolve: "replace" as const,
                resolve_from: "x" };
  const wire = wireEdit(row);
  expect("approved" in wire).toBe(false);
  expect(wire.identity_check && "alternatives" in wire.identity_check).toBe(false);
  // everything else rides along exactly as the save body always sent it
  const { approved: _a, identity_check: ic, ...rest } = row;
  const { alternatives: _alts, ...icRest } = ic;
  expect(wire).toEqual({ ...rest, identity_check: icRest });
  // the caller's row is not mutated
  expect(row.identity_check.alternatives).toHaveLength(1);
});

test("wireEdit leaves a row with no identity check exactly as the save always sent it", () => {
  const row = { ...edit({ review: cited() }), approved: true, judged: false };
  const { approved: _a, ...rest } = row;
  expect(wireEdit(row)).toEqual(rest);
  expect("identity_check" in wireEdit(row)).toBe(false);
});

test("a low uncertain row is not approved by default", () => {
  // Characterization: the band, not the check, is what withholds the tick --
  // the server stages every uncertain or unanswered row at `low`.
  expect(approvedByDefault({ ...asNew, review: cited({ band: "low" }),
                             identity_check: check() })).toBe(false);
});

test("identityChip wording per decision", () => {
  expect(identityChip(asNew)).toBeNull();
  expect(identityChip({ ...asNew, identity_check: check() })).toBe("Possible existing record");
  // an uncertain row with nothing to switch to still wears it -- those are the
  // downgrades, the rows most in need of a second look
  expect(identityChip({ ...asNew, identity_check: check({ alternatives: [] }) }))
    .toBe("Possible existing record");
  expect(identityChip({ ...asNew, identity_check: check({ status: "downgraded" }) }))
    .toBe("Possible existing record");
  expect(identityChip({ ...asNew, identity_check: check({ decision: "unchecked",
                                                           status: "hint_only" }) }))
    .toBe("Possible existing record");
  expect(identityChip({ ...asNew, identity_check: check({ decision: "new",
                                                           status: "hint_only" }) }))
    .toBe("Possible existing record");
  expect(identityChip({ ...ontoLedger, identity_check: check({ decision: "existing",
                                                                alternatives: [asNew] }) }))
    .toBe("Matched an existing record");
  expect(identityChip({ ...asNew, identity_check: check({ decision: "new" }) })).toBeNull();
});

test("isCandidateAlternative compares prefixed refs", () => {
  expect(isCandidateAlternative(ontoLedger, check())).toBe(true);
  expect(isCandidateAlternative(asNew, check())).toBe(false);
  // a commitment alternative is matched under the commitment prefix, never the thread one
  const debt = edit({ id: "commitment:the-debt", kind: "commitment",
                      target: { kind: "commitments", id: "the-debt" } });
  expect(isCandidateAlternative(debt, check())).toBe(false);
  expect(isCandidateAlternative(debt, check({ candidates: [
    { ...ledgerCandidate(), ref: "commitment:the-debt", title: "Mara's debt to the Saltmarch guild" }] })))
    .toBe(true);
});
