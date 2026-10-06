/** A hand-built Story Graph payload (`GET /continuity/graph`), shared by the
 *  graph's model, layout and page suites so each reads one campaign rather
 *  than re-describing it. Ids below are pinned: tests name them directly.
 *
 *  The present is day 738998. Scene 001 is dated after scene 002 (002 is a
 *  flashback), so a test can tell play order from calendar order. Every
 *  family is present: a merged thread, a closed thread whose id holds a `/`
 *  and a `:`, a commitment with a deadline and one without, a scheduled, a
 *  fired and an undated event, a holiday whose id holds a space, a
 *  month-precision birthday, a dated and an undated idea, one pair finding
 *  (an edge, and on both ends) and one lifecycle finding (on its node only).
 *
 *  Nodes and edges are in the payload's order (`graph._node_key`/`_edge_key`).
 *  A function, so a test may alter its copy without reaching the next one. */
import type { GraphEdge, GraphNode, NodePressure, StoryGraph } from "../api/types";

export const NOW_FIXED = 738998;

const OK: NodePressure = { state: "ok", in_days: null, friendly: "" };
const undated = { native: "", friendly: "", fixed: null, in_days: null };

function dated(fixed: number, native: string, friendly: string) {
  return { native, friendly, fixed, in_days: fixed - NOW_FIXED };
}

function edge(id: string, kind: Exclude<GraphEdge["kind"],
                "link" | "serves" | "anchored_to" | "feeling" | "bond">,
              from: string, to: string): GraphEdge {
  return { id, kind, from, to, source: "structural", relation: null, candidate_id: null };
}

