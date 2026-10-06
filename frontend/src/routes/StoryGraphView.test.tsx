import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api } from "../api/client";
import type { GraphNode, ShellPayload, StoryGraph } from "../api/types";
import { accessibleName, indexGraph, statusOf } from "../components/storyGraph/model";
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
