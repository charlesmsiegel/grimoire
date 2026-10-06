import type { EdgeKind, GraphNode, NodeKind, StoryGraph } from "../../api/types";
import { ledgerHref } from "../../ledgerPaths";
import { graphFixture, noPresentFixture, NOW_FIXED } from "../../testkit/storyGraph";
import { sanitizeSeed } from "../pressureControls";
import {
  accessibleName, arcNeighbourhood, arcRows, FINDING_PHRASE, indexGraph, isLens, KIND_NOUN,
  LENS_LABELS, LENSES, ledgerTarget, PART_LABELS, PRESETS, RELATION_PHRASE, seedFor,
  SHOW_LABELS, splitRef, STATE_WORDS, statusOf, visible, type Lens, type Show,
} from "./model";

const MAP = "thread:mara-s-map";
const CHART = "thread:winifred-s-chart";
const OLD = "thread:mara/s:map";
const TOLL = "thread:saltmarch-toll";
const OATH = "commitment:mara-s-oath";
const DEBT = "commitment:winifred-s-debt";
const CORONATION = "event:the-coronation";
const BELL = "event:the-harbour-bell";
const FAIR = "event:saltmarch-fair";
const EVE = "holiday:739000:Saltmarch Eve";
const BIRTHDAY = "birthday:characters:mara:month:1200-3";
const MEETING = "idea:the-harbour-meeting";
const LETTER = "idea:winifred-s-letter";
const S1 = "scene:001--saltmarch-harbour";
const S2 = "scene:002--mara-s-flashback";
const S3 = "scene:003--winifred-s-chart";
const MARA = "characters:mara";
const WINIFRED = "characters:winifred";
const SERAPHINE = "pcs:seraphine";
const HARBOUR = "locations:saltmarch-harbour";

const NONE: Show = { actors: false, locations: false, merged: false, candidates: false };
const ALL: Show = { actors: true, locations: true, merged: true, candidates: true };

function setup(g: StoryGraph = graphFixture()) {
  const ix = indexGraph(g);
  const view = (lens: Lens, show: Show = PRESETS[lens].show, arc: string | null = null) =>
    visible(g, ix, lens, show, arc);
  const ids = (lens: Lens, show?: Show, arc?: string | null) =>
    view(lens, show, arc).nodes.map((n) => n.id);
  const edgeIds = (lens: Lens, show?: Show, arc?: string | null) =>
    view(lens, show, arc).edges.map((e) => e.id);
  const node = (id: string) => {
    const n = ix.byId.get(id);
    if (!n) throw new Error(`no fixture node ${id}`);
    return n;
  };
  return { g, ix, view, ids, edgeIds, node };
}

describe("splitRef", () => {
  it("keeps everything after the first colon", () => {
    expect(splitRef("thread:mara/s:map")).toEqual(["thread", "mara/s:map"]);
    expect(splitRef("holiday:739000:Saltmarch Eve")).toEqual(["holiday", "739000:Saltmarch Eve"]);
    expect(splitRef("characters:mara")).toEqual(["characters", "mara"]);
    expect(splitRef("no-colon")).toEqual(["", "no-colon"]);
  });
});

