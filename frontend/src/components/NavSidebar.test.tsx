import { render, screen, within, waitFor, fireEvent, act } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import NavSidebar from "./NavSidebar";
import { campaignsChanged } from "../appEvents";

vi.mock("../api/client", () => ({
  api: { listCampaigns: vi.fn() },
}));
import { api } from "../api/client";

// `activity` folds in the newest scene; `updated` is campaign.md's alone. The
// two agree here so ordering assertions read plainly — the case where they
// disagree gets its own test below.
const CAMPAIGNS = [
  { id: "saltmarch", name: "Saltmarch", world: "w", updated: "2026-03-03", activity: "2026-03-03", scenes: 4, last_scene: "Tide" },
  { id: "realm", name: "Realm", world: "w", updated: "2026-02-02", activity: "2026-02-02", scenes: 2, last_scene: "Gate" },
  { id: "winifred", name: "Winifred", world: "w", updated: "2026-01-01", activity: "2026-01-01", scenes: 1, last_scene: "" },
  { id: "mara", name: "Mara", world: "w", updated: "2025-12-31", activity: "2025-12-31", scenes: 9, last_scene: "Ash" },
  { id: "seraphine", name: "Seraphine", world: "w", updated: "2025-12-30", activity: "2025-12-30", scenes: 3, last_scene: "Vow" },
  { id: "oldest", name: "Oldest", world: "w", updated: "2025-01-01", activity: "2025-01-01", scenes: 1, last_scene: "Dust" },
];

function Probe() {
  return <span data-testid="where">{useLocation().pathname}</span>;
}

function renderNav(path = "/", props: Partial<{ rail: boolean; onToggleRail: () => void }> = {}) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <NavSidebar rail={false} onToggleRail={() => {}} {...props} />
      <Routes><Route path="*" element={<Probe />} /></Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  (api.listCampaigns as any).mockResolvedValue(CAMPAIGNS);
});

test("links to Campaigns, the Library hub and Connections", async () => {
  renderNav();
  const nav = within(await screen.findByRole("navigation", { name: /primary/i }));
  expect(nav.getByRole("link", { name: "Campaigns" })).toHaveAttribute("href", "/");
  expect(nav.getByRole("link", { name: "Library" })).toHaveAttribute("href", "/library");
  expect(nav.getByRole("link", { name: "Connections" })).toHaveAttribute("href", "/connections");
});

test("navigates to the Library hub when its link is clicked", async () => {
  renderNav();
  fireEvent.click(await screen.findByRole("link", { name: "Library" }));
  expect(screen.getByTestId("where")).toHaveTextContent("/library");
});

test("lists the five most recent campaigns, newest first, and drops the rest", async () => {
  renderNav();
  const recent = within(await screen.findByTestId("nav-recent"));
  const rows = recent.getAllByRole("link");
  expect(rows.map((r) => r.textContent)).toEqual(
    ["Saltmarch", "Realm", "Winifred", "Mara", "Seraphine"]);
  expect(recent.queryByText("Oldest")).not.toBeInTheDocument();
});

test("a recent campaign links straight to its campaign page", async () => {
  renderNav();
  fireEvent.click(await screen.findByRole("link", { name: "Realm" }));
  expect(screen.getByTestId("where")).toHaveTextContent("/campaigns/realm");
});

test("says so when there are no campaigns rather than showing an empty heading", async () => {
  (api.listCampaigns as any).mockResolvedValue([]);
  renderNav();
  expect(await screen.findByText(/no campaigns yet/i)).toBeInTheDocument();
});

test("survives a failed campaign fetch instead of taking the whole shell down", async () => {
  (api.listCampaigns as any).mockRejectedValue(new Error("offline"));
  renderNav();
  expect(await screen.findByRole("link", { name: "Library" })).toBeInTheDocument();
  expect(within(screen.getByTestId("nav-recent")).queryAllByRole("link")).toHaveLength(0);
});

test("a failed fetch never claims the library is empty — that is a different fact", async () => {
  // a user with forty campaigns and a dead backend must not be told they
  // have none; unknown and empty are not the same state
  (api.listCampaigns as any).mockRejectedValue(new Error("offline"));
  renderNav();
  await screen.findByRole("link", { name: "Library" });
  await waitFor(() => expect(api.listCampaigns).toHaveBeenCalled());
  expect(screen.queryByText(/no campaigns yet/i)).not.toBeInTheDocument();
});

