import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { GraphNode, ShellPayload, StoryGraph } from "../api/types";
import { ACTION_LABELS, sanitizeSeed } from "../components/pressureControls";
import {
  FINDING_PHRASE, RELATION_PHRASE, STATE_WORDS, accessibleName, indexGraph, statusOf,
} from "../components/storyGraph/model";
import { GROUP_OF, ledgerHref } from "../ledgerPaths";
import { ShellPayloadProvider } from "../shell/ShellPayloadContext";
import { activeHotkeys } from "../shortcuts/registry";
import { graphFixture } from "../testkit/storyGraph";
import { declares, bodiesOf, stylesheet } from "../testkit/stylesheet";
import { setInnerWidth } from "../testkit/viewport";
import StoryGraphView from "./StoryGraphView";

// Only the graph read exists: any other API call the page made would throw,
// which is half of §28.9's "one read" (the other half is the shell spy below).
vi.mock("../api/client", () => ({ api: { continuityGraph: vi.fn() } }));

const SHELL: ShellPayload = {
  campaigns: 2, todo: null,
  campaign: {
    id: "c1", name: "A Run In Saltmarch", world_name: "Saltmarch", scenes: 3, open: [],
    ledger_open: 0, sheets: null, unreviewed: null, pending: [], images_undescribed: null,
  },
};

let retry: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.mocked(api.continuityGraph).mockReset();
  vi.mocked(api.continuityGraph).mockResolvedValue(graphFixture());
  retry = vi.fn();
});

function Probe() {
  return <div data-testid="probe">{JSON.stringify(useLocation().state)}</div>;
}

function LocationProbe() {
  return <div data-testid="search">{useLocation().search}</div>;
}

function Jump() {
  const navigate = useNavigate();
  return <button type="button" onClick={() => navigate("/campaigns/c2/graph")}>jump</button>;
}

function renderGraph(entry = "/campaigns/c1/graph") {
  return render(
    <ShellPayloadProvider value={{ status: "ready", payload: SHELL, retry, cid: "c1" }}>
      <MemoryRouter initialEntries={[entry]}>
        <Jump />
        <LocationProbe />
        <Routes>
          <Route path="/campaigns/:cid/graph" element={<StoryGraphView />} />
          <Route path="/campaigns/:cid/scenes" element={<Probe />} />
        </Routes>
      </MemoryRouter>
    </ShellPayloadProvider>);
}

const FIX = graphFixture();
const IX = indexGraph(FIX);
const byId = (id: string): GraphNode => IX.byId.get(id)!;
/** A node button's exact accessible name, from the model's own rule. */
const nameOf = (id: string) => accessibleName(byId(id), IX);

const drawing = () => screen.getByTestId("story-graph-drawing");
const column = () => screen.getByRole("complementary", { name: "Story graph filters" });
const search = () => screen.getByTestId("search").textContent ?? "";
const param = (key: string) => new URLSearchParams(search()).get(key);
const nodeButton = (id: string) => within(drawing()).getByRole("button", { name: nameOf(id) });
const queryNode = (id: string) => within(drawing()).queryByRole("button", { name: nameOf(id) });
const shell = () => document.querySelector(".shell");

async function ready(entry?: string) {
  const r = renderGraph(entry);
  await screen.findByTestId("story-graph-drawing");
  return r;
}

test("renders the drawing from one graph read", async () => {
  await ready();
  expect(nodeButton("scene:001--saltmarch-harbour")).toBeInTheDocument();
  expect(nodeButton("thread:mara-s-map")).toBeInTheDocument();
  expect(nodeButton("event:the-coronation")).toBeInTheDocument();
  expect(api.continuityGraph).toHaveBeenCalledWith("c1");
  expect(screen.getByRole("heading", { level: 1, name: "Story graph" })).toBeInTheDocument();
});

test("filter presets: lens rows switch the preset and write ?lens=", async () => {
  await ready();
  expect(queryNode("characters:mara")).not.toBeInTheDocument();
  fireEvent.click(within(column()).getByRole("button", { name: "Cast" }));
  expect(nodeButton("characters:mara")).toBeInTheDocument();
  expect(nodeButton("pcs:seraphine")).toBeInTheDocument();
  expect(param("lens")).toBe("cast");
  expect(within(column()).getByRole("button", { name: "Cast" }))
    .toHaveAttribute("aria-pressed", "true");
  fireEvent.click(within(column()).getByRole("button", { name: "Story" }));
  expect(queryNode("characters:mara")).not.toBeInTheDocument();
  expect(param("lens")).toBe("story");
});

test("switching the lens away and back restores its Show toggles (Decision 19)", async () => {
  await ready();
  const actors = () => within(column()).getByRole("checkbox", { name: "Actors" });
  fireEvent.click(actors());
  expect(actors()).toBeChecked();
  expect(nodeButton("characters:mara")).toBeInTheDocument();
  fireEvent.click(within(column()).getByRole("button", { name: "Cast" }));
  fireEvent.click(within(column()).getByRole("button", { name: "Story" }));
  expect(actors()).not.toBeChecked();
  expect(queryNode("characters:mara")).not.toBeInTheDocument();
});

function Back() {
  const navigate = useNavigate();
  return <button type="button" onClick={() => navigate(-1)}>back</button>;
}

