import type { RunHandle } from "../stream";
import type { KnowledgeFingerprint, PromptFieldStatuses } from "./context";

/** The story-pressure vocabulary (capstone §16, §20). Each union is held to
 *  its Python tuple by `backend/tests/test_suggest_controls.py`, which parses
 *  this file -- so each stays a plain union of string literals. */
export type DriverKind = "thread" | "commitment" | "event" | "birthday" | "holiday";
export type DriverAction =
  | "advance" | "close_candidate" | "address" | "fulfill_candidate" | "break_candidate"
  | "expire_candidate" | "anchor";
/** Declared in the chooser's display order (`pressure.SORT_ORDER`). */
export type PressureState =
  "overdue" | "today" | "due_soon" | "upcoming" | "passed" | "stale" | "ok";
export type TimeMode = "auto" | "near" | "move" | "anchor";
export type AnchorRelation = "before" | "on" | "after" | "by";
/** What the reader set on one driver. Not a server vocabulary: the request
 *  carries it as three ref lists. */
export type DriverControl = "normal" | "focus" | "avoid" | "must";
/** A reviewed link's relation (`effective.LINK_RELATIONS`). */
export type LinkRelation =
  | "continues" | "subthread_of" | "pays_off" | "before" | "on" | "after" | "by"
  | "related_to";
/** A reviewed link on a driver; `relation` is the story graph's `LinkRelation`
 *  (the server sends only `effective.LINK_RELATIONS` values here). */
export type DriverLink = { id: string; relation: LinkRelation; other: string;
                           direction: "out" | "in" | "both" };
/** One row of `GET /continuity/drivers`. */
export type Driver = {
  ref: string; kind: DriverKind; label: string; summary: string; actors: string[];
  status: string;
  pressure: { state: PressureState; in_days: number | null; friendly: string };
  time_anchors: string[]; links: DriverLink[];
};
/** A dated temporal item a batch may be anchored to. A month-only birthday has
 *  no `fixed` day, so it takes only the `on` relation. */
export type AnchorOption = {
  ref: string; kind: "event" | "birthday" | "holiday"; label: string; native: string;
  friendly: string; fixed: number | null; in_days: number | null;
  precision: "exact" | "yearless" | "month";
};
export type DriversSnapshot = {
  now: string; friendly: string; fixed: number | null; matching: "basic" | "semantic";
  drivers: Driver[]; anchors: AnchorOption[];
};
/** A driver a generated card claims to serve, as the server validated it. */
export type SuggestionDriver = { ref: string; kind: DriverKind; action: DriverAction;
                                 label: string };
export type SuggestionAnchor = { ref: string; kind: DriverKind; relation: AnchorRelation;
                                 label: string; friendly: string; in_days: number | null };
export type RefLabel = { ref: string; label: string };
/** A generated card. The provenance fields are optional: a reply from before
 *  them, or a fixture, has none, and reads as claiming nothing (§27). */
export type SceneSuggestion = {
  title: string; premise: string; date?: string;
  focus?: string;
  cast: { kind: string; id: string; name: string }[];
  location: { id: string; name: string } | null;
  drivers?: SuggestionDriver[];
  time_anchor?: SuggestionAnchor | null;
  date_friendly?: string;
  in_days?: number | null;
  date_rejected?: boolean;
  /** Which rule blanked the date: the card's anchor, or the `near`/`move`
   *  time setting -- which can refuse an anchored card's derived date too.
   *  Null when nothing did; absent on a reply from before it (§15.2). */
  date_rejected_by?: "anchor" | "time" | null;
  unmet_must?: RefLabel[];
  avoided?: RefLabel[];
};
/** The body of `POST /scene-suggestions`. Every field defaults in
 *  `api.sceneSuggestions`, so a caller names only what it sets. */
export type SceneSuggestionsOptions = {
  after?: string; offscreen?: boolean; direction?: string; rank?: boolean;
  focus_refs?: string[]; avoid_refs?: string[]; must_refs?: string[];
  time_mode?: TimeMode; time_anchor_ref?: string;
  time_anchor_relation?: AnchorRelation | "";
};
/** A row of the scene ledger (#88) — a saved idea, or a greeting composed into
 *  the same shape. Deliberately a superset of `SceneSuggestion`, so one card
 *  renderer covers both: `cast` and `location` come back resolved.
 *
 *  `source` is `"greeting"` for the composed entries, whose ids are
 *  `greeting:<gid>` and whose status writes delegate to the greeting's own
 *  marks server-side. */
