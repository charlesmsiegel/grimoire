import { MemoryRouter, useLocation, useNavigate } from "react-router-dom";
import { render, screen, fireEvent, waitFor, within, act } from "@testing-library/react";
import { GreetingEditor } from "./GreetingEditor";

vi.mock("../api/client", async () => (await import("../testkit/greetingEditorMocks")).greetingApiMock());
import { api } from "../api/client";
import type { GreetingDetail } from "../api/types";
import { fixture } from "../testkit/fixture";
import { Wrap, GREETINGS_DEFAULTS, greetingsWith, renderGreetings, greetingFixture,
         greeting, fail, installGreetingMocks, type Props } from "../testkit/greetingEditorHarness";

beforeEach(installGreetingMocks);

test("a refresh that lands after the reader starts editing is discarded", async () => {
  vi.mocked(api.listGreetings).mockResolvedValue(fixture([greeting("dawn", "Saltmarch Dawn", "seraphine", [])]));
  let land: (v: GreetingDetail) => void = () => {};
  const detail = fixture<GreetingDetail>({
    meta: { id: "dawn", name: "Saltmarch Dawn", character: "seraphine", version: "default",
            present: [], requires_tags: [], predecessor_join: "all" },
    body: "hi", edges: { leads_to: [], excludes: [] }, predecessors: [], rev: "r1",
  });
  vi.mocked(api.readGreeting).mockResolvedValue(detail);
  const { rerender } = render(
    <Wrap scope={{ kind: "world", id: "w" }} wid="w" refreshKey={0} selected="dawn" />);
  await screen.findByRole("button", { name: /^edit$/i });

  // the other view writes; this refresh read is slow
  vi.mocked(api.readGreeting).mockImplementation(() => new Promise((res) => { land = res; }));
  rerender(<Wrap scope={{ kind: "world", id: "w" }} wid="w" refreshKey={1} selected="dawn" />);

  // ...and while it is out, the reader starts a draft
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Half-written" } });
  land(detail);

  // The read was launched while this greeting was merely being READ. Landing
  // it now would replace the form and drop the mode back to view -- the draft
  // gone with no Save and no Cancel.
  await waitFor(() => expect(screen.getByLabelText("Name")).toHaveValue("Half-written"));
});

test("the editor holds still while the map's own write is on the wire", async () => {
  vi.mocked(api.listGreetings).mockResolvedValue(fixture([greeting("dawn", "Saltmarch Dawn", "seraphine", [])]));
  vi.mocked(api.readGreeting).mockResolvedValue(fixture({
    meta: { id: "dawn", name: "Saltmarch Dawn", character: "seraphine", version: "default",
            present: [], requires_tags: [], predecessor_join: "all" },
    body: "hi", edges: { leads_to: [], excludes: [] }, predecessors: [], rev: "r1",
  }));
  render(
    <Wrap scope={{ kind: "world", id: "w" }} wid="w" selected="dawn"
                    hold="The plot map is still writing these links." />);
  fireEvent.click(await screen.findByRole("button", { name: /^edit$/i }));

  // Both writers send whole arrays, so two overlapping saves can each discard
  // the other's edit in the one plotmap.json underneath.
  expect(screen.getByRole("button", { name: "Save greeting" })).toBeDisabled();
  expect(api.updateGreeting).not.toHaveBeenCalled();
});

test("an unsaved edge change is reported, so the map can hold still for it", async () => {
  vi.mocked(api.listGreetings).mockResolvedValue(fixture([
    greeting("dawn", "Saltmarch Dawn", "seraphine", []),
    greeting("vow", "Vow of Silence", "seraphine", []),
  ]));
  vi.mocked(api.readGreeting).mockResolvedValue(fixture({
    meta: { id: "dawn", name: "Saltmarch Dawn", character: "seraphine", version: "default",
            present: [], requires_tags: [], predecessor_join: "all" },
    body: "hi", edges: { leads_to: [], excludes: [] }, predecessors: [], rev: "r1",
  }));
  const onEdgeDraft = vi.fn();
  render(
    <Wrap scope={{ kind: "world", id: "w" }} wid="w" onEdgeDraft={onEdgeDraft} selected="dawn" />);
  fireEvent.click(await screen.findByRole("button", { name: /^edit$/i }));
  await waitFor(() => expect(onEdgeDraft).toHaveBeenLastCalledWith(false));

  const leadsTo = screen.getByText("Leads to").parentElement as HTMLElement;
  fireEvent.click(within(leadsTo).getByRole("button", { name: "Vow of Silence" }));
  // The untouched-list rule only protects drafts nobody touched; once the
  // chips move, this save WILL replace the arrays, so the map must wait.
  await waitFor(() => expect(onEdgeDraft).toHaveBeenLastCalledWith(true));
});

