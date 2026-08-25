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

/** PLOT plus a fourth greeting nothing is linked to, for the tests that need
 *  two links between DIFFERENT pairs (every pair in PLOT is already taken, and
 *  this view refuses to draw a second line between one). */
function plot4() {
  (api.listGreetings as any).mockResolvedValue([
    greeting("dawn", "Saltmarch Dawn"), greeting("ledger", "The Ledger"),
    greeting("word", "A Quiet Word"), greeting("vow", "Vow of Silence"),
  ]);
}

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

test("only one write is on the wire at a time, whichever greeting it is for", async () => {
  // `store.greetings.set_edges` is an unlocked read-modify-write of one
  // plotmap.json holding every greeting's edges, so two requests in flight at
  // once -- even for different greetings -- can lose one of the two.
  const gate: (() => void)[] = [];
  (api.setEdges as any).mockImplementation(() => new Promise<void>((res) => gate.push(res)));
  plot4();
  render(<PlotMapEditor scope={SCOPE} />);
  await screen.findByRole("button", { name: "Open Saltmarch Dawn" });

  fireEvent.click(screen.getByRole("button", { name: "Link from Saltmarch Dawn" }));
  fireEvent.click(screen.getByRole("button", { name: "Link Saltmarch Dawn to A Quiet Word" }));
  await waitFor(() => expect(api.setEdges).toHaveBeenCalledTimes(1));

  // while it is out, no second one can be started
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Link from The Ledger" })).toBeDisabled());
  gate[0]();

  // ...and once it lands, the map is writable again
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Link from The Ledger" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Link from The Ledger" }));
  fireEvent.click(screen.getByRole("button", { name: "Link The Ledger to Vow of Silence" }));
  await waitFor(() => expect(api.setEdges).toHaveBeenCalledTimes(2));
  expect((api.setEdges as any).mock.calls[1][1]).toBe("ledger");
  gate.forEach((res) => res());
});

test("both directions of one exclusion cannot be converted at once", async () => {
  plot({
    dawn: { leads_to: [], excludes: ["ledger"] },
    ledger: { leads_to: [], excludes: ["dawn"] },
    word: { leads_to: [], excludes: [] },
  });
  const gate: (() => void)[] = [];
  (api.setEdges as any).mockImplementation(() => new Promise<void>((res) => gate.push(res)));
  render(<PlotMapEditor scope={SCOPE} />);
  await screen.findByRole("button", { name: "Open Saltmarch Dawn" });
  fireEvent.click(edge("Excludes: Saltmarch Dawn ↮ The Ledger"));

  fireEvent.click(screen.getByRole("button", { name: "Unlock: Saltmarch Dawn → The Ledger" }));
  await waitFor(() => expect(api.setEdges).toHaveBeenCalledTimes(1));
  // Starting the opposite conversion mid-flight would interleave two
  // optimistic states and end with BOTH directions stored -- a second link
  // between one pair, which nothing else here permits.
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Unlock: The Ledger → Saltmarch Dawn" })).toBeDisabled());
  gate.forEach((res) => res());
});

test("a failed write re-reads the map rather than keeping what it guessed", async () => {
  const refused = Object.assign(new Error("refused"), { detail: "plot map is locked" });
  (api.setEdges as any).mockRejectedValue(refused);
  render(<PlotMapEditor scope={SCOPE} />);
  fireEvent.click(await screen.findByRole("button", { name: "Unlocks: Saltmarch Dawn → The Ledger" }));
  fireEvent.click(screen.getByRole("button", { name: /delete link/i }));

  await screen.findByText("plot map is locked");
  await waitFor(() => expect(api.listGreetings).toHaveBeenCalledTimes(2));
  expect(api.setEdges).toHaveBeenCalledTimes(1);
});

