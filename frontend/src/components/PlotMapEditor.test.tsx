import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { PlotMapEditor } from "./PlotMapEditor";

vi.mock("../api/client", () => ({
  api: {
    listGreetings: vi.fn(),
    readGreeting: vi.fn(),
    setEdges: vi.fn(),
  },
}));
import { api } from "../api/client";

const SCOPE = { kind: "world" as const, id: "w" };

/** A greeting summary as `listGreetings` returns one. */
function greeting(id: string, name: string, extra: Record<string, unknown> = {}) {
  return { id, name, character: "seraphine", version: "default", present: [],
           requires_tags: [], predecessor_join: "all" as const, ...extra };
}

/** The three-greeting plot the suite works against: dawn unlocks ledger, and
 *  ledger and word exclude each other in one direction only. */
const PLOT: Record<string, { leads_to: string[]; excludes: string[] }> = {
  dawn: { leads_to: ["ledger"], excludes: [] },
  ledger: { leads_to: [], excludes: ["word"] },
  word: { leads_to: [], excludes: [] },
};

function plot(map: Record<string, { leads_to: string[]; excludes: string[] }> = PLOT) {
  (api.listGreetings as any).mockResolvedValue([
    greeting("dawn", "Saltmarch Dawn"),
    greeting("ledger", "The Ledger"),
    greeting("word", "A Quiet Word"),
  ]);
  (api.readGreeting as any).mockImplementation(async (_s: unknown, gid: string) => ({
    meta: greeting(gid, gid), body: "", rev: "r1", predecessors: [],
    edges: map[gid] ?? { leads_to: [], excludes: [] },
  }));
}

beforeEach(() => {
  vi.clearAllMocks();
  (api.setEdges as any).mockResolvedValue({ ok: true });
  plot();
});

const edge = (label: RegExp | string) => screen.getByRole("button", { name: label });

test("every greeting is a node, and the two edge kinds render distinctly", async () => {
  const { container } = render(<PlotMapEditor scope={SCOPE} />);

  await screen.findByRole("button", { name: "Open Saltmarch Dawn" });
  expect(screen.getByRole("button", { name: "Open The Ledger" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Open A Quiet Word" })).toBeInTheDocument();
  expect(api.readGreeting).toHaveBeenCalledWith(SCOPE, "dawn");

  // An unlock is directed and an exclusion is not, so they are not the same
  // line with a different colour -- they carry different classes and say
  // different things.
  expect(edge("Unlocks: Saltmarch Dawn → The Ledger"))
    .toBe(container.querySelector(".pm-edge.leads_to"));
  expect(edge("Excludes: The Ledger ↮ A Quiet Word"))
    .toBe(container.querySelector(".pm-edge.excludes"));
  expect(container.querySelectorAll(".pm-edge")).toHaveLength(2);
});

test("a node opens its greeting in the editor", async () => {
  const onOpen = vi.fn();
  render(<PlotMapEditor scope={SCOPE} onOpenGreeting={onOpen} />);
  fireEvent.click(await screen.findByRole("button", { name: "Open The Ledger" }));
  expect(onOpen).toHaveBeenCalledWith("ledger");
  expect(api.setEdges).not.toHaveBeenCalled();
});

test("linking source then target writes the source's whole edge set", async () => {
  render(<PlotMapEditor scope={SCOPE} />);
  fireEvent.click(await screen.findByRole("button", { name: "Link from Saltmarch Dawn" }));
  // while linking, a node is a target rather than a way into the editor
  fireEvent.click(screen.getByRole("button", { name: "Link Saltmarch Dawn to A Quiet Word" }));

  // The whole array, including the edge that was already there: the route
  // REPLACES what the source holds rather than appending to it.
  await waitFor(() => expect(api.setEdges).toHaveBeenCalledWith(
    SCOPE, "dawn", { leads_to: ["ledger", "word"], excludes: [] }));
  // the new arrow is on screen without a reload
  await screen.findByRole("button", { name: "Unlocks: Saltmarch Dawn → A Quiet Word" });
  expect(api.listGreetings).toHaveBeenCalledTimes(1);
});

test("a pair that is already linked is refused rather than quietly relinked", async () => {
  render(<PlotMapEditor scope={SCOPE} />);
  // ledger already excludes word; drawing an unlock over it would delete an
  // authored exclusion on one click, so it says what is in the way instead.
  fireEvent.click(await screen.findByRole("button", { name: "Link from The Ledger" }));
  fireEvent.click(screen.getByRole("button", { name: "Link The Ledger to A Quiet Word" }));

  await screen.findByText(/already linked/i);
  expect(api.setEdges).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Excludes: The Ledger ↮ A Quiet Word" }))
    .toBeInTheDocument();
});

test("the link kind chip decides which array a new link lands in", async () => {
  render(<PlotMapEditor scope={SCOPE} />);
  await screen.findByRole("button", { name: "Open Saltmarch Dawn" });
  fireEvent.click(screen.getByRole("button", { name: "Excludes" }));
  fireEvent.click(screen.getByRole("button", { name: "Link from Saltmarch Dawn" }));
  fireEvent.click(screen.getByRole("button", { name: "Link Saltmarch Dawn to A Quiet Word" }));

  await waitFor(() => expect(api.setEdges).toHaveBeenCalledWith(
    SCOPE, "dawn", { leads_to: ["ledger"], excludes: ["word"] }));
});

test("a link to itself is refused, and clicking the source again cancels", async () => {
  render(<PlotMapEditor scope={SCOPE} />);
  fireEvent.click(await screen.findByRole("button", { name: "Link from Saltmarch Dawn" }));
  fireEvent.click(screen.getByRole("button", { name: "Cancel link from Saltmarch Dawn" }));

  expect(api.setEdges).not.toHaveBeenCalled();
  // back to opening greetings, not still armed
  expect(screen.getByRole("button", { name: "Open The Ledger" })).toBeInTheDocument();
});

test("selecting an edge deletes it out of the source's array", async () => {
  render(<PlotMapEditor scope={SCOPE} />);
  fireEvent.click(await screen.findByRole("button", { name: "Unlocks: Saltmarch Dawn → The Ledger" }));
  fireEvent.click(screen.getByRole("button", { name: /delete link/i }));

  await waitFor(() => expect(api.setEdges).toHaveBeenCalledWith(
    SCOPE, "dawn", { leads_to: [], excludes: [] }));
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: /Unlocks: Saltmarch Dawn/ })).toBeNull());
});

