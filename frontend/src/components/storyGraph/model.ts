/** The Story Graph's pure half (capstone §19): the four lenses over one
 *  payload, what each shows, the words a node is named by, and the two
 *  things a node hands elsewhere -- the chooser's seed (§16.5) and the
 *  Ledger's address (§12.1).
 *
 *  Every lens and toggle is a function of the one `GET /continuity/graph`
 *  payload, so switching one is arithmetic here and never a request (§28.9).
 *  A ref is never parsed for its kind: the node says what it is, and a ref is
 *  only split at its first `:` to address a Ledger row.
 *
 *  A leaf: no React. */
import type {
  EdgeKind, GraphEdge, GraphNode, GraphPart, LinkRelation, NodeKind, PressureState,
  StoryGraph,
} from "../../api/types";
import type { LedgerTarget } from "../../ledgerPaths";
import { KIND_PHRASES as FINDING_PHRASE, RELATION_PHRASES } from "../continuity/labels";
import { whenPhrase, type ChooserSeed } from "../pressureControls";

/** What a finding is, in D's words: one table for Todo, the Ledger and here. */
export { FINDING_PHRASE };

export const LENSES = ["story", "cast", "calendar", "continuity"] as const;
export type Lens = (typeof LENSES)[number];

export const LENS_LABELS: Record<Lens, string> = {
  story: "Story", cast: "Cast", calendar: "Calendar", continuity: "Continuity",
};

/** The Show toggles: what a lens adds on top of its own kinds. */
export type Show = { actors: boolean; locations: boolean; merged: boolean; candidates: boolean };

export const SHOW_LABELS: Record<keyof Show, string> = {
  actors: "Actors", locations: "Locations", merged: "Merged records",
  candidates: "Review candidates",
};

const OFF: Show = { actors: false, locations: false, merged: false, candidates: false };

/** Decision 20's table. Cast lists no actor kinds of its own: its actors come
 *  only from the Actors toggle, which it turns on, so unchecking Actors there
 *  removes them. */
export const PRESETS: Record<Lens, {
  nodes: NodeKind[]; edges: EdgeKind[]; show: Show; axis: "play" | "calendar";
}> = {
  story: {
    nodes: ["scene", "thread", "commitment", "event", "holiday", "birthday", "idea"],
    edges: ["opened_in", "advanced_in", "touched_in", "closed_in", "resolved_in", "serves",
            "anchored_to", "link"],
    show: OFF, axis: "play",
  },
  cast: {
    nodes: ["scene", "thread", "commitment"],
    edges: [],
    show: { ...OFF, actors: true }, axis: "play",
  },
  calendar: {
    nodes: ["scene", "event", "holiday", "birthday", "commitment"],
    edges: ["link", "birthday_of"],
    show: OFF, axis: "calendar",
  },
  continuity: {
    nodes: ["thread", "commitment", "event"],
    edges: ["link", "merged_into", "possible_duplicate", "possible_relation"],
    show: { ...OFF, merged: true, candidates: true }, axis: "play",
  },
};

const ACTOR_KINDS: readonly NodeKind[] = ["character", "pc"];
const ACTOR_EDGES: readonly EdgeKind[] = [
  "appeared_in", "involves", "feeling", "bond", "birthday_of",
];
/** The edges an arc's own movement draws, to the scenes it moved in. */
const MOVEMENT_EDGES: readonly EdgeKind[] = [
  "opened_in", "advanced_in", "touched_in", "closed_in", "resolved_in",
];

export function isLens(v: string | null): v is Lens {
  return v !== null && (LENSES as readonly string[]).includes(v);
}

/** A ref's prefix and everything after its FIRST `:` -- a plot id may hold a
 *  `:` or a `/`, and a holiday's a name with spaces. */
export function splitRef(ref: string): [string, string] {
  const at = ref.indexOf(":");
  return at < 0 ? ["", ref] : [ref.slice(0, at), ref.slice(at + 1)];
}

export type GraphIndex = {
  byId: Map<string, GraphNode>;
  out: Map<string, GraphEdge[]>;
  into: Map<string, GraphEdge[]>;
};

export function indexGraph(g: StoryGraph): GraphIndex {
  const byId = new Map(g.nodes.map((n) => [n.id, n] as const));
  const out = new Map<string, GraphEdge[]>();
  const into = new Map<string, GraphEdge[]>();
  const add = (m: Map<string, GraphEdge[]>, key: string, e: GraphEdge) => {
    const list = m.get(key);
    if (list) list.push(e);
    else m.set(key, [e]);
  };
  for (const e of g.edges) {
    add(out, e.from, e);
    add(into, e.to, e);
  }
  return { byId, out, into };
}

/** A thread or commitment merged away into another (Decision 7). */
function mergedInto(n: GraphNode): string | null {
  return n.kind === "thread" || n.kind === "commitment" ? n.merged_into : null;
}

/** §19.6's arc filter: the arc, its merged records, its reviewed-link
 *  neighbours either way, the scenes its movement reached and the actors it
 *  involves. Empty for a ref that names no node. */