export type SceneIdea = {
  id: string; title: string; premise: string; date: string;
  focus?: string;
  cast: { kind: string; id: string; name: string }[];
  location: { id: string; name: string } | null;
  pcless: boolean;
  source: "llm" | "user" | "greeting";
  status: "active" | "used" | "dismissed";
  created: string; used_scene: string;
  /** Derived on read (§17): what the idea was about and whether it still is.
   *  Absent on a composed greeting and on an older server. */
  drivers?: (SuggestionDriver & { state: "live" | "finished" })[];
  /** No `in_days`: the read derives the anchor's state, not its distance. */
  time_anchor?: (Omit<SuggestionAnchor, "in_days">
                 & { native: string; state: "live" | "finished" }) | null;
  stale_reason?: string;
  anchor_date?: string;
};
export type SceneIdeaDraft = {
  title?: string; premise?: string; cast?: string[]; location?: string;
  focus?: string;
  date?: string; pcless?: boolean; source?: "llm" | "user";
  drivers?: { ref: string; action: DriverAction }[];
  time_anchor?: { ref: string; relation: AnchorRelation };
};
export type SceneIntentResult = {
  title: string; date: string;
  focus?: string;
  prompt_field_statuses?: PromptFieldStatuses;
  location: { id: string; name: string } | null;
  cast: { kind: string; id: string; name: string }[];
};
export type ChronicleEntry = {
  id: string; one_line: string; summary: string; keywords: string[];
  cast: string[]; location: string; date: string; absorbed: string;
};
/** What a cascade post-delete actually did (#75). Counts rather than a bare
 *  ack, because the reversal reaches records the transcript does not show.
 *
 *  Two different kinds of incomplete, and they must not be conflated:
 *  `refused` names records that could not be put back (something wrote to them
 *  after this scene did, or the kind carries no reversal), which keep the value
 *  the deleted scene gave them; `failed` names cleanup STEPS that could not run
 *  at all — a garbled `plot.json` and the like. The cut itself always happened
 *  by the time either is non-empty, which is why they are reported rather than
 *  raised. A count of zero beside a name in `failed` means "not known", not
 *  "none". */
export type CascadeReport = {
  index: number; removed: number; was_absorbed: boolean;
  records: number; refused: { label: string; reason: string }[];
  chronicle: boolean; plot_beats: number; commitment_beats: number;
  changes: number; citations: number; failed: string[];
};
export type DiffLine = { op: "equal" | "insert" | "delete"; text: string };
export type FieldDiff = { field: string; label: string; diff: DiffLine[] };
export type RecordChange = {
  ref: { kind: string; id: string }; name: string;
  scene: { id: string; title: string; date: string };
  fields: FieldDiff[];
};

/** One row of the append-only change journal (#31). `RecordChange` is the
 *  rolling view — the latest delta per record — and this is the history behind
 *  it, newest first, with the reversal the server is willing to perform.
 *
 *  `undoable` is the SERVER's answer and is never re-derived here: whether a
 *  change can be put back depends on what it wrote and whether the record has
 *  moved since, and a second copy of that rule in the client would be the kind
 *  of drift that ends with a button offering what the store refuses. `why`
 *  carries the reason when it is false. */
export type JournalEntry = {
  id: string; ts: string; source: string; kind: string;
  ref: { kind: string; id: string }; name: string; label: string; field: string;
  scene: { id: string; title: string; date: string };
  diff: DiffLine[];
  undoable: boolean; why: string;
  undone: { ts: string; by: string } | null;
};

// continuity ledger (#117). `kind` is promise | threat | foreshadowing and
// `status` open | fulfilled | broken | expired — a commitment resolves, where a
// plot thread only advances. Contradictions are the fourth section this view is
// named for and arrive with #111.
export type LedgerScene = { id: string; title: string; date: string };
/** A record merged into a ledger row's canonical (capstone §12.5): it is not a
 *  row of its own, so its canonical names it. */
export type LedgerAlias = { ref: string; title: string; status: string };
/** How long a record has been owed (#103), computed at read time and never
 *  stored. `overdue` needs a `due` the campaign's calendar can parse — a
 *  deadline written in the fiction's own words ("before the harvest moon") ages
 *  by staleness alone. `due_in` is the not-yet-due side of the same number. */