test("does not flash 'no campaigns yet' before the first response arrives", async () => {
  let release: (v: unknown) => void = () => {};
  (api.listCampaigns as any).mockReturnValue(new Promise((r) => { release = r; }));
  renderNav();
  expect(screen.queryByText(/no campaigns yet/i)).not.toBeInTheDocument();
  release(CAMPAIGNS);
  expect(await screen.findByRole("link", { name: "Saltmarch" })).toBeInTheDocument();
});

test("reveals the library's sections only while one of them is open", async () => {
  renderNav("/");
  await screen.findByRole("link", { name: "Library" });
  expect(screen.queryByRole("link", { name: "Worlds" })).not.toBeInTheDocument();

  renderNav("/worlds");
  const subs = await screen.findAllByRole("link", { name: "Worlds" });
  expect(subs.length).toBeGreaterThan(0);
});

test("shows every library section as a link once inside the hub", async () => {
  renderNav("/library");
  const nav = within(await screen.findByRole("navigation", { name: /primary/i }));
  for (const [name, href] of [
    ["Worlds", "/worlds"], ["Modules", "/modules"], ["Styles", "/styles"],
    ["Response Presets", "/response-presets"], ["Climates", "/climates"],
  ] as const) {
    expect(nav.getByRole("link", { name })).toHaveAttribute("href", href);
  }
});

test("a nested library route still counts as being inside the library", async () => {
  renderNav("/worlds/saltmarch");
  expect(await screen.findByRole("link", { name: "Modules" })).toBeInTheDocument();
});

test("a route that merely starts with a library path does not count", async () => {
  // /modules-of-my-own is a different route, not a child of /modules
  renderNav("/modules-of-my-own");
  await screen.findByRole("link", { name: "Library" });
  expect(screen.queryByRole("link", { name: "Climates" })).not.toBeInTheDocument();
});

test("collapsed to a rail, every link keeps an accessible name", async () => {
  renderNav("/library", { rail: true });
  const nav = within(await screen.findByRole("navigation", { name: /primary/i }));
  expect(nav.getByRole("link", { name: "Campaigns" })).toBeInTheDocument();
  expect(nav.getByRole("link", { name: "Response Presets" })).toBeInTheDocument();
  expect(await screen.findByRole("link", { name: "Saltmarch" })).toBeInTheDocument();
});

test("the rail toggle reports which state it is in and asks for the other", async () => {
  const onToggleRail = vi.fn();
  renderNav("/", { rail: false, onToggleRail });
  const toggle = await screen.findByRole("button", { name: /collapse navigation/i });
  expect(toggle).toHaveAttribute("aria-expanded", "true");
  fireEvent.click(toggle);
  expect(onToggleRail).toHaveBeenCalledTimes(1);

  renderNav("/", { rail: true, onToggleRail });
  const collapsed = await screen.findByRole("button", { name: /expand navigation/i });
  expect(collapsed).toHaveAttribute("aria-expanded", "false");
});

test("ranks by play, not by when the campaign's metadata was last written", async () => {
  // `updated` says the renamed campaign is newest; `activity` says the played
  // one is. Ordering by `updated` would bury the campaign you were in last
  // night under one you renamed months ago.
  (api.listCampaigns as any).mockResolvedValue([
    { id: "renamed", name: "Renamed", world: "w", updated: "2026-06-01", activity: "2026-06-01", scenes: 0, last_scene: "" },
    { id: "played", name: "Played", world: "w", updated: "2026-01-01", activity: "2026-09-09", scenes: 12, last_scene: "Tide" },
  ]);
  renderNav();
  const recent = within(await screen.findByTestId("nav-recent"));
  await waitFor(() =>
    expect(recent.getAllByRole("link").map((r) => r.textContent)).toEqual(["Played", "Renamed"]));
});

test("falls back to updated when the server sends no activity stamp", async () => {
  // GET /campaigns/{cid} returns the bare meta, and an older backend has no
  // `activity` at all; neither may collapse the ordering to arbitrary.
  (api.listCampaigns as any).mockResolvedValue([
    { id: "older", name: "Older", world: "w", updated: "2025-01-01", scenes: 1, last_scene: "" },
    { id: "newer", name: "Newer", world: "w", updated: "2026-01-01", scenes: 1, last_scene: "" },
  ]);
  renderNav();
  const recent = within(await screen.findByTestId("nav-recent"));
  await waitFor(() =>
    expect(recent.getAllByRole("link").map((r) => r.textContent)).toEqual(["Newer", "Older"]));
});