test("Delete is held while the map is writing, as Save is", async () => {
  vi.mocked(api.listGreetings).mockResolvedValue(fixture([greeting("dawn", "Saltmarch Dawn", "seraphine", [])]));
  vi.mocked(api.readGreeting).mockResolvedValue(fixture({
    meta: { id: "dawn", name: "Saltmarch Dawn", character: "seraphine", version: "default",
            present: [], requires_tags: [], predecessor_join: "all" },
    body: "hi", edges: { leads_to: [], excludes: [] }, predecessors: [], rev: "r1",
  }));
  render(
    <Wrap scope={{ kind: "world", id: "w" }} wid="w" selected="dawn"
                    hold="The plot map is still writing these links." />);
  fireEvent.click(await screen.findByRole("button", { name: /^edit$/i }));

  // `delete_greeting` sweeps this id out of every other greeting's edges, so
  // it and an in-flight map write are two unlocked rewrites of one plotmap.
  expect(screen.getByRole("button", { name: /^delete$/i })).toBeDisabled();
  expect(api.deleteGreeting).not.toHaveBeenCalled();
});

test("busy stays true until the second of two overlapping saves settles", async () => {
  vi.mocked(api.listGreetings).mockResolvedValue(fixture([greeting("dawn", "Saltmarch Dawn", "seraphine", [])]));
  vi.mocked(api.readGreeting).mockResolvedValue(fixture({
    meta: { id: "dawn", name: "Saltmarch Dawn", character: "seraphine", version: "default",
            present: [], requires_tags: [], predecessor_join: "all" },
    body: "hi", edges: { leads_to: [], excludes: [] }, predecessors: [], rev: "r1",
  }));
  const gate: (() => void)[] = [];
  vi.mocked(api.updateGreeting).mockImplementation(() =>
    new Promise<Awaited<ReturnType<typeof api.updateGreeting>>>((res) => gate.push(() => res({ ok: true }))));
  const onBusy = vi.fn();
  render(
    <Wrap scope={{ kind: "world", id: "w" }} wid="w" onBusy={onBusy} selected="dawn" />);
  fireEvent.click(await screen.findByRole("button", { name: /^edit$/i }));

  const save = screen.getByRole("button", { name: "Save greeting" });
  fireEvent.click(save);
  await waitFor(() => expect(onBusy).toHaveBeenCalledWith(true));
  // the button is held while its own write is out, so a second cannot start
  await waitFor(() => expect(save).toBeDisabled());
  expect(api.updateGreeting).toHaveBeenCalledTimes(1);

  gate[0]();
  await waitFor(() => expect(onBusy).toHaveBeenLastCalledWith(false));
});

test("a graph write rebases a clean draft's chips without touching the form", async () => {
  vi.mocked(api.listGreetings).mockResolvedValue(fixture([
    greeting("dawn", "Saltmarch Dawn", "seraphine", []),
    greeting("vow", "Vow of Silence", "seraphine", []),
  ]));
  const before = fixture<GreetingDetail>({
    meta: { id: "dawn", name: "Saltmarch Dawn", character: "seraphine", version: "default",
            present: [], requires_tags: [], predecessor_join: "all" },
    body: "hi", edges: { leads_to: [], excludes: [] }, predecessors: [], rev: "r1",
  });
  vi.mocked(api.readGreeting).mockResolvedValue(before);
  const { rerender } = render(
    <Wrap scope={{ kind: "world", id: "w" }} wid="w" refreshKey={0} selected="dawn" />);
  fireEvent.click(await screen.findByRole("button", { name: /^edit$/i }));
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Half-written" } });

  // the map draws an edge on this greeting while the draft sits open
  vi.mocked(api.readGreeting).mockResolvedValue(fixture({
    ...before, edges: { leads_to: ["vow"], excludes: [] },
  }));
  rerender(<Wrap scope={{ kind: "world", id: "w" }} wid="w" refreshKey={1} selected="dawn" />);

  // The typing survives -- and the chips catch up, so touching one later does
  // not send a pre-map array back over the edge that was just drawn.
  await waitFor(() => {
    const leadsTo = screen.getByText("Leads to").parentElement as HTMLElement;
    expect(within(leadsTo).getByRole("button", { name: "Vow of Silence" }))
      .toHaveClass("chip", "on");
  });
  expect(screen.getByLabelText("Name")).toHaveValue("Half-written");
});

