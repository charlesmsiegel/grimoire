/** The campaign's Story Graph (capstone §19): one read of
 *  `GET /continuity/graph`, drawn through four client-side lenses.
 *
 *  The page is a screen of its own, so its records sit in the context column
 *  (lens, show toggles, arcs) and the drawing and the selected node's detail
 *  in main -- the detail below the drawing pane and outside it, so a phone
 *  reader picking a record lands on it rather than inside a sideways scroll.
 *
 *  **One read** (§28.9). The read depends on the campaign alone: a lens, a
 *  toggle, an arc filter or a selection is arithmetic over the payload the
 *  page already holds, never a request. The campaign's name comes from what
 *  the rail already holds (`demand: false`), so not even the shell is asked
 *  again.
 *
 *  **The address** (§19.1) holds only `?lens=`, `?arc=` and `?node=`, each
 *  read defensively: anything that names no lens, no canonical arc or no node
 *  reads as the default rather than as an error. A lens or arc change pushes
 *  history (a view the reader may go Back to); a selection replaces it (twenty
 *  taps should not take twenty Backs). The Show toggles are local state, and
 *  each lens brings its own defaults back when it is chosen.
 *
 *  **A campaign switch** keeps this page mounted -- the route is not keyed on
 *  `cid` -- so the read is held beside the campaign it answered, and only a
 *  read for the campaign in the address is drawn. */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { StoryGraph } from "../api/types";
import { ColumnSection, PageShell } from "../components/PageShell";
import { usePublishShellContext } from "../components/ShellStatus";
import { StoryGraphDrawing } from "../components/storyGraph/StoryGraphDrawing";
import { layout } from "../components/storyGraph/layout";
import {
  LENSES, LENS_LABELS, PART_LABELS, PRESETS, SHOW_LABELS, arcRows, indexGraph, isLens,
  statusOf, visible, type Lens, type Show,
} from "../components/storyGraph/model";
import { useCampaignShell } from "../shell/ShellPayloadContext";
import { useHotkeys } from "../shortcuts/useHotkeys";

type Loaded = { cid: string; graph: StoryGraph } | { cid: string; failed: true };

const SHOW_KEYS = Object.keys(SHOW_LABELS) as (keyof Show)[];