test("a lens change pushes history and a node pick replaces it (Decision 19)", async () => {
  render(
    <ShellPayloadProvider value={{ status: "ready", payload: SHELL, retry, cid: "c1" }}>
      <MemoryRouter initialEntries={["/elsewhere", "/campaigns/c1/graph"]} initialIndex={1}>
        <Back />
        <LocationProbe />
        <Routes>
          <Route path="/campaigns/:cid/graph" element={<StoryGraphView />} />
          <Route path="/elsewhere" element={<p>elsewhere</p>} />
        </Routes>
      </MemoryRouter>
    </ShellPayloadProvider>);
  await screen.findByTestId("story-graph-drawing");
  fireEvent.click(within(column()).getByRole("button", { name: "Cast" }));
  fireEvent.click(nodeButton("characters:mara"));
  fireEvent.click(nodeButton("pcs:seraphine"));
  expect(param("lens")).toBe("cast");
  expect(param("node")).toBe("pcs:seraphine");

  // One Back undoes the lens change and both picks with it: the picks
  // replaced the Cast entry, and the lens pushed it over the Story one.
  fireEvent.click(screen.getByRole("button", { name: "back" }));
  expect(screen.queryByText("elsewhere")).not.toBeInTheDocument();
  expect(within(column()).getByRole("button", { name: "Story" }))
    .toHaveAttribute("aria-pressed", "true");
  expect(param("lens")).toBeNull();
  expect(param("node")).toBeNull();
});

test("clearing the arc filter pushes history, so Back brings the filter back", async () => {
  render(
    <ShellPayloadProvider value={{ status: "ready", payload: SHELL, retry, cid: "c1" }}>
      <MemoryRouter initialEntries={["/elsewhere", "/campaigns/c1/graph?arc=thread%3Asaltmarch-toll"]}
                    initialIndex={1}>
        <Back />
        <LocationProbe />
        <Routes>
          <Route path="/campaigns/:cid/graph" element={<StoryGraphView />} />
          <Route path="/elsewhere" element={<p>elsewhere</p>} />
        </Routes>
      </MemoryRouter>
    </ShellPayloadProvider>);
  await screen.findByTestId("story-graph-drawing");
  expect(screen.getByText(/^Filtered to/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Show the whole graph" }));
  expect(param("arc")).toBeNull();
  expect(screen.queryByText(/^Filtered to/)).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "back" }));
  expect(screen.queryByText("elsewhere")).not.toBeInTheDocument();
  expect(param("arc")).toBe("thread:saltmarch-toll");
  expect(screen.getByText(/^Filtered to/)).toBeInTheDocument();
});

function renderWithHistory(entry: string) {
  render(
    <ShellPayloadProvider value={{ status: "ready", payload: SHELL, retry, cid: "c1" }}>
      <MemoryRouter initialEntries={["/elsewhere", entry]} initialIndex={1}>
        <Back />
        <LocationProbe />
        <Routes>
          <Route path="/campaigns/:cid/graph" element={<StoryGraphView />} />
          <Route path="/elsewhere" element={<p>elsewhere</p>} />
        </Routes>
      </MemoryRouter>
    </ShellPayloadProvider>);
  return screen.findByTestId("story-graph-drawing");
}

test.each([
  ["the default Story lens, with no ?lens=", "/campaigns/c1/graph", "Story", "1"],
  ["a lens the address names", "/campaigns/c1/graph?lens=cast", "Cast", "2"],
])("choosing %s again, by button or hotkey, pushes no history", async (_, entry, label, key) => {
  await renderWithHistory(entry);
  const before = search();
  fireEvent.click(within(column()).getByRole("button", { name: label }));
  fireEvent.keyDown(window, { key });
  expect(search()).toBe(before);

  // One Back leaves the page: nothing was stacked over the entry it opened on.
  fireEvent.click(screen.getByRole("button", { name: "back" }));
  expect(screen.getByText("elsewhere")).toBeInTheDocument();
});

test("choosing a different lens still pushes exactly one entry", async () => {
  await renderWithHistory("/campaigns/c1/graph");
  fireEvent.click(within(column()).getByRole("button", { name: "Cast" }));
  fireEvent.click(within(column()).getByRole("button", { name: "Cast" }));
  fireEvent.keyDown(window, { key: "2" });
  expect(param("lens")).toBe("cast");

  fireEvent.click(screen.getByRole("button", { name: "back" }));
  expect(screen.queryByText("elsewhere")).not.toBeInTheDocument();
  expect(within(column()).getByRole("button", { name: "Story" }))
    .toHaveAttribute("aria-pressed", "true");
  fireEvent.click(screen.getByRole("button", { name: "back" }));
  expect(screen.getByText("elsewhere")).toBeInTheDocument();
});

test("show toggles add actors and locations; merged records and candidates stay hidden until shown",
     async () => {
  await ready();
  expect(queryNode("characters:mara")).not.toBeInTheDocument();
  expect(queryNode("locations:saltmarch-harbour")).not.toBeInTheDocument();
  expect(queryNode("thread:winifred-s-chart")).not.toBeInTheDocument();
  expect(drawing().querySelector(".sg-edge.candidate")).toBeNull();

  fireEvent.click(within(column()).getByRole("checkbox", { name: "Actors" }));
  expect(nodeButton("characters:mara")).toBeInTheDocument();
  fireEvent.click(within(column()).getByRole("checkbox", { name: "Locations" }));
  expect(nodeButton("locations:saltmarch-harbour")).toBeInTheDocument();
  fireEvent.click(within(column()).getByRole("checkbox", { name: "Merged records" }));
  expect(nodeButton("thread:winifred-s-chart")).toBeInTheDocument();
  expect(drawing().querySelector(".sg-edge.alias")).not.toBeNull();
  fireEvent.click(within(column()).getByRole("checkbox", { name: "Review candidates" }));
  expect(drawing().querySelector(".sg-edge.candidate")).not.toBeNull();
  // Toggles are local state: the address names only lens, arc and node.
  expect(param("show")).toBeNull();
});

