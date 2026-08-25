import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type Edges, type EntityScope, type Greeting, type GreetingMark } from "../api/client";
import { errorText } from "../api/errors";
import { useHotkeys } from "../shortcuts/useHotkeys";

/** The plot map as a graph, beside the chip lists that authored it.
 *
 *  A greeting's edges are two flat lists on the greeting itself — "leads to"
 *  and "excludes" — which is the right shape to *store* and the wrong one to
 *  read: a plot with a dozen openings is a dozen chip lists that never say
 *  which of them is the start, what is downstream of what, or which branch a
 *  reader is looking at. Here it is one picture: a node per greeting, an arrow
 *  per unlock, a dashed line per exclusion.
 *
 *  It is a second VIEW and not a second model. Every write goes back through
 *  `api.setEdges` for the greeting the link starts at, exactly as the chip
 *  lists do, and nothing here is stored: the layout is derived on every read,
 *  so a map edited from either side agrees with the other. Predecessors are
 *  likewise derived rather than fetched — an arrow drawn into a node IS the
 *  reverse of some other node's `leads_to`, and asking the server for the same
 *  fact twice is how the two answers start to differ.
 *
 *  The layout is deliberately hand-rolled rather than a graph library: it is
 *  columns of boxes and a bezier per edge, which is a page of code, and a plot
 *  map is not a force-directed cloud — depth in the unlock order is the one
 *  thing the reader wants the x axis to mean.
 *
 *  The route REPLACES a greeting's arrays rather than merging, which is what
 *  makes every write here a whole-record write and gives this component its
 *  two hard rules: a greeting whose edges never loaded may not be written at
 *  all (guessed-empty arrays would delete what the failed read did not see),
 *  and a write applies to local state BEFORE it is sent, so a second edit to
 *  the same greeting builds on the first instead of racing it.
 */

/** Box geometry, in px. `COL`/`ROW` are strides, so the gap between two boxes
 *  is what is left over — the room a curve needs to leave one and arrive at the
 *  next without running along its edge. */
const NODE_W = 168;
const NODE_H = 52;
const COL = 256;
const ROW = 84;
const PAD = 16;

type Kind = "leads_to" | "excludes";

/** One line as drawn.
 *
 *  `both` marks an exclusion the two greetings each record about the other:
 *  exclusion bites in both directions whoever wrote it down (see
 *  `store.greetings.availability`), so it is one line, and deleting it has to
 *  clear both sides or half of it survives invisibly.
 *
 *  `lift` separates lines that would otherwise be drawn on top of each other.
 *  This view refuses to create a second link between a pair, but the chip
 *  editor never did: a map authored there can hold a→b and b→a, or an unlock
 *  and an exclusion for one pair, and a curve depends only on its endpoints —
 *  so without this the later line's hit target covers the earlier one and one
 *  of the two can never be selected. */
type Line = { key: string; kind: Kind; from: string; to: string; both: boolean; lift: number };

type Placed = { id: string; name: string; mark: GreetingMark; col: number; x: number; y: number };

const NO_EDGES: Edges = { leads_to: [], excludes: [] };

/** A key for an unordered pair of ids that cannot be forged by an id.
 *
 *  `join(" ")` cannot do this job: `safe_id` permits internal spaces, so
 *  ("a", "b c") and ("a b", "c") would key alike and the second exclusion
 *  would vanish from the map. Length-prefixing the first id makes the split
 *  unambiguous whatever the ids contain. */
const pairKey = (a: string, b: string) => {
  const [x, y] = a <= b ? [a, b] : [b, a];
  return `${x.length}:${x}:${y}`;
};

/** The unlock edges with every cycle broken, as a DAG.
 *
 *  A plot map is not guaranteed acyclic — nothing stops an author drawing a
 *  loop through the chip lists — and both halves of the layout need one that
 *  is: a depth pass over a cycle has no fixed point, and stopping it after a
 *  bounded number of passes bounds the running time without bounding the
 *  answer (an n-node loop can walk itself out to n² columns of empty canvas).
 *  So the cycle is cut here instead, at the one edge that closes it: a DFS
 *  drops each back edge — the arrow still draws, it just stops voting on
 *  which column its target belongs in. */
