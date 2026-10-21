import type { Message } from "./campaigns";
import type { AnchorRelation, CandidateKind, DriverAction, LedgerAlias, LinkRelation,
  PressureState } from "./ledger";
import type { EntityKindName, Secrecy } from "./records";

// ---- the story graph (capstone Slice F) --------------------------------
//
// `GET /continuity/graph` (§19, §20): one read-only payload of nodes and edges
// the page draws under four client-side lenses. The wire shapes are
// `store/continuity/graph.py`'s. A dated node is placed only by `fixed` and
// `in_days`; `native` is calendar text and is never parsed here.
//
// Each vocabulary below is held to its Python tuple by
// `backend/tests/test_continuity_graph.py`, which parses this file -- so each
// stays a plain union of string literals, in the tuple's order. The chooser's
// `DriverKind`, `DriverAction`, `PressureState`, `AnchorRelation` and the
// review's `CandidateKind` are reused, never redeclared.

export type NodeKind =
  | "scene" | "character" | "pc" | "location" | "group" | "thread" | "commitment" | "event" | "idea"
  | "birthday" | "holiday";
export type EdgeKind =
  | "appeared_in" | "occurred_at" | "opened_in" | "advanced_in" | "touched_in" | "closed_in"
  | "resolved_in" | "involves" | "serves" | "anchored_to" | "feeling" | "bond"
  | "birthday_of" | "member_of" | "leads" | "link" | "merged_into" | "possible_duplicate" | "possible_relation";
export type EdgeSource = "structural" | "reviewed" | "candidate" | "alias";
export type EventStatus = "scheduled" | "fired" | "passed" | "undated";
/** A source the read could not use; named in `omitted` so a broken read never
 *  looks like an empty campaign. */
export type GraphPart =
  | "calendar" | "scenes" | "chronicle" | "plot" | "commitments" | "events" | "continuity"
  | "candidates" | "relationships" | "scene_ideas" | "groups" | "names";

/** A dated node's four fields. A null `fixed` is undated (no calendar, or a
 *  date the calendar cannot read), never day zero. */
export type GraphDate = { native: string; friendly: string; fixed: number | null;
                          in_days: number | null };
export type NodePressure = { state: PressureState; in_days: number | null; friendly: string };
/** A visible review finding on the node it names; `other` is the pair's other
 *  end, null for a lifecycle finding. */
export type GraphFinding = { id: string; kind: CandidateKind; other: string | null };
/** A thread or commitment, canonical or merged away (`merged_into` set). */
export type ArcFields = {
  status: string; live: boolean; merged_into: string | null; aliases: LedgerAlias[];
  latest_beat: string; pressure: NodePressure | null; focusable: boolean;
  findings: GraphFinding[];
};
export type GraphNode = { id: string; label: string } & (
  | ({ kind: "scene"; order: number; done: boolean; pcless: boolean; place: string }
     & GraphDate)
  | { kind: "character" | "pc" | "location" }
  | { kind: "group"; group_type: string; secrecy: Secrecy }
  | ({ kind: "thread" } & ArcFields)
  | ({ kind: "commitment"; commitment_kind: string; due: string } & ArcFields & GraphDate)
  | ({ kind: "event"; status: EventStatus; pressure: NodePressure | null; anchorable: boolean;
       findings: GraphFinding[] } & GraphDate)
  | ({ kind: "holiday"; pressure: NodePressure; anchorable: boolean } & GraphDate)
  | ({ kind: "birthday"; actor: string; precision: "exact" | "yearless" | "month";
       age: number | null; pressure: NodePressure; anchorable: boolean } & GraphDate)
  | ({ kind: "idea"; premise: string; source: string; pcless: boolean;
       time_anchor: { ref: string; relation: AnchorRelation; native: string } | null }
     & GraphDate)
);
/** `relation` is typed by `kind` (§20: typed unions, not open string bags):
 *  only a link, a `serves` and an `anchored_to` edge carry one. */
