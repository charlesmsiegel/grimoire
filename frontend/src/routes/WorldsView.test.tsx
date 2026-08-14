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
});

function renderView() {
  render(
    <MemoryRouter>
      <WorldsView />
    </MemoryRouter>,
  );
}

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