test("tapping a node shows its detail below the drawing", async () => {
  await ready();
  fireEvent.click(nodeButton("thread:mara-s-map"));
  expect(search()).toContain("node=thread%3Amara-s-map");
  const detail = screen.getByRole("region", { name: "Selected: Mara's map" });
  expect(detail).toHaveClass("sg-detail");
  expect(drawing().contains(detail)).toBe(false);
  expect(nodeButton("thread:mara-s-map")).toHaveAttribute("aria-pressed", "true");
});

test("an Arcs row selects its node", async () => {
  await ready();
  const arcs = within(column()).getAllByRole("button")
    .filter((b) => b.textContent?.startsWith("The Saltmarch toll"));
  expect(arcs).toHaveLength(1);
  fireEvent.click(arcs[0]);
  expect(param("node")).toBe("thread:saltmarch-toll");
  expect(arcs[0]).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByRole("region", { name: "Selected: The Saltmarch toll" }))
    .toBeInTheDocument();
});

test("the Now boundary sits after the last scene and before future nodes", async () => {
  await ready();
  const now = screen.getByRole("separator", { name: "Now" });
  const left = (el: HTMLElement) => parseFloat(el.style.left);
  expect(left(now)).toBeGreaterThan(left(nodeButton("scene:003--winifred-s-chart")));
  expect(left(now)).toBeLessThan(left(nodeButton("event:the-coronation")));
});

test("at 375px an Arcs row or a node shows its detail in main, outside the drawing", async () => {
  const restore = setInnerWidth(375);
  try {
    await ready();
    const arcRow = () => within(column()).getAllByRole("button")
      .find((b) => b.textContent?.startsWith("The Saltmarch toll"))!;
    fireEvent.click(screen.getByRole("button", { name: "‹ Story graph filters" }));
    expect(shell()).toHaveClass("show-column");
    fireEvent.click(arcRow());
    expect(shell()).not.toHaveClass("show-column");

    // The same row again: the same `?node=`, and still a pick (Decision 22).
    fireEvent.click(screen.getByRole("button", { name: "‹ Story graph filters" }));
    expect(shell()).toHaveClass("show-column");
    fireEvent.click(arcRow());
    expect(shell()).not.toHaveClass("show-column");

    let detail = screen.getByRole("region", { name: "Selected: The Saltmarch toll" });
    expect(screen.getByRole("main").contains(detail)).toBe(true);
    expect(drawing().contains(detail)).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "‹ Story graph filters" }));
    expect(shell()).toHaveClass("show-column");
    fireEvent.click(nodeButton("event:the-coronation"));
    expect(shell()).not.toHaveClass("show-column");
    detail = screen.getByRole("region", { name: "Selected: The coronation" });
    expect(screen.getByRole("main").contains(detail)).toBe(true);
    expect(drawing().contains(detail)).toBe(false);

    Object.defineProperty(window, "innerWidth", { value: 1200, configurable: true, writable: true });
    fireEvent(window, new Event("resize"));
    expect(shell()).not.toHaveClass("phone");
  } finally { restore(); }
});

test("every node is a button named with its label and status", async () => {
  await ready();
  for (const name of ["Actors", "Locations", "Merged records"]) {
    fireEvent.click(within(column()).getByRole("checkbox", { name }));
  }
  const byName = new Map(FIX.nodes.map((n) => [accessibleName(n, IX), n] as const));
  const els = [...drawing().querySelectorAll<HTMLElement>(".sg-node")];
  expect(els.length).toBeGreaterThan(10);
  for (const el of els) {
    expect(el.tagName).toBe("BUTTON");
    const n = byName.get(el.getAttribute("aria-label") ?? "");
    expect(n).toBeDefined();
    expect(el).toHaveAccessibleName(accessibleName(n!, IX));
    expect(el.getAttribute("aria-label")).toContain(n!.label);
    expect(el.getAttribute("aria-label")).toContain(statusOf(n!, IX));
    expect(el).not.toHaveAttribute("title");
  }
});

test("only one graph read across mount, lenses, toggles, arcs and nodes", async () => {
  await ready();
  for (const name of ["Cast", "Calendar", "Continuity", "Story"]) {
    fireEvent.click(within(column()).getByRole("button", { name }));
  }
  for (const name of ["Actors", "Locations", "Merged records", "Review candidates"]) {
    fireEvent.click(within(column()).getByRole("checkbox", { name }));
  }
  const arc = within(column()).getAllByRole("button")
    .find((b) => b.textContent?.startsWith("The Saltmarch toll"))!;
  fireEvent.click(arc);
  fireEvent.click(nodeButton("event:the-coronation"));
  fireEvent.keyDown(window, { key: "Escape" });
  expect(param("node")).toBeNull();
  await screen.findByTestId("story-graph-drawing");
  expect(api.continuityGraph).toHaveBeenCalledTimes(1);
  // No shell read either: the name comes from what the rail already holds.
  expect(retry).not.toHaveBeenCalled();
});