// The route can never show the graph while a record segment is selected
// (`WorldView` forces the list), so a VIEWED record and a live-writing map
// no longer coexist through routing -- this exercises the `mode === "view"`
// half of the refresh directly, the way the map would have reached it before.
test("a refresh re-reads a viewed record's whole detail, not merely its chips", async () => {
  vi.mocked(api.listGreetings).mockResolvedValue(fixture([greeting("dawn", "Saltmarch Dawn", "seraphine", [])]));
  vi.mocked(api.readGreeting).mockResolvedValue(fixture({
    meta: { id: "dawn", name: "Saltmarch Dawn", character: "seraphine", version: "default",
            present: [], requires_tags: [], predecessor_join: "all" },
    body: "hi", edges: { leads_to: [], excludes: [] }, predecessors: [], rev: "r1",
  }));
  const { rerender } = render(
    <Wrap scope={{ kind: "world", id: "w" }} wid="w" refreshKey={0} selected="dawn" />);
  await screen.findByRole("button", { name: /^edit$/i });   // arrived read-only, no draft started
  const readsBefore = vi.mocked(api.readGreeting).mock.calls.length;

  rerender(<Wrap scope={{ kind: "world", id: "w" }} wid="w" refreshKey={1} selected="dawn" />);
  await waitFor(() =>
    expect(vi.mocked(api.readGreeting).mock.calls.length).toBeGreaterThan(readsBefore));
});

test("creating a greeting includes phase sequence and optional metadata", async () => {
  render(<Wrap scope={{ kind: "world", id: "w" }} wid="w" />);
  await waitFor(() => expect(api.listCharacters).toHaveBeenCalled());
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Open" } });
  fireEvent.change(screen.getByLabelText("Phase"), { target: { value: "arrival" } });
  fireEvent.change(screen.getByLabelText("Sequence"), { target: { value: "3" } });
  fireEvent.click(screen.getByRole("button", { name: "Optional within phase" }));
  fireEvent.click(screen.getByRole("button", { name: /create greeting/i }));

  await waitFor(() => expect(api.createGreeting).toHaveBeenCalledWith(
    { kind: "world", id: "w" },
    expect.objectContaining({ phase: "arrival", sequence: 3, optional: true }),
  ));
});


test("collection members expose subject controls for the selected picture", async () => {
  const id = "a".repeat(32);
  const member = `/api/worlds/realm/images/collection-image-${"b".repeat(64)}?v=1`;
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true,
    json: async () => ({ format: 1, id, members: [member] }) }));
  vi.mocked(api.readGreeting).mockResolvedValue(fixture({
    ...greetingFixture("open", "Open"),
    body: `![Scene](/api/worlds/realm/image-collections/${id}/image)`,
  }));
  try {
    renderGreetings({ selected: "open" });
    expect(await screen.findByAltText("Scene")).toHaveAttribute("src", member);
    fireEvent.click(await screen.findByRole("button", { name: /subjects/i }));
    fireEvent.click(screen.getByRole("button", { name: "None" }));
    await waitFor(() => expect(api.setImageSubjects).toHaveBeenCalledWith(
      "realm", "open", member.split("?")[0], []));
  } finally {
    vi.unstubAllGlobals();
  }
});

