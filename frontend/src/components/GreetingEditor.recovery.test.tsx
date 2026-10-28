import { act, fireEvent, render, screen } from "@testing-library/react";

vi.mock("../api/client", async () => (await import("../testkit/greetingEditorMocks")).greetingApiMock());
import { api } from "../api/client";
import { fixture } from "../testkit/fixture";
import { fail, greeting, greetingFixture, greetingsWith, installGreetingMocks, renderGreetings } from "../testkit/greetingEditorHarness";

beforeEach(installGreetingMocks);

function held<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

async function refusedSave() {
  vi.mocked(api.updateGreeting).mockRejectedValueOnce(fail(409, "changed on disk", "stale_record", { rev: "r2" }));
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Realm draft" } });
  fireEvent.click(screen.getByRole("button", { name: "Save greeting" }));
  return screen.findByRole("button", { name: "Discard mine and reload" });
}

test.each(["busy", "network"])("a %s greeting reread keeps the draft and allows another reload", async (reason) => {
  renderGreetings({ selected: "open" });
  const reload = await refusedSave();
  const error = reason === "busy" ? fail(409, "temporarily busy", "busy") : new Error("offline");
  vi.mocked(api.readGreeting).mockRejectedValueOnce(error);
  fireEvent.click(reload);
  await screen.findByText(reason === "busy" ? "temporarily busy" : "Error: offline");
  expect(screen.getByLabelText("Name")).toHaveValue("Realm draft");
  fireEvent.click(screen.getByRole("button", { name: "Discard mine and reload" }));
  expect(await screen.findByRole("heading", { name: "Open" })).toBeInTheDocument();
});

test.each([404, 503])("a list-refresh %s does not discard the selected greeting", async (status) => {
  renderGreetings({ selected: "open" });
  const reload = await refusedSave();
  vi.mocked(api.listGreetings).mockRejectedValueOnce(fail(status, "greeting list unavailable"));
  fireEvent.click(reload);
  await screen.findByText("greeting list unavailable");
  expect(screen.getByLabelText("Name")).toHaveValue("Realm draft");
  fireEvent.click(screen.getByRole("button", { name: "Discard mine and reload" }));
  expect(await screen.findByRole("heading", { name: "Open" })).toBeInTheDocument();
});

test("a selected-record 404 clears the deleted greeting instead of resending its old draft", async () => {
  renderGreetings({ selected: "open" });
  const reload = await refusedSave();
  vi.mocked(api.readGreeting).mockRejectedValueOnce(fail(404, "greeting was deleted"));
  fireEvent.click(reload);
  expect(await screen.findByRole("heading", { name: "New greeting" })).toBeInTheDocument();
  expect(screen.getByLabelText("Name")).toHaveValue("");
  expect(screen.queryByRole("button", { name: "Overwrite with mine" })).not.toBeInTheDocument();
});

test.each(["record", "scope"])("a delayed reload cannot reclaim the next %s", async (change) => {
  vi.mocked(api.listGreetings).mockResolvedValue(fixture([greeting("open", "Open", "", []), greeting("saltmarch", "Saltmarch", "", [])]));
  const { rerender } = renderGreetings({ selected: "open" });
  const reload = await refusedSave();
  const list = held<Awaited<ReturnType<typeof api.listGreetings>>>();
  vi.mocked(api.listGreetings).mockReturnValueOnce(list.promise);
  fireEvent.click(reload);
  vi.mocked(api.readGreeting).mockResolvedValue(fixture(greetingFixture("saltmarch", "Saltmarch")));
  rerender(greetingsWith({ selected: "saltmarch", ...(change === "scope"
    ? { scope: { kind: "world", id: "saltmarch" }, wid: "saltmarch" } : {}) }));
  await screen.findByRole("heading", { name: "Saltmarch" });
  // A request which starts only after the old list lands would appear to be
  // the newest read, so it must be retired before starting that follow-up.
  vi.mocked(api.readGreeting).mockResolvedValue(fixture(greetingFixture("open", "Realm old")));
  await act(async () => list.resolve([]));
  expect(screen.getByRole("heading", { name: "Saltmarch" })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Realm old" })).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Saltmarch" })).toBeInTheDocument();
});

test("a late reload error stays with its former selection", async () => {
  const { rerender } = renderGreetings({ selected: "open" });
  const reload = await refusedSave();
  const list = held<Awaited<ReturnType<typeof api.listGreetings>>>();
  vi.mocked(api.listGreetings).mockReturnValueOnce(list.promise);
  fireEvent.click(reload);
  vi.mocked(api.readGreeting).mockResolvedValue(fixture(greetingFixture("saltmarch", "Saltmarch")));
  rerender(greetingsWith({ selected: "saltmarch" }));
  await screen.findByRole("heading", { name: "Saltmarch" });
  await act(async () => list.reject(new Error("retired list failed")));
  expect(screen.getByRole("heading", { name: "Saltmarch" })).toBeInTheDocument();
  expect(screen.queryByText(/retired list failed/)).not.toBeInTheDocument();
});

