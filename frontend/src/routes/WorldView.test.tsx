import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import WorldView from "./WorldView";

vi.mock("../api/client", () => ({
  api: {
    getWorld: vi.fn(),
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

test("switching to the Import tab renders the lorebook importer", async () => {
  renderAt();
  await screen.findByText("Drowned Realm");
  fireEvent.click(screen.getByRole("button", { name: "Import" }));
  expect(screen.getByRole("button", { name: /parse/i })).toBeInTheDocument();
});
