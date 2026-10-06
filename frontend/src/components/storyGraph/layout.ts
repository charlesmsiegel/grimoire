/** The Story Graph's coordinates (capstone §19.4; plan Decision 21): a pure
 *  function of the payload, the visible set and an axis, so a jsdom test can
 *  assert where a node lands.
 *
 *  Columns are ordinal, never a linear day scale -- a day-scaled axis would
 *  put a year between two scenes a flashback apart. The **play** axis (Story,
 *  Cast, Continuity) runs the scene spine in play order, then Now, then what
 *  is ahead; the **calendar** axis (Calendar) runs one column per day by
 *  `fixed` with Now at `now.fixed`. Positions read only `fixed` and
 *  `in_days`; `native` is never parsed.
 *
 *  Every visible node's column is decided first and the columns are derived
 *  from those answers, so a bucket column exists exactly when some node sits
 *  in it, and no node can be given a column that was never allocated. The
 *  Now column holds only the marker.
 *
 *  A leaf: no React. */
import type { GraphEdge, GraphNode, NodeKind, StoryGraph } from "../../api/types";
import { whenPhrase } from "../pressureControls";
import { arcRows, isReached, type GraphIndex } from "./model";

/* Geometry, to be tuned against real campaigns later. NODE_H is the 44px
 * touch target, and a node button is exactly that tall, so it never grows
 * into the next row; NODE_W fits a short title at the column font; COL and
 * ROW are a node plus a gap wide enough for an edge to turn in; HEAD is one
 * line of column-head text, reserved above row 0 so a head never overlaps a
 * node; PAD is the canvas margin. */
export const COL = 184, ROW = 64, NODE_W = 160, NODE_H = 44, PAD = 16, HEAD = 28;

export type Placed = { id: string; x: number; y: number };
export type Column = {
  index: number;
  head: string;
  kind: "unplaced" | "scene" | "reached" | "now" | "future" | "undated" | "idea"
    | "unscheduled" | "day" | "not-dated" | "people";
};
export type Layout = {
  nodes: Placed[]; columns: Column[]; nowX: number | null; width: number; height: number;
};
type View = { nodes: GraphNode[]; edges: GraphEdge[] };

/** `NODE_KINDS` order: within a column, nodes stack by kind and then id.
 *  A Record, so a kind added to the union cannot be left unranked. */
const KIND_RANK: Record<NodeKind, number> = {
  scene: 0, character: 1, pc: 2, location: 3, thread: 4, commitment: 5, event: 6, idea: 7,
  birthday: 8, holiday: 9,
};

/** Left to right: a column's group on its axis, then its `rank`. */
const PLAY_GROUP: Column["kind"][] = [
  "unplaced", "scene", "reached", "now", "future", "undated", "idea", "unscheduled",
];
/** Day columns and Now share one group, ordered by day, so Now falls between
 *  the last day before the present and the first day from it on. */
const CALENDAR_GROUP: Partial<Record<Column["kind"], number>> = {
  day: 0, now: 0, undated: 1, "not-dated": 2, people: 3,
};

/** Where a node goes, before the columns are numbered. */
type Slot = { kind: Column["kind"]; rank: number; tie: string; head: string };

/** The play axis's first band; threads and commitments take lanes, and
 *  actors and locations their own bands below. */
const BAND_0 = new Set<NodeKind>(["scene", "event", "holiday", "birthday", "idea"]);

const MOVEMENT = new Set<GraphEdge["kind"]>([
  "opened_in", "advanced_in", "touched_in", "closed_in", "resolved_in",
]);