test("unmounting during a reload leaves its eventual list failure handled", async () => {
  const { unmount } = renderGreetings({ selected: "open" });
  const reload = await refusedSave();
  const list = held<Awaited<ReturnType<typeof api.listGreetings>>>();
  vi.mocked(api.listGreetings).mockReturnValueOnce(list.promise);
  fireEvent.click(reload);
  unmount();
  await act(async () => list.reject(new Error("retired list failed")));
  render(greetingsWith());
  expect(await screen.findByLabelText("Name")).toHaveValue("");
  expect(screen.queryByText(/retired list failed/)).not.toBeInTheDocument();
});

test("late catalog reads cannot replace another scope's character, location or tag choices", async () => {
  const characters = held<Awaited<ReturnType<typeof api.listCharacters>>>();
  const locations = held<Awaited<ReturnType<typeof api.listEntities>>>();
  const tags = held<Record<string, string>>();
  vi.mocked(api.listCharacters).mockReturnValueOnce(characters.promise).mockResolvedValue(fixture([
    { id: "winifred", name: "Winifred", default_version: "default", versions: [{ id: "default", name: "default" }] },
  ]));
  vi.mocked(api.listEntities).mockReturnValueOnce(locations.promise).mockResolvedValue(fixture([
    { id: "the-quay", name: "The Quay" },
  ]));
  vi.mocked(api.listTags).mockReturnValueOnce(tags.promise).mockResolvedValue({ saltmarch: "Saltmarch" });
  const { rerender } = renderGreetings();
  rerender(greetingsWith({ scope: { kind: "world", id: "saltmarch" }, wid: "saltmarch" }));
  await screen.findByRole("option", { name: "Winifred" });
  await act(async () => {
    characters.resolve(fixture([{ id: "seraphine", name: "Seraphine", default_version: "default", versions: [] }]));
    locations.resolve(fixture([{ id: "counting-house", name: "The Counting House" }]));
    tags.resolve({ realm: "Realm" });
  });
  expect(screen.getByRole("option", { name: "Winifred" })).toBeInTheDocument();
  expect(screen.getByRole("option", { name: "The Quay" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Saltmarch" })).toBeInTheDocument();
  expect(screen.queryByRole("option", { name: "Seraphine" })).not.toBeInTheDocument();
  expect(screen.queryByRole("option", { name: "The Counting House" })).not.toBeInTheDocument();
});

test("late image subjects do not relabel the next greeting's picture", async () => {
  const subjects = held<Record<string, string[]>>();
  vi.mocked(api.readGreeting).mockImplementation(async (_scope, id) => fixture({
    ...greetingFixture(id, id === "open" ? "Open" : "Saltmarch"),
    body: `![scene](/api/worlds/realm/greetings/${id}/images/picture.jpg)`,
  }));
  vi.mocked(api.getGreetingSubjects).mockReturnValueOnce(subjects.promise)
    .mockResolvedValue({ "picture.jpg": ["seraphine"] });
  const { rerender } = renderGreetings({ selected: "open" });
  await screen.findByRole("heading", { name: "Open" });
  rerender(greetingsWith({ selected: "saltmarch" }));
  await screen.findByRole("button", { name: "Seraphine" });
  await act(async () => subjects.resolve({ "picture.jpg": [] }));
  expect(screen.getByRole("button", { name: "Seraphine" })).toBeInTheDocument();
  expect(screen.queryByText("None")).not.toBeInTheDocument();
});

test("a completed subject save cannot relabel a greeting opened while it was pending", async () => {
  const write = held<Awaited<ReturnType<typeof api.setImageSubjects>>>();
  vi.mocked(api.setImageSubjects).mockReturnValueOnce(write.promise);
  vi.mocked(api.readGreeting).mockImplementation(async (_scope, id) => fixture({
    ...greetingFixture(id, id === "open" ? "Open" : "Saltmarch"),
    body: `![scene](/api/worlds/realm/greetings/${id}/images/picture.jpg)`,
  }));
  vi.mocked(api.getGreetingSubjects).mockImplementation(async (_wid, id) =>
    ({ "picture.jpg": id === "open" ? ["seraphine"] : [] }));
  const { rerender } = renderGreetings({ selected: "open" });
  await screen.findByRole("button", { name: "Seraphine" });
  fireEvent.click(screen.getByRole("button", { name: /subjects/i }));
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  rerender(greetingsWith({ selected: "saltmarch" }));
  await screen.findByText("None");
  await act(async () => write.resolve({ ok: true }));
  expect(screen.getByText("None")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Seraphine" })).not.toBeInTheDocument();
});

test("a closed tagging queue cannot relabel a greeting opened while its save was pending", async () => {
  const write = held<Awaited<ReturnType<typeof api.setImageSubjects>>>();
  vi.mocked(api.setImageSubjects).mockReturnValueOnce(write.promise);
  vi.mocked(api.listUntaggedImages).mockResolvedValue([
    { gid: "open", greeting_name: "Open", name: "picture.jpg", url: "/api/worlds/realm/greetings/open/images/picture.jpg" },
  ]);
  vi.mocked(api.readGreeting).mockImplementation(async (_scope, id) => fixture({
    ...greetingFixture(id, id === "open" ? "Open" : "Saltmarch"),
    body: `![scene](/api/worlds/realm/greetings/${id}/images/picture.jpg)`,
  }));
  vi.mocked(api.getGreetingSubjects).mockImplementation(async (_wid, id) =>
    ({ "picture.jpg": id === "open" ? ["seraphine"] : [] }));
  const { rerender } = renderGreetings({ selected: "open" });
  await screen.findByRole("button", { name: "Seraphine" });
  fireEvent.click(screen.getByRole("button", { name: /tag images \(1\)/i }));
  fireEvent.click(screen.getByRole("button", { name: "No subjects" }));
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  rerender(greetingsWith({ selected: "saltmarch" }));
  await screen.findByText("None");
  await act(async () => write.resolve({ ok: true }));
  expect(screen.getByText("None")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Seraphine" })).not.toBeInTheDocument();
});

test("a refused character import reports its error without discarding the greeting draft", async () => {
  renderGreetings({ selected: "open" });
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Realm draft" } });
  vi.mocked(api.importGreetings).mockRejectedValueOnce(new Error("Import offline"));
  fireEvent.click(screen.getByRole("button", { name: "Import greetings from this character/version" }));
  await screen.findByText("Error: Import offline");
  expect(screen.getByLabelText("Name")).toHaveValue("Realm draft");
  expect(screen.getByRole("button", { name: "Save greeting" })).toBeEnabled();
});

test("an old catalog request cannot replace a newer visit to the same scope", async () => {
  const old = held<Awaited<ReturnType<typeof api.listGreetings>>>();
  vi.mocked(api.listGreetings).mockReturnValueOnce(old.promise)
    .mockResolvedValueOnce(fixture([greeting("saltmarch", "Saltmarch", "", [])]))
    .mockResolvedValueOnce(fixture([greeting("open", "Realm current", "", [])]));
  const { rerender } = renderGreetings();
  rerender(greetingsWith({ scope: { kind: "world", id: "saltmarch" }, wid: "saltmarch" }));
  await screen.findByRole("link", { name: "Saltmarch" });
  rerender(greetingsWith());
  await screen.findByRole("link", { name: "Realm current" });
  await act(async () => old.resolve(fixture([greeting("open", "Realm retired", "", [])])));
  expect(screen.getByRole("link", { name: "Realm current" })).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Realm retired" })).not.toBeInTheDocument();
});

test("a newer catalog refresh wins over a delayed discard snapshot", async () => {
  const old = held<Awaited<ReturnType<typeof api.listGreetings>>>();
  renderGreetings({ selected: "open" });
  const reload = await refusedSave();
  vi.mocked(api.listGreetings).mockReturnValueOnce(old.promise)
    .mockResolvedValue(fixture([greeting("winifred", "Winifred", "", [])]));
  fireEvent.click(reload);
  fireEvent.click(screen.getByRole("button", { name: "Import greetings from this character/version" }));
  await screen.findByRole("link", { name: "Winifred" });
  await act(async () => old.resolve([]));
  expect(screen.getByRole("link", { name: "Winifred" })).toBeInTheDocument();
});

test("a previous scope's completed save cannot retire the current catalog read", async () => {
  const write = held<Awaited<ReturnType<typeof api.updateGreeting>>>();
  const catalog = held<Awaited<ReturnType<typeof api.listGreetings>>>();
  vi.mocked(api.updateGreeting).mockReturnValueOnce(write.promise);
  vi.mocked(api.listGreetings).mockResolvedValueOnce([]).mockReturnValueOnce(catalog.promise);
  const { rerender } = renderGreetings({ selected: "open" });
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Realm saved" } });
  fireEvent.click(screen.getByRole("button", { name: "Save greeting" }));
  vi.mocked(api.readGreeting).mockResolvedValue(fixture(greetingFixture("saltmarch", "Saltmarch")));
  rerender(greetingsWith({ wid: "saltmarch", scope: { kind: "world", id: "saltmarch" }, selected: "saltmarch" }));
  await screen.findByRole("heading", { name: "Saltmarch" });
  await act(async () => write.resolve({ ok: true }));
  await act(async () => catalog.resolve(fixture([greeting("saltmarch", "Saltmarch", "", [])])));
  expect(screen.getByRole("link", { name: "Saltmarch" })).toBeInTheDocument();
});