test("format 2 collection members expose subject controls keyed by member URL", async () => {
  const id = "a".repeat(32);
  const member = `/api/worlds/realm/image-collections/${id}/members/2?v=${"c".repeat(64)}`;
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true,
    json: async () => ({ format: 2, id, members: [member] }) }));
  vi.mocked(api.readGreeting).mockResolvedValue(fixture({
    ...greetingFixture("open", "Open"),
    body: `![Scene](/api/worlds/realm/image-collections/${id}/image)`,
  }));
  try {
    renderGreetings({ selected: "open" });
    expect(await screen.findByAltText("Scene")).toHaveAttribute("src", member);
    fireEvent.click(await screen.findByRole("button", { name: /subjects/i }));
    fireEvent.click(screen.getByRole("button", { name: "None" }));
    await waitFor(() => expect(api.setImageSubjects).toHaveBeenCalledWith(
      "realm", "open", `/api/worlds/realm/image-collections/${id}/members/2`, []));
  } finally {
    vi.unstubAllGlobals();
  }
});

test.each([
  ["/api/worlds/realm/characters/seraphine/versions/default/images/embed-art?v=1",
    "/api/worlds/realm/characters/seraphine/versions/default/images/embed-art"],
  ["/api/worlds/realm/lore/saltmarch/images/avatar?v=1", "/api/worlds/realm/lore/saltmarch/images/avatar"],
  ["https://example.test/art.png?variant=1", "https://example.test/art.png?variant=1"],
  ["/api/worlds/realm/greetings/open/images/embed-art?v=1", "embed-art"],
  ["/api/worlds/realm/characters/seraphine/versions/default/images/art%231",
    "/api/worlds/realm/characters/seraphine/versions/default/images/art%231"],
  ["/api/worlds/realm/greetings/open/images/art%231", "art#1"],
  ["https://example.test/a b.png?variant=1", "https://example.test/a%20b.png?variant=1"],
  ["HTTPS://example.test/art.png?variant=1", "https://example.test/art.png?variant=1"],
])("referenced image %s can be assigned to another character or explicit None", async (src, key) => {
  vi.mocked(api.listCharacters).mockResolvedValue([
    { id: "seraphine", name: "Seraphine", default_version: "default", versions: [] },
    { id: "mara", name: "Mara", default_version: "default", versions: [] },
  ]);
  vi.mocked(api.readGreeting).mockResolvedValue({
    ...greetingFixture("open", "Open"), body: `![Scene](<${src}>)`,
    meta: { ...greetingFixture("open", "Open").meta, phase: "", sequence: null, optional: false },
  });
  const { container } = renderGreetings({ selected: "open" });
  await waitFor(() => {
    fireEvent.click(within(container.querySelector(".img-extras") as HTMLElement)
      .getByRole("button", { name: /subjects/i }));
    expect(screen.getByRole("dialog", { name: /image subjects/i })).toBeInTheDocument();
  });
  fireEvent.click(screen.getByRole("button", { name: "Mara" }));
  vi.mocked(api.getGreetingSubjects).mockResolvedValue({ [key]: ["mara"] });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(within(container.querySelector(".img-extras") as HTMLElement)
    .getByRole("button", { name: "Mara" })).toBeInTheDocument());
  expect(api.setImageSubjects).toHaveBeenCalledWith("realm", "open", key, ["mara"]);
  fireEvent.click(within(container.querySelector(".img-extras") as HTMLElement)
    .getByRole("button", { name: /subjects/i }));
  vi.mocked(api.getGreetingSubjects).mockResolvedValue({ [key]: [] });
  fireEvent.click(screen.getByRole("button", { name: "None" }));
  await waitFor(() => expect(within(container.querySelector(".img-extras") as HTMLElement)
    .getByText("None")).toBeInTheDocument());
  expect(api.setImageSubjects).toHaveBeenLastCalledWith("realm", "open", key, []);
});


test("a Cancel reload refusal preserves the greeting draft and reports the failure", async () => {
  render(<Wrap scope={{ kind: "world", id: "realm" }} wid="realm" selected="open" />);
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Realm draft" } });
  vi.mocked(api.readGreeting).mockRejectedValueOnce({ detail: "Greeting reload refused" });
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(await screen.findByText("Greeting reload refused")).toBeInTheDocument();
  expect(screen.getByLabelText("Name")).toHaveValue("Realm draft");
});

