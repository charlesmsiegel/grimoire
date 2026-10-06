import type { GraphNode, StoryGraph } from "../../api/types";
import { graphFixture, noPresentFixture, NOW_FIXED } from "../../testkit/storyGraph";
import {
  COL, edgePath, HEAD, layout, NODE_H, NODE_W, PAD, ROW, type Column, type Layout,
} from "./layout";
import { indexGraph, LENSES, PRESETS, visible, type Lens, type Show } from "./model";

const MAP = "thread:mara-s-map";
const CHART = "thread:winifred-s-chart";
const OLD = "thread:mara/s:map";
const TOLL = "thread:saltmarch-toll";
const OATH = "commitment:mara-s-oath";
const DEBT = "commitment:winifred-s-debt";
const CORONATION = "event:the-coronation";
const FAIR = "event:saltmarch-fair";
const EVE = "holiday:739000:Saltmarch Eve";
const BIRTHDAY = "birthday:characters:mara:month:1200-3";
const MEETING = "idea:the-harbour-meeting";
const LETTER = "idea:winifred-s-letter";
const S1 = "scene:001--saltmarch-harbour";
const S2 = "scene:002--mara-s-flashback";
const S3 = "scene:003--winifred-s-chart";
const MARA = "characters:mara";
const SERAPHINE = "pcs:seraphine";

function draw(lens: Lens, opts: { g?: StoryGraph; show?: Show; arc?: string | null } = {}) {
  const g = opts.g ?? graphFixture();
  const ix = indexGraph(g);
  const view = visible(g, ix, lens, opts.show ?? PRESETS[lens].show, opts.arc ?? null);
  const lay = layout(g, ix, view, PRESETS[lens].axis);
  const at = (id: string) => {
    const p = lay.nodes.find((n) => n.id === id);
    if (!p) throw new Error(`${id} was not placed`);
    return p;
  };
  const colX = (c: Column) => PAD + c.index * COL;
  const column = (kind: Column["kind"], head?: string) => {
    const c = lay.columns.find((k) => k.kind === kind && (head === undefined || k.head === head));
    if (!c) throw new Error(`no ${kind} column ${head ?? ""}`);
    return c;
  };
  /** The column a placed node sits in, read back from its x. */
  const colOf = (id: string) => {
    const c = lay.columns.find((k) => colX(k) === at(id).x);
    if (!c) throw new Error(`${id} sits in no column`);
    return c;
  };
  return { g, ix, view, lay, at, colX, column, colOf };
}

function allFinite(lay: Layout): boolean {
  return lay.nodes.every((p) => Number.isFinite(p.x) && Number.isFinite(p.y));
}

function nowColumnX(lay: Layout): number | null {
  const now = lay.columns.find((c) => c.kind === "now");
  return now ? PAD + now.index * COL : null;
}

