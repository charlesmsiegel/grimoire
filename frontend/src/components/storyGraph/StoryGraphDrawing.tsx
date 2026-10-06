/** The Story Graph's drawing (capstone §19.4–§19.5): absolutely placed node
 *  buttons over one SVG edge layer, with the column heads and the Now marker
 *  laid over the canvas.
 *
 *  Every node is a `<button>` named by `accessibleName`, so the drawing is
 *  usable by keyboard and screen reader without the edges. The edge layer is
 *  `aria-hidden` and carries no interaction: every edge is restated in the
 *  detail of the node at either end (plan Decision 26). Nothing here uses a
 *  `title` -- no information lives only on hover.
 *
 *  Positions come from `layout.ts` and nothing is measured here, so a jsdom
 *  test reads exactly what a browser draws. */
import type { GraphEdge, GraphNode } from "../../api/types";
import { accessibleName, statusOf, type GraphIndex } from "./model";
import { COL, NODE_W, PAD, edgePath, type Layout } from "./layout";

export function StoryGraphDrawing({ ix, view, layout, selected, onSelect }: {
  ix: GraphIndex;
  view: { nodes: GraphNode[]; edges: GraphEdge[] };
  layout: Layout;
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const at = new Map(layout.nodes.map((p) => [p.id, p] as const));
  const { width, height } = layout;
  return (
    <div className="sg-canvas" style={{ width, height }}>
      <svg className="sg-edges" aria-hidden="true" width={width} height={height}>
        {view.edges.map((e) => {
          const a = at.get(e.from);
          const b = at.get(e.to);
          return a && b
            ? <path className={`sg-edge ${e.source} ${e.kind}`} d={edgePath(a, b)} key={e.id} />
            : null;
        })}
      </svg>
      {/* The Now column holds only the marker, which names itself. */}
      {layout.columns.filter((c) => c.kind !== "now").map((c) => (
        <div className="sg-col-head" key={`${c.kind}:${c.index}`}
             style={{ left: PAD + c.index * COL, width: NODE_W }}>{c.head}</div>
      ))}
      {layout.nowX !== null && (
        <div role="separator" aria-label="Now" className="sg-now"
             style={{ left: layout.nowX, height }}>Now</div>
      )}
      {view.nodes.map((n) => {
        const p = at.get(n.id);
        if (!p) return null;
        const on = selected === n.id;
        return (
          <button type="button" className={`sg-node ${n.kind}${on ? " selected" : ""}`}
                  aria-pressed={on} aria-label={accessibleName(n, ix)}
                  style={{ left: p.x, top: p.y, width: NODE_W }}
                  onClick={() => onSelect(n.id)} key={n.id}>
            <span className="sg-node-label">{n.label}</span>
            <span className="sg-node-status">{statusOf(n, ix)}</span>
          </button>
        );
      })}
    </div>
  );
}

export default StoryGraphDrawing;
