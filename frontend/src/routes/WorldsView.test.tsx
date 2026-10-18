import { act, render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import WorldsView from "./WorldsView";
import type { WorldMeta } from "../api/types";
import { INTENT_DWELL_MS, resetPrefetch } from "../api/prefetch";
import { announceRunSettled, bumpMemoEpoch } from "../api/memoEpoch";

const navigate = vi.fn();
vi.mock("react-router-dom", async () => ({
  ...(await vi.importActual<any>("react-router-dom")),
  useNavigate: () => navigate,
}));

vi.mock("../api/client", () => ({
  api: {
    listWorlds: vi.fn(),
    rememberedWorlds: vi.fn(),
    createWorld: vi.fn(),
    renameWorld: vi.fn(),
    deleteWorld: vi.fn(),
    exportWorldUrl: vi.fn(),
    importWorld: vi.fn(),
    forkWorld: vi.fn(),
    // What a world card's intent prefetch asks for.
    getWorld: vi.fn(), listCharacters: vi.fn(), listAppearances: vi.fn(),
    rememberedWorld: vi.fn(), rememberedCharacters: vi.fn(), rememberedAppearances: vi.fn(),
  },
}));
import { api } from "../api/client";

beforeEach(() => {
  vi.clearAllMocks();
  resetPrefetch();
  (api.getWorld as any).mockResolvedValue({ meta: { id: "w1", name: "Saltmarch" }, body: "", counts: {} });
  (api.listCharacters as any).mockResolvedValue([]);
  (api.rememberedWorld as any).mockReturnValue(undefined);
  (api.rememberedCharacters as any).mockReturnValue(undefined);
  (api.rememberedAppearances as any).mockReturnValue(undefined);
  (api.listWorlds as any).mockResolvedValue([]);
  (api.rememberedWorlds as any).mockReturnValue(undefined);
  (api.createWorld as any).mockResolvedValue({ id: "w1" });
  (api.renameWorld as any).mockResolvedValue({ id: "w1", name: "New" });
  (api.deleteWorld as any).mockResolvedValue({ ok: true });
  (api.exportWorldUrl as any).mockImplementation((wid: string) => `/api/worlds/${wid}/export.zip`);
  (api.importWorld as any).mockResolvedValue({ id: "imported" });
  (api.forkWorld as any).mockResolvedValue({ id: "saltmarch-fork" });
});

function renderView() {
  render(
    <MemoryRouter>
      <WorldsView />
    </MemoryRouter>,
  );
}

test("the grid is alphabetical, whatever order the listing arrives in", async () => {
  // `listWorlds` answers newest-first, which is the right order for a feed and
  // the wrong one for a shelf you come to looking for one world by name. No
  // toggle: a world is a reference library rather than something you play.
  (api.listWorlds as any).mockResolvedValue([
    { id: "w1", name: "Tidewrack", counts: {} },
    { id: "w2", name: "ashfall", counts: {} },
    { id: "w3", name: "The Saltmarch", counts: {} },
  ]);
  renderView();
  await screen.findByText("The Saltmarch");
  // Case-insensitively and past the article, so a world typed in lower case
  // sits in the sequence rather than in a block of its own below the
  // capitalised ones, and "The Saltmarch" files under S rather than filling
  // the T section along with everything else named that way.
  expect(Array.from(document.querySelectorAll(".world-card h3")).map((h) => h.textContent))
    .toEqual(["ashfall", "The Saltmarch", "Tidewrack"]);
});

test("lists worlds as cards with count footers", async () => {
  (api.listWorlds as any).mockResolvedValue([
    { id: "w1", name: "Saltmarch", created: "", updated: "",
      counts: { locations: 3, lore: 12, characters: 5, pcs: 1 } },
  ]);
  renderView();
  await screen.findByText("Saltmarch");
  expect(screen.getByText(/3 LOCATIONS · 6 CHARACTERS · 12 LORE/i)).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: /worlds/i })).toBeInTheDocument();
});

// The library column beside the page counts Worlds from the same endpoint, so
// every mount reads it twice: once for the grid, once for the count. `BASE` is
// that baseline, and the assertions below are about the *refresh* on top of it.
const BASE = 2;

test("creating a world posts the name and refreshes the list", async () => {
  renderView();
  await waitFor(() => expect(api.listWorlds).toHaveBeenCalled());
  fireEvent.change(screen.getByPlaceholderText(/world name/i), { target: { value: "Realm" } });
  fireEvent.click(screen.getByRole("button", { name: /^create$/i }));
  await waitFor(() => expect(api.createWorld).toHaveBeenCalledWith("Realm"));
  await waitFor(() => expect(api.listWorlds).toHaveBeenCalledTimes(BASE + 1));
});

test("a refused create says why, keeps the name, and refreshes nothing", async () => {
  // It used to be an unhandled rejection: the grid unchanged and nothing on
  // screen to tell a refused name from a click that never landed.
  (api.createWorld as any).mockRejectedValue({ detail: "a world named Realm already exists" });
  renderView();
  await waitFor(() => expect(api.listWorlds).toHaveBeenCalled());
  fireEvent.change(screen.getByPlaceholderText(/world name/i), { target: { value: "Realm" } });
  fireEvent.click(screen.getByRole("button", { name: /^create$/i }));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    /'Realm' could not be created: a world named Realm already exists/i);
  expect(screen.getByPlaceholderText(/world name/i)).toHaveValue("Realm");
  expect(api.listWorlds).toHaveBeenCalledTimes(BASE);
});

