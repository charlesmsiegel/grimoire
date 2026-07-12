import { render, screen, fireEvent } from "@testing-library/react";
import { WorldOverview } from "./WorldOverview";

vi.mock("../api/client", () => ({
  api: {
    getWorld: vi.fn(), listGreetings: vi.fn(), readGreeting: vi.fn(),
    listCharacters: vi.fn(), listUntaggedImages: vi.fn(),
  },
}));
import { api } from "../api/client";

beforeEach(() => {
  vi.clearAllMocks();
  (api.getWorld as any).mockResolvedValue({ meta: { id: "w", name: "Saltmarch" }, body: "",
    counts: { characters: 2, pcs: 1, locations: 3, lore: 5, items: 0, groups: 1, creatures: 0, greetings: 2 } });
  (api.listGreetings as any).mockResolvedValue([{ id: "g1", name: "Gala" }, { id: "g2", name: "Docks" }]);
  (api.readGreeting as any).mockResolvedValue({ meta: { id: "g1" }, body: "", predecessors: [],
    edges: { leads_to: ["g2"], excludes: [] } });
  (api.listCharacters as any).mockResolvedValue([
    { id: "mara", name: "Mara", default_version: "default", versions: [], tagline: "a smuggler" },
    { id: "winifred", name: "Winifred", default_version: "default", versions: [] },  // no tagline
  ]);
  (api.listUntaggedImages as any).mockResolvedValue([]);
});

test("renders count tiles that navigate to their tab", async () => {
  const nav = vi.fn();
  render(<WorldOverview wid="w" onNavigate={nav} />);
  fireEvent.click(await screen.findByRole("button", { name: /3\s+Locations/i }));
  expect(nav).toHaveBeenCalledWith("locations");
  fireEvent.click(screen.getByRole("button", { name: /1\s+Groups/i }));
  expect(nav).toHaveBeenCalledWith("groups");
});

test("derives the setup checklist", async () => {
  const nav = vi.fn();
  render(<WorldOverview wid="w" onNavigate={nav} />);
  expect(await screen.findByText(/plot map has connections/i)).toBeInTheDocument();
  const missing = screen.getByText(/1 character missing a tagline/i);
  fireEvent.click(missing);                       // next-action: jump to Characters
  expect(nav).toHaveBeenCalledWith("characters");
});