export type GraphEdge = {
  id: string; from: string; to: string; source: EdgeSource; candidate_id: string | null;
} & (
  | { kind: "link"; relation: LinkRelation }
  | { kind: "serves"; relation: DriverAction }
  | { kind: "anchored_to"; relation: AnchorRelation }
  | { kind: "feeling"; relation: null; trust: number; affection: number; tension: number;
      note: string }
  | { kind: "bond"; relation: null; bond_type: string; since_scene: string }
  | { kind: Exclude<EdgeKind, "link" | "serves" | "anchored_to" | "feeling" | "bond">;
      relation: null }
);
export type StoryGraph = {
  now: { native: string; friendly: string; fixed: number | null };
  nodes: GraphNode[]; edges: GraphEdge[]; omitted: GraphPart[];
};

// keyword search (#33)
/** One record the query matched.
 *
 *  `scope` and `root` together say *which* record: a world's lore and a
 *  campaign's fork of it carry the same `id`, and only the scope tells them
 *  apart. `sub` is what inside the record matched where a record has parts — a
 *  card version, a persona version, a relationship's side — and "" where it
 *  does not. */
export type SearchHit = {
  scope: "world" | "campaign";
  root: string;
  root_name: string;
  kind: string;
  id: string;
  sub: string;
  name: string;
  /** A one-line window of the body around the first matching term, ellipsed at
   *  either end. Plain text, not markdown: the emphasis runs are stripped. */
  snippet: string;
  score: number;
};
export type SearchMode = "keyword" | "semantic";

export type SearchResult = {
  q: string;
  /** The query as the server split it — phrases kept whole — so the client
   *  highlights exactly what matched rather than re-implementing the split.
   *  Empty in semantic mode: nothing matched a term, so nothing is marked. */
  terms: string[];
  /** Hits after the kind filter; `hits` is this list cut to the limit. */
  total: number;
  /** Hits per kind BEFORE the kind filter, so a chip can say what dropping the
   *  current filter would find. */
  facets: Record<string, number>;
  scopes: Record<string, number>;
  truncated: boolean;
  hits: SearchHit[];
  /** The ranking that actually produced this page, which is not always the one
   *  that was asked for: semantic mode needs an embeddings connection, and
   *  falls back to keyword when it has none rather than erroring (#34). */
  mode?: SearchMode;
  requested_mode?: SearchMode;
  /** Why the two differ, written to be shown to the reader. "" when they do
   *  not. */
  note?: string;
  /** Semantic mode only: passages of the corpus that had a vector to score
   *  against, out of how many there are. A query warms a bounded number of
   *  them, so a large library indexes over several searches rather than
   *  stalling the first one. */
  indexed?: number;
  corpus?: number;
};

// the play timeline (#198) — the ledger's other half. The ledger answers what
// is still open; this answers what happened, in play order, one card per scene.
//
// `one_line`, `location` and `done` exist only after the absorb, and a campaign
// being played is normally a scene or two ahead of it — so the ORDINARY card
// carries none of them and falls back to its title and its own date. Treat them
// as optional content, never as "still loading".
//
// The absorb's full `summary` is deliberately absent: a card is one line, and
// shipping the whole campaign's prose for a view that renders none of it is the
// biggest thing on the wire paying for nothing. `one_line` already falls back
// to it server-side for the save that left `one_line` empty.
/** One beat of a plot thread, on the card of the scene it landed in — the
 *  "thread pair" the timeline is for: what moved, and where. `title`/`status`
 *  are the THREAD's, repeated per beat so a card needs no second lookup. */
export type TimelineBeat = {
  thread: string; title: string; status: string; text: string;
};
export type TimelineScene = {
  id: string; title: string; one_line: string;
  /** The scene's own opening moment, falling back to the chronicle's date. */
  date: string;
  location: string; done: boolean; pcless: boolean; beats: TimelineBeat[];
  /** Present only on a closed branch: the absorbed sibling that closed it
   *  (the scene listing's `closed_by`). */
  closed_by?: { sid: string; title: string };
};
/** Only the threads with a beat on some card: a chip that filters to nothing
 *  is worse than no chip. */