test("the campaign being read tops Recent, even when its stamps say otherwise", async () => {
  // Playing advances `activity` server-side on every scene write, but the rail
  // does not refetch per post. The route already knows the answer: whatever
  // you are reading is the most recent thing.
  renderNav("/campaigns/oldest");
  const recent = within(await screen.findByTestId("nav-recent"));
  await waitFor(() =>
    expect(recent.getAllByRole("link")[0]).toHaveTextContent("Oldest"));
});

test("promoting the open campaign does not duplicate it or drop the limit", async () => {
  renderNav("/campaigns/mara");
  const recent = within(await screen.findByTestId("nav-recent"));
  await waitFor(() => expect(recent.getAllByRole("link")[0]).toHaveTextContent("Mara"));
  const names = recent.getAllByRole("link").map((r) => r.textContent);
  expect(names).toHaveLength(5);
  expect(names.filter((n) => n === "Mara")).toHaveLength(1);
  expect(names).toEqual(["Mara", "Saltmarch", "Realm", "Winifred", "Seraphine"]);
});

test("the new-campaign wizard is not mistaken for a campaign to promote", async () => {
  // "new" satisfies [^/]+ exactly like a real id would
  renderNav("/campaigns/new");
  const recent = within(await screen.findByTestId("nav-recent"));
  await waitFor(() =>
    expect(recent.getAllByRole("link")[0]).toHaveTextContent("Saltmarch"));
});

test("refreshes when a campaign is renamed on a route that never changes the path", async () => {
  // CampaignsView renames from "/" — the pathname never moves, so only the
  // mutation signal can save the rail from showing the old name.
  renderNav("/");
  await screen.findByRole("link", { name: "Saltmarch" });
  (api.listCampaigns as any).mockResolvedValue(
    [{ id: "saltmarch", name: "Saltmarch Reborn", world: "w", updated: "2026-03-04", activity: "2026-03-04", scenes: 4, last_scene: "Tide" }]);
  act(() => campaignsChanged());
  expect(await screen.findByRole("link", { name: "Saltmarch Reborn" })).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Saltmarch" })).not.toBeInTheDocument();
});

test("drops a deleted campaign rather than leaving a link to nothing", async () => {
  renderNav("/");
  await screen.findByRole("link", { name: "Realm" });
  (api.listCampaigns as any).mockResolvedValue(CAMPAIGNS.filter((c) => c.id !== "realm"));
  act(() => campaignsChanged());
  await waitFor(() =>
    expect(screen.queryByRole("link", { name: "Realm" })).not.toBeInTheDocument());
});

test("a mutation refetch demands a fresh read, not the GET already in flight", async () => {
  // The in-flight share would answer with a read issued before the mutation —
  // exactly the list the refetch exists to replace.
  renderNav("/");
  await screen.findByRole("link", { name: "Saltmarch" });
  (api.listCampaigns as any).mockClear();

  act(() => campaignsChanged());
  await waitFor(() => expect(api.listCampaigns).toHaveBeenCalledWith(true));
});

test("a navigation refetch shares the in-flight read, since it has no write to outrun", async () => {
  renderNav("/");
  await waitFor(() => expect(api.listCampaigns).toHaveBeenCalled());
  expect(api.listCampaigns).toHaveBeenCalledWith(false);
});

test("stops listening once unmounted, so a later mutation cannot set state on it", async () => {
  const { unmount } = renderNav("/");
  await screen.findByRole("link", { name: "Saltmarch" });
  unmount();
  expect(() => campaignsChanged()).not.toThrow();
});

test("refetches the recent list on navigation, so a campaign created elsewhere shows up", async () => {
  // navigating is the only signal the rail gets: creating, renaming and
  // deleting all happen on another route. Drop the [pathname] dependency and
  // this test fails — the click alone drives the refetch, nothing remounts.
  renderNav("/");
  await screen.findByRole("link", { name: "Saltmarch" });
  (api.listCampaigns as any).mockResolvedValue(
    [{ id: "fresh", name: "Fresh", world: "w", updated: "2026-09-09", scenes: 0, last_scene: "" }]);
  fireEvent.click(screen.getByRole("link", { name: "Library" }));
  expect(await screen.findByRole("link", { name: "Fresh" })).toBeInTheDocument();
});