describe("lens presets", () => {
  it("names four lenses, Story first, and labels each", () => {
    expect(LENSES).toEqual(["story", "cast", "calendar", "continuity"]);
    expect(LENSES.map((l) => LENS_LABELS[l])).toEqual(["Story", "Cast", "Calendar", "Continuity"]);
    expect(SHOW_LABELS).toEqual({
      actors: "Actors", locations: "Locations", merged: "Merged records",
      candidates: "Review candidates",
    });
    expect(LENSES.map((l) => PRESETS[l].axis)).toEqual(["play", "play", "calendar", "play"]);
    expect(isLens("cast")).toBe(true);
    expect(isLens("bogus")).toBe(false);
    expect(isLens(null)).toBe(false);
  });

  it("each lens preset shows its kinds", () => {
    const { view, ids, edgeIds } = setup();
    const kinds = (lens: Lens) => new Set(view(lens).nodes.map((n) => n.kind));
    expect(kinds("story").has("character")).toBe(false);
    expect(kinds("story").has("pc")).toBe(false);
    expect(kinds("cast").has("character")).toBe(true);
    expect(kinds("cast").has("pc")).toBe(true);
    expect(kinds("calendar").has("thread")).toBe(false);
    expect(ids("continuity")).toContain(CHART);
    expect(edgeIds("continuity")).toContain("cand-map-oath");
    expect([...kinds("story")].sort()).toEqual(
      ["birthday", "commitment", "event", "holiday", "idea", "scene", "thread"]);
    expect([...kinds("continuity")].sort()).toEqual(["commitment", "event", "thread"]);
    expect(PRESETS.story.show).toEqual(NONE);
    expect(PRESETS.cast.show).toEqual({ ...NONE, actors: true });
    expect(PRESETS.calendar.show).toEqual(NONE);
    expect(PRESETS.continuity.show).toEqual({ ...NONE, merged: true, candidates: true });
  });

  it("toggles add actors, locations, merged records and candidates", () => {
    const { ids, edgeIds } = setup();
    expect(ids("story", NONE)).not.toContain(MARA);
    expect(ids("story", { ...NONE, actors: true })).toEqual(
      expect.arrayContaining([MARA, WINIFRED, SERAPHINE]));
    expect(edgeIds("story", { ...NONE, actors: true })).toEqual(
      expect.arrayContaining(["e-mara-001", "e-map-mara", "e-mara-seraphine",
                              "e-winifred-mara", "e-birthday-mara"]));
    expect(ids("story", NONE)).not.toContain(HARBOUR);
    expect(ids("story", { ...NONE, locations: true })).toContain(HARBOUR);
    expect(edgeIds("story", { ...NONE, locations: true })).toContain("e-001-harbour");
    expect(ids("story", { ...NONE, merged: true })).toContain(CHART);
    expect(edgeIds("story", { ...NONE, merged: true })).toContain("e-chart-merged");
    expect(edgeIds("story", { ...NONE, candidates: true })).toContain("cand-map-oath");
  });

  it("Merged records adds merged nodes on every lens, Calendar included", () => {
    // A merged commitment as graph.py emits one: no deadline, so `native` is "".
    const g = graphFixture();
    const debt = g.nodes.find((n) => n.id === DEBT);
    if (debt?.kind !== "commitment") throw new Error("fixture");
    const vow = "commitment:winifred-s-vow";
    g.nodes.push({ ...debt, id: vow, label: "Winifred's vow", merged_into: OATH,
                   commitment_kind: "", due: "", latest_beat: "", pressure: null,
                   focusable: false });
    const { ids } = setup(g);
    for (const lens of LENSES) {
      const on = { ...PRESETS[lens].show, merged: true };
      const off = { ...PRESETS[lens].show, merged: false };
      expect([lens, ids(lens, on)]).toEqual([lens, expect.arrayContaining([CHART, vow])]);
      expect(ids(lens, off)).not.toContain(CHART);
      expect(ids(lens, off)).not.toContain(vow);
    }
    expect(ids("calendar", { ...NONE, merged: true })).toContain(CHART);
    expect(ids("calendar", NONE)).not.toContain(CHART);
  });

  it("unchecking Actors in Cast removes the actors", () => {
    const { view, ids } = setup();
    expect(PRESETS.cast.nodes).not.toContain("character");
    expect(PRESETS.cast.nodes).not.toContain("pc");
    expect(PRESETS.cast.edges).toEqual([]);
    expect(ids("cast")).toContain(MARA);
    const off = view("cast", { ...PRESETS.cast.show, actors: false });
    expect(off.nodes.some((n) => n.kind === "character" || n.kind === "pc")).toBe(false);
    expect(off.edges).toEqual([]);
  });

  it("the Calendar lens shows only commitments with a deadline", () => {
    const g = graphFixture();
    const oath = g.nodes.find((n) => n.id === OATH);
    if (oath?.kind !== "commitment") throw new Error("fixture");
    oath.fixed = null;
    oath.in_days = null;
    const { ids } = setup(g);
    expect(ids("calendar")).not.toContain(DEBT);
    expect(ids("calendar")).toContain(OATH);
    // The Story lens is not narrowed: every commitment is a story record there.
    expect(ids("story")).toEqual(expect.arrayContaining([OATH, DEBT]));
    // Ideas are not in the Calendar lens, so neither are their anchors.
    expect(ids("calendar")).not.toContain(MEETING);
  });

  it("every edge kind a preset lists can draw", () => {
    // What each edge kind may join, from its builder in store/continuity/graph.py.
    const ARCS: NodeKind[] = ["thread", "commitment"];
    const ACTORS: NodeKind[] = ["character", "pc"];
    const TEMPORAL: NodeKind[] = ["event", "holiday", "birthday"];
    const ENDS: Record<EdgeKind, [NodeKind[], NodeKind[]]> = {
      appeared_in: [ACTORS, ["scene"]],
      occurred_at: [["scene"], ["location"]],
      opened_in: [ARCS, ["scene"]],
      advanced_in: [["thread"], ["scene"]],
      touched_in: [["commitment"], ["scene"]],
      closed_in: [["thread"], ["scene"]],
      resolved_in: [["commitment"], ["scene"]],
      involves: [ARCS, ACTORS],
      serves: [["idea"], [...ARCS, ...TEMPORAL]],
      anchored_to: [["idea"], TEMPORAL],
      feeling: [ACTORS, ACTORS],
      bond: [ACTORS, ACTORS],
      birthday_of: [["birthday"], ACTORS],
      link: [[...ARCS, "event"], [...ARCS, "event"]],
      merged_into: [ARCS, ARCS],
      possible_duplicate: [ARCS, ARCS],
      possible_relation: [ARCS, [...ARCS, "event"]],
    };
    const toggled: NodeKind[] = ["character", "pc", "location"];
    for (const lens of LENSES) {
      const reach = new Set<NodeKind>([...PRESETS[lens].nodes, ...toggled]);
      for (const kind of PRESETS[lens].edges) {
        const [from, to] = ENDS[kind];
        expect([lens, kind, from.some((k) => reach.has(k)) && to.some((k) => reach.has(k))])
          .toEqual([lens, kind, true]);
      }
    }
  });

  it("merged records and candidate edges are hidden until shown", () => {
    const { ids, edgeIds } = setup();
    for (const lens of LENSES) {
      const off = { ...PRESETS[lens].show, merged: false, candidates: false };
      expect(ids(lens, off)).not.toContain(CHART);
      expect(edgeIds(lens, off)).not.toContain("e-chart-merged");
      expect(edgeIds(lens, off)).not.toContain("cand-map-oath");
    }
    // Continuity lists the candidate kinds, and still needs the toggle.
    expect(edgeIds("continuity", { ...NONE, merged: true })).not.toContain("cand-map-oath");
    expect(edgeIds("continuity", { ...NONE, candidates: true })).toContain("cand-map-oath");
  });

  it("the Story lens drops fired and passed events", () => {
    const g = graphFixture();
    const fair = g.nodes.find((n) => n.id === FAIR);
    if (fair?.kind !== "event") throw new Error("fixture");
    fair.status = "passed";
    const { ids } = setup(g);
    expect(ids("story")).toContain(CORONATION);
    expect(ids("story")).not.toContain(BELL);
    expect(ids("story")).not.toContain(FAIR);
    expect(ids("calendar")).toEqual(expect.arrayContaining([BELL, FAIR]));
    expect(ids("continuity")).toEqual(expect.arrayContaining([BELL, FAIR]));
  });

  it("an event fired today is still the present, so Story keeps it (§13.5 own day)", () => {
    // Reaching the day fires the event, and its driver still reads `today`:
    // the chooser lists it as a live driver and an anchor, so Story does too.
    const g = graphFixture();
    g.nodes = g.nodes.map((n) => (n.id === CORONATION && n.kind === "event"
      ? { ...n, status: "fired" as const, in_days: 0, fixed: NOW_FIXED,
          pressure: { state: "today" as const, in_days: 0, friendly: n.friendly } }
      : n));
    const { ids } = setup(g);
    expect(ids("story")).toContain(CORONATION);
    expect(ids("story")).not.toContain(BELL);
  });

  it("an edge shows only when both ends do", () => {
    const { view } = setup();
    for (const lens of LENSES) {
      for (const show of [NONE, ALL, PRESETS[lens].show]) {
        const v = view(lens, show);
        const shown = new Set(v.nodes.map((n) => n.id));
        for (const e of v.edges) {
          expect([e.id, shown.has(e.from), shown.has(e.to)]).toEqual([e.id, true, true]);
        }
      }
    }
    // The coronation is out of the Cast lens, so the oath's `by` link is too.
    expect(view("cast", ALL).edges.map((e) => e.id)).not.toContain("lnk-oath-by");
    // The serves edge needs the idea, which only the Story lens shows.
    expect(view("continuity", ALL).edges.map((e) => e.id)).not.toContain("e-meeting-map");
    expect(view("story").edges.map((e) => e.id)).toContain("e-meeting-map");
  });

  it("keeps payload order", () => {
    const { g, view } = setup();
    const v = view("story", ALL);
    const order = g.nodes.map((n) => n.id);
    expect(v.nodes.map((n) => n.id)).toEqual(order.filter((id) => v.nodes.some((n) => n.id === id)));
    const eorder = g.edges.map((e) => e.id);
    expect(v.edges.map((e) => e.id)).toEqual(eorder.filter((id) => v.edges.some((e) => e.id === id)));
  });
});