function SavingGreetingAddress(callbacks: Pick<Props, "onBusy" | "onChanged">) {
  const location = useLocation();
  const base = GREETINGS_DEFAULTS.sectionPath;
  const selected = location.pathname.startsWith(base + "/") ? location.pathname.slice(base.length + 1) : null;
  return <><GreetingEditor {...GREETINGS_DEFAULTS} selected={selected} isNew={new URLSearchParams(location.search).get("new") === "1"}
    {...callbacks} />
    <output aria-label="Address">{location.pathname + location.search}</output></>;
}

test.each([false, true])("review regression: a retired greeting create cannot reclaim a fresh form, refused=%s", async (refused) => {
  let finish: () => void = () => {};
  const onBusy = vi.fn();
  const onChanged = vi.fn();
  vi.mocked(api.createGreeting).mockImplementationOnce(() => new Promise((resolve, reject) => {
    finish = () => refused ? reject(fail(409, "Old greeting refused")) : resolve({ id: "open" });
  }));
  render(<MemoryRouter initialEntries={["/worlds/realm/greetings?new=1"]}>
    <SavingGreetingAddress onBusy={onBusy} onChanged={onChanged} /></MemoryRouter>);
  fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "Realm" } });
  fireEvent.click(screen.getByRole("button", { name: "Create greeting" }));
  fireEvent.click(screen.getByRole("link", { name: "Cancel" }));
  fireEvent.click(screen.getByRole("link", { name: "+ New greeting" }));
  fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "Saltmarch" } });
  expect(onBusy).toHaveBeenLastCalledWith(true);
  await act(async () => finish());
  expect(screen.getByLabelText("Name")).toHaveValue("Saltmarch");
  expect(screen.getByLabelText("Address")).toHaveTextContent("/worlds/realm/greetings?new=1");
  expect(screen.queryByText("Old greeting refused")).not.toBeInTheDocument();
  expect(onBusy.mock.calls.map(([busy]) => busy)).toEqual([true, false]);
  expect(onChanged).toHaveBeenCalledTimes(refused ? 0 : 1);
  expect(api.setEdges).toHaveBeenCalledTimes(refused ? 0 : 1);
});

test("review regression: a refused greeting Cancel preserves its draft after the older save commits", async () => {
  let finish: () => void = () => {};
  vi.mocked(api.updateGreeting).mockImplementationOnce(() => new Promise((resolve) => { finish = () => resolve({ ok: true }); }));
  const { rerender } = render(<Wrap scope={{ kind: "world", id: "realm" }} wid="realm" selected="open" />);
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Realm draft" } });
  fireEvent.click(screen.getByRole("button", { name: "Save greeting" }));
  // A different record still retires the pending form. Return to a fresh
  // visit to keep the refused-Cancel/late-save ownership control meaningful.
  rerender(<Wrap scope={{ kind: "world", id: "realm" }} wid="realm" selected="saltmarch" />);
  await screen.findByRole("button", { name: "Edit" });
  rerender(<Wrap scope={{ kind: "world", id: "realm" }} wid="realm" selected="open" />);
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Realm draft" } });
  // Greeting writes also hold the plot-map latch across record selection.
  // Its Cancel therefore becomes available only after that old write ends.
  await act(async () => finish());
  expect(screen.getByLabelText("Name")).toHaveValue("Realm draft");
  vi.mocked(api.readGreeting).mockRejectedValueOnce({ detail: "Cancel greeting refused" });
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  await screen.findByText("Cancel greeting refused");
  expect(screen.getByLabelText("Name")).toHaveValue("Realm draft");
  expect(screen.getByText("Cancel greeting refused")).toBeInTheDocument();
});