export function arcNeighbourhood(ix: GraphIndex, arc: string): Set<string> {
  if (!ix.byId.has(arc)) return new Set();
  const set = new Set([arc]);
  for (const e of ix.out.get(arc) ?? []) {
    if (e.kind === "link" || e.kind === "involves" || MOVEMENT_EDGES.includes(e.kind)) {
      set.add(e.to);
    }
  }
  for (const e of ix.into.get(arc) ?? []) {
    if (e.kind === "link" || e.kind === "merged_into") set.add(e.from);
  }
  return set;
}

/** An event that is played history: fired or passed, and not on its own day.
 *  Reaching the day fires an event, but the day is still the present -- its
 *  driver reads `today` and the chooser offers it as an anchor (§13.5's
 *  own-day carve-out) -- so an event on today is never "reached". */
export function isReached(n: GraphNode): boolean {
  return n.kind === "event" && (n.status === "fired" || n.status === "passed")
    && n.in_days !== 0;
}

function lensKeeps(n: GraphNode, lens: Lens, show: Show): boolean {
  // Merged records ADDS its nodes, as Actors does, on every lens: a merged
  // node has no date, so Calendar's kinds and deadline rule would otherwise
  // leave the checkbox live and doing nothing there (Task 10's "Not dated").
  if (mergedInto(n) !== null) return show.merged;
  const kinds = PRESETS[lens].nodes;
  const shown = kinds.includes(n.kind)
    || (show.actors && ACTOR_KINDS.includes(n.kind))
    || (show.locations && n.kind === "location");
  if (!shown) return false;
  // §19.4: the Story lens is about what is still ahead.
  if (lens === "story" && isReached(n)) {
    return false;
  }
  // §19.5 lists deadlines, not every commitment.
  if (lens === "calendar" && n.kind === "commitment" && n.native === "") return false;
  return true;
}

function lensDraws(e: GraphEdge, lens: Lens, show: Show): boolean {
  return PRESETS[lens].edges.includes(e.kind)
    || (show.actors && ACTOR_EDGES.includes(e.kind))
    || (show.locations && e.kind === "occurred_at")
    || (show.merged && e.kind === "merged_into")
    || (show.candidates && e.source === "candidate");
}

/** What a lens draws (Decision 20), in payload order. An arc filter replaces
 *  the lens's kinds and toggles with the arc's neighbourhood -- its own merged
 *  records included whatever Merged says, the more specific rule -- while
 *  candidate edges still need their toggle and merged records outside an arc
 *  filter still need theirs. An edge shows only when both its ends do. */
export function visible(g: StoryGraph, ix: GraphIndex, lens: Lens, show: Show,
                        arc: string | null): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const hood = arc !== null && ix.byId.has(arc) ? arcNeighbourhood(ix, arc) : null;
  const nodes = g.nodes.filter((n) => (hood ? hood.has(n.id) : lensKeeps(n, lens, show)));
  const shown = new Set(nodes.map((n) => n.id));
  const edges = g.edges.filter((e) => {
    if (!shown.has(e.from) || !shown.has(e.to)) return false;
    if (e.source === "candidate" && !show.candidates) return false;
    if (hood) return true;
    if (e.kind === "merged_into" && !show.merged) return false;
    return lensDraws(e, lens, show);
  });
  return { nodes, edges };
}

export const KIND_NOUN: Record<NodeKind, string> = {
  scene: "scene", character: "character", pc: "player character", location: "location",
  thread: "thread", commitment: "commitment", event: "event", idea: "scene idea",
  birthday: "birthday", holiday: "holiday",
};

/** A pressure state in words, never the raw token. */
export const STATE_WORDS: Record<PressureState, string> = {
  overdue: "overdue", today: "today", due_soon: "due soon", upcoming: "upcoming",
  passed: "passed", stale: "stale", ok: "ok",
};

/** What `omitted` names, for the one note the page shows. */
export const PART_LABELS: Record<GraphPart, string> = {
  calendar: "the calendar", scenes: "scenes", chronicle: "the chronicle", plot: "threads",
  commitments: "commitments", events: "events", continuity: "reviewed links and merges",
  candidates: "review findings", relationships: "relationships",
  scene_ideas: "saved scene ideas", names: "names",
};

/** A reviewed link read from either end: `out` from the link's `from`, `in`
 *  from its `to`. `out` is the Ledger's own phrase (`RELATION_PHRASES`), so a
 *  link reads one way on both pages; `in` has no Ledger counterpart. `in`
 *  still says the same relation, from b's side: `a before b` is a due before
 *  b, so b reads "Due before this: a", the direction an idea anchored
 *  `before` b reads in on the same node ("Scene idea before this:"). */
