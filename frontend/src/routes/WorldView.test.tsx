import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import WorldView from "./WorldView";

vi.mock("../api/client", () => ({
  api: {
    getWorld: vi.fn(),
    getCampaign: vi.fn(),
    listCharacters: vi.fn(),
    listPCs: vi.fn(),
    listTags: vi.fn(),
    listEntities: vi.fn(),
    listGreetings: vi.fn(),
  },
}));
import { api } from "../api/client";

beforeEach(() => {
  vi.clearAllMocks();
  (api.getWorld as any).mockResolvedValue({ meta: { id: "w", name: "Drowned Realm" }, body: "", counts: {} });
  (api.getCampaign as any).mockResolvedValue({ meta: { id: "c1", name: "Ashes of the Verdigris Crown", world: "w" } });
  (api.listCharacters as any).mockResolvedValue([]);
  (api.listPCs as any).mockResolvedValue([]);
  (api.listTags as any).mockResolvedValue({});
  (api.listEntities as any).mockResolvedValue([]);
  (api.listGreetings as any).mockResolvedValue([]);
});

function renderAt() {
  render(
    <MemoryRouter initialEntries={["/worlds/w"]}>
      <Routes>
        <Route path="/worlds/:wid" element={<WorldView />} />
      </Routes>
    </MemoryRouter>,
  );
}

test("shows the world name and the Characters tab by default", async () => {
  renderAt();
  await screen.findByText("Drowned Realm");
  await waitFor(() => expect(api.listCharacters).toHaveBeenCalledWith("w"));
  expect(screen.getByRole("button", { name: /new character/i })).toBeInTheDocument();
});

test("switching to the PCs tab renders the PC editor", async () => {
  renderAt();
  await screen.findByText("Drowned Realm");
  fireEvent.click(screen.getByRole("button", { name: "PCs" }));
  await waitFor(() => expect(api.listPCs).toHaveBeenCalledWith("w"));
  expect(screen.getByRole("button", { name: /new pc/i })).toBeInTheDocument();
});

test("world-copy mode shows the fork banner, campaign back link, and campaign entity scope", async () => {
  render(
    <MemoryRouter initialEntries={["/campaigns/c1/world"]}>
      <Routes>
        <Route path="/campaigns/:cid/world" element={<WorldView campaign />} />
      </Routes>
    </MemoryRouter>,
  );
  await screen.findByText(/ashes of the verdigris crown \/ world copy/i);
  expect(screen.getByText(/campaign copy/i)).toBeInTheDocument();
  // entity tabs read from the campaign fork, not the source world
  fireEvent.click(screen.getByRole("button", { name: "Locations" }));
  await waitFor(() =>
    expect(api.listEntities).toHaveBeenCalledWith({ kind: "campaign", id: "c1" }, "locations"));
});

test("the Lore tab hosts the lorebook importer", async () => {
  renderAt();
  await screen.findByText("Drowned Realm");
  fireEvent.click(screen.getByRole("button", { name: "Lore" }));
  fireEvent.click(screen.getByText(/import lorebook/i)); // expand the details
  expect(screen.getByRole("button", { name: /parse/i })).toBeInTheDocument();
});
