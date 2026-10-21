import type { CascadeReport, StandingFact } from "./ledger";

/** Where a cast member's text came from (#99). `library` is the world's record
 *  as this campaign locked it; `override` is that record with campaign edits on
 *  top; `emergent` is a character the campaign owns outright, with no library
 *  record behind it. Derived per read from hashes the lock already records —
 *  see `store/appearances/versions.py:actor_source`. */
export type CastSource = "library" | "override" | "emergent";
export type CastDetail = {
  kind: "characters" | "pcs"; id: string; name: string; version: string; body: string;
  source: CastSource;
  /** As `RosterEntry.avatar_v`: the drawer portrait's `?v=`. */
  avatar_v?: string | null;
};
/** One feeling this actor holds toward another in the room. The three axes run
 *  0–5 and the column draws them as five pips each. */
export type Feeling = {
  ref: string; kind: string; id: string; name: string;
  trust: number; affection: number; tension: number; note: string;
};
/** Everything the campaign has decided about one actor — the play view's
 *  dossier column. Every field is a record the absorb pass already writes;
 *  `standing` / `knows` / `suspects` and `feels_toward` had no reader outside a
 *  staged review row until this existed. */
export type Casefile = {
  // The casefile route only ever answers for an actor, and its portrait URL
  // keys on this, so it names the two actor kinds rather than any string.
  kind: "characters" | "pcs"; id: string; name: string; version: string; role: string;
  /** As `RosterEntry.avatar_v`: the dossier portrait's `?v=`. */
  avatar_v?: string | null;
  /** The scenes she is cast in, oldest first, labelled — a scene id is a
   *  filename, and the column puts these in a sentence. */
  scenes: { id: string; title: string }[];
  /** The title of the newest of them. */
  last_seen: string;
  standing: string; knows: string; suspects: string;
  dossier: string;
  /** The one-line identity, meaningful only for someone never played. */
  tagline: string;
  feels_toward: Feeling[];
  standing_facts: StandingFact[];
};
export type TimelineEvent = { date: string; text: string };
/** Why one stored field is what it is: the excerpt the extractor cited, who it
 *  attributed it to, its own 0–1 rating (`null` when it gave none) and the band
 *  `absorb/routing.py` weighed those into.
 *
 *  `band` is stored rather than derived here on purpose — it is certainty
 *  weighted by authority, and a second copy of that table on the client is how
 *  the panel and the review end up disagreeing about the same row. */
export type Citation = {
  quote: string; speaker: string; certainty: number | null;
  authority: string; band: string;
  /** The recording scene's id, and its title resolved at read time — a title
   *  frozen into the stored citation would name a scene a later rename
   *  retired. */
  scene: string; scene_title?: string;
  recorded: string;
};
/** Keyed `"<kind>/<id>#<field>"`. A field with no entry is the normal case: the
 *  later absorb phases rest on no transcript citation, and anything written
 *  before the store existed — or edited by hand — has none. */
export type Provenance = Record<string, Citation>;
/** How much weight one staged proposal has earned (#110/#112), computed by
 *  `store/absorb/routing.py`. Display and default-checkbox state only — the
 *  server never reads it back on save, and a `low` row a reviewer ticks anyway
 *  applies exactly like any other. */
export type EditReview = {
  /** The extractor's own 0-1 rating, or null when it gave none. Poorly
   *  calibrated by construction, so it is shown and ordered by, never trusted
   *  as a probability. */
  certainty: number | null;
  /** The excerpt it cited, and the transcript label it attributed them to.
   *  Both "" when it cited nothing. */
  quote: string; speaker: string;
  /** What the transcript actually corroborates about that speaker, relative to
   *  the record being changed. `unattributed` means the citation cannot be
   *  checked — nobody spoke under that name, or two speakers answer to it. */
  authority: "narration" | "self" | "other" | "unattributed" | "uncited";
  /** `certainty` weighted by `authority`, and the band the panel routes on. */
  score: number; band: "high" | "medium" | "low";
};
export type StagedEdit = {
  id: string; kind: "character_state" | "group_state" | "membership" | "lore" | "authored" | "relationship" | "bond" | "plot"
    | "commitment" | "fact" | "new_character" | "new_location" | "new_lore" | "sheet"
    | "dossier" | "voice_drift";
  target: { kind: string; id: string }; label: string; field: string;
  before: string; after: string; authored: boolean;
  payload?: Record<string, unknown>;
  /** Present on the rows `absorb.materialize` staged from the extraction, and
   *  absent on the ones staged by the later phases (dossier, voice, sheet),
   *  which rest on no transcript citation to weigh. An absent block routes as
   *  `medium`: shown and pre-approved, exactly as every row was before #110. */
  review?: EditReview;
  /** Set once the reviewer has answered a conflict on this row (#111): the
   *  reviewer's authorization to write over a target that moved since the
   *  scene was absorbed. Both values authorize; they differ in whether `after`
   *  is still the staged text or one the reviewer merged by hand. Absent means
   *  unanswered, and the save is refused. */
  resolve?: "replace" | "merge";
  /** The stored value the reviewer was shown when they answered. Sent with
   *  `resolve` so the server can hold the retry to it: the flag authorizes
   *  overwriting *that* text, not whatever the record holds by the time the
   *  save lands. */
  resolve_from?: string;
  /** Present on a proposed-new plot thread or commitment the existing-record
   *  check examined (spec §10.3), and only there: a row with no plausible
   *  stored neighbour carries none. Optional because reviews stored before the
   *  check existed lack it. */
  identity_check?: IdentityCheck;
};
/** Why a stored record was offered as a possible match, as the server measured
 *  it (`continuity.similarity.lexical`, plus the clause that admitted it). */