test.each([false, true])("Cancel waits for the greeting save before reading, refused=%s", async (refused) => {
  let finish: () => void = () => {};
  vi.mocked(api.updateGreeting).mockImplementationOnce(() => new Promise((resolve, reject) => {
    finish = () => refused ? reject(fail(409, "Save refused")) : resolve({ ok: true });
  }));
  render(<Wrap scope={{ kind: "world", id: "realm" }} wid="realm" selected="open" />);
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Realm draft" } });
  fireEvent.click(screen.getByRole("button", { name: "Save greeting" }));
  const reads = vi.mocked(api.readGreeting).mock.calls.length;
  const cancel = screen.getByRole("button", { name: "Cancel" });
  fireEvent.click(cancel);
  await act(async () => {});
  expect(api.readGreeting).toHaveBeenCalledTimes(reads);
  expect(cancel).toBeDisabled();
  if (!refused) vi.mocked(api.readGreeting).mockResolvedValue(fixture(greetingFixture("open", "Realm saved")));
  await act(async () => finish());
  if (refused) {
    expect(await screen.findByText(/Save refused/)).toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toHaveValue("Realm draft");
    expect(cancel).toBeEnabled();
    fireEvent.click(cancel);
    expect(await screen.findByRole("heading", { name: "Open" })).toBeInTheDocument();
  } else {
    expect(await screen.findByRole("heading", { name: "Realm saved" })).toBeInTheDocument();
  }
});

test("review regression: an owned greeting follow-up read cannot reclaim another selection", async () => {
  vi.mocked(api.listGreetings).mockResolvedValue(fixture([
    greeting("open", "Open", "", []), greeting("saltmarch", "Saltmarch", "", []),
  ]));
  render(<MemoryRouter initialEntries={["/worlds/realm/greetings/open"]}><SavingGreetingAddress /></MemoryRouter>);
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  let release: (value: Awaited<ReturnType<typeof api.readGreeting>>) => void = () => {};
  vi.mocked(api.readGreeting).mockReturnValueOnce(new Promise((resolve) => { release = resolve; }));
  fireEvent.click(screen.getByRole("button", { name: "Save greeting" }));
  await waitFor(() => expect(api.readGreeting).toHaveBeenCalledTimes(2));
  vi.mocked(api.readGreeting).mockResolvedValue(fixture(greetingFixture("saltmarch", "Saltmarch")));
  fireEvent.click(screen.getByRole("link", { name: "Saltmarch" }));
  await screen.findByRole("heading", { name: "Saltmarch" });
  await act(async () => release(fixture(greetingFixture("open", "Realm stale"))));
  expect(screen.getByRole("heading", { name: "Saltmarch" })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Realm stale" })).not.toBeInTheDocument();
});

test("review regression: a committed greeting refresh stays in its original scope", async () => {
  let finish: () => void = () => {};
  const onChanged = vi.fn();
  vi.mocked(api.createGreeting).mockImplementationOnce(() => new Promise((resolve) => { finish = () => resolve({ id: "open" }); }));
  vi.mocked(api.listGreetings).mockImplementation(async (scope) => fixture([
    greeting(scope.id, scope.id === "realm" ? "Realm" : "Saltmarch", "", []),
  ]));
  const { rerender } = renderGreetings({ onChanged });
  fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "Realm draft" } });
  fireEvent.click(screen.getByRole("button", { name: "Create greeting" }));
  rerender(greetingsWith({ scope: { kind: "world", id: "saltmarch" }, wid: "saltmarch", onChanged }));
  await screen.findByRole("link", { name: "Saltmarch" });
  await act(async () => finish());
  expect(screen.getByRole("link", { name: "Saltmarch" })).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Realm" })).not.toBeInTheDocument();
  expect(screen.getByLabelText("Name")).toHaveValue("");
  expect(api.setEdges).toHaveBeenCalledWith({ kind: "world", id: "realm" }, "open", { leads_to: [], excludes: [] });
  expect(onChanged).not.toHaveBeenCalled();
});

function GreetingQueryAddress(callbacks: Pick<Props, "onBusy" | "onChanged">) {
  const navigate = useNavigate();
  const base = GREETINGS_DEFAULTS.sectionPath;
  return <>
    <button onClick={() => void navigate(`${base}?new=1&view=graph`)}>Plot map</button>
    <button onClick={() => void navigate(`${base}?new=1`)}>List</button>
    <button onClick={() => void navigate(`${base}?new=1`)}>Reopen form</button>
    <SavingGreetingAddress {...callbacks} />
  </>;
}