export function graphFixture(): StoryGraph {
  const nodes: GraphNode[] = [
    { id: "scene:001--saltmarch-harbour", kind: "scene", label: "Saltmarch harbour", order: 0,
      done: true, pcless: false, place: "Saltmarch harbour",
      ...dated(738990, "1200-03-02", "2 March 1200") },
    { id: "scene:002--mara-s-flashback", kind: "scene", label: "Mara's flashback", order: 1,
      done: true, pcless: false, place: "",
      ...dated(738980, "1200-02-20", "20 February 1200") },
    { id: "scene:003--winifred-s-chart", kind: "scene", label: "Winifred's chart", order: 2,
      done: false, pcless: false, place: "",
      ...dated(738995, "1200-03-07", "7 March 1200") },
    { id: "characters:mara", kind: "character", label: "Mara" },
    { id: "characters:winifred", kind: "character", label: "Winifred" },
    { id: "pcs:seraphine", kind: "pc", label: "Seraphine" },
    { id: "locations:saltmarch-harbour", kind: "location", label: "Saltmarch harbour" },
    { id: "thread:mara-s-map", kind: "thread", label: "Mara's map", status: "open", live: true,
      merged_into: null,
      aliases: [{ ref: "thread:winifred-s-chart", title: "Winifred's chart", status: "open" }],
      latest_beat: "Mara unrolled the map on the harbour wall.", pressure: OK,
      focusable: true,
      findings: [
        { id: "cand-map-oath", kind: "possible_relation", other: "commitment:mara-s-oath" },
        { id: "cand-map-closure", kind: "possible_thread_closure", other: null },
      ] },
    { id: "thread:mara/s:map", kind: "thread", label: "Mara's first map", status: "closed",
      live: false, merged_into: null, aliases: [], latest_beat: "The first map burned.",
      pressure: null, focusable: false, findings: [] },
    { id: "thread:saltmarch-toll", kind: "thread", label: "The Saltmarch toll",
      status: "open", live: true, merged_into: null, aliases: [],
      latest_beat: "The harbourmaster raised the toll.", pressure: OK, focusable: true,
      findings: [] },
    { id: "thread:winifred-s-chart", kind: "thread", label: "Winifred's chart",
      status: "open", live: true, merged_into: "thread:mara-s-map", aliases: [],
      latest_beat: "", pressure: null, focusable: false, findings: [] },
    { id: "commitment:mara-s-oath", kind: "commitment", label: "Mara's oath",
      commitment_kind: "promise", due: "before Saltmarch Eve is out", status: "open",
      live: true, merged_into: null, aliases: [],
      latest_beat: "Mara swore on the harbour stones.",
      pressure: { state: "due_soon", in_days: 4, friendly: "14 March 1200" },
      focusable: true,
      findings: [
        { id: "cand-map-oath", kind: "possible_relation", other: "thread:mara-s-map" },
      ],
      ...dated(739002, "1200-03-14", "14 March 1200") },
    { id: "commitment:winifred-s-debt", kind: "commitment", label: "Winifred's debt",
      commitment_kind: "debt", due: "", status: "open", live: true, merged_into: null,
      aliases: [], latest_beat: "Winifred owes the harbour a debt.", pressure: OK,
      focusable: true, findings: [], ...undated },
    { id: "event:saltmarch-fair", kind: "event", label: "The Saltmarch fair",
      status: "undated", pressure: OK, anchorable: false, findings: [],
      native: "midsummer", friendly: "", fixed: null, in_days: null },
    { id: "event:the-coronation", kind: "event", label: "The coronation",
      status: "scheduled", pressure: { state: "upcoming", in_days: 3, friendly: "13 March 1200" },
      anchorable: true, findings: [], ...dated(739001, "1200-03-13", "13 March 1200") },
    { id: "event:the-harbour-bell", kind: "event", label: "The harbour bell", status: "fired",
      pressure: null, anchorable: false, findings: [],
      ...dated(738992, "1200-03-04", "4 March 1200") },
    { id: "idea:the-harbour-meeting", kind: "idea", label: "The harbour meeting",
      premise: "Mara meets Seraphine at the harbour before the coronation.", source: "llm",
      pcless: false,
      time_anchor: { ref: "event:the-coronation", relation: "before", native: "1200-03-13" },
      ...dated(738999, "1200-03-11", "11 March 1200") },
    { id: "idea:winifred-s-letter", kind: "idea", label: "Winifred's letter",
      premise: "A letter from Winifred reaches the harbour.", source: "user", pcless: true,
      time_anchor: null, ...undated },
    { id: "birthday:characters:mara:month:1200-3", kind: "birthday", label: "Mara's birthday",
      actor: "characters:mara", precision: "month", age: null,
      pressure: { state: "upcoming", in_days: null, friendly: "" }, anchorable: true,
      ...undated },
    { id: "holiday:739000:Saltmarch Eve", kind: "holiday", label: "Saltmarch Eve",
      pressure: { state: "upcoming", in_days: 2, friendly: "12 March 1200" },
      anchorable: true, ...dated(739000, "1200-03-12", "12 March 1200") },
  ];
  const edges: GraphEdge[] = [
    edge("e-mara-001", "appeared_in", "characters:mara", "scene:001--saltmarch-harbour"),
    edge("e-mara-002", "appeared_in", "characters:mara", "scene:002--mara-s-flashback"),
    edge("e-winifred-003", "appeared_in", "characters:winifred",
         "scene:003--winifred-s-chart"),
    edge("e-seraphine-001", "appeared_in", "pcs:seraphine", "scene:001--saltmarch-harbour"),
    edge("e-001-harbour", "occurred_at", "scene:001--saltmarch-harbour",
         "locations:saltmarch-harbour"),
    edge("e-oath-opened", "opened_in", "commitment:mara-s-oath",
         "scene:002--mara-s-flashback"),
    edge("e-map-opened", "opened_in", "thread:mara-s-map", "scene:001--saltmarch-harbour"),
    edge("e-old-opened", "opened_in", "thread:mara/s:map", "scene:001--saltmarch-harbour"),
    edge("e-toll-opened", "opened_in", "thread:saltmarch-toll", "scene:003--winifred-s-chart"),
    edge("e-map-advanced", "advanced_in", "thread:mara-s-map", "scene:003--winifred-s-chart"),
    edge("e-old-closed", "closed_in", "thread:mara/s:map", "scene:002--mara-s-flashback"),
    edge("e-map-mara", "involves", "thread:mara-s-map", "characters:mara"),
    edge("e-map-seraphine", "involves", "thread:mara-s-map", "pcs:seraphine"),
    edge("e-oath-mara", "involves", "commitment:mara-s-oath", "characters:mara"),
    { id: "e-meeting-map", kind: "serves", from: "idea:the-harbour-meeting",
      to: "thread:mara-s-map", source: "structural", relation: "advance", candidate_id: null },
    { id: "e-meeting-coronation", kind: "anchored_to", from: "idea:the-harbour-meeting",
      to: "event:the-coronation", source: "structural", relation: "before",
      candidate_id: null },
    { id: "e-mara-seraphine", kind: "feeling", from: "characters:mara", to: "pcs:seraphine",
      source: "structural", relation: null, candidate_id: null, trust: 3, affection: 4,
      tension: 1, note: "Owes her a map." },
    { id: "e-winifred-mara", kind: "bond", from: "characters:winifred",
      to: "characters:mara", source: "structural", relation: null, candidate_id: null,
      bond_type: "rival", since_scene: "002--mara-s-flashback" },
    edge("e-birthday-mara", "birthday_of", "birthday:characters:mara:month:1200-3",
         "characters:mara"),
    { id: "lnk-map-continues", kind: "link", from: "thread:mara-s-map",
      to: "thread:mara/s:map", source: "reviewed", relation: "continues",
      candidate_id: null },
    { id: "lnk-oath-by", kind: "link", from: "commitment:mara-s-oath",
      to: "event:the-coronation", source: "reviewed", relation: "by", candidate_id: null },
    { id: "e-chart-merged", kind: "merged_into", from: "thread:winifred-s-chart",
      to: "thread:mara-s-map", source: "alias", relation: null, candidate_id: null },
    { id: "cand-map-oath", kind: "possible_relation", from: "thread:mara-s-map",
      to: "commitment:mara-s-oath", source: "candidate", relation: null,
      candidate_id: "cand-map-oath" },
  ];
  return {
    now: { native: "1200-03-10", friendly: "10 March 1200", fixed: NOW_FIXED },
    nodes, edges, omitted: [],
  };
}

/** The same campaign with no present: no clock, so no `in_days` anywhere and
 *  no holiday or birthday (both are computed against the present). A dated
 *  record keeps its `fixed`, `native` and `friendly`. */
export function noPresentFixture(): StoryGraph {
  const g = graphFixture();
  g.now = { native: "", friendly: "", fixed: null };
  g.nodes = g.nodes
    .filter((n) => n.kind !== "holiday" && n.kind !== "birthday")
    .map((n): GraphNode => ("in_days" in n ? { ...n, in_days: null } : n));
  return g;
}