function forwardEdges(ids: string[], edges: Record<string, Edges>): Map<string, string[]> {
  const known = new Set(ids);
  const kept = new Map<string, string[]>(ids.map((id) => [id, []]));
  const OPEN = 1, DONE = 2;
  const state = new Map<string, number>();
  for (const root of ids) {
    if (state.has(root)) continue;
    state.set(root, OPEN);
    const stack: { id: string; i: number }[] = [{ id: root, i: 0 }];
    while (stack.length) {
      const top = stack[stack.length - 1];
      const outs = (edges[top.id] ?? NO_EDGES).leads_to;
      if (top.i >= outs.length) { state.set(top.id, DONE); stack.pop(); continue; }
      const tgt = outs[top.i++];
      if (!known.has(tgt) || tgt === top.id) continue;
      if (state.get(tgt) === OPEN) continue;   // the back edge that closes a cycle
      kept.get(top.id)!.push(tgt);
      if (!state.has(tgt)) { state.set(tgt, OPEN); stack.push({ id: tgt, i: 0 }); }
    }
  }
  return kept;
}

/** Depth = the longest chain of unlocks that has to be played first, which is
 *  what the columns mean. Over a DAG this settles, and in at most one column
 *  per greeting. */
function depths(ids: string[], edges: Record<string, Edges>): Record<string, number> {
  const kept = forwardEdges(ids, edges);
  const depth: Record<string, number> = Object.fromEntries(ids.map((id) => [id, 0]));
  for (let pass = 0; pass < ids.length; pass++) {
    let moved = false;
    for (const src of ids) {
      for (const tgt of kept.get(src) ?? []) {
        if (depth[tgt] < depth[src] + 1) { depth[tgt] = depth[src] + 1; moved = true; }
      }
    }
    if (!moved) break;
  }
  return depth;
}

/** Where each node sits. Columns by depth, and within a column by name, so the
 *  map does not reshuffle itself every time an edge is added. */
function layout(greetings: Greeting[], edges: Record<string, Edges>): Placed[] {
  const ids = greetings.map((g) => g.id);
  const depth = depths(ids, edges);
  const byCol = new Map<number, Greeting[]>();
  for (const g of greetings) {
    const col = depth[g.id] ?? 0;
    byCol.set(col, [...(byCol.get(col) ?? []), g]);
  }
  const out: Placed[] = [];
  for (const [col, rows] of byCol) {
    [...rows].sort((a, b) => a.name.localeCompare(b.name)).forEach((g, i) => {
      out.push({ id: g.id, name: g.name, mark: g.mark ?? null, col,
                 x: PAD + col * COL, y: PAD + i * ROW });
    });
  }
  return out;
}

/** The lines to draw: each exclusion pair collapsed to one, and every line
 *  that shares a pair with another lifted clear of it. */
function lines(greetings: Greeting[], edges: Record<string, Edges>): Line[] {
  const known = new Set(greetings.map((g) => g.id));
  const out: Omit<Line, "lift">[] = [];
  const seen = new Set<string>();
  for (const g of greetings) {
    const e = edges[g.id] ?? NO_EDGES;
    for (const to of e.leads_to) {
      if (!known.has(to) || to === g.id) continue;
      out.push({ key: `leads_to:${g.id.length}:${g.id}:${to}`, kind: "leads_to",
                 from: g.id, to, both: false });
    }
    for (const to of e.excludes) {
      if (!known.has(to) || to === g.id) continue;
      const pair = pairKey(g.id, to);
      if (seen.has(pair)) continue;
      seen.add(pair);
      out.push({ key: `excludes:${pair}`, kind: "excludes", from: g.id, to,
                 both: (edges[to] ?? NO_EDGES).excludes.includes(g.id) });
    }
  }
  const crowd = new Map<string, number>();
  for (const l of out) crowd.set(pairKey(l.from, l.to), (crowd.get(pairKey(l.from, l.to)) ?? 0) + 1);
  const taken = new Map<string, number>();
  return out.map((l) => {
    const pair = pairKey(l.from, l.to);
    const n = crowd.get(pair) ?? 1;
    const i = taken.get(pair) ?? 0;
    taken.set(pair, i + 1);
    return { ...l, lift: (i - (n - 1) / 2) * 26 };
  });
}

/** A curve between two boxes, leaving and arriving on whichever sides face
 *  each other — including the column a back-edge has to reach behind it, and
 *  the sideways loop two nodes in the same column need. `lift` pushes it clear
 *  of another line between the same pair. */