test("?lens= ?arc= ?node= are read from the address; unknown values fall back", async () => {
  const { unmount } = renderGraph(
    "/campaigns/c1/graph?lens=calendar&node=event%3Athe-coronation");
  await screen.findByTestId("story-graph-drawing");
  expect(within(column()).getByRole("button", { name: "Calendar" }))
    .toHaveAttribute("aria-pressed", "true");
  expect(screen.getByRole("region", { name: "Selected: The coronation" })).toBeInTheDocument();
  unmount();

  renderGraph("/campaigns/c1/graph?lens=bogus&node=thread%3Agone&arc=event%3Athe-coronation");
  await screen.findByTestId("story-graph-drawing");
  expect(within(column()).getByRole("button", { name: "Story" }))
    .toHaveAttribute("aria-pressed", "true");
  expect(screen.queryByRole("region", { name: /^Selected:/ })).not.toBeInTheDocument();
  expect(screen.queryByText(/^Filtered to/)).not.toBeInTheDocument();
  // No filter: a thread outside the coronation's neighbourhood is still drawn.
  expect(nodeButton("thread:saltmarch-toll")).toBeInTheDocument();
});

test("a node id with a colon, slash and space round-trips through ?node=", async () => {
  const first = await ready();
  for (const [id, label] of [["holiday:739000:Saltmarch Eve", "Saltmarch Eve"],
                             ["thread:mara/s:map", "Mara's first map"]] as const) {
    fireEvent.click(nodeButton(id));
    expect(param("node")).toBe(id);
    expect(screen.getByRole("region", { name: `Selected: ${label}` })).toBeInTheDocument();
    expect(nodeButton(id)).toHaveAttribute("aria-pressed", "true");
  }
  // ...and back in from the address.
  first.unmount();
  const q = new URLSearchParams({ node: "holiday:739000:Saltmarch Eve" }).toString();
  await ready(`/campaigns/c1/graph?${q}`);
  expect(screen.getByRole("region", { name: "Selected: Saltmarch Eve" })).toBeInTheDocument();
});

test("the lens keys and Escape are registered and listed", async () => {
  await ready();
  fireEvent.keyDown(window, { key: "2" });
  expect(param("lens")).toBe("cast");
  fireEvent.click(nodeButton("thread:mara-s-map"));
  expect(param("node")).toBe("thread:mara-s-map");
  fireEvent.keyDown(window, { key: "Escape" });
  expect(param("node")).toBeNull();
  expect(activeHotkeys().filter((r) => r.key.group === "STORY GRAPH").map((r) => r.key.label))
    .toEqual(["Story lens", "Cast lens", "Calendar lens", "Continuity lens",
              "Clear the selection"]);
});

test("a campaign switch drops the superseded read", async () => {
  let release: (g: StoryGraph) => void = () => {};
  const c1Graph = graphFixture();
  const c2Graph: StoryGraph = {
    now: c1Graph.now, edges: [], omitted: [],
    nodes: [{ id: "scene:001--the-realm-gate", kind: "scene", label: "The Realm gate",
              order: 0, done: false, pcless: false, place: "", native: "", friendly: "",
              fixed: null, in_days: null }],
  };
  vi.mocked(api.continuityGraph).mockReset();
  vi.mocked(api.continuityGraph)
    .mockImplementationOnce(() => new Promise<StoryGraph>((r) => { release = r; }))
    .mockResolvedValueOnce(c2Graph);
  renderGraph();
  expect(await screen.findByText("Reading the story graph…")).toBeInTheDocument();
  // The route is not keyed on `cid`, so the page stays mounted across the
  // jump: the same main pane before and after.
  const main = screen.getByRole("main");
  fireEvent.click(screen.getByRole("button", { name: "jump" }));
  await screen.findByTestId("story-graph-drawing");
  expect(screen.getByRole("main")).toBe(main);
  const pane = drawing();
  release(c1Graph);
  await screen.findByTestId("story-graph-drawing");
  expect(drawing()).toBe(pane);
  expect(screen.getByText("The Realm gate")).toBeInTheDocument();
  for (const label of ["Mara's map", "Saltmarch harbour", "The coronation"]) {
    expect(screen.queryByText(label)).not.toBeInTheDocument();
  }
  expect(api.continuityGraph).toHaveBeenNthCalledWith(1, "c1");
  expect(api.continuityGraph).toHaveBeenNthCalledWith(2, "c2");
  expect(api.continuityGraph).toHaveBeenCalledTimes(2);
});

test("a malformed part is named, not hidden", async () => {
  vi.mocked(api.continuityGraph).mockResolvedValue({ ...graphFixture(), omitted: ["relationships"] });
  await ready();
  expect(screen.getByText("Some records could not be read: relationships."))
    .toBeInTheDocument();
});

test("an empty graph with omitted parts names them and does not claim an empty campaign",
     async () => {
  vi.mocked(api.continuityGraph).mockResolvedValue({
    ...graphFixture(), nodes: [], edges: [], omitted: ["scenes", "scene_ideas"],
  });
  renderGraph();
  expect(await screen.findByText("Some records could not be read: scenes, saved scene ideas."))
    .toBeInTheDocument();
  expect(screen.queryByText(/Nothing to draw yet/)).not.toBeInTheDocument();
});