export const RELATION_PHRASE: Record<LinkRelation, { out: string; in: string }> = {
  continues: { out: RELATION_PHRASES.continues, in: "Continued by" },
  subthread_of: { out: RELATION_PHRASES.subthread_of, in: "Has subthread" },
  pays_off: { out: RELATION_PHRASES.pays_off, in: "Paid off by" },
  before: { out: RELATION_PHRASES.before, in: "Due before this:" },
  on: { out: RELATION_PHRASES.on, in: "On this day:" },
  after: { out: RELATION_PHRASES.after, in: "Due after this:" },
  by: { out: RELATION_PHRASES.by, in: "Deadline for" },
  related_to: { out: RELATION_PHRASES.related_to, in: "Related to" },
};

/** What an idea's anchor was, when its moment has no node any more (past, or
 *  beyond pressure's horizon): named by its prefix, never by the ref itself. */
export const ANCHOR_NOUN: Readonly<Partial<Record<string, string>>> = {
  event: "an event", birthday: "a birthday", holiday: "a holiday",
};

export function anchorNoun(ref: string): string {
  return ANCHOR_NOUN[splitRef(ref)[0]] ?? "a date";
}

/** The finding kinds a node carries alone: one ref, so never an edge. */
const LIFECYCLE = new Set(["possible_thread_closure", "possible_commitment_resolution"]);

function scenes(count: number): string {
  return count === 1 ? "1 scene" : `${count} scenes`;
}

function edgeCount(list: GraphEdge[] | undefined, kind: EdgeKind): number {
  return (list ?? []).filter((e) => e.kind === kind).length;
}

/** When a dated record falls: its distance from the present, or, with no
 *  present to measure from, the day itself. A dated record never reads
 *  "undated" because the campaign has no clock. */
function whenOf(n: { fixed: number | null; in_days: number | null; friendly: string;
                     native: string }): string {
  if (n.in_days === null && n.fixed !== null) return n.friendly || n.native || "undated";
  return whenPhrase(n.in_days);
}

/** A node's one-line status, the same on every lens and whatever the toggles
 *  say: the Arcs row, the node's own line and its accessible name read it. */
export function statusOf(n: GraphNode, ix: GraphIndex): string {
  switch (n.kind) {
    case "scene":
      return n.done ? "absorbed" : "in play";
    case "character":
    case "pc":
      return scenes(edgeCount(ix.out.get(n.id), "appeared_in"));
    case "location":
      return scenes(edgeCount(ix.into.get(n.id), "occurred_at"));
    case "thread":
    case "commitment": {
      if (n.merged_into !== null) {
        return `merged into ${ix.byId.get(n.merged_into)?.label ?? "another record"}`;
      }
      const parts = [n.status];
      if (n.pressure && n.pressure.state !== "ok") parts.push(STATE_WORDS[n.pressure.state]);
      for (const f of n.findings) {
        const phrase = FINDING_PHRASE[f.kind];
        if (LIFECYCLE.has(f.kind) && !parts.includes(phrase)) parts.push(phrase);
      }
      return parts.filter(Boolean).join(", ");
    }
    case "event":
      return n.status === "scheduled" ? whenOf(n) : n.status;
    case "holiday":
      return whenPhrase(n.in_days);
    case "birthday":
      return whenPhrase(n.in_days, n.precision);
    case "idea":
      return n.fixed === null ? "unscheduled" : whenOf(n);
  }
}

/** What a node button is called: its label, what it is, and its status. */
export function accessibleName(n: GraphNode, ix: GraphIndex): string {
  return `${n.label}, ${KIND_NOUN[n.kind]}, ${statusOf(n, ix)}`;
}

/** The history state that opens the chooser seeded (§16.5): exactly one of
 *  the two shapes E's `sanitizeSeed` accepts. */
export function seedFor(n: GraphNode, action: "focus" | "anchor"): { chooser: ChooserSeed } {
  return action === "focus"
    ? { chooser: { drivers: { [n.id]: "focus" } } }
    : { chooser: { anchor: { ref: n.id, relation: "on" } } };
}

/** The Ledger row a thread or commitment opens (§12.1), or null. A merged
 *  record opens its own row; the Ledger resolves it to the canonical. */
export function ledgerTarget(n: GraphNode): LedgerTarget | null {
  if (n.kind === "thread") return { section: "threads", row: splitRef(n.id)[1] };
  if (n.kind === "commitment") return { section: "commitments", row: splitRef(n.id)[1] };
  return null;
}

function compare(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

/** The Arcs column: canonical threads, then commitments; within each, live
 *  before closed, then label case-insensitively, then id. */
export function arcRows(g: StoryGraph): GraphNode[] {
  const rank = (n: GraphNode) => (n.kind === "thread" ? 0 : 1);
  const live = (n: GraphNode) => (n.kind === "thread" || n.kind === "commitment") && n.live;
  return g.nodes
    .filter((n) => (n.kind === "thread" || n.kind === "commitment") && n.merged_into === null)
    .sort((a, b) => rank(a) - rank(b)
      || Number(live(b)) - Number(live(a))
      || compare(a.label.toLowerCase(), b.label.toLowerCase())
      || compare(a.id, b.id));
}