export type Aging = {
  state: "ok" | "stale" | "overdue";
  days_since: number | null; days_over: number | null; due_in: number | null;
};
export type PlotThread = {
  id: string; title: string; status: string;
  last_scene: string; latest_beat: string; scene: LedgerScene;
  /** Present on ledger and digest rows (#103); the digest's own type restates
   *  it as required, since every row there is aged. */
  aging?: Aging;
  /** What was merged into this row. Present on ledger and digest rows (empty
   *  when nothing was); optional because other rows of this shape omit it. */
  aliases?: LedgerAlias[];
};
export type Commitment = PlotThread & { kind: string; due: string };
/** A standing fact on the ledger (#114). `scene` is the scene that RECORDED it,
 *  not one that last moved it: a fact's text never changes once written, and a
 *  fact that stopped being true is retired off this list rather than rewritten. */
export type StandingFact = {
  id: string; text: string; date: string; scene: LedgerScene;
};
/** A fact that stopped being true (#114), and the half of facts.json that never
 *  left the server until the ledger got its own screen (4e).
 *
 *  `scene` is still the scene that RECORDED it — a retired fact keeps its dated
 *  place in the ledger — and `retired_scene` is the one that ENDED it.
 *  `superseded_by` names the fact that replaced this one and is "" when nothing
 *  did, which is the whole difference between a truth another truth overtook
 *  and one that simply lapsed. It is a bare id pointing into `facts` or back
 *  into `retired` of the same response: the replacement's text is on its own
 *  row, and shipping it twice would let the two copies disagree. */
export type RetiredFact = StandingFact & {
  superseded_by: string; retired_scene: LedgerScene;
};
/** One line of relationships.json. Two shapes share it because the reader's
 *  question is what stands between two people: `kind: "feeling"` is directed
 *  (a→b, metered 0–5, not reciprocated by construction) and `kind: "bond"` is
 *  symmetric, `type`d ("kin", "sworn") and dated to `scene`, which is the empty
 *  label for every feeling. */
export type LedgerRelationship = {
  id: string; kind: string; a: string; b: string; a_name: string; b_name: string;
  trust: number; affection: number; tension: number;
  note: string; type: string; since_scene: string; scene: LedgerScene;
};
/** One applied relationship delta (#63): how a standing on the ledger got
 *  where it is.
 *
 *  `LedgerRelationship` is the current-value view — what `relationships.json`
 *  holds now — and this is the append-only account behind it, newest first. The
 *  pair is unjoined and ORDERED for a feeling (`a` feels toward `b`) and
 *  unordered for a bond, the same asymmetry the store's two key formats carry;
 *  `label` is how the absorb named the pair at the time, which survives a
 *  rename that `a_name`/`b_name` follow.
 *
 *  `before`/`after` are rendered standings, not values: the numbers live in
 *  `relationships.json`, and this row is the sentence about them changing.
 *  `source` is "absorb" or "undo" — a reversal appends its own row rather than
 *  deleting the one it put back. It says a reversal happened and not which
 *  direction, since undoing an undo is a redo; `before`/`after` say which way
 *  this one ran. */
export type RelationshipChange = {
  id: string; ts: string; source: string; kind: string;
  a: string; b: string; a_name: string; b_name: string;
  label: string; before: string; after: string;
  scene: LedgerScene;
};
export type LedgerFact = { id: string; one_line: string; date: string; title: string };
/** Payloads for the ledger's hand edits (`routes/ledger.py`).
 *
 *  Optional rather than defaulted-to-empty throughout, and the distinction is
 *  load-bearing: the store mutators behind these read a blank as "keep what is
 *  stored" (a title, a status) or as "clear it" (a commitment's deadline), so
 *  sending `""` where the user changed nothing is an instruction they never
 *  gave. Omitted means the payload said nothing about that field.
 */
export type ThreadSave = {
  title?: string;
  status?: string;
  /** APPENDED, not replaced — a beat is a thing that happened. */
  beat?: string;
  scene?: string;
};
export type CommitmentSave = ThreadSave & {
  kind?: string;
  /** Three-valued: absent keeps the stored deadline, `""` clears it, text sets it. */
  due?: string;
};
export type FactSave = { text?: string; date?: string; scene?: string };
export type FactRecord = { text: string; date?: string; scene?: string; supersedes?: string };
export type RelationshipSave = {
  a: string; b: string;
  trust?: number; affection?: number; tension?: number; note?: string;
  /** Present addresses the undirected bond and the meters are ignored; absent,
   *  this is the directional feeling `a` holds toward `b`. */
  bond?: string;
  scene?: string;
};
export type ChronicleLineSave = { one_line?: string; date?: string };