describe("the arc filter", () => {
  it("shows the arc, its merges, link neighbours, touched scenes and involved actors", () => {
    const { ix, ids } = setup();
    const expected = [S1, S3, MARA, SERAPHINE, MAP, OLD, CHART];
    expect([...arcNeighbourhood(ix, MAP)].sort()).toEqual([...expected].sort());
    for (const lens of LENSES) {
      // Merged is off, and the arc's own merge is still shown (Decision 20).
      expect(ids(lens, NONE, MAP)).toEqual(expected);
    }
    // Without an arc filter the merged node stays hidden.
    expect(ids("story", NONE)).not.toContain(CHART);
    expect(ids("story", NONE, MAP)).not.toContain(TOLL);
  });

  it("draws the arc's own edges, and candidates only with their toggle", () => {
    const { edgeIds } = setup();
    expect(edgeIds("story", NONE, MAP)).toEqual(expect.arrayContaining(
      ["e-chart-merged", "lnk-map-continues", "e-map-opened", "e-map-advanced", "e-map-mara"]));
    expect(edgeIds("story", NONE, MAP)).not.toContain("cand-map-oath");
  });

  it("names the link neighbours on either end", () => {
    const { ix } = setup();
    expect(arcNeighbourhood(ix, OATH).has(CORONATION)).toBe(true);
    expect(arcNeighbourhood(ix, OLD).has(MAP)).toBe(true);
    expect(arcNeighbourhood(ix, OLD).has(S2)).toBe(true);
  });

  it("an arc that names no node filters nothing", () => {
    const { ids } = setup();
    expect(arcNeighbourhood(indexGraph(graphFixture()), "thread:gone").size).toBe(0);
    expect(ids("story", NONE, "thread:gone")).toEqual(ids("story", NONE));
  });
});

