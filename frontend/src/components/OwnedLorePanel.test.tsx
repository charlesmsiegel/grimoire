import { render, screen, fireEvent } from "@testing-library/react";
import { OwnedLorePanel } from "./OwnedLorePanel";

vi.mock("../api/client", () => ({ api: { listEntities: vi.fn() } }));
import { api } from "../api/client";

beforeEach(() => {
  vi.clearAllMocks();
  (api.listEntities as any).mockResolvedValue([
    { id: "a", name: "Exile", owners: "characters:tanaka" },
    { id: "b", name: "World fact" },
    { id: "c", name: "Duel", owners: "characters:tanaka, locations:dojo" },
  ]);
});

test("lists only entries owned by the ref and opens one", async () => {
  const onOpenEntry = vi.fn();
  render(<OwnedLorePanel wid="w" ownerRef="characters:tanaka" onOpenEntry={onOpenEntry} onNewEntry={vi.fn()} />);
  expect(await screen.findByText("Exile")).toBeInTheDocument();
  expect(await screen.findByText("Duel")).toBeInTheDocument();
  expect(screen.queryByText("World fact")).toBeNull();
  fireEvent.click(screen.getByText("Exile"));
  expect(onOpenEntry).toHaveBeenCalledWith("a");
});

test("the + New lore button fires onNewEntry", async () => {
  const onNewEntry = vi.fn();
  render(<OwnedLorePanel wid="w" ownerRef="characters:tanaka" onOpenEntry={vi.fn()} onNewEntry={onNewEntry} />);
  fireEvent.click(await screen.findByRole("button", { name: /\+ new lore/i }));
  expect(onNewEntry).toHaveBeenCalled();
});