test("an empty graph with nothing omitted says so", async () => {
  vi.mocked(api.continuityGraph).mockResolvedValue({
    ...graphFixture(), nodes: [], edges: [], omitted: [],
  });
  renderGraph();
  expect(await screen.findByText(
    "Nothing to draw yet — play a scene and its threads appear here.")).toBeInTheDocument();
});

test("a failed read says so and Retry reads again", async () => {
  vi.mocked(api.continuityGraph).mockReset();
  vi.mocked(api.continuityGraph)
    .mockRejectedValueOnce(new Error("boom"))
    .mockResolvedValueOnce(graphFixture());
  renderGraph();
  expect(await screen.findByText("The story graph could not be read.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await screen.findByTestId("story-graph-drawing");
  expect(api.continuityGraph).toHaveBeenCalledTimes(2);
});

test("the drawing pane scrolls sideways and the main pane does not", () => {
  const { css } = stylesheet();
  const body = bodiesOf(css, ".sg-scroll").join(";");
  expect(declares(body, "overflow-x")).toBe("auto");
  expect(declares(body, "min-width")).toBe("0");
});

test("node boxes are fixed-height and overlays let taps through", () => {
  const { css } = stylesheet();
  const node = bodiesOf(css, ".sg-node").join(";");
  expect(declares(node, "height")).toBe("44px");
  expect(declares(node, "overflow")).toBe("hidden");
  expect(declares(bodiesOf(css, ".sg-now").join(";"), "pointer-events")).toBe("none");
  expect(declares(bodiesOf(css, ".sg-col-head").join(";"), "pointer-events")).toBe("none");
});

// ---- node detail (Task 13) ----

const detail = (label: string) => screen.getByRole("region", { name: `Selected: ${label}` });
const actions = (label: string) =>
  within(detail(label)).getByRole("complementary", { name: "Node actions" });
/** A `.side-section` of `scope`, found by its `<h4>`, or null. */
function sideSection(scope: HTMLElement, heading: string): HTMLElement | null {
  const h = within(scope).queryAllByRole("heading", { level: 4 })
    .find((x) => x.textContent === heading);
  return (h?.closest(".side-section") as HTMLElement | null) ?? null;
}
const section = (scope: HTMLElement, heading: string) => {
  const s = sideSection(scope, heading);
  if (!s) throw new Error(`no ${heading} section`);
  return s;
};
/** A fact's value in the detail's main column, by its term. */
function fact(scope: HTMLElement, term: string): string {
  const dt = [...scope.querySelectorAll("dt")].find((x) => x.textContent === term);
  if (!dt) throw new Error(`no ${term} fact`);
  return dt.nextElementSibling?.textContent ?? "";
}
const chip = (scope: HTMLElement, name: string) => {
  const b = within(scope).getByRole("button", { name });
  expect(b).toHaveClass("chip");
  return b;
};
/** A side section's rows, as read: one string per `<li>`. */
const entries = (scope: HTMLElement) =>
  [...scope.querySelectorAll("li")].map((li) => (li.textContent ?? "").replace(/\s+/g, " ").trim());
const at = (id: string) => `/campaigns/c1/graph?node=${encodeURIComponent(id)}`;
const probe = () => screen.getByTestId("probe").textContent ?? "";
/** A detail's text never shows an internal token: no underscore, no ref, no
 *  scene filename. */
function noTokens(d: HTMLElement) {
  const text = d.textContent ?? "";
  expect(text).not.toContain("_");
  for (const n of FIX.nodes) expect(text).not.toContain(n.id);
  expect(text).not.toMatch(/\d{3}--/);
}

test("a thread's detail shows title, merges, status, latest beat, scenes, actors, pressure, links and findings",
     async () => {
  await ready(at("thread:mara-s-map"));
  const d = detail("Mara's map");
  expect(within(d).getByRole("heading", { level: 3, name: "Mara's map" })).toBeInTheDocument();
  chip(section(d, "Merged"), "Winifred's chart");
  expect(fact(d, "Status")).toBe("open");
  expect(fact(d, "Latest beat")).toBe("Mara unrolled the map on the harbour wall.");
  expect(fact(d, "Pressure")).toBe(STATE_WORDS.ok);
  const scenes = section(d, "Scenes");
  expect(entries(scenes)).toEqual(["opened Saltmarch harbour", "advanced Winifred's chart"]);
  chip(scenes, "Saltmarch harbour");
  const involves = section(d, "Involves");
  chip(involves, "Mara");
  chip(involves, "Seraphine");
  const links = section(d, "Links");
  expect(links).toHaveTextContent(RELATION_PHRASE.continues.out);
  chip(links, "Mara's first map");
  const findings = section(d, "Findings");
  expect(findings).toHaveTextContent(FINDING_PHRASE.possible_relation);
  expect(findings).toHaveTextContent(FINDING_PHRASE.possible_thread_closure);
  noTokens(d);
});

test("a commitment's detail shows its kind, due and a dated pressure line", async () => {
  await ready(at("commitment:mara-s-oath"));
  const d = detail("Mara's oath");
  expect(fact(d, "Kind")).toBe("promise");
  expect(fact(d, "Due")).toBe("before Saltmarch Eve is out");
  expect(fact(d, "Pressure")).toBe(`${STATE_WORDS.due_soon}, in 4 days, 14 March 1200`);
  const links = section(d, "Links");
  expect(links).toHaveTextContent(RELATION_PHRASE.by.out);
  chip(links, "The coronation");
  expect(entries(section(d, "Scenes"))).toEqual(["opened Mara's flashback"]);
  noTokens(d);
});

test("a pair finding names the record it pairs with", async () => {
  await ready(at("thread:mara-s-map"));
  const d = detail("Mara's map");
  const findings = section(d, "Findings");
  expect(findings).toHaveTextContent(`${FINDING_PHRASE.possible_relation} with`);
  expect(FINDING_PHRASE.possible_relation).toBe("Possible relation");
  noTokens(d);
  fireEvent.click(chip(findings, "Mara's oath"));
  expect(param("node")).toBe("commitment:mara-s-oath");
});

test("Focus next scene sends the drivers handoff", async () => {
  await ready(at("thread:mara-s-map"));
  fireEvent.click(within(actions("Mara's map")).getByRole("button", { name: "Focus next scene" }));
  expect(probe()).toBe('{"chooser":{"drivers":{"thread:mara-s-map":"focus"}}}');
});

test("Anchor next scene sends the anchor handoff", async () => {
  await ready(at("event:the-coronation"));
  fireEvent.click(within(actions("The coronation"))
    .getByRole("button", { name: "Anchor next scene" }));
  expect(probe()).toBe('{"chooser":{"anchor":{"ref":"event:the-coronation","relation":"on"}}}');
});

test("Focus is disabled on a closed thread and absent on a merged record", async () => {
  const first = await ready(at("thread:mara/s:map"));
  const focus = within(actions("Mara's first map"))
    .getByRole("button", { name: "Focus next scene" });
  expect(focus).toBeDisabled();
  const hint = within(actions("Mara's first map")).getByText(
    "Only an open thread or an unresolved commitment can steer the next scene.");
  expect(hint).toHaveClass("field-hint");
  expect(hint).toBeVisible();
  first.unmount();

  await ready(at("thread:winifred-s-chart"));
  expect(within(detail("Winifred's chart")).queryByRole("button", { name: "Focus next scene" }))
    .not.toBeInTheDocument();
  fireEvent.click(chip(detail("Winifred's chart"), "Merged into Mara's map"));
  expect(param("node")).toBe("thread:mara-s-map");
});

test("Anchor is disabled on a fired event", async () => {
  await ready(at("event:the-harbour-bell"));
  const a = actions("The harbour bell");
  expect(within(a).getByRole("button", { name: "Anchor next scene" })).toBeDisabled();
  expect(within(a).getByText("Only an upcoming dated moment can anchor the next scene."))
    .toHaveClass("field-hint");
});

test("the sent state is one sanitizeSeed accepts", async () => {
  const first = await ready(at("thread:mara-s-map"));
  fireEvent.click(within(actions("Mara's map")).getByRole("button", { name: "Focus next scene" }));
  expect(sanitizeSeed(JSON.parse(probe()))).not.toBeNull();
  first.unmount();

  await ready(at("holiday:739000:Saltmarch Eve"));
  fireEvent.click(within(actions("Saltmarch Eve"))
    .getByRole("button", { name: "Anchor next scene" }));
  expect(sanitizeSeed(JSON.parse(probe()))).not.toBeNull();
});

test("Open ledger entry links to the record's ledger row", async () => {
  for (const [id, label, row] of [
    ["thread:mara-s-map", "Mara's map", "mara-s-map"],
    ["thread:winifred-s-chart", "Winifred's chart", "winifred-s-chart"],
    ["thread:mara/s:map", "Mara's first map", "mara/s:map"],
  ] as const) {
    const r = await ready(at(id));
    expect(within(actions(label)).getByRole("link", { name: "Open ledger entry" }))
      .toHaveAttribute("href", ledgerHref("c1", { section: "threads", row }));
    r.unmount();
  }
  await ready(at("commitment:mara-s-oath"));
  expect(within(actions("Mara's oath")).getByRole("link", { name: "Open ledger entry" }))
    .toHaveAttribute("href", ledgerHref("c1", { section: "commitments", row: "mara-s-oath" }));
});

test("a finding links to its review address", async () => {
  await ready(at("thread:mara-s-map"));
  const hrefs = within(section(detail("Mara's map"), "Findings"))
    .getAllByRole("link", { name: "Review this finding" })
    .map((a) => a.getAttribute("href"));
  expect(hrefs).toEqual([
    ledgerHref("c1", { section: "continuity", group: GROUP_OF.possible_relation,
                       candidate: "cand-map-oath" }),
    ledgerHref("c1", { section: "continuity", group: GROUP_OF.possible_thread_closure,
                       candidate: "cand-map-closure" }),
  ]);
});

test("Filter to this arc sets ?arc= and narrows the drawing; Show the whole graph clears it",
     async () => {
  await ready(at("thread:mara-s-map"));
  expect(nodeButton("thread:saltmarch-toll")).toBeInTheDocument();
  fireEvent.click(within(actions("Mara's map")).getByRole("button", { name: "Filter to this arc" }));
  expect(param("arc")).toBe("thread:mara-s-map");
  expect(queryNode("thread:saltmarch-toll")).not.toBeInTheDocument();
  fireEvent.click(within(actions("Mara's map"))
    .getByRole("button", { name: "Show the whole graph" }));
  expect(param("arc")).toBeNull();
  expect(nodeButton("thread:saltmarch-toll")).toBeInTheDocument();
  expect(within(actions("Mara's map")).getByRole("button", { name: "Filter to this arc" }))
    .toBeInTheDocument();
});

test("Filter to this arc on a merged record filters to its canonical", async () => {
  await ready(at("thread:winifred-s-chart"));
  fireEvent.click(within(actions("Winifred's chart"))
    .getByRole("button", { name: "Filter to this arc" }));
  expect(param("arc")).toBe("thread:mara-s-map");
  expect(search()).toContain("arc=thread%3Amara-s-map");
  expect(queryNode("thread:saltmarch-toll")).not.toBeInTheDocument();
  const clear = within(actions("Winifred's chart"))
    .getByRole("button", { name: "Show the whole graph" });
  fireEvent.click(clear);
  expect(param("arc")).toBeNull();
  expect(nodeButton("thread:saltmarch-toll")).toBeInTheDocument();
});

test('a stale reading shows its state and no false "undated"', async () => {
  const g = graphFixture();
  const map = g.nodes.find((n) => n.id === "thread:mara-s-map")!;
  if (map.kind !== "thread") throw new Error("fixture moved");
  map.pressure = { state: "stale", in_days: null, friendly: "" };
  vi.mocked(api.continuityGraph).mockResolvedValue(g);
  await ready(at("thread:mara-s-map"));
  const d = detail("Mara's map");
  expect(fact(d, "Pressure")).toContain(STATE_WORDS.stale);
  expect(d.textContent).not.toContain("undated");
});

test("a lifecycle finding shows on the node in the Continuity lens", async () => {
  await ready("/campaigns/c1/graph?lens=continuity");
  const b = nodeButton("thread:mara-s-map");
  expect(b.getAttribute("aria-label")).toContain("May be finished");
  expect(b.querySelector(".sg-node-status")?.textContent).toContain("May be finished");
});

test("an actor with no birthday node shows no Birthday section", async () => {
  const actors = FIX.nodes.filter((n) => n.kind === "character" || n.kind === "pc");
  const named = new Set(FIX.nodes.flatMap((n) => (n.kind === "birthday" ? [n.actor] : [])));
  const without = actors.find((n) => !named.has(n.id))!;
  const withOne = actors.find((n) => named.has(n.id))!;
  expect(without).toBeDefined();
  expect(withOne).toBeDefined();

  const first = await ready(at(without.id));
  expect(sideSection(detail(without.label), "Birthday")).toBeNull();
  first.unmount();
  await ready(at(withOne.id));
  expect(sideSection(detail(withOne.label), "Birthday")).not.toBeNull();
});

test("a chip selects the record it names", async () => {
  await ready(at("scene:001--saltmarch-harbour"));
  fireEvent.click(chip(section(detail("Saltmarch harbour"), "Cast"), "Mara"));
  expect(search()).toContain("node=characters%3Amara");
  expect(detail("Mara")).toBeInTheDocument();
});

test("actor, scene, event, idea and location details show their sections", async () => {
  // Actor: scenes, related active drivers, relationships, an upcoming birthday.
  let r = await ready(at("characters:mara"));
  let d = detail("Mara");
  expect(entries(section(d, "Scenes"))).toEqual(["Saltmarch harbour", "Mara's flashback"]);
  const drivers = section(d, "Active drivers");
  chip(drivers, "Mara's map");
  chip(drivers, "Mara's oath");
  const rel = section(d, "Relationships");
  chip(rel, "Seraphine");
  expect(rel).toHaveTextContent("trust 3");
  expect(rel).toHaveTextContent("affection 4");
  expect(rel).toHaveTextContent("tension 1");
  expect(rel).toHaveTextContent("Owes her a map.");
  chip(rel, "Winifred");
  expect(rel).toHaveTextContent("rival");
  chip(rel, "Mara's flashback");
  chip(section(d, "Birthday"), "Mara's birthday");
  noTokens(d);
  r.unmount();

  // Scene: cast, where, when, what moved here, and a way into it.
  r = await ready(at("scene:001--saltmarch-harbour"));
  d = detail("Saltmarch harbour");
  chip(section(d, "Cast"), "Seraphine");
  chip(section(d, "Where"), "Saltmarch harbour");
  expect(section(d, "When")).toHaveTextContent("2 March 1200");
  expect(entries(section(d, "Moved here"))).toEqual(["opened Mara's map", "opened Mara's first map"]);
  expect(within(d).getByRole("link", { name: "Open scene" }))
    .toHaveAttribute("href", "/campaigns/c1/scenes/001--saltmarch-harbour");
  noTokens(d);
  r.unmount();

  // Event: its date, in-days, status, linked records and findings.
  r = await ready(at("event:the-coronation"));
  d = detail("The coronation");
  expect(fact(d, "Date")).toBe("13 March 1200");
  expect(fact(d, "When")).toBe("in 3 days");
  expect(fact(d, "Status")).toBe("scheduled");
  const linked = section(d, "Linked");
  expect(linked).toHaveTextContent(RELATION_PHRASE.by.in);
  chip(linked, "Mara's oath");
  chip(linked, "The harbour meeting");
  expect(within(actions("The coronation")).getByRole("button", { name: "Anchor next scene" }))
    .toBeEnabled();
  noTokens(d);
  r.unmount();

  // Birthday: an undated month-precision occurrence and whose it is.
  r = await ready(at("birthday:characters:mara:month:1200-3"));
  d = detail("Mara's birthday");
  expect(fact(d, "Date")).toBe("Undated");
  expect(fact(d, "When")).toBe("day unknown");
  expect(fact(d, "Whose")).toBe("Mara");
  chip(section(d, "Linked"), "Mara");
  noTokens(d);
  r.unmount();

  // Idea: premise, date, what it serves (with its status) and its anchor.
  r = await ready(at("idea:the-harbour-meeting"));
  d = detail("The harbour meeting");
  expect(fact(d, "Premise")).toBe("Mara meets Seraphine at the harbour before the coronation.");
  expect(fact(d, "Date")).toBe("11 March 1200");
  const serves = section(d, "Serves");
  chip(serves, "Mara's map");
  expect(serves).toHaveTextContent(statusOf(byId("thread:mara-s-map"), IX));
  const anchor = section(d, "Anchored to");
  expect(anchor).toHaveTextContent(RELATION_PHRASE.before.out);
  chip(anchor, "The coronation");
  noTokens(d);
  r.unmount();

  // Location: the scenes set there.
  await ready(at("locations:saltmarch-harbour"));
  d = detail("Saltmarch harbour");
  chip(section(d, "Scenes"), "Saltmarch harbour");
  noTokens(d);
});

test("a serves edge shows the driver action it carries, from both ends", async () => {
  // Two ideas serve one thread with different actions; a third serves an
  // event. The edge layer is aria-hidden, so the action is read here or nowhere.
  const g = graphFixture();
  g.edges.push(
    { id: "e-letter-map", kind: "serves", from: "idea:winifred-s-letter",
      to: "thread:mara-s-map", source: "structural", relation: "close_candidate",
      candidate_id: null },
    { id: "e-letter-coronation", kind: "serves", from: "idea:winifred-s-letter",
      to: "event:the-coronation", source: "structural", relation: "address",
      candidate_id: null },
  );
  vi.mocked(api.continuityGraph).mockResolvedValue(g);
  const ix = indexGraph(g);
  const status = (id: string) => statusOf(ix.byId.get(id)!, ix);

  let r = await ready(at("idea:the-harbour-meeting"));
  let d = detail("The harbour meeting");
  expect(entries(section(d, "Serves")))
    .toEqual([`${ACTION_LABELS.advance} Mara's map ${status("thread:mara-s-map")}`]);
  noTokens(d);
  r.unmount();

  r = await ready(at("idea:winifred-s-letter"));
  d = detail("Winifred's letter");
  expect(entries(section(d, "Serves"))).toEqual([
    `${ACTION_LABELS.close_candidate} Mara's map ${status("thread:mara-s-map")}`,
    `${ACTION_LABELS.address} The coronation ${status("event:the-coronation")}`,
  ]);
  noTokens(d);
  r.unmount();

  r = await ready(at("thread:mara-s-map"));
  d = detail("Mara's map");
  const served = section(d, "Served by");
  expect(entries(served)).toEqual([
    `The harbour meeting ${ACTION_LABELS.advance}`,
    `Winifred's letter ${ACTION_LABELS.close_candidate}`,
  ]);
  fireEvent.click(chip(served, "Winifred's letter"));
  expect(detail("Winifred's letter")).toBeInTheDocument();
  r.unmount();

  await ready(at("event:the-coronation"));
  d = detail("The coronation");
  expect(entries(section(d, "Linked")))
    .toContain(`Served by Winifred's letter ${ACTION_LABELS.address}`);
  noTokens(d);
});

test("an idea anchored to a moment with no node names it without its ref", async () => {
  const g = graphFixture();
  const letter = g.nodes.find((n) => n.id === "idea:winifred-s-letter")!;
  if (letter.kind !== "idea") throw new Error("fixture moved");
  letter.time_anchor = { ref: "birthday:characters:winifred:exact:1200-05-01", relation: "on",
                         native: "1200-05-01" };
  vi.mocked(api.continuityGraph).mockResolvedValue(g);
  const first = await ready(at("idea:winifred-s-letter"));
  let anchor = section(detail("Winifred's letter"), "Anchored to");
  expect(anchor).toHaveTextContent("a birthday");
  expect(anchor).toHaveTextContent("1200-05-01");
  expect(anchor.textContent).not.toContain("characters:winifred");
  expect(within(anchor).queryByRole("button")).toBeNull();
  first.unmount();

  letter.time_anchor = { ref: "holiday:739090:Realm Day", relation: "after", native: "" };
  vi.mocked(api.continuityGraph).mockResolvedValue(g);
  await ready(at("idea:winifred-s-letter"));
  anchor = section(detail("Winifred's letter"), "Anchored to");
  expect(anchor).toHaveTextContent("a holiday");
  expect(anchor).toHaveTextContent("no longer upcoming");
  expect(anchor.textContent).not.toContain("739090");
});

test("a bond whose scene was deleted says so, never the filename", async () => {
  const g = graphFixture();
  const bond = g.edges.find((e) => e.kind === "bond")!;
  if (bond.kind !== "bond") throw new Error("fixture moved");
  bond.since_scene = "009--saltmarch-gone";
  vi.mocked(api.continuityGraph).mockResolvedValue(g);
  await ready(at("characters:winifred"));
  const rel = section(detail("Winifred"), "Relationships");
  expect(rel).toHaveTextContent("a deleted scene");
  expect(rel.textContent).not.toContain("009--");
});