export type IdentitySignals = {
  title_equal: boolean; slug_equal: boolean;
  /** Token and character-3-gram Jaccard over the identity texts. */
  tokens: number; chars: number;
  /** Null when semantic matching was off or either text had no vector. */
  cosine: number | null;
  actors: string[]; scenes: string[]; anchors: string[];
  via?: "lexical" | "structural" | "semantic" | null;
};
/** One stored record the proposed row might be. `ref` is a prefixed canonical
 *  ref (`thread:<id>` / `commitment:<id>`), never a bare id. */
export type IdentityCandidate = {
  ref: string; title: string; status: string; latest_beat: string;
  signals: IdentitySignals;
};
export type IdentityCheck = {
  /** The resolver's word, or `unchecked` when it gave none for this row. */
  decision: "existing" | "new" | "uncertain" | "unchecked";
  /** `downgraded`: an `existing` the acceptance guard refused (closed, already
   *  moved, never offered). `hint_only`: the check never answered. */
  status: "accepted" | "downgraded" | "hint_only";
  reason: string;
  proposed: { title: string; why_new: string; distinguished_from: string[] };
  candidates: IdentityCandidate[];
  /** Complete staged rows the reviewer may swap this one for, each with its
   *  own server-computed `before`. Client-side only once staged: the save
   *  strips them (`editRows.wireEdit`). */
  alternatives?: StagedEdit[];
};
/** A staged edit whose target no longer matches the value it was staged
 *  against (#111). Carries everything the three choices need — what is stored
 *  now, and a merged draft where merging into the field makes sense — so
 *  answering one costs no extra round-trip. */
export type EditConflict = {
  id: string; label: string; kind: string; field: string;
  before: string; after: string; stored: string; reason: string;
  mergeable: boolean; merged: string;
  /** Position in the submitted `edits` array. The only reliable way back to
   *  the row: `id` is not unique (only plot threads are deduped), and the
   *  response omits the rows that were fine, so ordinal position among the
   *  conflicts does not line up with ordinal position among the edits. */
  index: number;
};
export type MechanicsDrop = { id: string; field?: string; reason: string };
/** The two facts a bare status cannot carry, on every phase that makes an LLM
 *  call: whether a request reached the model at all, and whether the absorb's
 *  shared time budget is why it did not. A phase stopped by the clock is worth
 *  retrying as-is; one that failed on its own merits is not. */
export type PhaseAttempt = { attempted: boolean; budget_exhausted: boolean };
export type Mechanics = PhaseAttempt & {
  status: "ok" | "degraded" | "failed" | "skipped"; reason: string | null;
  warnings: string[]; dropped: MechanicsDrop[];
};
export type DossierFailure = { id: string; reason: string };
export type Dossiers = PhaseAttempt & {
  status: "ok" | "degraded" | "failed" | "skipped"; reason: string | null;
  proposed: string[]; failed: DossierFailure[];
  /** NPCs the absorb budget ran out before reaching — never attempted (#243). */
  skipped: string[];
};
export type VoiceCheck = PhaseAttempt & {
  status: "ok" | "degraded" | "failed" | "skipped"; reason: string | null;
  /** NPCs whose dialogue was judged against their anchor — only ones that HAVE
   *  an anchor are judged at all, which is what keeps the extra calls opt-in. */
  checked: string[];
  /** The subset of `checked` that came back out of voice (#59). */
  flagged: string[];
  /** The subset of `checked` that said too little to judge. Named separately
   *  because `checked` minus `flagged` would otherwise read as "confirmed in
   *  voice" for a character nobody actually heard — and silence never clears a
   *  standing corrective. */
  unjudged: string[];
  /** The subset of `flagged` whose verdict came without a note, because the
   *  Decision model answers natively and a native answer has no rationale.
   *  Shown, but nothing is stored for them. Optional: reviews stored before
   *  this existed lack it. */
  noteless?: string[];
  failed: DossierFailure[];
  /** Anchored NPCs the absorb budget ran out before reaching — never attempted. */
  skipped: string[];
};
/** One row per LLM-backed step of a single absorb, in run order. A projection of
 *  `mechanics`/`dossiers`/`voice` (never a second source of truth) that also covers
 *  the extraction, so a run cut short by the time budget is legible as one instead
 *  of looking like a model with nothing to suggest. */