describe("layout: the play axis", () => {
  it("scenes run left to right in payload order, not by date", () => {
    const { g, at } = draw("story");
    const s1 = g.nodes.find((n) => n.id === S1);
    const s2 = g.nodes.find((n) => n.id === S2);
    // The fixture's scene 002 is a flashback: dated before 001, played after it.
    expect(s1 && "fixed" in s1 && s2 && "fixed" in s2 && s1.fixed! > s2.fixed!).toBe(true);
    expect(at(S1).x).toBeLessThan(at(S2).x);
    expect(at(S2).x).toBeLessThan(at(S3).x);
  });

  it("the Now boundary is drawn immediately after the last scene on the Story lens", () => {
    const { lay, at, colOf, column } = draw("story");
    expect(lay.nowX).not.toBeNull();
    expect(lay.nowX!).toBeGreaterThan(at(S3).x);
    expect(column("now").index).toBe(colOf(S3).index + 1);
  });

  it("future nodes sit right of Now by ascending in_days, then ideas", () => {
    const { lay, at } = draw("story");
    const now = lay.nowX!;
    expect(at(EVE).x).toBeGreaterThan(now);
    expect(at(EVE).x).toBeLessThan(at(CORONATION).x);
    // The idea is a day away, sooner than both, and still comes after every
    // temporal slot (§19.4).
    expect(at(CORONATION).x).toBeLessThan(at(MEETING).x);
    expect(at(OATH).x).toBeLessThan(at(MEETING).x);
  });

  it("undated ideas go in a trailing Unscheduled column", () => {
    const { lay, colOf } = draw("story");
    const c = colOf(LETTER);
    expect(c.kind).toBe("unscheduled");
    expect(c.head).toBe("Unscheduled");
    expect(c.index).toBe(lay.columns.length - 1);
  });

  it("a month-precision birthday goes in Undated and every placed x is finite", () => {
    const story = draw("story");
    const undated = story.column("undated", "Undated");
    expect(story.at(BIRTHDAY).x).toBe(story.colX(undated));
    const futures = story.lay.columns.filter((c) => c.kind === "future");
    const ideas = story.lay.columns.filter((c) => c.kind === "idea");
    expect(futures.length).toBeGreaterThan(0);
    expect(ideas.length).toBeGreaterThan(0);
    for (const c of futures) expect(c.index).toBeLessThan(undated.index);
    for (const c of ideas) expect(c.index).toBeGreaterThan(undated.index);

    const calendar = draw("calendar");
    expect(calendar.colOf(BIRTHDAY).head).toBe("Undated");

    for (const lens of LENSES) {
      const { lay } = draw(lens);
      expect(allFinite(lay)).toBe(true);
      expect(Number.isFinite(lay.width) && Number.isFinite(lay.height)).toBe(true);
    }
  });

  it("an undated event goes in Undated, not Unscheduled", () => {
    const { colOf } = draw("story");
    expect(colOf(FAIR).kind).toBe("undated");
    expect(colOf(FAIR).head).toBe("Undated");
  });

  it("a past-dated idea takes an idea slot, not Unscheduled", () => {
    const g = graphFixture();
    g.nodes = g.nodes.map((n) => (n.id === MEETING && n.kind === "idea"
      ? { ...n, fixed: NOW_FIXED - 5, in_days: -5 } : n));
    const { lay, colOf } = draw("story", { g });
    const c = colOf(MEETING);
    expect(c.kind).toBe("idea");
    expect(c.head).toBe("5 days ago");
    expect(c.index).toBeGreaterThan(lay.columns.findIndex((k) => k.kind === "now"));
  });

  it("the Continuity lens puts arcs left of Reached and Now", () => {
    const { lay, view, at, colOf } = draw("continuity");
    const now = lay.nowX!;
    const arcs = view.nodes.filter((n) => n.kind === "thread" || n.kind === "commitment");
    const events = view.nodes.filter((n) => n.kind === "event");
    expect(arcs.length).toBeGreaterThan(0);
    expect(events.length).toBeGreaterThan(0);
    const eventCols = new Set(events.map((e) => colOf(e.id).index));
    for (const a of arcs) {
      expect(eventCols.has(colOf(a.id).index)).toBe(false);
      // A deadline still ahead is a dated moment (§19.4 puts parseable
      // deadlines right of Now); every other arc has no visible scene here.
      if (a.kind === "commitment" && a.in_days !== null && a.in_days >= 0) {
        expect(colOf(a.id).kind).toBe("future");
        continue;
      }
      expect(at(a.id).x + NODE_W).toBeLessThan(now);
      expect(colOf(a.id).head).toBe("Not in a scene");
    }
    const reached = lay.columns.find((c) => c.kind === "reached");
    expect(reached).toBeDefined();
    expect(colOf(MAP).index).toBeLessThan(reached!.index);
  });

  it("no node shares the Now column on any lens", () => {
    for (const lens of LENSES) {
      const { lay } = draw(lens);
      const nowX = nowColumnX(lay);
      for (const p of lay.nodes) expect(p.x).not.toBe(nowX);
    }
  });

  it("row 0 starts below the column heads", () => {
    for (const lens of LENSES) {
      const { lay } = draw(lens);
      expect(Math.min(...lay.nodes.map((p) => p.y))).toBeGreaterThanOrEqual(HEAD + PAD);
    }
  });

  it("a commitment with a future deadline sits in its temporal slot; a thread sits at its last scene", () => {
    const { at, colOf, lay } = draw("story");
    const slot = colOf(OATH);
    expect(slot.kind).toBe("future");
    expect(slot.head).toBe("in 4 days");
    expect(at(OATH).x).toBeGreaterThan(lay.nowX!);
    // Opened in 001, advanced in 003: its last scene is 003.
    expect(at(MAP).x).toBe(at(S3).x);
    // Opened in 001, closed in 002.
    expect(at(OLD).x).toBe(at(S2).x);
  });

  it("each arc owns its own lane", () => {
    const { view, at } = draw("story");
    const arcs = view.nodes.filter((n) => n.kind === "thread" || n.kind === "commitment");
    expect(arcs.map((n) => n.id)).toEqual(expect.arrayContaining([MAP, OLD, TOLL, OATH, DEBT]));
    const ys = arcs.map((n) => at(n.id).y);
    expect(new Set(ys).size).toBe(ys.length);
    // A lane sits below every band-0 node.
    const band0 = view.nodes.filter((n) => !["thread", "commitment"].includes(n.kind));
    const lowest = Math.max(...band0.map((n) => at(n.id).y));
    for (const y of ys) expect(y).toBeGreaterThan(lowest);
  });

  it("a merged node gets its own lane directly below its canonical", () => {
    const { view, lay, at } = draw("continuity");
    expect(view.nodes.map((n) => n.id)).toContain(CHART);
    expect(lay.nodes.map((p) => p.id).sort()).toEqual(view.nodes.map((n) => n.id).sort());
    expect(allFinite(lay)).toBe(true);
    const others = view.nodes
      .filter((n) => (n.kind === "thread" || n.kind === "commitment") && n.id !== CHART)
      .map((n) => at(n.id).y);
    expect(others).not.toContain(at(CHART).y);
    expect(at(CHART).y).toBe(at(MAP).y + ROW);
    expect(at(CHART).x).toBe(at(MAP).x);
  });

  it("a merged node takes its canonical's column, even a deadline's", () => {
    // A merged record carries no date and no movement of its own, so on its
    // own it would sit at the last scene; under a canonical with a deadline it
    // still sits in that deadline's slot, directly below it.
    const g = graphFixture();
    const vow = "commitment:mara-s-vow";
    g.nodes.push({ id: vow, kind: "commitment", label: "Mara's vow", commitment_kind: "promise",
      due: "", status: "open", live: true, merged_into: OATH, aliases: [], latest_beat: "",
      pressure: null, focusable: false, findings: [],
      native: "", friendly: "", fixed: null, in_days: null });
    const { at, colOf } = draw("story", { g, show: { ...PRESETS.story.show, merged: true } });
    expect(colOf(OATH).kind).toBe("future");
    expect(at(vow).x).toBe(at(OATH).x);
    expect(at(vow).y).toBe(at(OATH).y + ROW);
  });

  it("actors stack below the lanes, at the first scene they appear in", () => {
    const { at } = draw("cast");
    // Mara appears in 001 and 002; Seraphine in 001.
    expect(at(MARA).x).toBe(at(S1).x);
    expect(at(SERAPHINE).x).toBe(at(S1).x);
    expect(at(MARA).y).toBeGreaterThan(at(OATH).y);
    expect(at(MARA).y).not.toBe(at(SERAPHINE).y);
  });
});