test.each([
  ["write", false], ["write", true], ["refresh", false], ["refresh", true],
] as const)("same-form query: greeting %s retains success/error after view changes, refused=%s", async (stage, refused) => {
  const onBusy = vi.fn();
  const onChanged = vi.fn();
  render(<MemoryRouter initialEntries={["/worlds/realm/greetings?new=1"]}>
    <GreetingQueryAddress onBusy={onBusy} onChanged={onChanged} /></MemoryRouter>);
  fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "Realm" } });
  await waitFor(() => expect(api.listGreetings).toHaveBeenCalledTimes(1));
  let finish: () => void = () => {};
  if (stage === "write") {
    vi.mocked(api.createGreeting).mockImplementationOnce(() => new Promise((resolve, reject) => {
      finish = () => refused ? reject(fail(503, "Greeting refused")) : resolve({ id: "open" });
    }));
  } else {
    vi.mocked(api.listGreetings).mockImplementationOnce(() => new Promise((resolve, reject) => {
      finish = () => refused ? reject(fail(503, "Greeting refresh refused")) : resolve([]);
    }));
  }
  fireEvent.click(screen.getByRole("button", { name: "Create greeting" }));
  await waitFor(() => expect(stage === "write" ? api.createGreeting : api.listGreetings).toHaveBeenCalledTimes(stage === "write" ? 1 : 2));
  fireEvent.click(screen.getByRole("button", { name: "Plot map" }));
  expect(screen.getByLabelText("Address")).toHaveTextContent("?new=1&view=graph");
  fireEvent.click(screen.getByRole("button", { name: "List" }));
  expect(screen.getByLabelText("Name")).toHaveValue("Realm");
  expect(screen.getByRole("button", { name: "Create greeting" })).toBeDisabled();
  await act(async () => finish());
  if (refused) {
    await screen.findByText(stage === "write" ? "Greeting refused" : "Greeting refresh refused");
    expect(screen.getByLabelText("Name")).toHaveValue("Realm");
    expect(screen.getByRole("button", { name: "Create greeting" })).toBeEnabled();
    expect(screen.getByLabelText("Address")).toHaveTextContent("/worlds/realm/greetings?new=1");
  } else {
    await screen.findByRole("heading", { name: "Open" });
    expect(screen.getByLabelText("Address")).toHaveTextContent("/worlds/realm/greetings/open");
    expect(screen.queryByRole("button", { name: "Create greeting" })).not.toBeInTheDocument();
  }
  expect(api.createGreeting).toHaveBeenCalledTimes(1);
  expect(onBusy.mock.calls.map(([busy]) => busy)).toEqual([true, false]);
  expect(onChanged).toHaveBeenCalledTimes(refused ? 0 : 1);
});

test.each(["write", "refresh"])("same-form query control: an identical-address fresh greeting entry retires its %s", async (stage) => {
  const onBusy = vi.fn();
  render(<MemoryRouter initialEntries={["/worlds/realm/greetings?new=1"]}>
    <GreetingQueryAddress onBusy={onBusy} /></MemoryRouter>);
  fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "Realm" } });
  await waitFor(() => expect(api.listGreetings).toHaveBeenCalledTimes(1));
  let finish: () => void = () => {};
  if (stage === "write") {
    vi.mocked(api.createGreeting).mockImplementationOnce(() => new Promise((resolve) => {
      finish = () => resolve({ id: "open" });
    }));
  } else {
    vi.mocked(api.listGreetings).mockImplementationOnce(() => new Promise((resolve) => {
      finish = () => resolve([]);
    }));
  }
  fireEvent.click(screen.getByRole("button", { name: "Create greeting" }));
  await waitFor(() => expect(stage === "write" ? api.createGreeting : api.listGreetings).toHaveBeenCalledTimes(stage === "write" ? 1 : 2));
  fireEvent.click(screen.getByRole("button", { name: "Reopen form" }));
  await act(async () => finish());
  expect(screen.getByLabelText("Address")).toHaveTextContent("/worlds/realm/greetings?new=1");
  expect(screen.getByLabelText("Name")).toHaveValue("Realm");
  expect(screen.getByRole("button", { name: "Create greeting" })).toBeEnabled();
  expect(onBusy.mock.calls.map(([busy]) => busy)).toEqual([true, false]);
});