test("a mutation half-done when the scope changes does not finish into the old one", async () => {
  plot({
    dawn: { leads_to: [], excludes: ["ledger"] },
    ledger: { leads_to: [], excludes: ["dawn"] },
    word: { leads_to: [], excludes: [] },
  });
  const gate: (() => void)[] = [];
  (api.setEdges as any).mockImplementation(() => new Promise<void>((res) => gate.push(res)));
  const { rerender } = render(<PlotMapEditor scope={SCOPE} />);
  await screen.findByRole("button", { name: "Open Saltmarch Dawn" });

  // deleting a mutual exclusion is two writes; the far half is still to come
  fireEvent.click(edge("Excludes: Saltmarch Dawn ↮ The Ledger"));
  fireEvent.click(screen.getByRole("button", { name: /delete link/i }));
  await waitFor(() => expect(api.setEdges).toHaveBeenCalledTimes(1));

  (api.listGreetings as any).mockResolvedValue([]);
  rerender(<PlotMapEditor scope={{ kind: "world", id: "other" }} />);
  gate[0]();

  // The continuation would read the NEW scope's edges and write them to the
  // OLD one -- an id the new map may not even have, sent back with empty
  // arrays. It stops instead.
  await waitFor(() => expect(screen.queryByRole("button", { name: "Open Saltmarch Dawn" })).toBeNull());
  expect(api.setEdges).toHaveBeenCalledTimes(1);
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

test("a greeting whose edges cannot be read is a node, but not a link source", async () => {
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

  // A write is the greeting's WHOLE pair of arrays, so linking from a node
  // whose arrays never arrived would send the empty ones this guessed and
  // delete whatever it could not see.
  expect(screen.getByRole("button", { name: "Link from A Quiet Word" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Link from The Ledger" })).toBeEnabled();

  // Retry re-reads, and the node becomes a source once its edges land
  (api.readGreeting as any).mockImplementation(async (_s: unknown, gid: string) => ({
    meta: greeting(gid, gid), body: "", rev: "r1", predecessors: [], edges: PLOT[gid],
  }));
  fireEvent.click(screen.getByRole("button", { name: /retry/i }));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Link from A Quiet Word" })).toBeEnabled());
  expect(screen.queryByText(/could not be read/i)).toBeNull();
});

test("a scope change clears the map instead of leaving the old one clickable", async () => {
  const { rerender } = render(<PlotMapEditor scope={SCOPE} />);
  await screen.findByRole("button", { name: "Open Saltmarch Dawn" });

  // The next scope's list has not answered yet -- and while it has not, a node
  // still on screen is one whose id and edges would be written into the world
  // the reader has moved to.
  (api.listGreetings as any).mockReturnValue(new Promise(() => {}));
  rerender(<PlotMapEditor scope={{ kind: "world", id: "other" }} />);
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: "Open Saltmarch Dawn" })).toBeNull());
  expect(document.querySelector(".pm-canvas")).toBeNull();
});

test("a cycle is drawn in as many columns as it has greetings, not more", async () => {
  (api.listGreetings as any).mockResolvedValue([
    greeting("dawn", "Saltmarch Dawn"),
    greeting("ledger", "The Ledger"),
    greeting("word", "A Quiet Word"),
  ]);
  (api.readGreeting as any).mockImplementation(async (_s: unknown, gid: string) => ({
    meta: greeting(gid, gid), body: "", rev: "r1", predecessors: [],
    edges: { leads_to: [{ dawn: "ledger", ledger: "word", word: "dawn" }[gid]!], excludes: [] },
  }));
  const { container } = render(<PlotMapEditor scope={SCOPE} />);
  await screen.findByRole("button", { name: "Open Saltmarch Dawn" });

  // Three greetings in a loop are three columns: PAD + 2 strides + a box + PAD.
  // Relaxation over a cycle has no fixed point, so the depth pass has to cut
  // the loop rather than merely stop -- unbounded, it walks the nodes out to
  // an enormous mostly-empty canvas.
  const canvas = container.querySelector(".pm-canvas") as HTMLElement;
  expect(canvas.style.width).toBe("712px");
  expect(container.querySelectorAll(".pm-edge")).toHaveLength(3);
});

test("two links between one pair are drawn apart so each can be selected", async () => {
  // Not something this view can author -- it refuses a second link between a
  // pair -- but the chip editor never did, and a curve depends only on its
  // endpoints, so the later line's hit target would cover the earlier one.
  plot({
    dawn: { leads_to: ["ledger"], excludes: ["ledger"] },
    ledger: { leads_to: [], excludes: [] },
    word: { leads_to: [], excludes: [] },
  });
  const { container } = render(<PlotMapEditor scope={SCOPE} />);
  await screen.findByRole("button", { name: "Open Saltmarch Dawn" });
  await waitFor(() => expect(container.querySelectorAll(".pm-edge")).toHaveLength(2));

  const paths = [...container.querySelectorAll(".pm-edge .pm-line")]
    .map((p) => p.getAttribute("d"));
  expect(paths[0]).not.toEqual(paths[1]);
});

test("ids that contain spaces cannot collide into one exclusion", async () => {
  (api.listGreetings as any).mockResolvedValue([
    greeting("a", "A"), greeting("b c", "B C"), greeting("a b", "A B"), greeting("c", "C"),
  ]);
  const map: Record<string, { leads_to: string[]; excludes: string[] }> = {
    a: { leads_to: [], excludes: ["b c"] },
    "a b": { leads_to: [], excludes: ["c"] },
    "b c": { leads_to: [], excludes: [] },
    c: { leads_to: [], excludes: [] },
  };
  (api.readGreeting as any).mockImplementation(async (_s: unknown, gid: string) => ({
    meta: greeting(gid, gid), body: "", rev: "r1", predecessors: [], edges: map[gid],
  }));
  const { container } = render(<PlotMapEditor scope={SCOPE} />);
  await screen.findByRole("button", { name: "Open A B" });
  // ("a", "b c") and ("a b", "c") are different pairs, whatever a naive join says
  await waitFor(() => expect(container.querySelectorAll(".pm-edge")).toHaveLength(2));
});

test("an exclusion converts to an unlock in the direction the reader picks", async () => {
  render(<PlotMapEditor scope={SCOPE} />);
  fireEvent.click(await screen.findByRole("button", { name: "Excludes: The Ledger ↮ A Quiet Word" }));

  // Both directions are offered: an exclusion is undirected and an unlock is
  // not, so which greeting becomes the source is the reader's to say rather
  // than the map's iteration order.
  expect(screen.getByRole("button", { name: "Unlock: The Ledger → A Quiet Word" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Unlock: A Quiet Word → The Ledger" }));

  await waitFor(() => expect(api.setEdges).toHaveBeenCalledWith(
    SCOPE, "word", { leads_to: ["ledger"], excludes: [] }));
  await screen.findByRole("button", { name: "Unlocks: A Quiet Word → The Ledger" });
});

test("flipping a mutual exclusion clears the far half before the unlock lands", async () => {
  plot({
    dawn: { leads_to: [], excludes: ["ledger"] },
    ledger: { leads_to: [], excludes: ["dawn"] },
    word: { leads_to: [], excludes: [] },
  });
  render(<PlotMapEditor scope={SCOPE} />);
  fireEvent.click(await screen.findByRole("button", { name: "Excludes: Saltmarch Dawn ↮ The Ledger" }));
  fireEvent.click(screen.getByRole("button", { name: "Unlock: Saltmarch Dawn → The Ledger" }));

  await waitFor(() => expect(api.setEdges).toHaveBeenCalledTimes(2));
  // The far half first: until it is gone, an unlock on the near half would
  // coexist with an exclusion that still blocks the greeting it opens.
  expect((api.setEdges as any).mock.calls[0].slice(1))
    .toEqual(["ledger", { leads_to: [], excludes: [] }]);
  expect((api.setEdges as any).mock.calls[1].slice(1))
    .toEqual(["dawn", { leads_to: ["ledger"], excludes: [] }]);
});

test("selecting an edge from the keyboard leaves link mode, as the pointer does", async () => {
  const onOpen = vi.fn();
  render(<PlotMapEditor scope={SCOPE} onOpenGreeting={onOpen} />);
  fireEvent.click(await screen.findByRole("button", { name: "Link from Saltmarch Dawn" }));
  fireEvent.keyDown(screen.getByRole("button", { name: "Excludes: The Ledger ↮ A Quiet Word" }),
                    { key: "Enter" });

  // still armed, the next node click would draw a line nobody asked for
  fireEvent.click(screen.getByRole("button", { name: "Open A Quiet Word" }));
  expect(onOpen).toHaveBeenCalledWith("word");
  expect(api.setEdges).not.toHaveBeenCalled();
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

test("a conversion whose source is unread is refused before it deletes anything", async () => {
  // dawn and ledger exclude each other; ledger's edges never arrive.
  plot({
    dawn: { leads_to: [], excludes: ["ledger"] },
    ledger: { leads_to: [], excludes: ["dawn"] },
    word: { leads_to: [], excludes: [] },
  });
  const real = (api.readGreeting as any).getMockImplementation();
  (api.readGreeting as any).mockImplementation(async (s: unknown, gid: string) => {
    if (gid === "ledger") throw new Error("boom");
    return real(s, gid);
  });
  render(<PlotMapEditor scope={SCOPE} />);
  await screen.findByText(/could not be read/i);

  fireEvent.click(edge("Excludes: Saltmarch Dawn ↮ The Ledger"));
  fireEvent.click(screen.getByRole("button", { name: "Unlock: The Ledger → Saltmarch Dawn" }));

  // Clearing the far half and then being refused on the near one would delete
  // an authored exclusion in the course of failing to replace it.
  await screen.findByText(/The Ledger: links could not be read/i);
  expect(api.setEdges).not.toHaveBeenCalled();
  expect(edge("Excludes: Saltmarch Dawn ↮ The Ledger")).toBeInTheDocument();
});

test("the edge toolbar is gone while the map is re-reading", async () => {
  const real = (api.readGreeting as any).getMockImplementation();
  (api.readGreeting as any).mockImplementation(async (sc: unknown, gid: string) => {
    if (gid === "word") throw new Error("boom");
    return real(sc, gid);
  });
  render(<PlotMapEditor scope={SCOPE} />);
  await screen.findByText(/could not be read/i);
  fireEvent.click(edge("Unlocks: Saltmarch Dawn → The Ledger"));
  expect(screen.getByRole("button", { name: /delete link/i })).toBeInTheDocument();

  // Retry, held open: the reload will replace every greeting's edges with what
  // the server says, so an edit made against the old ones in the meantime is
  // one the snapshot would silently undo.
  (api.listGreetings as any).mockImplementation(() => new Promise(() => {}));
  fireEvent.click(screen.getByRole("button", { name: /retry/i }));
  await waitFor(() => expect(screen.queryByRole("button", { name: /delete link/i })).toBeNull());
});

test("a list that failed is not reported as a map with nothing in it", async () => {
  (api.listGreetings as any).mockRejectedValue({ detail: "world not found" });
  render(<PlotMapEditor scope={SCOPE} />);

  await screen.findByText("world not found");
  // "no greetings" and "we could not ask" are different answers
  expect(screen.queryByText(/no greetings to map/i)).toBeNull();
});