/** Roadmap 11's knowledge overrides (`routes/ledger.py`, `store/knowledge.py`):
 *  the user's word on who knows, suspects or never heard what happened. */
export type KnowledgeStatus = "knows" | "suspects" | "unaware";
export type KnowledgePostRef = { key: string; part: string };
export type KnowledgeEntry = {
  id: string;
  /** `characters:<id>`, `groups:<id>` or `*`. */
  audience: string;
  /** The audience's name when the statement was written ("" for everyone). */
  audience_name: string;
  /** Its name now; null when it no longer resolves (deleted, reclassified). */
  audience_now: string | null;
  status: KnowledgeStatus;
  note: string;
  source: "manual" | "accepted";
  created?: string;
  updated?: string;
  subject: { kind: "posts"; scene: string; posts: (KnowledgePostRef & { variant: string; digest: string })[] }
         | { kind: "scene"; scene: string; digest: string };
  /** The scene it is about, by its current id, with its transcript digest as
   *  this read saw it; null when that scene was deleted or `unverifiable`. */
  scene: { sid: string; title: string; digest: string; shown: string } | null;
  /** The scan for its scene could not finish (a synced folder mid-write). */
  unverifiable: boolean;
  /** Each named post as it reads now (display phase), with the fingerprint of
   *  that text -- what a re-stamp sends back as `expect`. */
  posts: { key: string; part: string; index: number | null; speaker: string; text: string;
           missing: boolean; variant?: string; digest?: string; shown?: string }[];
  /** A grant that no longer grants, and why. */
  stale: boolean;
  stale_reason: "changed" | "hidden" | "gone" | "unverifiable" | "audience" | null;
};
/** What a statement about one scene could name (`.../knowledge/scenes/{sid}`). */
export type KnowledgeScene = {
  sid: string; digest: string; shown: string;
  /** What a whole-scene grant also exposes, besides the posts. */
  title: string; summary: string; one_line: string;
  keyless: number;
  keyless_posts: { index: number; speaker: string; text: string }[];
  /** Each keyed post in its prompt view, with its fingerprint; `shown` is the
   *  digest of `text`, which a grant sends back. */
  posts: { key: string; part: string; variant: string; digest: string; shown: string;
           index: number; speaker: string; text: string; hidden: boolean }[];
};
export type KnowledgeList = { entries: KnowledgeEntry[]; intact: boolean };
/** A grant carries what the reader was shown (`expect` / `expect_digest`);
 *  the server refuses it 409 `moved` when the text changed since. */
export type KnowledgeSave = {
  audience: string; status: KnowledgeStatus; scene: string;
  /** null with `whole_scene`. */
  posts: KnowledgePostRef[] | null; whole_scene: boolean;
  expect: KnowledgeFingerprint[] | null; expect_digest: string; expect_shown: string;
  /** The audience's name as the form showed it: a different current name is 409. */
  audience_name?: string;
  note?: string; source?: "manual" | "accepted";
};
export type KnowledgeEdit = {
  audience?: string; status?: KnowledgeStatus; note?: string;
  scene?: string; posts?: KnowledgePostRef[] | null; whole_scene?: boolean;
  expect?: KnowledgeFingerprint[] | null; expect_digest?: string; expect_shown?: string;
  audience_name?: string; restamp?: boolean;
};

export type Ledger = {
  plot: PlotThread[]; commitments: Commitment[]; facts: StandingFact[];
  retired: RetiredFact[]; relationships: LedgerRelationship[];
  chronicle: LedgerFact[];
  /** This campaign's staleness threshold, beside the rows rather than on each
   *  of them: a panel saying "40 days untouched" needs to be able to say what
   *  this campaign calls too long. */
  stale_after_days: number;
};

// ---- continuity review (capstone Slice D) ------------------------------
//
// Findings the reconciliation sweep cached in `continuity_candidates.json`,
// joined at read time to the records they name (§12.2), and what acting on
// one sends (§21). The wire shapes are the backend's (`routes/continuity.py`,
// `store/continuity/pending.py`); nothing here re-derives a verdict.

/** The four finding kinds (§6.1). Nothing else is ever cached. */
export type CandidateKind =
  | "possible_duplicate" | "possible_relation"
  | "possible_thread_closure" | "possible_commitment_resolution";
/** Where a finding is reviewed (§6.2), plus the two lists of what was already
 *  decided: aliases and links, and dismissals. */
