import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
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

function show(props: { hrefFor?: (id: string) => string; newHref?: string } = {}) {
  return render(
    <MemoryRouter>
      <OwnedLorePanel scope={{ kind: "world", id: "w" }} ownerRef="characters:tanaka"
                      hrefFor={(id) => `/worlds/w/lore/${id}`}
                      newHref="/worlds/w/lore?owner=characters%3Atanaka"
                      {...props} />
    </MemoryRouter>,
  );
}

test("lists only entries owned by the ref and links each to its own address", async () => {
  show();
  expect(await screen.findByRole("link", { name: "Exile" })).toHaveAttribute("href", "/worlds/w/lore/a");
  expect(screen.getByRole("link", { name: "Duel" })).toHaveAttribute("href", "/worlds/w/lore/c");
  expect(screen.queryByText("World fact")).toBeNull();
});

test("the + New lore link pre-owns the form", async () => {
  show();
  expect(await screen.findByRole("link", { name: /\+ new lore/i }))
    .toHaveAttribute("href", "/worlds/w/lore?owner=characters%3Atanaka");
});
