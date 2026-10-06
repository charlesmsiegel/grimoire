/** The selected node's detail (capstone §19.6): the list/detail pattern's
 *  read-only `.detail-view`, below the drawing pane.
 *
 *  Main holds the node's own facts; the sidebar holds its actions and, in
 *  `.side-section` blocks, every record it touches. Every edge in the drawing
 *  is restated here from at least one end (plan Decision 26), so nothing the
 *  `aria-hidden` edge layer shows is lost to a reader who cannot see it. A
 *  record named here is a `chip` button that selects it; a plain attribute is
 *  a `chip on` span or hint text. No line shows a ref, an underscore token or
 *  a scene filename: refs are named through the node they point at, and one
 *  that points at no node is named by what it was (§19.6, Decision 11).
 *
 *  The two chooser actions send only what the chooser accepts (§16.5): Focus
 *  is offered on a canonical record and enabled iff the chooser lists it as a
 *  driver (`focusable`); Anchor is enabled iff it lists the moment as an
 *  anchor (`anchorable`). A disabled action says why beneath it, never only
 *  in a `title`. */
import { forwardRef, useId, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import type {
  AnchorRelation, EdgeKind, GraphEdge, GraphFinding, GraphNode, NodePressure, StoryGraph,
} from "../../api/types";
import { GROUP_OF, ledgerHref } from "../../ledgerPaths";
import { encodeSegment } from "../../urlSegment";
import { whenPhrase } from "../pressureControls";
import {
  FINDING_PHRASE, RELATION_PHRASE, STATE_WORDS, anchorNoun, ledgerTarget, seedFor, splitRef,
  statusOf, type GraphIndex,
} from "./model";

/** The verb a movement edge is read with, in the order a record moves. */
const MOVED: Partial<Record<EdgeKind, string>> = {
  opened_in: "opened", advanced_in: "advanced", touched_in: "touched", closed_in: "closed",
  resolved_in: "resolved",
};
const MOVE_ORDER = Object.keys(MOVED);

/** An idea anchored to this moment, read from the moment's side. */
const ANCHORED_HERE: Record<AnchorRelation, string> = {
  before: "Scene idea before this:", on: "Scene idea on this day:",
  after: "Scene idea after this:", by: "Scene idea due by this:",
};

const FOCUS_HINT = "Only an open thread or an unresolved commitment can steer the next scene.";
const ANCHOR_HINT = "Only an upcoming dated moment can anchor the next scene.";

type Props = {
  cid: string;
  graph: StoryGraph;
  ix: GraphIndex;
  node: GraphNode;
  /** The canonical arc the page is filtered to, or null. */
  arc: string | null;
  onSelect: (id: string) => void;
  onArc: (arc: string | null) => void;
};

type Fact = [term: string, value: string];

/** A reading's words: its state, then when only when it is dated, then the
 *  day only when one was named -- a stale or undated reading carries neither,
 *  and "undated" here would read as a missing date (B Decision 10). */
function pressureLine(p: NodePressure): string {
  const parts = [STATE_WORDS[p.state]];
  if (p.in_days !== null) parts.push(whenPhrase(p.in_days));
  if (p.friendly) parts.push(p.friendly);
  return parts.join(", ");
}

/** A day as the calendar names it, else as stored, else `none`. */
function dayOf(n: { native: string; friendly: string }, none: string): string {
  return n.friendly || n.native || none;
}

function nonEmpty(facts: (Fact | null)[]): Fact[] {
  return facts.filter((f): f is Fact => f !== null && f[1] !== "");
}

export const NodeDetail = forwardRef<HTMLElement, Props>(function NodeDetail(
  { cid, graph, ix, node, arc, onSelect, onArc }, ref,
) {
  const navigate = useNavigate();
  const hintId = useId();
  const outOf = (kind: EdgeKind) => (ix.out.get(node.id) ?? []).filter((e) => e.kind === kind);
  const intoOf = (kind: EdgeKind) => (ix.into.get(node.id) ?? []).filter((e) => e.kind === kind);
  const order = (id: string) => {
    const n = ix.byId.get(id);
    return n?.kind === "scene" ? n.order : Number.MAX_SAFE_INTEGER;
  };

  const chip = (id: string, text?: string) => {
    const target = ix.byId.get(id);
    if (!target) return null;
    return (
      <button type="button" className="chip" onClick={() => onSelect(id)}>
        {text ?? target.label}
      </button>
    );
  };

  const side = (heading: string, items: { key: string; body: ReactNode }[]) =>
    items.length === 0 ? null : (
      <div className="side-section" key={heading}>
        <h4>{heading}</h4>
        <ul className="sg-list">
          {items.map((it) => <li key={it.key}>{it.body}</li>)}
        </ul>
      </div>
    );

  /** Edges whose far end is a node, as `{key, body}` rows. */
  const rows = (edges: GraphEdge[], far: (e: GraphEdge) => string,
                body: (e: GraphEdge, other: string) => ReactNode) =>
    edges.filter((e) => ix.byId.has(far(e)))
      .map((e) => ({ key: e.id, body: body(e, far(e)) }));

  const scenesOf = (edges: GraphEdge[], far: (e: GraphEdge) => string) =>
    rows([...edges].sort((a, b) => order(far(a)) - order(far(b))), far,
         (_e, other) => chip(other));

  const links = () => [
    ...rows(outOf("link"), (e) => e.to, (e, other) => (
      <>{e.kind === "link" ? RELATION_PHRASE[e.relation].out : ""} {chip(other)}</>)),
    ...rows(intoOf("link"), (e) => e.from, (e, other) => (
      <>{e.kind === "link" ? RELATION_PHRASE[e.relation].in : ""} {chip(other)}</>)),
  ];

  const findings = (list: GraphFinding[]) => list.map((f) => ({
    key: f.id,
    body: (
      <>
        {FINDING_PHRASE[f.kind]}
        {f.other !== null && ix.byId.has(f.other) && <> with {chip(f.other)}</>}
        {" "}
        <Link to={ledgerHref(cid, { section: "continuity", group: GROUP_OF[f.kind],
                                    candidate: f.id })}>
          Review this finding
        </Link>
      </>
    ),
  }));

  /** A disabled action's reason sits beneath it, where it is read. */
  const action = (label: string, enabled: boolean, hint: string, run: () => void) => (
    <>
      <button type="button" disabled={!enabled} onClick={run}
              aria-describedby={enabled ? undefined : hintId}>
        {label}
      </button>
      {!enabled && <p className="field-hint" id={hintId}>{hint}</p>}
    </>
  );

  let facts: Fact[] = [];
  let lead: ReactNode = null;
  let actions: ReactNode = null;
  let sections: ReactNode[] = [];

  switch (node.kind) {
    case "thread":
    case "commitment": {
      const canon = node.merged_into ?? node.id;
      const target = ledgerTarget(node);
      facts = nonEmpty([
        ["Status", node.status],
        node.kind === "commitment" ? ["Kind", node.commitment_kind] : null,
        node.kind === "commitment" ? ["Due", node.due] : null,
        ["Latest beat", node.latest_beat],
        node.pressure ? ["Pressure", pressureLine(node.pressure)] : null,
      ]);
      if (node.merged_into !== null) {
        lead = (
          <p className="chips">
            {chip(node.merged_into, `Merged into ${ix.byId.get(node.merged_into)?.label ?? ""}`)
              ?? <span className="field-hint">Merged into another record.</span>}
          </p>
        );
      }
      const moved = (ix.out.get(node.id) ?? [])
        .filter((e) => MOVED[e.kind] !== undefined)
        .sort((a, b) => order(a.to) - order(b.to)
          || MOVE_ORDER.indexOf(a.kind) - MOVE_ORDER.indexOf(b.kind));
      sections = [
        side("Merged", node.aliases.map((a) => ({
          key: a.ref,
          body: chip(a.ref) ?? <span className="chip on">{a.title}</span>,
        }))),
        side("Scenes", rows(moved, (e) => e.to, (e, other) => (
          <>{MOVED[e.kind]} {chip(other)}</>))),
        side("Involves", rows(outOf("involves"), (e) => e.to, (_e, other) => chip(other))),
        side("Links", links()),
        side("Findings", findings(node.findings)),
      ];
      actions = (
        <>
          {node.merged_into === null && action(
            "Focus next scene", node.focusable, FOCUS_HINT,
            () => navigate(`/campaigns/${encodeSegment(cid)}/scenes`,
                           { state: seedFor(node, "focus") }))}
          {target && <Link className="btn-outline" to={ledgerHref(cid, target)}>
            Open ledger entry
          </Link>}
          <button type="button" onClick={() => onArc(arc === canon ? null : canon)}>
            {arc === canon ? "Show the whole graph" : "Filter to this arc"}
          </button>
        </>
      );
      break;
    }
    case "event":
    case "holiday":
    case "birthday": {
      const precision = node.kind === "birthday" ? node.precision : undefined;
      const whose = node.kind === "birthday" ? ix.byId.get(node.actor)?.label ?? "" : "";
      facts = nonEmpty([
        ["Date", dayOf(node, "Undated")],
        // An undated reading says so through Date already; "undated" twice
        // would read as two missing things.
        node.in_days !== null || precision === "month"
          ? ["When", whenPhrase(node.in_days, precision)] : null,
        node.kind === "event" ? ["Status", node.status] : null,
        ["Whose", whose],
      ]);
      sections = [
        side("Linked", [
          ...links(),
          ...rows(intoOf("anchored_to"), (e) => e.from, (e, other) => (
            <>{e.kind === "anchored_to" ? ANCHORED_HERE[e.relation] : ""} {chip(other)}</>)),
          ...rows(intoOf("serves"), (e) => e.from, (_e, other) => (
            <>Served by {chip(other)}</>)),
          ...rows(outOf("birthday_of"), (e) => e.to, (_e, other) => (
            <>Birthday of {chip(other)}</>)),
        ]),
        node.kind === "event" ? side("Findings", findings(node.findings)) : null,
      ];
      actions = action(
        "Anchor next scene", node.anchorable, ANCHOR_HINT,
        () => navigate(`/campaigns/${encodeSegment(cid)}/scenes`,
                       { state: seedFor(node, "anchor") }));
      break;
    }
    case "character":
    case "pc": {
      const birthdays = graph.nodes.filter((n) => n.kind === "birthday" && n.actor === node.id);
      const relation = (e: GraphEdge, other: string): ReactNode => {
        if (e.kind === "feeling") {
          return (
            <>
              {e.from === node.id ? "Feels toward" : "Felt by"} {chip(other)}{" "}
              <span className="chip on">trust {e.trust}</span>{" "}
              <span className="chip on">affection {e.affection}</span>{" "}
              <span className="chip on">tension {e.tension}</span>
              {e.note && <span className="field-hint"> {e.note}</span>}
            </>
          );
        }
        if (e.kind !== "bond") return null;
        const since = e.since_scene ? `scene:${e.since_scene}` : null;
        return (
          <>
            {chip(other)} <span className="chip on">{e.bond_type.replace(/_/g, " ")}</span>
            {since !== null && (ix.byId.has(since)
              ? <> since {chip(since)}</>
              : <span className="field-hint"> since a deleted scene</span>)}
          </>
        );
      };
      const both = (kind: EdgeKind) => [
        ...rows(outOf(kind), (e) => e.to, relation),
        ...rows(intoOf(kind), (e) => e.from, relation),
      ];
      sections = [
        side("Scenes", scenesOf(outOf("appeared_in"), (e) => e.to)),
        side("Active drivers", rows(
          intoOf("involves").filter((e) => {
            const from = ix.byId.get(e.from);
            return (from?.kind === "thread" || from?.kind === "commitment") && from.focusable;
          }),
          (e) => e.from, (_e, other) => chip(other))),
        side("Relationships", [...both("feeling"), ...both("bond")]),
        side("Birthday", birthdays.map((b) => ({
          key: b.id,
          body: <>{chip(b.id)} <span className="field-hint">{statusOf(b, ix)}</span></>,
        }))),
      ];
      break;
    }
    case "location":
      sections = [side("Scenes", scenesOf(intoOf("occurred_at"), (e) => e.from))];
      break;
    case "scene": {
      const where = rows(outOf("occurred_at"), (e) => e.to, (_e, other) => chip(other));
      if (where.length === 0 && node.place) {
        where.push({ key: "place", body: <span className="chip on">{node.place}</span> });
      }
      sections = [
        side("Cast", rows(intoOf("appeared_in"), (e) => e.from, (_e, other) => chip(other))),
        side("Where", where),
        side("When", [{ key: "when", body: dayOf(node, "Undated") }]),
        side("Moved here", rows(
          (ix.into.get(node.id) ?? []).filter((e) => MOVED[e.kind] !== undefined),
          (e) => e.from, (e, other) => <>{MOVED[e.kind]} {chip(other)}</>)),
      ];
      actions = (
        <Link className="btn-outline"
              to={`/campaigns/${encodeSegment(cid)}/scenes/${encodeSegment(splitRef(node.id)[1])}`}>
          Open scene
        </Link>
      );
      break;
    }
    case "idea": {
      const anchoredTo = rows(outOf("anchored_to"), (e) => e.to, (e, other) => (
        <>{e.kind === "anchored_to" ? RELATION_PHRASE[e.relation].out : ""} {chip(other)}</>));
      const anchor = node.time_anchor;
      if (anchoredTo.length === 0 && anchor) {
        // The moment has no node (past, or beyond pressure's horizon): say
        // what it was, never its ref.
        anchoredTo.push({
          key: "anchor",
          body: `${RELATION_PHRASE[anchor.relation].out} ${anchorNoun(anchor.ref)}, ${
            anchor.native || "no longer upcoming"}`,
        });
      }
      facts = nonEmpty([
        ["Premise", node.premise],
        ["Date", dayOf(node, "Unscheduled")],
        node.in_days !== null ? ["When", whenPhrase(node.in_days)] : null,
      ]);
      sections = [
        side("Serves", rows(outOf("serves"), (e) => e.to, (_e, other) => {
          const target = ix.byId.get(other);
          return (
            <>{chip(other)} {target && <span className="field-hint">{statusOf(target, ix)}</span>}</>
          );
        })),
        side("Anchored to", anchoredTo),
      ];
      break;
    }
  }

  return (
    <section className="sg-detail" aria-label={`Selected: ${node.label}`} ref={ref}>
      <div className="detail-view">
        <div className="detail-main">
          <h3>{node.label}</h3>
          {lead}
          {facts.length > 0 && (
            <dl className="sg-facts">
              {facts.map(([term, value]) => (
                <div key={term}><dt>{term}</dt><dd>{value}</dd></div>
              ))}
            </dl>
          )}
        </div>
        <aside className="detail-sidebar" aria-label="Node actions">
          {actions && <div className="form-actions">{actions}</div>}
          {sections}
        </aside>
      </div>
    </section>
  );
});