export type ContinuityGroup = "overlaps" | "closures" | "resolutions" | "reviewed" | "dismissed";

/** Why a finding was found. A pair carries the similarity signals; a lifecycle
 *  finding carries its `reason` (`stale`, `overdue`, `touched`) and a temporal
 *  pairing `reason: "temporal"`. Every key is optional because which ones a
 *  record has depends on how it was found. */
export type CandidateSignals = {
  title_exact?: boolean; slug_equal?: boolean;
  /** max(token Jaccard, character-3-gram Jaccard). */
  lexical?: number;
  /** Null when semantic matching was off or a text had no vector. */
  cosine?: number | null;
  shared_actors?: string[]; shared_scenes?: string[]; shared_anchors?: string[];
  /** The clause that admitted a pair, or a pressure item's kind for an
   *  overdue commitment. */
  via?: string | null;
  reason?: "stale" | "overdue" | "touched" | "temporal";
  days_since?: number | null;
  in_days?: number | null;
  /** The event an overdue commitment's linked deadline names. */
  event?: string;
};
/** The model's proposed decision, or null when none was asked or it was
 *  voided. `from`/`to` are refs; blank on a downgraded `uncertain`. */
export type CandidateProposal = {
  decision: string; from: string; to: string; relation: string; status: string;
  reason: string; evidence_scenes: string[];
  /** What the investigation that proposed it read (roadmap 12 §6.2), when one did. */
  investigation?: InvestigationTrace;
};
/** One tool call an investigation made (roadmap 12 §8.1): its arguments
 *  (strings clipped) and its result's refs and size -- never the text. */
export type InvestigationStep = {
  turn: number; tool: string; args: Record<string, unknown>;
  result: { ok: boolean; refs: string[]; chars: number; truncated: boolean; error?: string };
};
/** The three money columns of a run, each null where no call was priced
 *  that way: never added, and never a zero nobody reported. */
export type InvestigationCost = {
  cost_usd: number | null; estimated_usd: number | null; modelled_usd: number | null;
};
export type InvestigationTrace = {
  version: number; run: string; mode: string; task: string; model: string;
  started: string; outcome: string; limit: string;
  steps: InvestigationStep[];
  /** The scenes whose text a tool returned, as `scene:<id>` refs. */
  inspected: string[];
  final: Record<string, unknown> | null;
  usage: {
    model_turns: number; tool_calls: number; decisions: number; seconds: number | null;
    cost: InvestigationCost; unpriced_calls: number;
  };
};
/** How an investigation ended (roadmap 12 §5). */
export type InvestigationOutcome<R = Record<string, unknown>> = {
  state: "completed" | "no_change" | "insufficient_evidence" | "budget_exhausted"
    | "refused" | "failed" | "cancelled";
  limit: string; reason: string; error: string;
  result: R | null;
  trace: InvestigationTrace;
};
/** A continuity investigation's run result (roadmap 12, E2). */
export type CandidateInvestigated = {
  outcome: InvestigationOutcome; persisted: boolean; candidate: string;
};
/** A history question's run result (roadmap 12, E3): never stored. */
export type HistoryAnswer = {
  question: string;
  outcome: InvestigationOutcome<{ text: string; citations: string[] }>;
};
/** One record a finding names, as it is now. `gone` when it is no longer a
 *  current canonical record (deleted, merged away). An event side carries only
 *  its title and date (`due`). */