export default function StoryGraphView() {
  const { cid = "" } = useParams();
  const [params, setParams] = useSearchParams();

  // ---- the read, held with the campaign it answered ----
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let live = true;
    void api.continuityGraph(cid).then(
      (graph) => { if (live) setLoaded({ cid, graph }); },
      () => { if (live) setLoaded({ cid, failed: true }); },
    );
    return () => { live = false; };
  }, [cid, attempt]);
  const current = loaded?.cid === cid ? loaded : null;
  const graph = current && "graph" in current ? current.graph : null;
  const ix = useMemo(() => (graph ? indexGraph(graph) : null), [graph]);

  // ---- the address ----
  const lensParam = params.get("lens");
  const lens: Lens = isLens(lensParam) ? lensParam : "story";
  const nodeParam = params.get("node");
  const node = nodeParam !== null && ix?.byId.has(nodeParam) ? nodeParam : null;
  const arcParam = params.get("arc");
  const arcNode = arcParam !== null ? ix?.byId.get(arcParam) : undefined;
  const arc = arcNode && (arcNode.kind === "thread" || arcNode.kind === "commitment")
    && arcNode.merged_into === null ? arcNode.id : null;

  const setQuery = useCallback((next: Record<string, string | null>, replace: boolean) => {
    setParams((prev) => {
      const q = new URLSearchParams(prev);
      for (const [k, v] of Object.entries(next)) {
        if (v === null) q.delete(k);
        else q.set(k, v);
      }
      return q;
    }, { replace });
  }, [setParams]);

  // ---- the Show toggles: local, and reset to the lens's own on a lens change ----
  const [showState, setShowState] = useState<{ lens: Lens; show: Show } | null>(null);
  const show = showState?.lens === lens ? showState.show : PRESETS[lens].show;
  const [seenLens, setSeenLens] = useState(lens);
  if (seenLens !== lens) {
    // Adjusted during render rather than in an effect, so no frame draws the
    // new lens with the old lens's toggles.
    setSeenLens(lens);
    setShowState(null);
  }
  const toggle = (k: keyof Show) => setShowState({ lens, show: { ...show, [k]: !show[k] } });

  // ---- the chrome ----
  const name = useCampaignShell(cid, { demand: false }).payload?.campaign?.name ?? null;
  usePublishShellContext(name ? { campaign: name, scene: "" } : null);

  // Every pick bumps this, so re-tapping the selected arc still closes the
  // phone column (plan Decision 22); a lens or toggle does not, and keeps it up.
  const [pick, setPick] = useState(0);
  const select = (id: string) => {
    setPick((p) => p + 1);
    setQuery({ node: id }, true);
  };

  useHotkeys([
    ...LENSES.map((l, i) => ({
      keys: String(i + 1), label: `${LENS_LABELS[l]} lens`, group: "STORY GRAPH",
      run: () => setQuery({ lens: l }, false),
    })),
    {
      keys: "escape", label: "Clear the selection", group: "STORY GRAPH", enabled: !!node,
      run: () => setQuery({ node: null }, true),
    },
  ]);

  const detailRef = useRef<HTMLElement>(null);
  useEffect(() => {
    if (node) detailRef.current?.scrollIntoView?.({ block: "nearest" });
  }, [node]);

  const view = useMemo(
    () => (graph && ix ? visible(graph, ix, lens, show, arc) : null),
    [graph, ix, lens, show, arc]);
  const placed = useMemo(
    () => (graph && ix && view ? layout(graph, ix, view, PRESETS[lens].axis) : null),
    [graph, ix, view, lens]);
  const arcs = useMemo(() => (graph ? arcRows(graph) : []), [graph]);
  const selected = node && ix ? ix.byId.get(node) ?? null : null;

  const column = (
    <>
      <Link className="column-back" to={`/campaigns/${cid}`}>‹ {name ?? "Campaign"}</Link>
      <ColumnSection label="Lens">
        {LENSES.map((l) => (
          <button type="button" key={l} aria-pressed={lens === l}
                  className={"column-row" + (lens === l ? " active" : "")}
                  onClick={() => setQuery({ lens: l }, false)}>
            <span className="column-row-label">{LENS_LABELS[l]}</span>
          </button>
        ))}
      </ColumnSection>
      <ColumnSection label="Show">
        {SHOW_KEYS.map((k) => (
          <label key={k} className="column-row sg-toggle">
            <input type="checkbox" checked={show[k]} onChange={() => toggle(k)} />
            <span className="column-row-label">{SHOW_LABELS[k]}</span>
          </label>
        ))}
      </ColumnSection>
      <ColumnSection label="Arcs" count={arcs.length}>
        {ix && arcs.map((n) => (
          <button type="button" key={n.id} aria-pressed={node === n.id}
                  className={"column-row" + (node === n.id ? " active" : "")}
                  onClick={() => select(n.id)}>
            <span className="column-row-label">{n.label}</span>
            <span className="column-row-count">{statusOf(n, ix)}</span>
          </button>
        ))}
      </ColumnSection>
    </>
  );

  let body;
  if (!current) {
    body = <p className="field-hint">Reading the story graph…</p>;
  } else if (!graph || !ix || !view || !placed) {
    body = (
      <div>
        <p>The story graph could not be read.</p>
        <button type="button" onClick={() => { setLoaded(null); setAttempt((a) => a + 1); }}>
          Retry
        </button>
      </div>
    );
  } else {
    const omitted = graph.omitted;
    body = (
      <>
        {omitted.length > 0 && (
          <p className="field-hint">
            {`Some records could not be read: ${omitted.map((p) => PART_LABELS[p]).join(", ")}.`}
          </p>
        )}
        {graph.nodes.length === 0 && omitted.length === 0 && (
          <p className="field-hint">
            Nothing to draw yet — play a scene and its threads appear here.
          </p>
        )}
        {graph.nodes.length > 0 && (
          <>
            {arc && (
              <p className="sg-filter">
                Filtered to {ix.byId.get(arc)?.label}
                <button type="button" onClick={() => setQuery({ arc: null }, false)}>
                  Show the whole graph
                </button>
              </p>
            )}
            <div className="sg-scroll" data-testid="story-graph-drawing">
              <StoryGraphDrawing ix={ix} view={view} layout={placed} selected={node}
                                 onSelect={select} />
            </div>
            {selected && (
              <section className="sg-detail" aria-label={`Selected: ${selected.label}`}
                       ref={detailRef}>
                <h3>{selected.label}</h3>
              </section>
            )}
          </>
        )}
      </>
    );
  }

  return (
    <PageShell column={column} columnLabel="Story graph filters"
               dismissKey={`${node ?? ""}#${pick}`}>
      <div className="page-wide view-anim">
        {name && <div className="eyebrow">{name}</div>}
        <h1 className="screen-title">Story graph</h1>
        {body}
      </div>
    </PageShell>
  );
}