test("flipping an unlock moves it to excludes in one write", async () => {
  render(<PlotMapEditor scope={SCOPE} />);
  fireEvent.click(await screen.findByRole("button", { name: "Unlocks: Saltmarch Dawn → The Ledger" }));
  fireEvent.click(screen.getByRole("button", { name: /make exclusion/i }));

  await waitFor(() => expect(api.setEdges).toHaveBeenCalledWith(
    SCOPE, "dawn", { leads_to: [], excludes: ["ledger"] }));
  await screen.findByRole("button", { name: "Excludes: Saltmarch Dawn ↮ The Ledger" });
});

test("an exclusion recorded from both sides is one line, and deleting it clears both", async () => {
  plot({
    dawn: { leads_to: [], excludes: ["ledger"] },
    ledger: { leads_to: [], excludes: ["dawn"] },
    word: { leads_to: [], excludes: [] },
  });
  const { container } = render(<PlotMapEditor scope={SCOPE} />);
  await screen.findByRole("button", { name: "Open Saltmarch Dawn" });
  await waitFor(() => expect(container.querySelectorAll(".pm-edge")).toHaveLength(1));

  fireEvent.click(edge("Excludes: Saltmarch Dawn ↮ The Ledger"));
  fireEvent.click(screen.getByRole("button", { name: /delete link/i }));

  await waitFor(() => expect(api.setEdges).toHaveBeenCalledWith(
    SCOPE, "dawn", { leads_to: [], excludes: [] }));
  await waitFor(() => expect(api.setEdges).toHaveBeenCalledWith(
    SCOPE, "ledger", { leads_to: [], excludes: [] }));
});

test("a write that fails says so and leaves the drawn map alone", async () => {
  (api.setEdges as any).mockRejectedValue({ detail: "plot map is locked" });
  render(<PlotMapEditor scope={SCOPE} />);
  fireEvent.click(await screen.findByRole("button", { name: "Unlocks: Saltmarch Dawn → The Ledger" }));
  fireEvent.click(screen.getByRole("button", { name: /delete link/i }));

  await screen.findByText("plot map is locked");
  expect(screen.getByRole("button", { name: "Unlocks: Saltmarch Dawn → The Ledger" }))
    .toBeInTheDocument();
});

test("a greeting whose edges cannot be read is still a node", async () => {
  (api.readGreeting as any).mockImplementation(async (_s: unknown, gid: string) => {
    if (gid === "word") throw new Error("boom");
    return { meta: greeting(gid, gid), body: "", rev: "r1", predecessors: [],
             edges: PLOT[gid] };
  });
  render(<PlotMapEditor scope={SCOPE} />);
  await screen.findByRole("button", { name: "Open A Quiet Word" });
  await screen.findByText(/could not be read/i);
  // ...and the edges that DID read are still drawn
  expect(screen.getByRole("button", { name: "Unlocks: Saltmarch Dawn → The Ledger" }))
    .toBeInTheDocument();
});

test("with no greetings the map says so rather than drawing an empty grid", async () => {
  (api.listGreetings as any).mockResolvedValue([]);
  const { container } = render(<PlotMapEditor scope={SCOPE} />);
  await screen.findByText(/no greetings/i);
  expect(container.querySelector(".pm-canvas")).toBeNull();
});

test("a campaign's marks ride along on the nodes", async () => {
  (api.listGreetings as any).mockResolvedValue([
    greeting("dawn", "Saltmarch Dawn", { mark: "completed" }),
    greeting("ledger", "The Ledger", { mark: "skipped" }),
  ]);
  const { container } = render(<PlotMapEditor scope={{ kind: "campaign", id: "run" }} />);
  await screen.findByRole("button", { name: "Open Saltmarch Dawn" });
  const node = container.querySelector(".pm-node.completed") as HTMLElement;
  expect(within(node).getByText("Saltmarch Dawn")).toBeInTheDocument();
  expect(container.querySelector(".pm-node.skipped")).not.toBeNull();
});