describe("names", () => {
  it("statusOf and accessibleName include the label and the status", () => {
    const g = graphFixture();
    g.nodes.push({ id: "commitment:winifred-s-gift", kind: "commitment",
                   label: "Winifred's gift", commitment_kind: "promise", due: "",
                   status: "open", live: true, merged_into: "commitment:mara-s-oath",
                   aliases: [], latest_beat: "", pressure: null, focusable: false,
                   findings: [], native: "", friendly: "", fixed: null, in_days: null });
    const { ix, node } = setup(g);
    const cases: [string, string, string][] = [
      [S1, "scene", "absorbed"],
      [S3, "scene", "in play"],
      [MARA, "character", "2 scenes"],
      [WINIFRED, "character", "1 scene"],
      [SERAPHINE, "player character", "1 scene"],
      [HARBOUR, "location", "1 scene"],
      [MAP, "thread", "open, May be finished"],
      [OLD, "thread", "closed"],
      [CHART, "thread", "merged into Mara's map"],
      [OATH, "commitment", "open, due soon"],
      [DEBT, "commitment", "open"],
      ["commitment:winifred-s-gift", "commitment", "merged into Mara's oath"],
      [CORONATION, "event", "in 3 days"],
      [BELL, "event", "fired"],
      [FAIR, "event", "undated"],
      [MEETING, "scene idea", "in 1 day"],
      [LETTER, "scene idea", "unscheduled"],
      [BIRTHDAY, "birthday", "day unknown"],
      [EVE, "holiday", "in 2 days"],
    ];
    for (const [id, noun, status] of cases) {
      const n = node(id);
      expect([id, statusOf(n, ix)]).toEqual([id, status]);
      expect(accessibleName(n, ix)).toBe(`${n.label}, ${noun}, ${status}`);
      expect(KIND_NOUN[n.kind]).toBe(noun);
    }
    const kinds = new Set(cases.map(([id]) => node(id).kind));
    expect(kinds.size).toBe(Object.keys(KIND_NOUN).length);
  });

  it("a lifecycle finding is named on its node's status", () => {
    const { g, ix, node } = setup();
    expect(accessibleName(node(MAP), ix)).toContain("May be finished");
    expect(accessibleName(node(TOLL), ix)).not.toContain("May be finished");
    // The pair finding is an edge, and the detail restates it: neither end's
    // status names it.
    for (const id of [MAP, OATH]) {
      expect(statusOf(node(id), ix)).not.toContain(FINDING_PHRASE.possible_relation);
    }
    // statusOf takes no lens or toggle, so every caller sees the same line.
    expect(statusOf.length).toBe(2);
    for (const lens of LENSES) {
      for (const show of [NONE, ALL]) {
        const n = visible(g, ix, lens, show, null).nodes.find((v) => v.id === MAP);
        if (n) expect(statusOf(n, ix)).toBe("open, May be finished");
      }
    }
  });

  it("a lifecycle phrase is said once, after the pressure word", () => {
    const g = graphFixture();
    const map = g.nodes.find((n) => n.id === MAP);
    if (map?.kind !== "thread") throw new Error("fixture");
    map.pressure = { state: "stale", in_days: null, friendly: "" };
    map.findings.push({ id: "cand-again", kind: "possible_thread_closure", other: null });
    const { ix } = setup(g);
    expect(statusOf(map, ix)).toBe("open, stale, May be finished");
  });

  it("with no present, a dated record says its date rather than 'undated'", () => {
    const { ix, node } = setup(noPresentFixture());
    const cases: [string, string, string][] = [
      [CORONATION, "event", "13 March 1200"],
      [MEETING, "scene idea", "11 March 1200"],
      // Only what has no day of its own is undated or unscheduled.
      [FAIR, "event", "undated"],
      [LETTER, "scene idea", "unscheduled"],
      [BELL, "event", "fired"],
    ];
    for (const [id, noun, status] of cases) {
      const n = node(id);
      expect([id, statusOf(n, ix)]).toEqual([id, status]);
      expect(accessibleName(n, ix)).toBe(`${n.label}, ${noun}, ${status}`);
    }
    // A day the calendar could not phrase still reads as that day.
    const bare = noPresentFixture();
    for (const n of bare.nodes) if ("friendly" in n) n.friendly = "";
    const b = setup(bare);
    expect(statusOf(b.node(CORONATION), b.ix)).toBe("1200-03-13");
    expect(statusOf(b.node(MEETING), b.ix)).toBe("1200-03-11");
  });

  it("no shown string carries an internal token", () => {
    const { g, ix } = setup();
    const REF = /\b(scene|characters|pcs|locations|thread|commitment|event|idea|birthday|holiday):/;
    for (const n of g.nodes) {
      for (const s of [statusOf(n, ix), accessibleName(n, ix)]) {
        expect([n.id, s.includes("_"), REF.test(s)]).toEqual([n.id, false, false]);
      }
    }
    const values = [
      ...Object.values(PART_LABELS), ...Object.values(STATE_WORDS),
      ...Object.values(FINDING_PHRASE),
      ...Object.values(RELATION_PHRASE).flatMap((p) => [p.out, p.in]),
      ...Object.values(KIND_NOUN), ...Object.values(LENS_LABELS), ...Object.values(SHOW_LABELS),
    ];
    for (const v of values) expect([v, v.includes("_")]).toEqual([v, false]);
  });

  it("carries the copy the page shows", () => {
    expect(PART_LABELS).toEqual({
      calendar: "the calendar", scenes: "scenes", chronicle: "the chronicle", plot: "threads",
      commitments: "commitments", events: "events", continuity: "reviewed links and merges",
      candidates: "review findings", relationships: "relationships",
      scene_ideas: "saved scene ideas", names: "names",
    });
    expect(STATE_WORDS.due_soon).toBe("due soon");
    expect(FINDING_PHRASE.possible_thread_closure).toBe("May be finished");
    expect(RELATION_PHRASE.by).toEqual({ out: "Due by", in: "Deadline for" });
    expect(RELATION_PHRASE.before).toEqual({ out: "Before", in: "After this:" });
  });
});