function pathOf(a: Placed, b: Placed, lift = 0): string {
  if (a.col === b.col) {
    // Down the column and out to the right, by enough to clear whatever rows
    // it passes -- neighbours barely bow at all, and only a line that has to
    // reach across other nodes swings wide enough to be in their way.
    const x = a.x + NODE_W / 2;
    const down = a.y <= b.y;
    const y1 = down ? a.y + NODE_H : a.y;
    const y2 = down ? b.y : b.y + NODE_H;
    const rows = Math.max(1, Math.round(Math.abs(b.y - a.y) / ROW));
    const bow = x + 20 + 44 * (rows - 1) + Math.abs(lift);
    return `M ${x} ${y1} C ${bow} ${y1}, ${bow} ${y2}, ${x} ${y2}`;
  }
  const rightwards = b.x > a.x;
  const x1 = rightwards ? a.x + NODE_W : a.x;
  const x2 = rightwards ? b.x : b.x + NODE_W;
  const y1 = a.y + NODE_H / 2;
  const y2 = b.y + NODE_H / 2;
  const reach = Math.max(48, Math.abs(x2 - x1) / 2);
  const c1 = rightwards ? x1 + reach : x1 - reach;
  const c2 = rightwards ? x2 - reach : x2 + reach;
  // A line that skips a column would otherwise run flat through whatever
  // stands in it -- and land exactly on top of the arrows between those, which
  // is worse than crossing them: it reads as one dashed line where there were
  // two solid ones. Dip it below the row instead, by enough to clear a box and
  // not so far that it lands in the row beneath.
  const span = Math.abs(b.col - a.col);
  const dip = (span > 1 ? Math.min(68, 26 + 14 * (span - 1)) : 0) + lift;
  return `M ${x1} ${y1} C ${c1} ${y1 + dip}, ${c2} ${y2 + dip}, ${x2} ${y2}`;
}

const KIND_LABEL: Record<Kind, string> = { leads_to: "Unlocks", excludes: "Excludes" };
const ARROW: Record<Kind, string> = { leads_to: "→", excludes: "↮" };

/** Thrown by a queued write the map has already moved past — a scope change,
 *  or an earlier write in the queue that failed. Not an error to report: the
 *  reload that invalidated it is what the reader sees. */
class Skipped extends Error {}

