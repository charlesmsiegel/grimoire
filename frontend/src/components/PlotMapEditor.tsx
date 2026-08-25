import { useCallback, useEffect, useMemo, useState } from "react";
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

/** One line as drawn. `both` marks an exclusion the two greetings each record
 *  about the other: exclusion bites in both directions whoever wrote it down
 *  (see `store.greetings.availability`), so it is one line, and deleting it has
 *  to clear both sides or half of it survives invisibly. */
type Line = { key: string; kind: Kind; from: string; to: string; both: boolean };

type Placed = { id: string; name: string; mark: GreetingMark; col: number; x: number; y: number };

const NO_EDGES: Edges = { leads_to: [], excludes: [] };

/** Depth = the longest chain of unlocks that has to be played first, which is
 *  what the columns mean. Relaxation rather than a topological sort because a
 *  plot map is not guaranteed acyclic — nothing stops an author drawing a loop,
 *  and a layout that hangs on one would be a worse bug than the loop. Each pass
 *  can only push a node right, and the cap means a cycle settles rather than
 *  spins. */
function depths(ids: string[], edges: Record<string, Edges>): Record<string, number> {
  const known = new Set(ids);
  const depth: Record<string, number> = Object.fromEntries(ids.map((id) => [id, 0]));
  for (let pass = 0; pass < ids.length; pass++) {
    let moved = false;
    for (const src of ids) {
      for (const tgt of (edges[src] ?? NO_EDGES).leads_to) {
        if (!known.has(tgt) || tgt === src) continue;
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

/** The lines to draw, with each exclusion pair collapsed to one. */
function lines(greetings: Greeting[], edges: Record<string, Edges>): Line[] {
  const known = new Set(greetings.map((g) => g.id));
  const out: Line[] = [];
  const seen = new Set<string>();
  for (const g of greetings) {
    const e = edges[g.id] ?? NO_EDGES;
    for (const to of e.leads_to) {
      if (!known.has(to) || to === g.id) continue;
      out.push({ key: `leads_to:${g.id}:${to}`, kind: "leads_to", from: g.id, to, both: false });
    }
    for (const to of e.excludes) {
      if (!known.has(to) || to === g.id) continue;
      const pair = [g.id, to].sort().join(" ");
      if (seen.has(pair)) continue;
      seen.add(pair);
      out.push({ key: `excludes:${pair}`, kind: "excludes", from: g.id, to,
                 both: (edges[to] ?? NO_EDGES).excludes.includes(g.id) });
    }
  }
  return out;
}

/** A curve between two boxes, leaving and arriving on whichever sides face
 *  each other — including the column a back-edge has to reach behind it, and
 *  the sideways loop two nodes in the same column need. */
function pathOf(a: Placed, b: Placed): string {
  if (a.col === b.col) {
    // Down the column and out to the right, by enough to clear whatever rows
    // it passes -- neighbours barely bow at all, and only a line that has to
    // reach across other nodes swings wide enough to be in their way.
    const x = a.x + NODE_W / 2;
    const down = a.y <= b.y;
    const y1 = down ? a.y + NODE_H : a.y;
    const y2 = down ? b.y : b.y + NODE_H;
    const rows = Math.max(1, Math.round(Math.abs(b.y - a.y) / ROW));
    const bow = x + 20 + 44 * (rows - 1);
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
  const dip = span > 1 ? Math.min(68, 26 + 14 * (span - 1)) : 0;
  return `M ${x1} ${y1} C ${c1} ${y1 + dip}, ${c2} ${y2 + dip}, ${x2} ${y2}`;
}

const KIND_LABEL: Record<Kind, string> = { leads_to: "Unlocks", excludes: "Excludes" };
const ARROW: Record<Kind, string> = { leads_to: "→", excludes: "↮" };

export function PlotMapEditor({ scope, onOpenGreeting }:
  { scope: EntityScope; onOpenGreeting?: (gid: string) => void }) {
  const [greetings, setGreetings] = useState<Greeting[]>([]);
  const [edges, setEdgeMap] = useState<Record<string, Edges>>({});
  const [ready, setReady] = useState(false);
  const [partial, setPartial] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** The greeting a new link starts at, once the reader has armed one. */
  const [linking, setLinking] = useState<string | null>(null);
  /** Which kind that link will be. Unlock is the common one and the default. */
  const [kind, setKind] = useState<Kind>("leads_to");
  const [picked, setPicked] = useState<string | null>(null); // a selected line's key

  useEffect(() => {
    let live = true;
    setReady(false);
    setPartial(false);
    setError(null);
    setLinking(null);
    setPicked(null);
    api.listGreetings(scope).then(async (list) => {
      // Edges live in the plot map, and only the per-greeting read carries
      // them -- the list endpoint returns summaries. One read each, and a
      // greeting whose read fails is still a node: a map that silently omits
      // an opening is worse than one that admits it is missing some lines.
      let missed = false;
      const pairs = await Promise.all(list.map((g) =>
        api.readGreeting(scope, g.id)
          .then((d) => [g.id, d.edges ?? NO_EDGES] as const)
          .catch(() => { missed = true; return [g.id, NO_EDGES] as const })));
      if (!live) return;
      setGreetings(list);
      setEdgeMap(Object.fromEntries(pairs));
      setPartial(missed);
      setReady(true);
    }).catch((err: unknown) => {
      if (!live) return;
      setError(errorText(err));
      setReady(true);
    });
    return () => { live = false; };
  }, [scope.kind, scope.id]);  // eslint-disable-line react-hooks/exhaustive-deps -- scope is two scalars

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
   *  drop the edges it did not mention. */
  async function write(gid: string, next: Edges): Promise<boolean> {
    try {
      await api.setEdges(scope, gid, { leads_to: next.leads_to, excludes: next.excludes });
      setEdgeMap((m) => ({ ...m, [gid]: next }));
      return true;
    } catch (err: unknown) {
      setError(errorText(err));
      return false;
    }
  }

  const edgesOf = (gid: string): Edges => edges[gid] ?? NO_EDGES;
  const without = (list: string[], id: string) => list.filter((x) => x !== id);

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
    const cur = edgesOf(line.from);
    const ok = await write(line.from, { leads_to: without(cur.leads_to, line.to),
                                        excludes: without(cur.excludes, line.to) });
    // The far half of a mutual exclusion. Second, and only if the first
    // landed: a half-deleted pair still excludes, and one that failed on the
    // near side should not lose the far one either.
    if (ok && line.both) {
      const back = edgesOf(line.to);
      await write(line.to, { ...back, excludes: without(back.excludes, line.from) });
    }
    setPicked(null);
  }

  async function flip(line: Line) {
    setError(null);
    const to: Kind = line.kind === "leads_to" ? "excludes" : "leads_to";
    const cur = edgesOf(line.from);
    const next: Edges = { leads_to: without(cur.leads_to, line.to),
                          excludes: without(cur.excludes, line.to) };
    next[to] = [...next[to], line.to];
    const ok = await write(line.from, next);
    // An exclusion the other side also records cannot become an unlock while
    // that half stands -- it would still block the greeting it now unlocks.
    if (ok && line.both && to === "leads_to") {
      const back = edgesOf(line.to);
      await write(line.to, { ...back, excludes: without(back.excludes, line.from) });
    }
    setPicked(line.kind === "leads_to"
      ? `excludes:${[line.from, line.to].sort().join(" ")}`
      : `leads_to:${line.from}:${line.to}`);
  }

  const width = PAD * 2 + Math.max(...placed.map((p) => p.x + NODE_W), NODE_W);
  const height = PAD * 2 + Math.max(...placed.map((p) => p.y + NODE_H), NODE_H);

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
      {partial && (
        <div className="field-hint">
          Some greetings could not be read, so the map may be missing links.
        </div>
      )}
      {selected && (
        <div className="plotmap-selected">
          <span className="chip on">
            {KIND_LABEL[selected.kind]}: {nameOf(selected.from)} {ARROW[selected.kind]} {nameOf(selected.to)}
          </span>
          <button className="chip" onClick={() => void flip(selected)}>
            {selected.kind === "leads_to" ? "Make exclusion" : "Make unlock"}
          </button>
          <button className="chip" onClick={() => void remove(selected)}>Delete link</button>
          <button className="chip" onClick={() => setPicked(null)}>Done</button>
        </div>
      )}
      {ready && greetings.length === 0 ? (
        <div className="editor-empty">No greetings to map yet.</div>
      ) : greetings.length > 0 ? (
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
              const d = pathOf(a, b);
              return (
                <g key={line.key} role="button" tabIndex={0}
                   className={`pm-edge ${line.kind}` + (picked === line.key ? " picked" : "")}
                   aria-label={`${KIND_LABEL[line.kind]}: ${a.name} ${ARROW[line.kind]} ${b.name}`}
                   onClick={() => { setLinking(null); setPicked(line.key); }}
                   onKeyDown={(e) => {
                     if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setPicked(line.key); }
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