describe("layout: an arc filter", () => {
  it("an arc filter places every node on every lens", () => {
    for (const lens of LENSES) {
      const { view, lay } = draw(lens, { arc: MAP });
      expect(lay.nodes.map((p) => p.id).sort()).toEqual(view.nodes.map((n) => n.id).sort());
      expect(allFinite(lay)).toBe(true);
      const nowX = nowColumnX(lay);
      for (const p of lay.nodes) expect(p.x).not.toBe(nowX);
    }

    const cal = draw("calendar", { arc: MAP });
    const notDated = cal.column("not-dated", "Not dated");
    const people = cal.column("people", "People and places");
    expect(cal.colOf(MAP)).toEqual(notDated);
    expect(cal.colOf(CHART)).toEqual(notDated);
    expect(cal.colOf(OLD)).toEqual(notDated);
    expect(cal.colOf(MARA)).toEqual(people);
    expect(notDated.index).toBeLessThan(people.index);
    const undated = cal.lay.columns.find((c) => c.kind === "undated");
    if (undated) expect(undated.index).toBeLessThan(notDated.index);

    // With an undated scene beside it, "Undated" is present and comes first.
    const g = graphFixture();
    g.nodes = g.nodes.map((n) => (n.id === S1 && n.kind === "scene"
      ? { ...n, fixed: null, in_days: null } : n));
    const withUndated = draw("calendar", { g, arc: MAP });
    expect(withUndated.colOf(S1).kind).toBe("undated");
    expect(withUndated.column("undated").index)
      .toBeLessThan(withUndated.column("not-dated").index);

    const debt = draw("calendar", { arc: DEBT });
    expect(debt.view.nodes.map((n) => n.id)).toContain(DEBT);
    expect(debt.colOf(DEBT).kind).toBe("not-dated");
    expect(debt.colOf(DEBT).head).toBe("Not dated");
  });

  it("Reached follows the scenes and precedes Now, and an arc filter brings it onto Story (§19.4)", () => {
    // The coronation fired; the oath is linked to it by `by`.
    const g = graphFixture();
    g.nodes = g.nodes.map((n) => (n.id === CORONATION && n.kind === "event"
      ? { ...n, status: "fired" as const } : n));
    // The Story preset drops a fired event, and Cast shows no events at all.
    expect(draw("story", { g }).view.nodes.map((n) => n.id)).not.toContain(CORONATION);
    expect(draw("cast", { g }).lay.columns.some((c) => c.kind === "reached")).toBe(false);

    // An arc filter's link neighbours are not the preset's to drop.
    const { lay, colOf } = draw("story", { g, arc: OATH });
    const reached = colOf(CORONATION);
    expect(reached.kind).toBe("reached");
    expect(reached.head).toBe("Reached");
    const scene = colOf(S2);
    expect(scene.kind).toBe("scene");
    const now = lay.columns.find((c) => c.kind === "now");
    expect(now).toBeDefined();
    expect(scene.index).toBeLessThan(reached.index);
    expect(reached.index).toBeLessThan(now!.index);
  });
});