export type TimelineThread = { id: string; title: string; status: string };
export type Timeline = { scenes: TimelineScene[]; threads: TimelineThread[] };

// pre-scene briefing (#118) — the ledger's per-scene sibling. The rows are the
// ledger's, minus the `scene` label (this view is about who, not when) and plus
// `involves`: the display names of the scene's cast this row can be traced to,
// empty for a row it cannot. `focus` names who the flag was computed against —
// the scene's players, or its whole cast when it is an offscreen scene with
// none. Rows are ordered flagged-first and never filtered: an unflagged
// commitment is still owed.
export type BriefingRow = {
  id: string; title: string; status: string;
  last_scene: string; latest_beat: string; involves: string[];
};
export type BriefingCommitment = BriefingRow & { kind: string; due: string };
export type BriefingFact = { id: string; one_line: string; title: string; date: string };
export type Briefing = {
  focus: string[]; plot: BriefingRow[]; commitments: BriefingCommitment[];
  relationships: string[]; last_time: BriefingFact | null;
};

// scene import (#92) — a grimoire transcript read back in. The draft is a
// proposal: parsing writes nothing, and every field here is one the review form
// can change before it is committed. `cast` is what the speaker labels resolved
// to in this campaign, `unmatched` the labels that resolved to nobody, and
// `warnings` everything the file could not settle on its own (a header bit that
// is either a date or a location, a date this campaign's calendar cannot read,
// text the marker grammar will not carry).
export type SceneImportCast = {
  label: string; kind: "characters" | "pcs"; id: string; name: string; role: "player" | "npc";
};
export type SceneImportDraft = {
  title: string; date: string; location: string; pcless: boolean;
  messages: Message[];
  /** The source's reply boundaries, when it had some that still fit. Nothing
   *  for the reviewer to decide — it rides the draft back to the commit so an
   *  imported scene rerolls one generation rather than its whole trailing run. */
  turn_sizes: number[] | null;
  cast: SceneImportCast[];
  unmatched: string[];
  warnings: string[];
};

// lorebook import
// `EntityKindName`, not `EntityKind`: a draft's category is whatever the server
// said a row may be filed under (`GET /api/entity-kinds`), which is allowed to
// name a kind added after this build shipped (#138). Narrowing it to the local
// union would only be a cast that claims something the round trip does not.
export type LoreEntryDraft = {
  name: string; keys: string[]; body: string; category: EntityKindName;
  /** The advanced ST activation fields parse stashed (#20) — carried through
   *  the review table untouched and committed as `st_extensions` frontmatter.
   *  Absent when the source entry had none. */
  extensions?: Record<string, unknown>;
};

// scenario-card import (#217) — one card describing a whole setting, split into
// the records a world is made of. A proposal speaks in cast NAMES, not ids: the
// characters it proposes do not exist while it is being reviewed, and the
// backend resolves the names once they do.
export type ScenarioCharacterDraft = {
  name: string; description: string; personality: string;
  /** The import will reuse a world character of this name rather than create
   *  one. Advisory: the backend re-resolves at import time. */
  exists?: boolean;
};
export type ScenarioGreetingDraft = {
  name: string; body: string; character: string; present: string[];
};
export type ScenarioProposal = {
  characters: ScenarioCharacterDraft[];
  entries: LoreEntryDraft[];
  greetings: ScenarioGreetingDraft[];
};
export type ScenarioArtSummary = {
  total: number; localized: number; skipped: number; failed: number; capped: boolean;
};
export type ScenarioImportResult = {
  characters: { name: string; id: string; version: string; created: boolean }[];
  entries: { kind: string; id: string }[];
  greetings: { name: string; id: string }[];
  art: ScenarioArtSummary;
};