describe("handoff and ledger targets", () => {
  it("seedFor builds exactly the two section 16.5 shapes", () => {
    const { node } = setup();
    const focus = seedFor(node(MAP), "focus");
    const anchor = seedFor(node(CORONATION), "anchor");
    expect(focus).toEqual({ chooser: { drivers: { [MAP]: "focus" } } });
    expect(anchor).toEqual({ chooser: { anchor: { ref: CORONATION, relation: "on" } } });
    expect(sanitizeSeed(focus)).toEqual(focus.chooser);
    expect(sanitizeSeed(anchor)).toEqual(anchor.chooser);
    // An id with a space survives the validator unchanged.
    const eve = seedFor(node(EVE), "anchor");
    expect(sanitizeSeed(eve)).toEqual({ anchor: { ref: EVE, relation: "on" } });
  });

  it("ledgerTarget of an odd thread id", () => {
    const { node } = setup();
    const target = ledgerTarget(node(OLD));
    expect(target).toEqual({ section: "threads", row: "mara/s:map" });
    expect(ledgerHref("run", target!).endsWith("/ledger/threads/mara%2Fs%3Amap")).toBe(true);
    expect(ledgerTarget(node(OATH))).toEqual({ section: "commitments", row: "mara-s-oath" });
    // A merged record opens its own row; the Ledger resolves it.
    expect(ledgerTarget(node(CHART))).toEqual({ section: "threads", row: "winifred-s-chart" });
    for (const id of [CORONATION, EVE, BIRTHDAY, MEETING, S1, MARA, HARBOUR]) {
      expect(ledgerTarget(node(id))).toBeNull();
    }
  });
});

describe("arcRows", () => {
  it("lists canonical threads, then commitments; live first, then label, then id", () => {
    const g = graphFixture();
    const twin: GraphNode = { id: "thread:a-twin", kind: "thread", label: "mara's map",
                              status: "open", live: true, merged_into: null, aliases: [],
                              latest_beat: "", pressure: null, focusable: true, findings: [] };
    const twin2: GraphNode = { ...twin, id: "thread:b-twin", label: "MARA'S MAP" };
    g.nodes.push(twin2, twin);
    expect(arcRows(g).map((n) => n.id)).toEqual([
      "thread:a-twin", "thread:b-twin", MAP, TOLL, OLD, OATH, DEBT,
    ]);
  });
});