describe("layout: an event fired today", () => {
  it("sits right of Now on its own day, never under Reached (§13.5 own day)", () => {
    const g = graphFixture();
    g.nodes = g.nodes.map((n) => (n.id === CORONATION && n.kind === "event"
      ? { ...n, status: "fired" as const, in_days: 0, fixed: NOW_FIXED,
          pressure: { state: "today" as const, in_days: 0, friendly: n.friendly } }
      : n));
    for (const lens of ["story", "continuity"] as const) {
      const { lay, colOf } = draw(lens, { g });
      const col = colOf(CORONATION);
      expect(col.kind).toBe("future");
      const now = lay.columns.find((c) => c.kind === "now");
      expect(now).toBeDefined();
      expect(col.index).toBeGreaterThan(now!.index);
    }
  });
});

describe("layout: the calendar axis", () => {
  it("actors on the Calendar lens go in People and places, not Undated", () => {
    const show = { ...PRESETS.calendar.show, actors: true, locations: true };
    const { colOf, lay } = draw("calendar", { show });
    expect(colOf(MARA).head).toBe("People and places");
    expect(colOf(SERAPHINE).kind).toBe("people");
    expect(colOf("locations:saltmarch-harbour").kind).toBe("people");
    expect(lay.columns[lay.columns.length - 1].kind).toBe("people");
  });

  it("a null fixed goes in the Undated bucket", () => {
    const g = graphFixture();
    g.now = { ...g.now, fixed: null };
    g.nodes = g.nodes.map((n): GraphNode => ("fixed" in n
      ? { ...n, fixed: null, in_days: null } : n));
    const { lay, view } = draw("calendar", { g });
    expect(view.nodes.length).toBeGreaterThan(0);
    expect(lay.columns).toEqual([{ index: 0, head: "Undated", kind: "undated" }]);
    expect(lay.nowX).toBeNull();
    expect(allFinite(lay)).toBe(true);
  });

  it("the Calendar lens orders by fixed and puts Now at now.fixed", () => {
    const g = graphFixture();
    // Played first, dated after the present.
    g.nodes = g.nodes.map((n) => (n.id === S1 && n.kind === "scene"
      ? { ...n, fixed: NOW_FIXED + 7, in_days: 7, friendly: "17 March 1200" } : n));
    const { at, lay, colOf } = draw("calendar", { g });
    expect(at(S2).x).toBeLessThan(lay.nowX!);
    expect(at(S3).x).toBeLessThan(lay.nowX!);
    expect(at(S2).x).toBeLessThan(at(S3).x);
    expect(at(S1).x).toBeGreaterThan(lay.nowX!);
    expect(colOf(S1).head).toBe("17 March 1200");
    expect(colOf(S1).kind).toBe("day");
  });

  it("two nodes on one day share a column and stack", () => {
    const g = graphFixture();
    g.nodes = g.nodes.map((n) => (n.id === CORONATION && n.kind === "event"
      ? { ...n, fixed: 739000, in_days: 2 } : n));
    const { at } = draw("calendar", { g });
    expect(at(CORONATION).x).toBe(at(EVE).x);
    expect(at(CORONATION).y).not.toBe(at(EVE).y);
  });
});