function compare(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

function byKindThenId(a: GraphNode, b: GraphNode): number {
  return KIND_RANK[a.kind] - KIND_RANK[b.kind] || compare(a.id, b.id);
}

function slotKey(s: Slot): string {
  return `${s.kind}\0${s.rank}\0${s.tie}`;
}

function bucket(kind: Column["kind"], head: string): Slot {
  return { kind, rank: 0, tie: "", head };
}

const UNPLACED = bucket("unplaced", "Not in a scene");
const UNDATED = bucket("undated", "Undated");

function future(inDays: number): Slot {
  return { kind: "future", rank: inDays, tie: "", head: whenPhrase(inDays) };
}

/** An event's, holiday's or birthday's slot right of Now: by `in_days`, or,
 *  in a campaign with no present (where no node has an `in_days`), by
 *  `fixed`, headed by its date -- a day the calendar can name is never filed
 *  as Undated. The two never meet in one payload, since `in_days` is null for
 *  a node with a `fixed` only when `now.fixed` is, and the distinct `tie`
 *  keeps their columns apart regardless. */
function temporal(n: { fixed: number | null; in_days: number | null; friendly: string;
                       native: string }): Slot {
  if (n.in_days !== null) return n.in_days >= 0 ? future(n.in_days) : UNDATED;
  return n.fixed === null
    ? UNDATED
    : { kind: "future", rank: n.fixed, tie: "fixed", head: n.friendly || n.native };
}

function x(col: number): number {
  return PAD + col * COL;
}

function y(row: number): number {
  return HEAD + PAD + row * ROW;
}

function mergedInto(n: GraphNode): string | null {
  return n.kind === "thread" || n.kind === "commitment" ? n.merged_into : null;
}

/** The play axis's answer for one node, given the visible scenes' slots.
 *  Merged nodes are decided by the caller, from their canonical. */
function playSlot(n: GraphNode, ix: GraphIndex, scenes: Map<string, Slot>,
                  lastScene: Slot | null): Slot {
  const reached = (kinds: Set<GraphEdge["kind"]>, edges: GraphEdge[] | undefined,
                   end: "from" | "to") => (edges ?? [])
    .filter((e) => kinds.has(e.kind))
    .map((e) => scenes.get(e[end]))
    .filter((s): s is Slot => s !== undefined)
    .sort((a, b) => a.rank - b.rank || compare(a.tie, b.tie));
  switch (n.kind) {
    case "scene":
      return scenes.get(n.id) ?? UNPLACED;
    case "event":
      if (isReached(n)) return bucket("reached", "Reached");
      return temporal(n);
    case "holiday":
    case "birthday":
      return temporal(n);
    case "idea":
      // Keyed on `fixed`, which orders exactly as `in_days` does and still
      // answers when the present is unknown.
      return n.fixed === null
        ? bucket("unscheduled", "Unscheduled")
        : { kind: "idea", rank: n.fixed, tie: "",
            head: n.in_days === null ? n.friendly : whenPhrase(n.in_days) };
    case "thread":
    case "commitment": {
      // Only a deadline known to be ahead leaves the lane's scene, and that
      // needs a present: with none, a dated commitment keeps its scene.
      if (n.kind === "commitment" && n.in_days !== null && n.in_days >= 0) {
        return future(n.in_days);
      }
      const moved = reached(MOVEMENT, ix.out.get(n.id), "to");
      return moved[moved.length - 1] ?? lastScene ?? UNPLACED;
    }
    case "character":
    case "pc":
      return reached(new Set(["appeared_in"]), ix.out.get(n.id), "to")[0] ?? UNPLACED;
    case "location":
      return reached(new Set(["occurred_at"]), ix.into.get(n.id), "from")[0] ?? UNPLACED;
  }
}

function playSlots(ix: GraphIndex, view: View): Map<string, Slot> {
  const scenes = new Map<string, Slot>();
  let lastScene: Slot | null = null;
  for (const n of view.nodes) {
    if (n.kind !== "scene") continue;
    const s: Slot = { kind: "scene", rank: n.order, tie: n.id, head: `Scene ${n.order + 1}` };
    scenes.set(n.id, s);
    if (!lastScene || s.rank > lastScene.rank
        || (s.rank === lastScene.rank && compare(s.tie, lastScene.tie) > 0)) lastScene = s;
  }
  const slots = new Map<string, Slot>();
  for (const n of view.nodes) {
    if (mergedInto(n) === null) slots.set(n.id, playSlot(n, ix, scenes, lastScene));
  }
  // A merged node sits under its canonical when that is drawn; otherwise it
  // is placed as any record with no movement is.
  for (const n of view.nodes) {
    const canonical = mergedInto(n);
    if (canonical === null) continue;
    slots.set(n.id, slots.get(canonical) ?? playSlot(n, ix, scenes, lastScene));
  }
  return slots;
}

function calendarSlot(n: GraphNode): Slot {
  switch (n.kind) {
    case "character":
    case "pc":
    case "location":
      return bucket("people", "People and places");
    case "thread":
      return bucket("not-dated", "Not dated");
    default:
      break;
  }
  if (mergedInto(n) !== null || (n.kind === "commitment" && n.native === "")) {
    return bucket("not-dated", "Not dated");
  }
  // A dated kind. An idea is never on the Calendar lens; were one to reach
  // it, it is dated as the others are rather than left unplaced.
  // The head is the day's first node's `friendly`, set by the caller.
  return n.fixed === null ? UNDATED : { kind: "day", rank: n.fixed, tie: "", head: "" };
}

/** The columns a set of slots needs, `now` among them, numbered left to
 *  right: by group, then rank, Now before a day of the same rank, then tie. */
function columnsOf(slots: Iterable<Slot>, now: Slot | null,
                   group: (kind: Column["kind"]) => number): Columns {
  const distinct = new Map<string, Slot>();
  for (const s of slots) if (!distinct.has(slotKey(s))) distinct.set(slotKey(s), s);
  if (now) distinct.set(slotKey(now), now);
  const first = (s: Slot) => (s.kind === "now" ? 0 : 1);
  const sorted = [...distinct.values()].sort((a, b) => group(a.kind) - group(b.kind)
    || a.rank - b.rank || first(a) - first(b) || compare(a.tie, b.tie));
  const index = new Map(sorted.map((s, i) => [slotKey(s), i] as const));
  const columns = sorted.map((s, i): Column => ({ index: i, head: s.head, kind: s.kind }));
  return { columns, index, nowCol: now ? index.get(slotKey(now)) ?? null : null };
}

type Columns = { columns: Column[]; index: Map<string, number>; nowCol: number | null };

/** The column index of a node already given a slot. Every visible node is
 *  given one before the columns are derived from them, so this never misses. */
function columnOf(slots: Map<string, Slot>, cols: Columns): (n: GraphNode) => number {
  return (n) => {
    const s = slots.get(n.id);
    return (s && cols.index.get(slotKey(s))) ?? 0;
  };
}

/** Stack `nodes` per column from `row`, in the order given; answers the
 *  number of rows the tallest stack used. */
function stack(nodes: GraphNode[], colOf: (n: GraphNode) => number, row: number,
               out: Map<string, Placed>): number {
  const depth = new Map<number, number>();
  for (const n of nodes) {
    const col = colOf(n);
    const d = depth.get(col) ?? 0;
    out.set(n.id, { id: n.id, x: x(col), y: y(row + d) });
    depth.set(col, d + 1);
  }
  return Math.max(0, ...depth.values());
}

/** One lane per visible thread or commitment: canonicals in the Arcs
 *  column's order, each followed by its visible merged records by id, then
 *  any merged record whose canonical is not drawn. */
function lanes(g: StoryGraph, view: View): GraphNode[] {
  const arcs = view.nodes.filter((n) => n.kind === "thread" || n.kind === "commitment");
  const shown = new Set(arcs.map((n) => n.id));
  const merged = arcs.filter((n) => mergedInto(n) !== null)
    .sort((a, b) => compare(a.id, b.id));
  const out: GraphNode[] = [];
  for (const c of arcRows(g)) {
    if (!shown.has(c.id)) continue;
    out.push(c, ...merged.filter((m) => mergedInto(m) === c.id));
  }
  out.push(...merged.filter((m) => !shown.has(mergedInto(m) ?? "")));
  return out;
}

function playLayout(g: StoryGraph, ix: GraphIndex, view: View): Layout {
  const slots = playSlots(ix, view);
  const cols = columnsOf(slots.values(), bucket("now", "Now"), (k) => PLAY_GROUP.indexOf(k));
  const colOf = columnOf(slots, cols);
  const placed = new Map<string, Placed>();
  const byId = (a: GraphNode, b: GraphNode) => compare(a.id, b.id);
  // Band 0: the spine and everything dated or bucketed beside it.
  let row = stack(view.nodes.filter((n) => BAND_0.has(n.kind)).sort(byKindThenId),
                  colOf, 0, placed);
  for (const n of lanes(g, view)) {
    placed.set(n.id, { id: n.id, x: x(colOf(n)), y: y(row) });
    row += 1;
  }
  row += stack(view.nodes.filter((n) => n.kind === "character" || n.kind === "pc").sort(byId),
               colOf, row, placed);
  row += stack(view.nodes.filter((n) => n.kind === "location").sort(byId), colOf, row, placed);
  return finish(view, placed, cols, row);
}

function calendarLayout(g: StoryGraph, view: View): Layout {
  const nowFixed = g.now.fixed;
  const sorted = [...view.nodes].sort(byKindThenId);
  const slots = new Map<string, Slot>();
  const heads = new Map<string, string>();
  for (const n of sorted) {
    const s = calendarSlot(n);
    // A day column is headed by its first node's `friendly`, in kind-then-id
    // order.
    if (s.kind === "day" && "friendly" in n && !heads.has(slotKey(s))) {
      heads.set(slotKey(s), n.friendly || n.native);
    }
    slots.set(n.id, s.kind === "day" ? { ...s, head: heads.get(slotKey(s)) ?? "" } : s);
  }
  const now = nowFixed === null ? null : { ...bucket("now", "Now"), rank: nowFixed };
  const cols = columnsOf(slots.values(), now, (k) => CALENDAR_GROUP[k] ?? 0);
  const placed = new Map<string, Placed>();
  const rows = stack(sorted, columnOf(slots, cols), 0, placed);
  return finish(view, placed, cols, rows);
}

function finish(view: View, placed: Map<string, Placed>, cols: Columns,
                rows: number): Layout {
  return {
    nodes: view.nodes.flatMap((n) => placed.get(n.id) ?? []),
    columns: cols.columns,
    nowX: cols.nowCol === null ? null : x(cols.nowCol) + NODE_W / 2,
    width: PAD + cols.columns.length * COL,
    height: HEAD + PAD + rows * ROW + PAD,
  };
}

/** Where every visible node goes, on the axis its lens draws. */
export function layout(g: StoryGraph, ix: GraphIndex, view: View,
                       axis: "play" | "calendar"): Layout {
  return axis === "play" ? playLayout(g, ix, view) : calendarLayout(g, view);
}

/** A cubic from one node's centre to another's, turning at the horizontal
 *  midpoint. */
export function edgePath(a: Placed, b: Placed): string {
  const [x1, y1] = [a.x + NODE_W / 2, a.y + NODE_H / 2];
  const [x2, y2] = [b.x + NODE_W / 2, b.y + NODE_H / 2];
  const mx = (x1 + x2) / 2;
  return `M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`;
}
