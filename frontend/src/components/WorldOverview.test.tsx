import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
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

/** Every producer here goes through this, same as `WorldView` passes its own
 *  `hrefFor` down — a tab name becomes a path under the world this suite's
 *  default mocks describe. */
const hrefFor = (t: string) => `/worlds/w/${t}`;

function show(props: Partial<Parameters<typeof WorldOverview>[0]> = {}) {
  return render(
    <MemoryRouter>
      <WorldOverview wid="w" hrefFor={hrefFor} {...props} />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  (api.getWorld as any).mockResolvedValue({ meta: { id: "w", name: "Saltmarch" }, body: "",
    counts: { characters: 2, pcs: 1, locations: 3, lore: 5, items: 0, groups: 1, creatures: 0, greetings: 2 } });
  // Each row carries its plot-map edges, which is all the connections row needs.
  (api.listGreetings as any).mockResolvedValue([
    { id: "g1", name: "Gala", edges: { leads_to: ["g2"], excludes: [] } },
    { id: "g2", name: "Docks", edges: { leads_to: [], excludes: [] } }]);
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

test("renders count tiles that link to their section", async () => {
  show();
  expect(await screen.findByRole("link", { name: /3\s+Locations/i }))
    .toHaveAttribute("href", "/worlds/w/locations");
  expect(screen.getByRole("link", { name: /1\s+Groups/i }))
    .toHaveAttribute("href", "/worlds/w/groups");
});

test("a tile is a link to its section", async () => {
  render(<MemoryRouter><WorldOverview wid="realm" hrefFor={(t) => `/worlds/realm/${t}`} /></MemoryRouter>);
  expect(await screen.findByRole("link", { name: /Locations/ }))
    .toHaveAttribute("href", "/worlds/realm/locations");
});

test("derives the setup checklist", async () => {
  show();
  expect(await screen.findByText(/plot map has connections/i)).toBeInTheDocument();
  const missing = screen.getByText(/1 character missing a tagline/i);
  expect(missing.closest("a")).toHaveAttribute("href", "/worlds/w/characters");
});

// ---- the plot map row: one list read, not one read per greeting ----

test("the plot map row reads the list's edges, and no greeting one by one", async () => {
  // A read per greeting was dozens of requests on every open of a world, all
  // landing just as the reader clicks through to its characters.
  show();
  expect(await screen.findByText(/✓ Plot map has connections/)).toBeInTheDocument();
  expect(api.listGreetings).toHaveBeenCalledTimes(1);
  expect(api.readGreeting).not.toHaveBeenCalled();
});

test("an exclusion is a connection too", async () => {
  (api.listGreetings as any).mockResolvedValue([
    { id: "g1", name: "Gala", edges: { leads_to: [], excludes: ["g2"] } },
    { id: "g2", name: "Docks", edges: { leads_to: [], excludes: [] } }]);
  show();
  expect(await screen.findByText(/✓ Plot map has connections/)).toBeInTheDocument();
});

test("greetings with no edges leave the plot map row open", async () => {
  (api.listGreetings as any).mockResolvedValue([
    { id: "g1", name: "Gala", edges: { leads_to: [], excludes: [] } },
    { id: "g2", name: "Docks", edges: { leads_to: [], excludes: [] } }]);
  show();
  expect(await screen.findByText(/○ Plot map has connections/)).toBeInTheDocument();
  expect(screen.getByText(/✓ Has a greeting/)).toBeInTheDocument();
});

test("a plot map the server could not read is no row at all, not an open one", async () => {
  // Rows without `edges` mean the map could not be read -- unknown, which is
  // not "no connections", and a chore that drawing a link would never clear.
  (api.listGreetings as any).mockResolvedValue([{ id: "g1", name: "Gala" }, { id: "g2", name: "Docks" }]);
  show();
  expect(await screen.findByText(/✓ Has a greeting/)).toBeInTheDocument();
  expect(screen.queryByText(/Plot map has connections/)).toBeNull();
});

test("a checklist row is a link, and a row with no section is not", async () => {
  render(<MemoryRouter><WorldOverview wid="realm" hrefFor={(t) => `/worlds/realm/${t}`} /></MemoryRouter>);
  expect(await screen.findByRole("link", { name: /Has a location/ }))
    .toHaveAttribute("href", "/worlds/realm/locations");
  expect(screen.queryByRole("link", { name: /Calendar confirmed/ })).toBeNull();
});

// ---- the calendar's confirmed flag (#223) ----

test("an unconfirmed world calendar is an open checklist item", async () => {
  show();
  expect(await screen.findByText(/○ Calendar confirmed/)).toBeInTheDocument();
});

test("a confirmed world calendar closes it", async () => {
  (api.getCalendarConfig as any).mockResolvedValue({
    primary: { provider: "gregorian", region: "US", custom_holidays: [], anchor: null },
    secondary: null, confirmed: true, stale_after_days: 30 });
  show();
  expect(await screen.findByText(/✓ Calendar confirmed/)).toBeInTheDocument();
});

test("the calendar row is a statement, not a next-action — the editor is on this page", async () => {
  // Every other row jumps to the tab that fixes it. This one has nowhere to
  // jump: the world's calendar editor is a section of the Overview itself, so
  // a link here would be a click that did nothing.
  show();
  const row = await screen.findByText(/○ Calendar confirmed/);
  expect(row.closest("a")).toBeNull();
  expect(await screen.findByLabelText("Calendar")).toBeInTheDocument();
});

test("confirming the calendar closes the checklist item without a reload", async () => {
  show();
  fireEvent.click(await screen.findByLabelText(/confirmed/i));
  fireEvent.click(screen.getByRole("button", { name: "Save calendar" }));
  expect(await screen.findByText(/✓ Calendar confirmed/)).toBeInTheDocument();
});

test("a world whose calendar cannot be read shows no calendar row at all", async () => {
  // Unknown is not "unconfirmed": a failed read must not put a chore on the
  // list that confirming would never clear.
  (api.getCalendarConfig as any).mockRejectedValue(new Error("nope"));
  show();
  expect(await screen.findByText(/plot map has connections/i)).toBeInTheDocument();
  expect(screen.queryByText(/Calendar confirmed/)).toBeNull();
});

test("the checklist reads the world's calendar once, not once per component", async () => {
  show();
  await screen.findByText(/○ Calendar confirmed/);
  expect(api.getCalendarConfig).toHaveBeenCalledTimes(1);
});

test("switching worlds drops the previous world's calendar row", async () => {
  // The row and the section below it are one claim. Leaving the old flag up
  // while the section says "Loading calendar…" is the page contradicting itself
  // about the world whose name is at the top of it.
  (api.getCalendarConfig as any).mockImplementation((scope: { id: string }) =>
    scope.id === "w"
      ? Promise.resolve({ primary: { provider: "gregorian", region: "US", custom_holidays: [], anchor: null },
                          secondary: null, confirmed: true, stale_after_days: 30 })
      : new Promise(() => {}));                    // the next world never answers
  const { rerender } = render(
    <MemoryRouter><WorldOverview wid="w" hrefFor={hrefFor} /></MemoryRouter>);
  expect(await screen.findByText(/✓ Calendar confirmed/)).toBeInTheDocument();
  rerender(<MemoryRouter><WorldOverview wid="w2" hrefFor={hrefFor} /></MemoryRouter>);
  expect(await screen.findByText(/loading calendar/i)).toBeInTheDocument();
  expect(screen.queryByText(/Calendar confirmed/)).toBeNull();
});
