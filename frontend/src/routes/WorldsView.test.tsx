import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import WorldsView from "./WorldsView";

const navigate = vi.fn();
vi.mock("react-router-dom", async () => ({
  ...(await vi.importActual<any>("react-router-dom")),
  useNavigate: () => navigate,
}));

vi.mock("../api/client", () => ({
  api: {
    listWorlds: vi.fn(),
    createWorld: vi.fn(),
    renameWorld: vi.fn(),
    deleteWorld: vi.fn(),
    exportWorldUrl: vi.fn(),
    importWorld: vi.fn(),
    forkWorld: vi.fn(),
  },
}));
import { api } from "../api/client";

beforeEach(() => {
  vi.clearAllMocks();
  (api.listWorlds as any).mockResolvedValue([]);
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
    .mockResolvedValueOnce(ONE_WORLD)
    .mockResolvedValueOnce(ONE_WORLD)
    .mockRejectedValueOnce(new Error("network"));
  const prompt = vi.spyOn(window, "prompt").mockReturnValue("Winifred");
  renderView();
  fireEvent.click(await screen.findByLabelText("Fork Saltmarch"));
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent(/'Winifred' was created/i);
  expect(alert).not.toHaveTextContent(/could not be forked/i);
  // And the button comes back, rather than being stuck disabled by the throw.
  await waitFor(() => expect(screen.getByLabelText("Fork Saltmarch")).toBeEnabled());
  prompt.mockRestore();
});