/** The payload of a campaign with no present (no clock, no dated chronicle):
 *  `now` is blank, nothing has an `in_days`, and pressure dated no holiday or
 *  birthday -- but a record that carries its own date still has `fixed`. */
describe("layout: a campaign with no present", () => {
  it("dated records are placed on the calendar axis by fixed", () => {
    const { at, lay, colOf, view } = draw("calendar", { g: noPresentFixture() });
    expect(view.nodes.map((n) => n.id)).toEqual(expect.arrayContaining([OATH, CORONATION]));
    for (const id of [OATH, CORONATION, S1]) {
      expect(colOf(id).kind).toBe("day");
    }
    expect(colOf(OATH).head).toBe("14 March 1200");
    expect(colOf(CORONATION).head).toBe("13 March 1200");
    expect(at(CORONATION).x).toBeLessThan(at(OATH).x);
    expect(at(S1).x).toBeLessThan(at(CORONATION).x);
    // The free-text event is the only thing that is undated.
    expect(colOf(FAIR).kind).toBe("undated");
    expect(lay.columns.some((c) => c.kind === "not-dated")).toBe(false);
    expect(lay.nowX).toBeNull();
  });

  it("an unfired dated event sits right of Now by fixed on the play axis, not in Undated", () => {
    const { column, colOf, at } = draw("story", { g: noPresentFixture() });
    const now = column("now");
    const coronation = colOf(CORONATION);
    expect(coronation.kind).toBe("future");
    expect(coronation.head).toBe("13 March 1200");
    expect(coronation.index).toBeGreaterThan(now.index);
    expect(colOf(FAIR).kind).toBe("undated");
    expect(coronation.index).toBeLessThan(colOf(FAIR).index);
    // A commitment's "still ahead" needs a present: it keeps its scene lane.
    expect(colOf(OATH).kind).toBe("scene");
    expect(at(OATH).x).toBeLessThan(at(CORONATION).x);
  });
});

describe("layout: purity and geometry", () => {
  it("the layout is a function of its inputs", () => {
    for (const lens of LENSES) {
      expect(draw(lens).lay).toEqual(draw(lens).lay);
    }
  });

  it("width and height cover every column and row", () => {
    for (const lens of LENSES) {
      const { lay } = draw(lens);
      expect(lay.width).toBe(PAD + lay.columns.length * COL);
      for (const p of lay.nodes) {
        expect(p.x + NODE_W).toBeLessThanOrEqual(lay.width);
        expect(p.y + NODE_H).toBeLessThanOrEqual(lay.height);
      }
    }
  });

  it("edgePath joins two centres", () => {
    const a = { id: "a", x: 0, y: 0 };
    const b = { id: "b", x: COL, y: ROW };
    const [x1, y1] = [NODE_W / 2, NODE_H / 2];
    const [x2, y2] = [COL + NODE_W / 2, ROW + NODE_H / 2];
    const mx = (x1 + x2) / 2;
    expect(edgePath(a, b)).toBe(`M ${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`);
  });
});