test.each(["Enter", "click"])("a pending create ignores another synchronous %s", async (again) => {
  const creating = held<Awaited<ReturnType<typeof api.createWorld>>>();
  vi.mocked(api.createWorld).mockReturnValue(creating.promise);
  renderView();
  await waitFor(() => expect(api.listWorlds).toHaveBeenCalledTimes(BASE));
  const input = screen.getByLabelText("World name");
  const button = screen.getByRole("button", { name: /^create$/i });
  fireEvent.change(input, { target: { value: "Realm" } });

  // Both handlers run before React paints the pending state: disabling the
  // rendered controls alone cannot serialize these submissions.
  act(() => {
    fireEvent.keyDown(input, { key: "Enter" });
    if (again === "Enter") fireEvent.keyDown(input, { key: "Enter" });
    else fireEvent.click(button);
  });
  expect(api.createWorld).toHaveBeenCalledTimes(1);
  expect(input).toBeDisabled();
  expect(screen.getByRole("button", { name: /creating/i })).toBeDisabled();
  await act(async () => { creating.resolve({ id: "realm" }); });
  await waitFor(() => expect(api.listWorlds).toHaveBeenCalledTimes(BASE + 1));
  expect(input).toBeEnabled();
  expect(input).toHaveValue("");
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("a create stays pending until the successful world's list refresh settles", async () => {
  const refreshed = held<WorldMeta[]>();
  vi.mocked(api.listWorlds).mockImplementation((fresh) => fresh ? refreshed.promise : Promise.resolve([]));
  renderView();
  await waitFor(() => expect(api.listWorlds).toHaveBeenCalledTimes(BASE));
  const input = screen.getByLabelText("World name");
  fireEvent.change(input, { target: { value: "Realm" } });
  fireEvent.click(screen.getByRole("button", { name: /^create$/i }));
  await waitFor(() => expect(api.listWorlds).toHaveBeenCalledWith(true));

  expect(input).toHaveValue("");
  expect(input).toBeDisabled();
  expect(screen.getByRole("button", { name: /creating/i })).toBeDisabled();
  await act(async () => { refreshed.resolve([]); });
  expect(input).toBeEnabled();
  expect(screen.getByRole("button", { name: /^create$/i })).toBeDisabled();
  expect(api.createWorld).toHaveBeenCalledTimes(1);
});

test("a refused pending create keeps its profile and releases the form for retry", async () => {
  const creating = held<Awaited<ReturnType<typeof api.createWorld>>>();
  vi.mocked(api.createWorld).mockReturnValueOnce(creating.promise);
  renderView();
  await waitFor(() => expect(api.listWorlds).toHaveBeenCalledTimes(BASE));
  const input = screen.getByLabelText("World name");
  const details = screen.getByRole("button", { name: "More details" });
  fireEvent.change(input, { target: { value: "Realm" } });
  fireEvent.click(details);
  fireEvent.change(screen.getByLabelText("Genre"), { target: { value: "Gothic" } });
  fireEvent.click(screen.getByRole("button", { name: /^create$/i }));
  expect(input).toBeDisabled();
  expect(details).toBeDisabled();
  for (const label of ["Genre", "Tone", "Themes", "Description"]) {
    expect(screen.getByLabelText(label)).toBeDisabled();
  }

  await act(async () => { creating.reject({ detail: "world id is already in use" }); });
  expect(await screen.findByRole("alert")).toHaveTextContent(
    /'Realm' could not be created: world id is already in use/i);
  expect(input).toBeEnabled();
  expect(input).toHaveValue("Realm");
  expect(screen.getByLabelText("Genre")).toHaveValue("Gothic");
  expect(screen.getByLabelText("Genre")).toBeEnabled();
  expect(details).toBeEnabled();
  expect(api.listWorlds).toHaveBeenCalledTimes(BASE);

  vi.mocked(api.createWorld).mockResolvedValueOnce({ id: "realm" });
  fireEvent.click(screen.getByRole("button", { name: /^create$/i }));
  await waitFor(() => expect(api.listWorlds).toHaveBeenCalledTimes(BASE + 1));
  expect(api.createWorld).toHaveBeenCalledTimes(2);
  expect(api.createWorld).toHaveBeenLastCalledWith("Realm", {
    genre: "Gothic", tone: "", themes: [], description: "",
  });
  expect(input).toHaveValue("");
  expect(screen.queryByLabelText("Genre")).not.toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("a failed refresh after creating a world confirms creation and retries only the read", async () => {
  vi.mocked(api.listWorlds).mockImplementation((fresh) => fresh
    ? Promise.reject(new Error("offline")) : Promise.resolve([]));
  renderView();
  await waitFor(() => expect(api.listWorlds).toHaveBeenCalledTimes(BASE));
  const input = screen.getByLabelText("World name");
  fireEvent.change(input, { target: { value: "Realm" } });
  fireEvent.click(screen.getByRole("button", { name: /^create$/i }));
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent(/'Realm' was created, but the list could not be refreshed/i);
  expect(alert).not.toHaveTextContent(/could not be created/i);
  expect(input).toHaveValue("");
  expect(input).toBeEnabled();

  vi.mocked(api.listWorlds).mockResolvedValue([
    { id: "realm", name: "Realm", created: "", updated: "", counts: {} },
  ]);
  fireEvent.click(screen.getByRole("button", { name: /try again/i }));
  expect(await screen.findByText("Realm")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(api.createWorld).toHaveBeenCalledTimes(1);
});

test.each(["create", "fork"])("a %s refresh warning retires after an automatic confirmed read", async (operation) => {
  vi.mocked(api.listWorlds).mockResolvedValue(ONE_WORLD);
  renderView();
  await screen.findByText("Saltmarch");
  vi.mocked(api.listWorlds).mockRejectedValue(new Error("offline"));
  if (operation === "create") {
    fireEvent.change(screen.getByLabelText("World name"), { target: { value: "Realm" } });
    fireEvent.click(screen.getByRole("button", { name: /^create$/i }));
  } else {
    vi.spyOn(window, "prompt").mockReturnValue("Realm");
    fireEvent.click(screen.getByRole("button", { name: "Fork Saltmarch" }));
  }
  expect(await screen.findByRole("alert")).toHaveTextContent(/'Realm' was created, but the list could not be refreshed/i);

  const recovered = held<WorldMeta[]>();
  vi.mocked(api.listWorlds).mockReturnValue(recovered.promise);
  act(() => { bumpMemoEpoch(); announceRunSettled(); });
  expect(screen.queryByRole("button", { name: /try again/i })).not.toBeInTheDocument();
  await act(async () => { recovered.resolve([
    ...ONE_WORLD, { id: "realm", name: "Realm", created: "", updated: "", counts: {} },
  ]); });
  expect(await screen.findByText("Realm")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();

  // A later failed read is unrelated to the completed recovery and cannot
  // resurrect the warning about the already confirmed creation.
  vi.mocked(api.listWorlds).mockRejectedValue(new Error("offline again"));
  act(() => { bumpMemoEpoch(); announceRunSettled(); });
  await screen.findByRole("button", { name: /try again/i });
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(operation === "create" ? api.createWorld : api.forkWorld).toHaveBeenCalledTimes(1);
});

test.each(["create", "fork"])("a batched %s recovery with unchanged list identity retires its warning", async (operation) => {
  vi.mocked(api.listWorlds).mockResolvedValue(ONE_WORLD);
  renderView();
  await screen.findByText("Saltmarch");
  const failed = held<WorldMeta[]>();
  vi.mocked(api.listWorlds).mockReturnValue(failed.promise);
  vi.spyOn(window, "prompt").mockReturnValue("Realm");
  fireEvent.change(screen.getByLabelText("World name"), { target: { value: "Realm" } });

  // Both outcomes land before React commits. The successful read reuses the
  // previously confirmed array, so an effect keyed only by that array cannot
  // notice that this creation's read warning also needs retiring.
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: operation === "create" ? /^create$/i : "Fork Saltmarch" }));
    await Promise.resolve();
    failed.reject(new Error("offline"));
    await failed.promise.catch(() => {});
    await Promise.resolve();
    vi.mocked(api.listWorlds).mockResolvedValue(ONE_WORLD);
    bumpMemoEpoch();
    announceRunSettled();
    await Promise.resolve();
  });
  expect(screen.getByText("Saltmarch")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();

  vi.mocked(api.listWorlds).mockRejectedValue(new Error("unrelated read failure"));
  act(() => { bumpMemoEpoch(); announceRunSettled(); });
  await screen.findByRole("button", { name: /try again/i });
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(operation === "create" ? api.createWorld : api.forkWorld).toHaveBeenCalledTimes(1);
});

test("an automatic list recovery preserves a refused create and its draft", async () => {
  vi.mocked(api.createWorld).mockRejectedValue(new Error("name refused"));
  renderView();
  await waitFor(() => expect(api.listWorlds).toHaveBeenCalledTimes(BASE));
  fireEvent.change(screen.getByLabelText("World name"), { target: { value: "Realm" } });
  fireEvent.click(screen.getByRole("button", { name: /^create$/i }));
  expect(await screen.findByRole("alert")).toHaveTextContent(/could not be created: .*name refused/i);
  vi.mocked(api.listWorlds).mockResolvedValue(ONE_WORLD);
  act(() => { bumpMemoEpoch(); announceRunSettled(); });
  await screen.findByText("Saltmarch");
  expect(screen.getByRole("alert")).toHaveTextContent(/could not be created: .*name refused/i);
  expect(screen.getByLabelText("World name")).toHaveValue("Realm");
});

test("create is disabled with no name", async () => {
  renderView();
  await waitFor(() => expect(api.listWorlds).toHaveBeenCalled());
  expect(screen.getByRole("button", { name: /^create$/i })).toBeDisabled();
});

test("renames a world", async () => {
  (api.listWorlds as any).mockResolvedValue([
    { id: "w1", name: "Old", created: "", updated: "", counts: {} },
  ]);
  renderView();
  await screen.findByText("Old");
  fireEvent.click(screen.getByRole("button", { name: /rename/i }));
  const input = screen.getByDisplayValue("Old");
  fireEvent.change(input, { target: { value: "New" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(api.renameWorld).toHaveBeenCalledWith("w1", "New"));
});

test("deletes a world after confirm", async () => {
  (api.listWorlds as any).mockResolvedValue([
    { id: "w1", name: "Doomed", created: "", updated: "", counts: {} },
  ]);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  renderView();
  await screen.findByText("Doomed");
  fireEvent.click(screen.getByRole("button", { name: /delete/i }));
  await waitFor(() => expect(api.deleteWorld).toHaveBeenCalledWith("w1"));
});

test("a blocked world delete shows the server's message", async () => {
  (api.listWorlds as any).mockResolvedValue([
    { id: "w1", name: "W1", created: "", updated: "", counts: {} },
  ]);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  (api.deleteWorld as any).mockRejectedValue(new Error("world is used by campaigns: C"));
  const alert = vi.spyOn(window, "alert").mockImplementation(() => {});
  renderView();
  fireEvent.click(await screen.findByLabelText("Delete W1"));
  await waitFor(() => expect(alert).toHaveBeenCalledWith(
    expect.stringContaining("world is used by campaigns")));
  alert.mockRestore();
});

// ---- world bundles (#54) ----

test("every card offers a download link to its own export bundle", async () => {
  // Two worlds, so an implementation that renders Export on the first card
  // only -- or points every card at one world -- fails here.
  (api.listWorlds as any).mockResolvedValue([
    { id: "w1", name: "Saltmarch", created: "", updated: "", counts: {} },
    { id: "w2", name: "Realm", created: "", updated: "", counts: {} },
  ]);
  renderView();
  // A plain href, not a fetch: the browser streams a gigabyte-scale zip to
  // disk itself rather than the page holding it in a Blob.
  for (const [name, wid] of [["Saltmarch", "w1"], ["Realm", "w2"]]) {
    const link = await screen.findByLabelText(`Export ${name}`);
    expect(link).toHaveAttribute("href", `/api/worlds/${wid}/export.zip`);
    expect(link).toHaveAttribute("download");
  }
});

test.each([
  [409, "image-store maintenance is running; try again when it finishes"],
  [404, "world not found"],
  [422, "image exceeds the bundle limit: worlds/realm/avatar.png"],
])("a refused export (%i) displays the actual GET's server detail", async (_status, detail) => {
  (api.listWorlds as any).mockResolvedValue(ONE_WORLD);
  renderView();
  fireEvent.click(await screen.findByLabelText("Export Saltmarch"));

  const frame = screen.getByTitle("Export Saltmarch response");
  expect(frame).toHaveAttribute("src", "/api/worlds/w1/export.zip");
  const response = document.implementation.createHTMLDocument();
  response.body.textContent = JSON.stringify({ detail, kind: "maintenance_running" });
  Object.defineProperty(frame, "contentDocument", { value: response });
  fireEvent.load(frame);

  expect(await screen.findByRole("alert")).toHaveTextContent(
    `'Saltmarch' could not be exported: ${detail}`);
  expect(navigate).not.toHaveBeenCalled();
});

test("an export's initial blank frame is ignored and a malformed response is visible", async () => {
  (api.listWorlds as any).mockResolvedValue(ONE_WORLD);
  renderView();
  fireEvent.click(await screen.findByLabelText("Export Saltmarch"));
  const frame = screen.getByTitle("Export Saltmarch response");
  Object.defineProperty(frame, "contentWindow", {
    configurable: true, value: { location: new URL("about:blank") },
  });
  fireEvent.load(frame);
  expect(screen.queryByRole("alert")).toBeNull();

  Object.defineProperty(frame, "contentWindow", {
    value: { location: new URL("/api/worlds/w1/export.zip", window.location.href) },
  });
  const response = document.implementation.createHTMLDocument();
  response.body.textContent = "Bad Gateway";
  Object.defineProperty(frame, "contentDocument", { value: response });
  fireEvent.load(frame);
  expect(await screen.findByRole("alert")).toHaveTextContent(/could not be exported/);
});

test("an export never accepts refusal text from a different document", async () => {
  (api.listWorlds as any).mockResolvedValue(ONE_WORLD);
  renderView();
  fireEvent.click(await screen.findByLabelText("Export Saltmarch"));
  const frame = screen.getByTitle("Export Saltmarch response");
  const response = document.implementation.createHTMLDocument();
  response.body.textContent = JSON.stringify({ detail: "untrusted refusal" });
  Object.defineProperty(frame, "contentDocument", { value: response });
  Object.defineProperty(frame, "contentWindow", {
    value: { location: new URL("https://example.org/api/worlds/w1/export.zip") },
  });
  fireEvent.load(frame);
  expect(screen.queryByText(/untrusted refusal/)).toBeNull();
});

test("repeating an export issues another streamed GET without cancelling a different world", async () => {
  (api.listWorlds as any).mockResolvedValue([
    ...ONE_WORLD, { id: "w2", name: "Realm", created: "", updated: "", counts: {} },
  ]);
  renderView();
  const saltmarch = await screen.findByLabelText("Export Saltmarch");
  fireEvent.click(saltmarch);
  const first = screen.getByTitle("Export Saltmarch response");
  fireEvent.click(screen.getByLabelText("Export Realm"));
  fireEvent.click(saltmarch);

  expect(screen.getAllByTitle("Export Saltmarch response")).toHaveLength(2);
  expect(first).toBeInTheDocument();
  expect(screen.getByTitle("Export Realm response")).toHaveAttribute("src", "/api/worlds/w2/export.zip");
});

test("importing a bundle posts the file, refreshes, and opens the new world", async () => {
  renderView();
  await waitFor(() => expect(api.listWorlds).toHaveBeenCalled());
  const file = new File([new Uint8Array([0x50, 0x4b])], "saltmarch-world.zip",
                        { type: "application/zip" });
  fireEvent.change(screen.getByLabelText("Import world bundle"), { target: { files: [file] } });
  await waitFor(() => expect(api.importWorld).toHaveBeenCalledWith(file));
  await waitFor(() => expect(navigate).toHaveBeenCalledWith("/worlds/imported"));
  expect(api.listWorlds).toHaveBeenCalledTimes(BASE + 1);
});

test("a failed refresh after a successful import is not reported as a failure", async () => {
  // Receiving the id is the commit point -- the world exists. Reporting the
  // refresh's failure as the import's would send the user to retry, and the
  // retry imports a second copy.
  (api.listWorlds as any)
    .mockResolvedValueOnce([])
    .mockRejectedValueOnce(new Error("network"));
  renderView();
  await waitFor(() => expect(api.listWorlds).toHaveBeenCalled());
  const file = new File(["zip"], "saltmarch-world.zip", { type: "application/zip" });
  fireEvent.change(screen.getByLabelText("Import world bundle"), { target: { files: [file] } });
  await waitFor(() => expect(navigate).toHaveBeenCalledWith("/worlds/imported"));
  // No error shown *at all* -- matching only on "could not import" would let
  // the refresh's own message ("network") through as a passing test.
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

test("a rejected bundle shows the server's reason and creates nothing", async () => {
  (api.importWorld as any).mockRejectedValue({ detail: "not a world bundle: no grimoire-bundle.json" });
  renderView();
  await waitFor(() => expect(api.listWorlds).toHaveBeenCalled());
  const file = new File(["junk"], "notes.zip", { type: "application/zip" });
  fireEvent.change(screen.getByLabelText("Import world bundle"), { target: { files: [file] } });
  expect(await screen.findByRole("alert")).toHaveTextContent(/not a world bundle/i);
  expect(navigate).not.toHaveBeenCalled();
  expect(api.listWorlds).toHaveBeenCalledTimes(BASE);   // no refresh, nothing changed
});

// ---- world fork (#41) ----

const ONE_WORLD = [
  { id: "w1", name: "Saltmarch", created: "", updated: "", counts: {} },
];

test("forking prompts for a name, posts it, and refreshes the grid", async () => {
  // Refreshed rather than navigated to: `listWorlds` orders by `updated` and
  // the fork stamps its own, so the copy lands at the front of the grid the
  // user is already looking at.
  (api.listWorlds as any).mockResolvedValue(ONE_WORLD);
  const prompt = vi.spyOn(window, "prompt").mockReturnValue("Winifred");
  renderView();
  fireEvent.click(await screen.findByLabelText("Fork Saltmarch"));
  await waitFor(() => expect(api.forkWorld).toHaveBeenCalledWith("w1", "Winifred"));
  await waitFor(() => expect(api.listWorlds).toHaveBeenCalledTimes(BASE + 1));
  expect(navigate).not.toHaveBeenCalled();
  expect(prompt).toHaveBeenCalledWith("Fork 'Saltmarch' as?", "Saltmarch (fork)");
  prompt.mockRestore();
});

test("a dismissed or blank fork prompt copies nothing", async () => {
  (api.listWorlds as any).mockResolvedValue(ONE_WORLD);
  const prompt = vi.spyOn(window, "prompt").mockReturnValue(null);
  renderView();
  fireEvent.click(await screen.findByLabelText("Fork Saltmarch"));
  prompt.mockReturnValue("   ");
  fireEvent.click(screen.getByLabelText("Fork Saltmarch"));
  await waitFor(() => expect(api.listWorlds).toHaveBeenCalledTimes(BASE));
  expect(api.forkWorld).not.toHaveBeenCalled();
  prompt.mockRestore();
});

test("a failed fork names the world and the reason, and refreshes nothing", async () => {
  (api.listWorlds as any).mockResolvedValue(ONE_WORLD);
  (api.forkWorld as any).mockRejectedValue({ detail: "could not claim a world id" });
  const prompt = vi.spyOn(window, "prompt").mockReturnValue("Winifred");
  renderView();
  fireEvent.click(await screen.findByLabelText("Fork Saltmarch"));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    /'Saltmarch' could not be forked: could not claim a world id/i);
  expect(api.listWorlds).toHaveBeenCalledTimes(BASE);
  prompt.mockRestore();
});

test("a fork in flight cannot be started twice", async () => {
  // A world runs to a gigabyte of art, so this request takes real time and a
  // second click on a pending one would make a second copy.
  (api.listWorlds as any).mockResolvedValue([
    ...ONE_WORLD,
    { id: "w2", name: "Realm", created: "", updated: "", counts: {} },
  ]);
  let release: (v: unknown) => void = () => {};
  (api.forkWorld as any).mockReturnValue(new Promise((r) => { release = r; }));
  const prompt = vi.spyOn(window, "prompt").mockReturnValue("Winifred");
  renderView();
  fireEvent.click(await screen.findByLabelText("Fork Saltmarch"));
  await waitFor(() => expect(screen.getByLabelText("Fork Saltmarch")).toBeDisabled());
  expect(screen.getByLabelText("Fork Realm")).toBeDisabled();
  fireEvent.click(screen.getByLabelText("Fork Realm"));
  expect(api.forkWorld).toHaveBeenCalledTimes(1);

  release({ id: "copy" });
  await waitFor(() => expect(screen.getByLabelText("Fork Saltmarch")).toBeEnabled());
  prompt.mockRestore();
});

test("deleting a world is blocked while a fork is copying it", async () => {
  // The copy walks the SOURCE for as long as it runs, so deleting that world
  // mid-walk fails the fork and leaves neither the copy nor the original.
  (api.listWorlds as any).mockResolvedValue(ONE_WORLD);
  let release: (v: unknown) => void = () => {};
  (api.forkWorld as any).mockReturnValue(new Promise((r) => { release = r; }));
  const prompt = vi.spyOn(window, "prompt").mockReturnValue("Winifred");
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  renderView();

  expect(await screen.findByLabelText("Delete Saltmarch")).toBeEnabled();
  fireEvent.click(screen.getByLabelText("Fork Saltmarch"));
  await waitFor(() => expect(screen.getByLabelText("Delete Saltmarch")).toBeDisabled());
  fireEvent.click(screen.getByLabelText("Delete Saltmarch"));
  expect(api.deleteWorld).not.toHaveBeenCalled();

  release({ id: "copy" });
  await waitFor(() => expect(screen.getByLabelText("Delete Saltmarch")).toBeEnabled());
  prompt.mockRestore();
  confirm.mockRestore();
});

test("the post-fork refresh does not join a read started before it published", async () => {
  // `request()` shares in-flight GETs by path, so a world-list read issued
  // while the copy was running would answer with a list the fork is not in --
  // no error, button re-enabled, and the obvious retry makes a second copy.
  (api.listWorlds as any).mockResolvedValue(ONE_WORLD);
  const prompt = vi.spyOn(window, "prompt").mockReturnValue("Winifred");
  renderView();
  fireEvent.click(await screen.findByLabelText("Fork Saltmarch"));
  await waitFor(() => expect(api.forkWorld).toHaveBeenCalled());
  await waitFor(() => expect(api.listWorlds).toHaveBeenCalledWith(true));
  prompt.mockRestore();
});

test("a failed refresh after a successful fork says the copy exists", async () => {
  // Returning from the call is the commit point. Reporting the refresh's
  // failure as the fork's would send the user to retry, and after a minute of
  // copying a gigabyte the retry makes a second copy. Swallowing it silently
  // is no better here: unlike an import, nothing navigates, so a quiet no-op
  // looks exactly like a fork that never happened.
  (api.listWorlds as any)
    .mockResolvedValue([...ONE_WORLD, { id: "winifred", name: "Winifred", counts: {} }])
    .mockResolvedValueOnce(ONE_WORLD)
    .mockResolvedValueOnce(ONE_WORLD)
    .mockRejectedValueOnce(new Error("network"));
  const prompt = vi.spyOn(window, "prompt").mockReturnValue("Winifred");
  renderView();
  fireEvent.click(await screen.findByLabelText("Fork Saltmarch"));
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent(/'Winifred' was created/i);
  expect(alert).not.toHaveTextContent(/could not be forked/i);
  // The grid that could not be confirmed is not kept: the page says it could
  // not read the worlds, and Try again brings them back -- the copy with
  // them, and the fork button live rather than stuck disabled by the throw.
  fireEvent.click(screen.getByRole("button", { name: /try again/i }));
  expect(await screen.findByText("Winifred")).toBeInTheDocument();
  expect(screen.getByLabelText("Fork Saltmarch")).toBeEnabled();
  prompt.mockRestore();
});

// ---- intent prefetch ----

const SALTMARCH = { id: "w1", name: "Saltmarch", created: "", updated: "", counts: {} };
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function worldCard() {
  (api.listWorlds as any).mockResolvedValue([SALTMARCH]);
  renderView();
  await screen.findByText("Saltmarch");
  return screen.getByText("Saltmarch").closest("button") as HTMLElement;
}

test("resting on a world card starts its reads, once", async () => {
  // The world page's record and its character rows, started while the reader
  // is still deciding: the page that mounts on the click joins them in flight
  // or paints what they brought back.
  const card = await worldCard();
  fireEvent.pointerEnter(card);
  await waitFor(() => expect(api.getWorld).toHaveBeenCalledWith("w1"));
  expect(api.listCharacters).toHaveBeenCalledWith({ kind: "world", id: "w1" });
  // The press that follows, and a second rest, ask for nothing more.
  fireEvent.pointerDown(card);
  fireEvent.pointerLeave(card);
  fireEvent.pointerEnter(card);
  await sleep(INTENT_DWELL_MS * 2);
  expect(api.getWorld).toHaveBeenCalledTimes(1);
  expect(api.listCharacters).toHaveBeenCalledTimes(1);
});

test("a pointer passing over a card on its way elsewhere asks for nothing", async () => {
  const card = await worldCard();
  fireEvent.pointerEnter(card);
  fireEvent.pointerLeave(card);
  await sleep(INTENT_DWELL_MS * 2);
  expect(api.getWorld).not.toHaveBeenCalled();
  expect(api.listCharacters).not.toHaveBeenCalled();
});

test("a press prefetches at once, with no dwell", async () => {
  // A mouse or pen press. A finger waits for a rest first (`prefetch.test.ts`),
  // because on a phone a press is also how every scroll begins.
  const card = await worldCard();
  fireEvent.pointerDown(card);
  await waitFor(() => expect(api.listCharacters).toHaveBeenCalledWith({ kind: "world", id: "w1" }));
  fireEvent.pointerDown(card);
  await sleep(10);
  expect(api.getWorld).toHaveBeenCalledTimes(1);
  expect(api.listCharacters).toHaveBeenCalledTimes(1);
});

test("keyboard focus resting on a card is intent too", async () => {
  const card = await worldCard();
  fireEvent.focus(card);
  await waitFor(() => expect(api.getWorld).toHaveBeenCalledWith("w1"));
});

test("what is already remembered is not prefetched again", async () => {
  (api.rememberedWorld as any).mockReturnValue({ meta: SALTMARCH, body: "", counts: {} });
  (api.rememberedCharacters as any).mockReturnValue([]);
  const card = await worldCard();
  fireEvent.pointerDown(card);
  await sleep(10);
  expect(api.getWorld).not.toHaveBeenCalled();
  expect(api.listCharacters).not.toHaveBeenCalled();
});

test("a prefetch that fails is swallowed -- the page's own read is the one that reports", async () => {
  (api.getWorld as any).mockRejectedValue(new Error("offline"));
  (api.listCharacters as any).mockRejectedValue(new Error("offline"));
  const card = await worldCard();
  fireEvent.pointerDown(card);
  await waitFor(() => expect(api.listCharacters).toHaveBeenCalled());
  await sleep(10);
  expect(screen.queryByText(/offline/)).toBeNull();
});

test("a world can be created with its profile, and a card shows the genre", async () => {
  (api.listWorlds as any).mockResolvedValue([
    { id: "realm", name: "Realm", genre: "Coastal gothic", created: "", updated: "", counts: {} }]);
  render(<MemoryRouter><WorldsView /></MemoryRouter>);
  expect(await screen.findByText("Coastal gothic")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("World name"), { target: { value: "Saltmarch" } });
  expect(screen.queryByLabelText("Genre")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "More details" }));
  fireEvent.change(screen.getByLabelText("Genre"), { target: { value: "Gothic" } });
  fireEvent.change(screen.getByLabelText("Tone"), { target: { value: "Dread" } });
  fireEvent.change(screen.getByLabelText("Themes"), { target: { value: "salt, debt, " } });
  fireEvent.change(screen.getByLabelText("Description"), { target: { value: "A coast." } });
  fireEvent.click(screen.getByRole("button", { name: "Create" }));
  await waitFor(() => expect(api.createWorld).toHaveBeenCalledWith("Saltmarch", {
    genre: "Gothic", tone: "Dread", themes: ["salt", "debt"], description: "A coast." }));
  // the form closes behind it, ready for the next one-line create
  await waitFor(() => expect(screen.queryByLabelText("Genre")).not.toBeInTheDocument());
});

// ---- first paint from memory, then revalidate (roadmap 04, 6.2) ----

/** A promise and the hand that settles it. */
function held<T>() {
  let resolve: (v: T) => void = () => {};
  let reject: (reason: unknown) => void = () => {};
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

test("a remembered grid paints on the first render, busy, with rename, fork and delete held", async () => {
  (api.rememberedWorlds as any).mockReturnValue(ONE_WORLD);
  const answer = held<WorldMeta[]>();
  (api.listWorlds as any).mockReturnValue(answer.promise);
  renderView();
  // Read synchronously, before any await: this is the frame painted from
  // memory, and an await would settle past it.
  expect(screen.getByText("Saltmarch")).toBeInTheDocument();
  expect(screen.getByRole("main")).toHaveAttribute("aria-busy", "true");
  expect(screen.getByLabelText("Rename Saltmarch")).toBeDisabled();
  expect(screen.getByLabelText("Fork Saltmarch")).toBeDisabled();
  expect(screen.getByLabelText("Delete Saltmarch")).toBeDisabled();
  // Export and the card itself act on nothing, and stay live.
  expect(screen.getByLabelText("Export Saltmarch")).toHaveAttribute("href", "/api/worlds/w1/export.zip");
  expect(screen.getByText("Saltmarch").closest("button")).toBeEnabled();
  expect(api.listWorlds).toHaveBeenCalled();   // painting from memory never replaces asking

  // The visit's own answer replaces the frame wholesale.
  answer.resolve([{ id: "w2", name: "Realm", created: "", updated: "", counts: {} }]);
  expect(await screen.findByText("Realm")).toBeInTheDocument();
  expect(screen.queryByText("Saltmarch")).toBeNull();
  expect(screen.getByRole("main")).not.toHaveAttribute("aria-busy");
  expect(screen.getByLabelText("Rename Realm")).toBeEnabled();
  expect(screen.getByLabelText("Fork Realm")).toBeEnabled();
  expect(screen.getByLabelText("Delete Realm")).toBeEnabled();
});

test("no count is stated before a read has landed", async () => {
  const answer = held<WorldMeta[]>();
  (api.listWorlds as any).mockReturnValue(answer.promise);
  renderView();
  // "0 worlds" is a statement; nothing has been read to make it.
  expect(screen.queryByText(/0 worlds/i)).toBeNull();
  expect(screen.getByRole("main")).not.toHaveAttribute("aria-busy");   // nothing stale either
  answer.resolve([]);
  expect(await screen.findByText(/0 worlds/i)).toBeInTheDocument();
});

test("a remembered empty grid states nothing until its read lands", async () => {
  (api.rememberedWorlds as any).mockReturnValue([]);
  const answer = held<WorldMeta[]>();
  (api.listWorlds as any).mockReturnValue(answer.promise);
  renderView();
  expect(screen.queryByText(/0 worlds/i)).toBeNull();
  answer.resolve(ONE_WORLD);
  expect(await screen.findByText(/^1 world$/i)).toBeInTheDocument();
});

test("a failed read shows its banner and no count, and Try again re-reads", async () => {
  (api.rememberedWorlds as any).mockReturnValue(ONE_WORLD);
  (api.listWorlds as any).mockRejectedValue(new Error("offline"));
  renderView();
  expect(await screen.findByText(/the worlds could not be read/i)).toBeInTheDocument();
  // The remembered grid is dropped with the failure, and no count stands in.
  expect(screen.queryByText("Saltmarch")).toBeNull();
  expect(screen.queryByText(/0 worlds/i)).toBeNull();

  (api.listWorlds as any).mockResolvedValue(ONE_WORLD);
  fireEvent.click(screen.getByRole("button", { name: /try again/i }));
  expect(await screen.findByText("Saltmarch")).toBeInTheDocument();
  expect(api.listWorlds).toHaveBeenLastCalledWith(true);
  expect(screen.queryByText(/could not be read/i)).toBeNull();
});

test("every refresh after a change is a fresh read", async () => {
  // A non-fresh read would join one the visit sent before the change.
  (api.listWorlds as any).mockResolvedValue(ONE_WORLD);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  renderView();
  await screen.findByText("Saltmarch");
  expect(api.listWorlds).not.toHaveBeenCalledWith(true);

  fireEvent.change(screen.getByPlaceholderText(/world name/i), { target: { value: "Realm" } });
  fireEvent.click(screen.getByRole("button", { name: /^create$/i }));
  await waitFor(() => expect(api.listWorlds).toHaveBeenCalledTimes(BASE + 1));
  expect(api.listWorlds).toHaveBeenLastCalledWith(true);

  fireEvent.click(screen.getByLabelText("Rename Saltmarch"));
  fireEvent.keyDown(screen.getByLabelText("Rename world"), { key: "Enter" });
  await waitFor(() => expect(api.listWorlds).toHaveBeenCalledTimes(BASE + 2));
  expect(api.listWorlds).toHaveBeenLastCalledWith(true);

  fireEvent.click(screen.getByLabelText("Delete Saltmarch"));
  await waitFor(() => expect(api.listWorlds).toHaveBeenCalledTimes(BASE + 3));
  expect(api.listWorlds).toHaveBeenLastCalledWith(true);
});

test("the visit's own read, landing late, cannot paint over a refresh after a change", async () => {
  // Create is live on a stale frame (it names no existing world), so it can
  // overtake the mount-time read: only the newest read may paint.
  (api.rememberedWorlds as any).mockReturnValue(ONE_WORLD);
  const mount = held<WorldMeta[]>();
  const after = [...ONE_WORLD, { id: "w2", name: "Realm", created: "", updated: "", counts: {} }];
  (api.listWorlds as any).mockImplementation((fresh?: boolean) =>
    (fresh ? Promise.resolve(after) : mount.promise));
  renderView();
  fireEvent.change(screen.getByPlaceholderText(/world name/i), { target: { value: "Realm" } });
  fireEvent.click(screen.getByRole("button", { name: /^create$/i }));
  expect(await screen.findByText("Realm")).toBeInTheDocument();
  mount.resolve(ONE_WORLD);
  await screen.findByText("Saltmarch");
  expect(screen.getByText("Realm")).toBeInTheDocument();
});