export type CandidateRecord = {
  ref: string;
  /** The record type off the ref prefix: `thread`, `commitment` or `event`. */
  kind: string;
  title: string; status: string;
  /** A commitment's own kind (`promise`, `threat`, `foreshadowing`), as the
   *  play prompt prints it. Blank for a thread, an event or a gone record. A
   *  merge keeps the canonical's (§5.1). */
  commitment_kind: string;
  latest_beat: string;
  last_scene: { id: string; title: string };
  pressure: { state: string; in_days: number | null; friendly: string } | null;
  aliases: LedgerAlias[];
  due: string;
  beats: { scene: string; text: string }[];
  gone: boolean;
};
export type ContinuityCandidate = {
  id: string; kind: CandidateKind; group: ContinuityGroup; refs: string[];
  /** The CACHED fingerprint: what an apply's default `expect_fingerprint` is. */
  fingerprint: string;
  stale: boolean;
  stale_reason: "records" | "evidence" | null;
  signals: CandidateSignals;
  proposal: CandidateProposal | null;
  created: string;
  records: CandidateRecord[];
};
/** What the effective view set aside, and why (§26). */
export type ContinuityDiagnostics = {
  dangling_aliases: { ref: string; to: string; reason: string }[];
  broken_links: { id: string; reason: string }[];
  hidden_links: { id: string; reason: string }[];
  unreadable: string[];
};
/** `GET .../continuity/candidates` (§12.2, Decision 23). */
export type ContinuityCandidates = {
  generated: string;
  matching: "basic" | "semantic";
  diagnostics: ContinuityDiagnostics & {
    /** continuity.json sections the reader had to discard. */
    malformed: string[];
    cache_malformed: boolean;
  };
  /** The sweep running on the campaign now, so the section can follow it. */
  run: RunHandle | null;
  /** A display name for every scene id, actor ref and event ref mentioned. */
  names: Record<string, string>;
  /** Every scene, newest first by play order -- for a finding's detail, so it
   *  is empty when the cache holds no finding (the read skips the scene list). */
  scenes: { id: string; title: string }[];
  candidates: ContinuityCandidate[];
};
/** `POST .../candidates/{id}/apply` (§21): flat, with a key named `from`. */
export type ContinuityApply = {
  op: "alias" | "link" | "close" | "resolve" | "keep_open";
  canonical?: string;
  from?: string; to?: string; relation?: string;
  status?: string;
  beat?: string; scene?: string;
  copy_due?: boolean;
  accept_status_change?: boolean;
  expect_fingerprint?: string;
};
/** What a landed apply answers. `affected` comes with a merge only: the other
 *  records that now resolve to the kept one (§5.1's Response), each with the
 *  name the review shows for it, so no ref is worded on the client. */
export type ContinuityApplied = {
  ok: boolean;
  applied: string[];
  affected?: { ref: string; name: string }[];
};
/** A reconciliation run's result (`routes/continuity._blank_result`). */
export type ReconcileResult = {
  sweep: "full" | "incremental";
  matching: string;
  embedding: "off" | "configured" | "failure";
  llm: "off" | "ok" | "failed" | "skipped";
  reason: string;
  /** The refusal's fixed kind (`incapable`, `missing_key`, ...) when `llm` is
   *  "off"; "" otherwise. */
  reason_kind: string;
  candidates: number; adjudicated: number;
  /** Candidates the model's chunks did not read (a failed or garbled chunk
   *  beside an answered one). Counted only on an `ok` sweep: a failed run
   *  throws, so no result carries it. */
  unanswered: number;
  pairs_capped: boolean; superseded: boolean;
  continuity: "ok" | "malformed";
  follow_on: boolean;
};
/** A failed reconciliation run's `error` (`routes/continuity._reconcile_work`):
 *  `saved` is whether its first persist landed, so the findings listed are this
 *  sweep's, and `follow_on` whether it was the follow-on pass that failed. Read
 *  as `Partial` by `failedNote`, since a refusal of another kind carries none
 *  of it. */
export type ReconcileRunError = {
  kind: string; detail: string; status: number;
  sweep: "full" | "incremental"; saved: boolean; follow_on: boolean;
};
export type ContinuityAliasRow = {
  ref: string; to: string; canonical: string; title: string; to_title: string;
  created: string; source: string; note: string; dangling: boolean; reason: string;
};
export type ContinuityLinkRow = {
  id: string; relation: string; a: string; b: string; a_raw: string; b_raw: string;
  scene: string; note: string; created: string; a_title: string; b_title: string;
};
export type ContinuityRawLink = {
  id: string; a: string; b: string; relation: string; scene: string; note: string;
  created: string; state: "ok" | "broken" | "hidden"; reason: string;
  /** Each end's title, or its ref when the record is gone (`review.describe`). */
  a_title: string; b_title: string;
};
/** A stored dismissal. `live` is whether it still names the records as they
 *  are; one whose records have since changed suppresses nothing (§12.7). */
export type ContinuitySuppression = {
  fingerprint: string; kind: string; refs: string[]; decision: string; created: string;
  live: boolean; titles: string[];
};
/** `GET .../continuity`. */
export type ContinuityState = {
  aliases: ContinuityAliasRow[]; links: ContinuityLinkRow[]; raw_links: ContinuityRawLink[];
  suppressions: ContinuitySuppression[];
  diagnostics: ContinuityDiagnostics;
  malformed: string[]; unreadable: string[];
  matching: "basic" | "semantic";
};
