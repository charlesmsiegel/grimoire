import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { EntityEditor } from "./EntityEditor";

vi.mock("../api/client", () => ({
  api: {
    listEntities: vi.fn(),
    createEntity: vi.fn(),
    readEntity: vi.fn(),
    updateEntity: vi.fn(),
    deleteEntity: vi.fn(),
  },
}));
import { api } from "../api/client";

beforeEach(() => {
  vi.clearAllMocks();
  (api.listEntities as any).mockResolvedValue([]);
  (api.createEntity as any).mockResolvedValue({ id: "e1" });
  (api.updateEntity as any).mockResolvedValue({ ok: true });
  (api.deleteEntity as any).mockResolvedValue({ ok: true });
});

test("lists entities and creates one with keys", async () => {
  render(<EntityEditor wid="w" kind="lore" />);
  await waitFor(() => expect(api.listEntities).toHaveBeenCalledWith({ kind: "world", id: "w" }, "lore"));
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Salt Pact" } });
  fireEvent.change(screen.getByLabelText("Body"), { target: { value: "binds" } });
  fireEvent.change(screen.getByLabelText("Keys"), { target: { value: "pact,salt" } });
  fireEvent.click(screen.getByRole("button", { name: /create lore entry/i }));
  await waitFor(() =>
    expect(api.createEntity).toHaveBeenCalledWith({ kind: "world", id: "w" }, "lore", {
      name: "Salt Pact",
      body: "binds",
      keys: "pact,salt",
    }),
  );
});

test("editing an entity saves with updated keys", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "salt", name: "Salt", keys: "pact" }]);
  (api.readEntity as any).mockResolvedValue({ meta: { id: "salt", name: "Salt", keys: "pact" }, body: "x" });
  render(<EntityEditor wid="w" kind="lore" />);
  fireEvent.click(await screen.findByText("Salt"));
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  fireEvent.change(screen.getByLabelText("Keys"), { target: { value: "pact,brine" } });
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
  await waitFor(() =>
    expect(api.updateEntity).toHaveBeenCalledWith({ kind: "world", id: "w" }, "lore", "salt",
      expect.objectContaining({ keys: "pact,brine" })),
  );
});

test("deletes after confirm", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "salt", name: "Salt" }]);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<EntityEditor wid="w" kind="lore" />);
  await screen.findByText("Salt");
  fireEvent.click(screen.getByRole("button", { name: /delete/i }));
  await waitFor(() => expect(api.deleteEntity).toHaveBeenCalledWith({ kind: "world", id: "w" }, "lore", "salt"));
});