export function PlotMapEditor({ scope, onOpenGreeting, onChanged, reloadKey = 0, busy = false }:
  { scope: EntityScope; onOpenGreeting?: (gid: string) => void;
    /** Fired after a write lands here, so the chip-list editor -- which stays
     *  mounted behind this view and holds its own copy of a greeting's edges --
     *  can re-read rather than save over what was just drawn. */
    onChanged?: () => void;
    reloadKey?: number;
    /** True while the chip-list editor is mid-write. Both views send WHOLE
     *  arrays, so a graph edit that lands between that save's body write and
     *  its edge write is one the older payload overwrites. */
    busy?: boolean }) {
  const [greetings, setGreetings] = useState<Greeting[]>([]);
  const [edges, setEdgeMap] = useState<Record<string, Edges>>({});
  const [ready, setReady] = useState(false);
  /** Whether the LIST came back. "No greetings" and "the list request failed"
   *  are different answers, and only this one licenses the first. */
  const [listed, setListed] = useState(false);
  /** Greetings the list named but whose edges never arrived. They are nodes,
   *  and they are not writable — see `write`. */
  const [unread, setUnread] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  /** The greeting a new link starts at, once the reader has armed one. */
  const [linking, setLinking] = useState<string | null>(null);
  /** Which kind that link will be. Unlock is the common one and the default. */
  const [kind, setKind] = useState<Kind>("leads_to");
  const [picked, setPicked] = useState<string | null>(null); // a selected line's key
  /** A mutation of this map is in flight. Both unlock directions of one
   *  exclusion stay on screen while the first is running, and clicking the
   *  second starts a conversion whose optimistic state interleaves with it --
   *  ending with both directions stored, which nothing here would otherwise
   *  allow. */
  const [mutating, setMutating] = useState(false);

  /** The edge map as the mutations see it, updated synchronously.
   *
   *  React state is a render behind, and these payloads are whole records: two
   *  edits to one greeting in quick succession would both derive from the map
   *  as it was before either, and the second would send the first's edge back
   *  to the server as deleted. */
  const edgesRef = useRef<Record<string, Edges>>({});
  /** One chain for the whole map, not one per greeting.
   *
   *  Per-greeting was enough for this component's own arithmetic, and not
   *  enough for the file underneath it: `store.greetings.set_edges` is an
   *  unlocked read-modify-write of one `plotmap.json` holding every greeting's
   *  edges (`store.greetings` is not in `locks.DOMAIN_MODULES`), so two
   *  requests for *different* greetings can read the same map and the second
   *  write drops the first. That race is the backend's and predates this view;
   *  what this view owes is not to be the thing that provokes it. */
  const chain = useRef<Promise<unknown>>(Promise.resolve());
  /** Which load is current: a scope change or a retry makes every earlier one
   *  stale, including one already in flight. */
  const loadId = useRef(0);

  const putEdges = useCallback((gid: string, next: Edges) => {
    edgesRef.current = { ...edgesRef.current, [gid]: next };
    setEdgeMap(edgesRef.current);
  }, []);

  /** Re-read the scope. `keepError` is for the one caller that is reporting a
   *  failure *by* re-reading -- clearing the banner it just set would leave the
   *  map silently rearranging itself. */
  const load = useCallback((keepError = false) => {
    const mine = ++loadId.current;
    setReady(false);
    setListed(false);
    if (!keepError) setError(null);
    // Behind whatever is already on the wire. A read that overtakes an
    // in-flight write comes back describing the map without it, and this
    // replaces `edgesRef` with that -- so the next whole-array write deletes
    // an edit the server had already accepted.
    chain.current.then(() => api.listGreetings(scope)).then(async (list) => {
      // Edges live in the plot map, and only the per-greeting read carries
      // them -- the list endpoint returns summaries. One read each, and a
      // greeting whose read fails is still a node: a map that silently omits
      // an opening is worse than one that admits it is missing some lines.
      const missed: string[] = [];
      const pairs = await Promise.all(list.map((g) =>
        api.readGreeting(scope, g.id)
          .then((d) => [g.id, d.edges ?? NO_EDGES] as const)
          .catch(() => { missed.push(g.id); return [g.id, NO_EDGES] as const })));
      if (loadId.current !== mine) return;
      edgesRef.current = Object.fromEntries(pairs);
      setGreetings(list);
      setEdgeMap(edgesRef.current);
      setUnread(missed);
      setListed(true);
      setReady(true);
    }).catch((err: unknown) => {
      if (loadId.current !== mine) return;
      // Cleared, not kept: on a same-scope re-read the retained nodes are the
      // optimistic ones this reload was called to confirm, and leaving them
      // drawn makes them writable again from state nobody has confirmed.
      setGreetings([]);
      setEdgeMap({});
      edgesRef.current = {};
      setUnread([]);
      setError(errorText(err));
      setReady(true);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps -- scope is two scalars
  }, [scope.kind, scope.id, reloadKey]);

  useEffect(() => {
    // Cleared rather than left standing while the next scope loads: this
    // component is reused across a scope change, and a node still on screen is
    // a node that can be clicked -- which would write the previous world's
    // greeting id, and its edges, into the new one.
    setGreetings([]);
    setEdgeMap({});
    setListed(false);
    edgesRef.current = {};
    chain.current = Promise.resolve();
    setUnread([]);
    setLinking(null);
    setPicked(null);
    load();
  }, [load]);

  const placed = useMemo(() => layout(greetings, edges), [greetings, edges]);
  const drawn = useMemo(() => lines(greetings, edges), [greetings, edges]);
  const at = useMemo(() => new Map(placed.map((p) => [p.id, p])), [placed]);
  const nameOf = useCallback((id: string) => at.get(id)?.name ?? id, [at]);
  const selected = drawn.find((l) => l.key === picked) ?? null;

  const clear = useCallback(() => { setLinking(null); setPicked(null); }, []);
  useHotkeys([{ keys: "escape", label: "Cancel the link being drawn",
                group: "PLOT MAP", enabled: !!linking || !!picked, run: clear }]);

  /** Every write is the source greeting's WHOLE pair of arrays: the route
   *  replaces what it is given rather than merging, so a partial body would
   *  drop the edges it did not mention.
   *
   *  Which is also why it applies locally first and queues behind whatever is
   *  already in flight for that greeting, and why a greeting whose edges never
   *  loaded is refused outright rather than written from a guess. */
  async function write(gid: string, next: Edges, gen = loadId.current): Promise<boolean> {
    if (unread.includes(gid)) {
      setError(`${nameOf(gid)}'s links could not be read, so they cannot be `
               + "changed without erasing whatever they are. Retry the read first.");
      return false;
    }
    // A mutation that spans two writes captures `gen` before the first, so its
    // second half cannot land in a scope the reader has since left: `edgesRef`
    // by then holds the new scope's edges, while `scope` here is still the old
    // render's -- which is how a greeting id absent from the new map would be
    // sent back to the old one with empty arrays.
    if (loadId.current !== gen) return false;
    putEdges(gid, next);
    const task = chain.current.then(async () => {
      if (loadId.current !== gen) throw new Skipped();
      await api.setEdges(scope, gid, { leads_to: next.leads_to, excludes: next.excludes });
    });
    chain.current = task.catch(() => undefined);
    setMutating(true);
    try {
      await task;
      onChanged?.();
      return true;
    } catch (err: unknown) {
      if (err instanceof Skipped) return false;
      // Re-read rather than revert. The optimistic map may already carry a
      // later edit whose payload was built on this one, and that write is
      // still queued behind this failure -- restoring a snapshot would leave
      // the screen claiming one thing and the store holding another. `load`
      // bumps the generation, which is also what discards those queued
      // payloads: they describe a map that never existed.
      setError(errorText(err));
      load(true);
      return false;
    } finally {
      setMutating(false);
    }
  }

  const edgesOf = (gid: string): Edges => edgesRef.current[gid] ?? NO_EDGES;
  const without = (list: string[], id: string) => list.filter((x) => x !== id);

  /** Every greeting a mutation is going to write, checked BEFORE its first
   *  write rather than at each one.
   *
   *  A two-step mutation refused halfway is worse than one refused outright:
   *  converting an exclusion whose chosen source is unread would clear the far
   *  half, then be refused on the near one -- deleting an authored exclusion in
   *  the course of failing to replace it. */
  function unwritable(...gids: string[]): string[] {
    return [...new Set(gids)].filter((g) => unread.includes(g));
  }

  /** Why a mutation cannot start now, or null. */
  function held(): string | null {
    if (busy) return "The greeting editor is saving. Its save writes these same links, so the map waits for it.";
    if (mutating) return "A link is still being written. Wait for it before changing another.";
    return null;
  }

  function refuse(bad: string[]): void {
    setError(`${bad.map(nameOf).join(" and ")}: links could not be read, so they `
             + "cannot be changed without erasing whatever they are. Retry the read first.");
  }

  /** What already ties two greetings together, in either direction. */
  function linkBetween(a: string, b: string): Kind | null {
    for (const [x, y] of [[a, b], [b, a]] as const) {
      if (edgesOf(x).leads_to.includes(y)) return "leads_to";
      if (edgesOf(x).excludes.includes(y)) return "excludes";
    }
    return null;
  }

  async function link(to: string) {
    const from = linking;
    if (!from) return;
    setLinking(null);
    const hold = held();
    if (hold) { setError(hold); return; }
    if (from === to) return;               // handled by the source node itself
    const existing = linkBetween(from, to);
    if (existing) {
      // Drawing over an existing line would delete an authored edge on one
      // click. The line is right there and selecting it can flip or remove it,
      // so this says that rather than doing it.
      setError(`${nameOf(from)} and ${nameOf(to)} are already linked `
               + `(${KIND_LABEL[existing].toLowerCase()}) — select that line to change or delete it.`);
      return;
    }
    setError(null);
    const cur = edgesOf(from);
    await write(from, { ...cur, [kind]: [...cur[kind], to] });
  }

  async function remove(line: Line) {
    setError(null);
    const hold = held();
    if (hold) { setError(hold); return; }
    // Both endpoints when the exclusion is mutual: the second write is as much
    // a part of this delete as the first.
    const bad = unwritable(line.from, ...(line.both ? [line.to] : []));
    if (bad.length) { refuse(bad); return; }
    const gen = loadId.current;
    const cur = edgesOf(line.from);
    const ok = await write(line.from, { leads_to: without(cur.leads_to, line.to),
                                        excludes: without(cur.excludes, line.to) }, gen);
    // The far half of a mutual exclusion. Second, and only if the first
    // landed: a half-deleted pair still excludes, which is the state the
    // reader already had, so an interrupted delete cannot invent a rule.
    if (ok && line.both) {
      const back = edgesOf(line.to);
      await write(line.to, { ...back, excludes: without(back.excludes, line.from) }, gen);
    }
    setPicked(null);
  }

  /** An unlock becomes an exclusion: one record, one write. */
  async function toExclusion(line: Line) {
    setError(null);
    const hold = held();
    if (hold) { setError(hold); return; }
    const bad = unwritable(line.from);
    if (bad.length) { refuse(bad); return; }
    const cur = edgesOf(line.from);
    const ok = await write(line.from, { leads_to: without(cur.leads_to, line.to),
                                        excludes: [...without(cur.excludes, line.to), line.to] });
    if (ok) setPicked(`excludes:${pairKey(line.from, line.to)}`);
  }

  /** An exclusion becomes an unlock, in the direction the reader picked.
   *
   *  An exclusion is undirected and an unlock is not, so the direction is
   *  asked for rather than inherited from whichever greeting happened to
   *  record the exclusion -- the map's own iteration order is not plot.
   *
   *  Ordered so no intermediate state is a rule nobody wrote: the far half of
   *  a mutual exclusion goes first (leaving a one-sided exclusion, which still
   *  excludes exactly as before), and the unlock lands in the same write that
   *  clears the near half. An unlock that coexisted with a live exclusion
   *  would block the greeting it claims to open. */
  async function toUnlock(line: Line, from: string) {
    setError(null);
    const hold = held();
    if (hold) { setError(hold); return; }
    const to = from === line.from ? line.to : line.from;
    // BOTH endpoints, always. An unread endpoint's edges read as empty, which
    // cannot tell a pair that only A records apart from one B records too --
    // and converting the second kind leaves B's exclusion standing, still
    // blocking the greeting the new arrow claims to open.
    const bad = unwritable(from, to);
    if (bad.length) { refuse(bad); return; }
    const gen = loadId.current;
    const back = edgesOf(to);
    if (back.excludes.includes(from)) {
      if (!await write(to, { ...back, excludes: without(back.excludes, from) }, gen)) return;
    }
    const cur = edgesOf(from);
    const ok = await write(from, { leads_to: [...without(cur.leads_to, to), to],
                                   excludes: without(cur.excludes, to) }, gen);
    if (ok) setPicked(`leads_to:${from.length}:${from}:${to}`);
  }

  // `x`/`y` already start at PAD, so one more of it is the far margin.
  const width = PAD + Math.max(...placed.map((p) => p.x + NODE_W), NODE_W);
  const height = PAD + Math.max(...placed.map((p) => p.y + NODE_H), NODE_H);

  return (
    <div className="plotmap">
      <div className="plotmap-bar">
        <div className="chips" role="group" aria-label="New link kind">
          {(["leads_to", "excludes"] as Kind[]).map((k) => (
            <button key={k} className={"chip" + (kind === k ? " on" : "")}
                    aria-pressed={kind === k} onClick={() => setKind(k)}>
              {KIND_LABEL[k]}
            </button>
          ))}
        </div>
        {/* Always in the DOM, never mounted on demand: it is what tells the
            reader a link is armed, and a live region has to exist before its
            text changes to be announced. */}
        <div className="field-hint plotmap-hint" role="status" aria-live="polite">
          {linking
            ? `Pick what ${nameOf(linking)} ${kind === "leads_to" ? "unlocks" : "excludes"}…`
            : "Link ⇢ on a greeting, then pick the other end. Click a line to change it."}
        </div>
      </div>
      {error && <div className="banner">{error}</div>}
      {unread.length > 0 && (
        <div className="banner plotmap-unread">
          <span>
            {unread.length === 1
              ? `${nameOf(unread[0])}'s links could not be read, so the map may be missing lines. Its own links cannot be edited until the read succeeds.`
              : `${unread.length} greetings' links could not be read, so the map may be missing lines. Their own links cannot be edited until the read succeeds.`}
          </span>
          <button className="retry" onClick={() => load()} disabled={!ready || mutating}>Retry</button>
        </div>
      )}
      {/* Only while the map it describes is on screen. A reload replaces every
          greeting's edges with what the server says, so a mutation started
          against the old ones -- during a Retry, say -- would be overwritten by
          a snapshot that predates it. */}
      {ready && selected && (
        <div className="plotmap-selected" aria-busy={mutating || busy}>
          <span className="chip on">
            {KIND_LABEL[selected.kind]}: {nameOf(selected.from)} {ARROW[selected.kind]} {nameOf(selected.to)}
          </span>
          {selected.kind === "leads_to" ? (
            <button className="chip" disabled={!!held()}
                    onClick={() => void toExclusion(selected)}>Make exclusion</button>
          ) : (
            [selected.from, selected.to].map((from) => (
              <button key={from} className="chip" disabled={!!held()}
                      onClick={() => void toUnlock(selected, from)}>
                Unlock: {nameOf(from)} → {nameOf(from === selected.from ? selected.to : selected.from)}
              </button>
            ))
          )}
          <button className="chip" disabled={!!held()}
                  onClick={() => void remove(selected)}>Delete link</button>
          <button className="chip" onClick={() => setPicked(null)}>Done</button>
        </div>
      )}
      {ready && listed && greetings.length === 0 ? (
        <div className="editor-empty">No greetings to map yet.</div>
      ) : ready && greetings.length > 0 ? (
        <div className="plotmap-canvas pm-canvas" style={{ width, height }}>
          <svg className="pm-edges" width={width} height={height}>
            <defs>
              <marker id="pm-arrow" viewBox="0 0 10 10" refX="9" refY="5"
                      markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                <path d="M 0 0 L 10 5 L 0 10 z" />
              </marker>
            </defs>
            {drawn.map((line) => {
              const a = at.get(line.from);
              const b = at.get(line.to);
              if (!a || !b) return null;
              const d = pathOf(a, b, line.lift);
              const select = () => { setLinking(null); setPicked(line.key); };
              return (
                <g key={line.key} role="button" tabIndex={0}
                   className={`pm-edge ${line.kind}` + (picked === line.key ? " picked" : "")}
                   aria-label={`${KIND_LABEL[line.kind]}: ${a.name} ${ARROW[line.kind]} ${b.name}`}
                   onClick={select}
                   onKeyDown={(e) => {
                     if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(); }
                   }}>
                  {/* A 2px curve is not a click target, so a fat invisible
                      twin under it is what the pointer actually hits. */}
                  <path className="pm-hit" d={d} />
                  <path className="pm-line" d={d}
                        markerEnd={line.kind === "leads_to" ? "url(#pm-arrow)" : undefined} />
                </g>
              );
            })}
          </svg>
          {placed.map((p) => (
            <div key={p.id}
                 className={`pm-node${p.mark ? ` ${p.mark}` : ""}`
                   // While a link is armed every OTHER node is a target, and
                   // saying so is the difference between a mode the reader can
                   // see and one they have to remember they are in.
                   + (linking && linking !== p.id ? " targetable" : "")}
                 style={{ left: p.x, top: p.y, width: NODE_W, height: NODE_H }}>
              <button className="pm-open" title={p.name}
                      aria-label={linking === null ? `Open ${p.name}`
                        : linking === p.id ? `Cancel link from ${p.name}`
                        : `Link ${nameOf(linking)} to ${p.name}`}
                      onClick={() => {
                        if (linking === null) { onOpenGreeting?.(p.id); return; }
                        if (linking === p.id) { setLinking(null); return; }
                        void link(p.id);
                      }}>
                <span className="pm-name">{p.name}</span>
                {p.mark && <span className={`mark-badge ${p.mark}`}>{p.mark}</span>}
              </button>
              <button className="pm-link" aria-label={`Link from ${p.name}`}
                      aria-pressed={linking === p.id}
                      // A node whose own edges never loaded cannot be a source:
                      // the write would send the arrays this never read.
                      disabled={unread.includes(p.id) || !!held()}
                      title={unread.includes(p.id) ? "Links could not be read" : (held() ?? `Link from ${p.name}`)}
                      onClick={() => { setPicked(null); setLinking(linking === p.id ? null : p.id); }}>
                ⇢
              </button>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
