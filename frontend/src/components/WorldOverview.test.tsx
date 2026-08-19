import { render, screen, fireEvent } from "@testing-library/react";
import { WorldOverview } from "./WorldOverview";

vi.mock("../api/client", () => ({
  api: {
    getWorld: vi.fn(), listGreetings: vi.fn(), readGreeting: vi.fn(),
    listCharacters: vi.fn(), listUntaggedImages: vi.fn(),
    listModules: vi.fn(), setWorldModule: vi.fn(),
    getCalendarConfig: vi.fn(), setCalendarConfig: vi.fn(), getCalendarProviders: vi.fn(),
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
  (api.listModules as any).mockResolvedValue([]);
  (api.setWorldModule as any).mockResolvedValue({ ok: true });
  (api.getCalendarConfig as any).mockResolvedValue({
    primary: { provider: "gregorian", region: "US", custom_holidays: [], anchor: null },
    secondary: null, confirmed: false, stale_after_days: 30 });
  (api.setCalendarConfig as any).mockResolvedValue({ ok: true });
  (api.getCalendarProviders as any).mockResolvedValue({ providers: [
    { id: "gregorian", name: "Gregorian" }, { id: "hebrew", name: "Hebrew" }] });
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

// ---- the calendar's confirmed flag (#223) ----

test("an unconfirmed world calendar is an open checklist item", async () => {
  render(<WorldOverview wid="w" onNavigate={vi.fn()} />);
  expect(await screen.findByText(/○ Calendar confirmed/)).toBeInTheDocument();
});

test("a confirmed world calendar closes it", async () => {
  (api.getCalendarConfig as any).mockResolvedValue({
    primary: { provider: "gregorian", region: "US", custom_holidays: [], anchor: null },
    secondary: null, confirmed: true, stale_after_days: 30 });
  render(<WorldOverview wid="w" onNavigate={vi.fn()} />);
  expect(await screen.findByText(/✓ Calendar confirmed/)).toBeInTheDocument();
});

test("the calendar row is a statement, not a next-action — the editor is on this page", async () => {
  // Every other row jumps to the tab that fixes it. This one has nowhere to
  // jump: the world's calendar editor is a section of the Overview itself, so
  // a button here would be a click that did nothing.
  render(<WorldOverview wid="w" onNavigate={vi.fn()} />);
  const row = await screen.findByText(/○ Calendar confirmed/);
  expect(row.closest("button")).toBeNull();
  expect(await screen.findByLabelText("Calendar")).toBeInTheDocument();
});

test("confirming the calendar closes the checklist item without a reload", async () => {
  render(<WorldOverview wid="w" onNavigate={vi.fn()} />);
  fireEvent.click(await screen.findByLabelText(/confirmed/i));
  fireEvent.click(screen.getByRole("button", { name: "Save calendar" }));
  expect(await screen.findByText(/✓ Calendar confirmed/)).toBeInTheDocument();
});

test("a world whose calendar cannot be read shows no calendar row at all", async () => {
  // Unknown is not "unconfirmed": a failed read must not put a chore on the
  // list that confirming would never clear.
  (api.getCalendarConfig as any).mockRejectedValue(new Error("nope"));
  render(<WorldOverview wid="w" onNavigate={vi.fn()} />);
  expect(await screen.findByText(/plot map has connections/i)).toBeInTheDocument();
  expect(screen.queryByText(/Calendar confirmed/)).toBeNull();
});

test("the checklist reads the world's calendar once, not once per component", async () => {
  render(<WorldOverview wid="w" onNavigate={vi.fn()} />);
  await screen.findByText(/○ Calendar confirmed/);
  expect(api.getCalendarConfig).toHaveBeenCalledTimes(1);
});