export type AbsorbPhase = PhaseAttempt & {
  name: "extraction" | "identity" | "dossiers" | "voice" | "audit";
  status: "ok" | "degraded" | "failed" | "skipped"; reason: string | null;
};
/** The existing-record check's phase block (spec §10.4). `matching` says where
 *  the possible-match lists came from; `counts` is flat and text-free.
 *  `fallback` is why semantic matching, though configured, did not stand this
 *  run (empty when it did) -- kept apart from `reason`, which a partial or
 *  failed resolver owns. Absent on reviews stored before it existed. */
export type IdentityPhase = PhaseAttempt & {
  status: "ok" | "degraded" | "failed" | "skipped"; reason: string | null;
  matching: "basic" | "semantic";
  fallback?: string;
  counts: Record<string, number>;
};
export type SceneAbsorb = {
  one_line: string; summary: string; keywords: string[];
  timeline_events: TimelineEvent[]; cast: string[]; location: string; date: string;
  edits: StagedEdit[];
  mechanics: Mechanics;
  /** Idempotency key for this review's save (#235) — replaying a spent one
   *  returns the first result instead of committing twice. */
  commit_token: string;
  dossiers: Dossiers;
  voice: VoiceCheck;
  /** Absent on reviews stored before the existing-record check existed. */
  identity?: IdentityPhase;
  phases: AbsorbPhase[];
  /** Empty for the ordinary end-of-scene absorb, which has no later scene to
   *  disagree with. Non-empty after a retcon of an older scene, which is the
   *  case it exists for. */
  contradictions: Contradiction[];
};
/** One staged row a scene played AFTER this one already answered differently
 *  (#78). Advisory: it names the later scene and how that scene's authorship was
 *  established, and nothing about the save changes because of it — the reviewer
 *  reads the badge and decides. `source` is `citation` (the quote behind the
 *  later write), `changes` (that scene's write-back to this record) or `thread`
 *  (a plot or commitment thread whose last beat is that scene's). */
export type Contradiction = {
  id: string; scene: string; label: string; source: "citation" | "changes" | "thread";
};
/** What a retcon did beyond rewriting the post (#78): the cascade's reversal
 *  report, plus the scenes played after this one — the ones a re-extraction can
 *  contradict, and the reason to re-absorb the scene and read the badges. */
export type RetconReport = CascadeReport & { later: string[] };
/** A live retcon replay (#79). `next` is what the walk owes: a model turn to
 *  generate, the player's own posts to re-post first, or nothing. `gone` means
 *  the scene was deleted under the session — its backlog is the only copy of
 *  those posts, so it is reported rather than silently discarded. */
export type ReplaySession = {
  scene: string; cut: number; done: number; steps: number; turns_left: number;
  next: "generation" | "verbatim" | "done"; staged: boolean; created: string;
  gone: boolean;
  /** A replayed reply is in the transcript, waiting on accept or another try.
   *  The server's answer rather than the client's memory of having run a turn —
   *  a reload loses that memory, and running the turn again would land a second
   *  reply beside the first. */
  pending: boolean;
};
/** What starting a replay answers: the session, the cut's cascade report and,
 *  for a replay begun inside a branch, the sibling it runs in. */
export type ReplayStarted = ReplaySession & { cascade: CascadeReport; branched?: string };
/** What a replay from this post would cost, before anything is cut (#79/#80).
 *  `fork` is the nudge: over the configured threshold, offer to copy the
 *  campaign first. `blocked`, when non-empty, is why this span cannot be
 *  replayed at all. */
export type ReplayPreview = {
  posts: number; turns: number; threshold: number; fork: boolean; blocked: string;
};
