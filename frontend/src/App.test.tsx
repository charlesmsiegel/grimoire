import { render, screen, within, fireEvent, waitFor, act } from "@testing-library/react";
import { MemoryRouter, useNavigate, useParams } from "react-router-dom";
import { useEffect } from "react";
import App from "./App";
import { configChanged } from "./appEvents";

vi.mock("./api/client", () => ({
  api: {
    getConfig: vi.fn(),
    listCampaigns: vi.fn().mockResolvedValue([]),
    listWorlds: vi.fn().mockResolvedValue([]),
    listModules: vi.fn().mockResolvedValue([]),
    listStyles: vi.fn().mockResolvedValue([]),
    listResponsePresets: vi.fn().mockResolvedValue([]),
    listClimates: vi.fn().mockResolvedValue({ climates: [] }),
  },
}));
import { api } from "./api/client";

const campaignMounts: string[] = [];
vi.mock("./routes/CampaignView", () => ({
  default: ({ topbarCollapsed, onToggleTopbar }: any) => {
    // Mount-only deps on purpose: this must record one entry per *mount*, so
    // a test can tell a remount (fresh state) from a re-render with a new
    // param (stale state kept). Keyed on [cid] it would fire either way and
    // the test below would pass without the fix.
    const { cid } = useParams();
    // eslint-disable-next-line react-hooks/exhaustive-deps
    useEffect(() => { campaignMounts.push(cid ?? ""); }, []);
    return (
      <div data-testid="campaign-view">
        <button onClick={onToggleTopbar}>toggle-topbar</button>
        <span>{topbarCollapsed ? "collapsed" : "expanded"}</span>
        <span data-testid="campaign-view-cid">{cid}</span>
      </div>
    );
  },
}));

vi.mock("./routes/CampaignWizard", () => ({
  default: () => <div data-testid="campaign-wizard" />,
}));

// Stands in for the real wizard's exit: report completion, then leave for "/",
// which is exactly what SetupWizard.finish() does.
vi.mock("./routes/SetupWizard", () => ({
  default: ({ onDone }: any) => {
    const navigate = useNavigate();
    return (
      <div data-testid="setup-wizard">
        <button onClick={() => { onDone("/home/u/.grimoire"); navigate("/", { replace: true }); }}>
          finish-setup
        </button>
      </div>
    );
  },
}));

const READY_OPENROUTER = {
  theme: "codex", system_prompt: "", quote_color: "off", user_label: "You", assistant_label: "Grimoire",
  active_connection_id: "openrouter",
  active_connection: { id: "openrouter", kind: "openrouter", name: "OpenRouter", model: "vendor/model-x" },
  ready: true, setup_done: "on", first_run: false, data_dir: "/home/u/.grimoire",
};

const nav = () => within(screen.getByRole("navigation", { name: /primary/i }));
// CampaignsView's own heading, NOT `api.listCampaigns`. The sidebar's Recent
// rail lists campaigns on every route, so the call is no longer evidence that
// the campaigns *page* rendered -- and asserting on it would let these pass
// with the wizard on screen (or with nothing on screen at all).
const campaignsPage = () => screen.queryByRole("heading", { level: 1, name: "Campaigns" });

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
  (api.getConfig as any).mockResolvedValue(READY_OPENROUTER);
});

test("renders the brand and the Config link in the slim top strip", async () => {
  render(<MemoryRouter><App /></MemoryRouter>);
  expect(await screen.findByText(/GRIMOIRE/)).toBeInTheDocument();
  const topbar = within(screen.getByRole("banner"));
  expect(topbar.getByRole("link", { name: /config/i })).toBeInTheDocument();
});

test("the persistent sidebar carries the navigation, not the top strip", async () => {
  render(<MemoryRouter><App /></MemoryRouter>);
  await screen.findByRole("navigation", { name: /primary/i });
  expect(nav().getByRole("link", { name: "Campaigns" })).toHaveAttribute("href", "/");
  expect(nav().getByRole("link", { name: "Library" })).toHaveAttribute("href", "/library");
  expect(nav().getByRole("link", { name: "Connections" })).toHaveAttribute("href", "/connections");
  // the links really left the top strip rather than being duplicated
  const topbar = within(screen.getByRole("banner"));
  expect(topbar.queryByRole("link", { name: "Library" })).not.toBeInTheDocument();
});

test("the sidebar persists across a route change, keeping its recent campaigns", async () => {
  (api.listCampaigns as any).mockResolvedValue([
    { id: "saltmarch", name: "Saltmarch", world: "w", updated: "2026-03-03", scenes: 1, last_scene: "" },
  ]);
  render(<MemoryRouter initialEntries={["/"]}><App /></MemoryRouter>);
  await screen.findByRole("link", { name: "Saltmarch" });

  fireEvent.click(nav().getByRole("link", { name: "Library" }));
  expect(await screen.findByRole("heading", { name: /library/i })).toBeInTheDocument();
  expect(nav().getByRole("link", { name: "Saltmarch" })).toBeInTheDocument();
});

test("the Library hub is reachable and lists the libraries it gathers", async () => {
  render(<MemoryRouter initialEntries={["/library"]}><App /></MemoryRouter>);
  expect(await screen.findByRole("heading", { name: /library/i })).toBeInTheDocument();
  const main = within(screen.getByRole("main"));
  expect(main.getByRole("link", { name: /worlds/i })).toHaveAttribute("href", "/worlds");
  expect(main.getByRole("link", { name: /climates/i })).toHaveAttribute("href", "/climates");
});

test("the global status bar names the connection and the active model", async () => {
  render(<MemoryRouter><App /></MemoryRouter>);
  await waitFor(() =>
    expect(screen.getByTestId("status-connection")).toHaveTextContent(/OPENROUTER · CONNECTED/i));
  expect(screen.getByTestId("status-model")).toHaveTextContent("vendor/model-x");
});

test("the status bar shows NOT READY and the connection's name when unready", async () => {
  (api.getConfig as any).mockResolvedValue({
    ...READY_OPENROUTER, ready: false,
    active_connection: { id: "zai-glm", kind: "openai_compatible", name: "z.ai GLM", model: "" },
  });
  render(<MemoryRouter><App /></MemoryRouter>);
  await waitFor(() =>
    expect(screen.getByTestId("status-connection")).toHaveTextContent(/z\.ai glm · not ready/i));
  expect(screen.getByTestId("status-model")).toHaveTextContent("—");
});

test("the status bar refetches and updates after navigating, without a reload", async () => {
  render(<MemoryRouter initialEntries={["/"]}><App /></MemoryRouter>);
  await waitFor(() =>
    expect(screen.getByTestId("status-connection")).toHaveTextContent(/openrouter · connected/i));

  // simulate the active connection having changed elsewhere (Config/Connections
  // page) — the next getConfig() call reflects it
  (api.getConfig as any).mockResolvedValue({
    ...READY_OPENROUTER, ready: false, active_connection_id: "claude",
    active_connection: { id: "claude", kind: "claude", name: "Claude", model: "vendor/model-y" },
  });
  fireEvent.click(nav().getByRole("link", { name: "Library" }));
  await waitFor(() =>
    expect(screen.getByTestId("status-connection")).toHaveTextContent(/claude · not ready/i));
  expect(screen.getByTestId("status-model")).toHaveTextContent("vendor/model-y");
});

test("the status bar follows a connection change made without leaving the page", async () => {
  // /config switches the active connection and /connections edits its model,
  // neither of which moves the pathname — the exact workflow whose whole point
  // is to change what the bar reports.
  render(<MemoryRouter initialEntries={["/"]}><App /></MemoryRouter>);
  await waitFor(() =>
    expect(screen.getByTestId("status-connection")).toHaveTextContent(/openrouter · connected/i));

  (api.getConfig as any).mockResolvedValue({
    ...READY_OPENROUTER, ready: false, active_connection_id: "claude",
    active_connection: { id: "claude", kind: "claude", name: "Claude", model: "opus" },
  });
  act(() => configChanged());
  await waitFor(() =>
    expect(screen.getByTestId("status-connection")).toHaveTextContent(/claude · not ready/i));
  expect(screen.getByTestId("status-model")).toHaveTextContent("opus");
});

test("the status bar's reserved slots stay dashed — nothing feeds them yet", async () => {
  render(<MemoryRouter><App /></MemoryRouter>);
  await screen.findByTestId("status-tokens");
  for (const slot of ["tokens", "queue", "drift"]) {
    expect(screen.getByTestId(`status-${slot}`)).toHaveTextContent("—");
  }
});

test("the sidebar starts as a rail on a campaign page and full-width elsewhere", async () => {
  const { unmount } = render(<MemoryRouter initialEntries={["/campaigns/run"]}><App /></MemoryRouter>);
  await screen.findByTestId("campaign-view");
  expect(screen.getByRole("navigation", { name: /primary/i })).toHaveClass("rail");
  unmount();

  render(<MemoryRouter initialEntries={["/worlds"]}><App /></MemoryRouter>);
  await screen.findByRole("navigation", { name: /primary/i });
  expect(screen.getByRole("navigation", { name: /primary/i })).not.toHaveClass("rail");
});

test("once the rail is toggled the choice sticks, and outlives a reload", async () => {
  const { unmount } = render(<MemoryRouter initialEntries={["/worlds"]}><App /></MemoryRouter>);
  fireEvent.click(await screen.findByRole("button", { name: /collapse navigation/i }));
  expect(screen.getByRole("navigation", { name: /primary/i })).toHaveClass("rail");
  unmount();

  // a stored preference beats the per-route default, on every route
  render(<MemoryRouter initialEntries={["/library"]}><App /></MemoryRouter>);
  await screen.findByRole("navigation", { name: /primary/i });
  expect(screen.getByRole("navigation", { name: /primary/i })).toHaveClass("rail");
});

test("expanding the rail on a campaign page overrides that page's default", async () => {
  render(<MemoryRouter initialEntries={["/campaigns/run"]}><App /></MemoryRouter>);
  fireEvent.click(await screen.findByRole("button", { name: /expand navigation/i }));
  expect(screen.getByRole("navigation", { name: /primary/i })).not.toHaveClass("rail");
  expect(localStorage.getItem("grimoire.nav.rail")).toBe("0");
});

test("CampaignView's Nav toggle hides the sidebar and the top strip together", async () => {
  render(<MemoryRouter initialEntries={["/campaigns/run"]}><App /></MemoryRouter>);
  const view = await screen.findByTestId("campaign-view");
  expect(within(view).getByText("expanded")).toBeInTheDocument();
  expect(screen.getByRole("banner")).not.toHaveClass("collapsed");
  expect(screen.getByRole("navigation", { name: /primary/i })).toBeInTheDocument();

  fireEvent.click(within(view).getByText("toggle-topbar"));
  expect(within(view).getByText("collapsed")).toBeInTheDocument();
  expect(screen.getByRole("banner")).toHaveClass("collapsed");
  expect(screen.queryByRole("navigation", { name: /primary/i })).not.toBeInTheDocument();
  // the status bar is not "nav" — it stays put
  expect(screen.getByTestId("status-connection")).toBeInTheDocument();
});

test("navigating resets the scroll port, so the destination opens at its top", async () => {
  // .shell-main never unmounts — only the routed child swaps — so without an
  // explicit reset the next page inherits the last one's offset and opens with
  // its heading scrolled off.
  render(<MemoryRouter initialEntries={["/"]}><App /></MemoryRouter>);
  await screen.findByRole("navigation", { name: /primary/i });
  const main = screen.getByRole("main");
  const scrollTo = vi.fn();
  main.scrollTo = scrollTo as any;

  fireEvent.click(nav().getByRole("link", { name: "Library" }));
  await screen.findByRole("heading", { name: /library/i });
  expect(scrollTo).toHaveBeenCalledWith(0, 0);
});

test("a previously-hidden nav does not stay hidden on non-campaign routes", async () => {
  localStorage.setItem("grimoire.topbar.collapsed", "1");
  render(<MemoryRouter initialEntries={["/worlds"]}><App /></MemoryRouter>);
  await screen.findByText(/GRIMOIRE/);
  expect(screen.getByRole("banner")).not.toHaveClass("collapsed");
  expect(screen.getByRole("navigation", { name: /primary/i })).toBeInTheDocument();
});

test("the shell stays fully visible on /campaigns/new even with a stored hidden preference", async () => {
  localStorage.setItem("grimoire.topbar.collapsed", "1");
  render(<MemoryRouter initialEntries={["/campaigns/new"]}><App /></MemoryRouter>);
  await screen.findByTestId("campaign-wizard");
  expect(screen.getByRole("banner")).not.toHaveClass("collapsed");
  expect(screen.getByRole("navigation", { name: /primary/i })).toBeInTheDocument();
});

test("clicking another Recent campaign remounts the view instead of reusing it", async () => {
  // Same route, different param: React reuses CampaignView, whose [cid] effect
  // refetches without synchronously dropping the old campaign's scenes,
  // transcript or activeId. Until those land — or forever, if the new list is
  // empty or the request fails — the page shows campaign A's scene while its
  // handlers carry B's cid, and scene ids repeat across campaigns.
  (api.listCampaigns as any).mockResolvedValue([
    { id: "saltmarch", name: "Saltmarch", world: "w", updated: "2026-03-03", activity: "2026-03-03", scenes: 1, last_scene: "" },
    { id: "realm", name: "Realm", world: "w", updated: "2026-02-02", activity: "2026-02-02", scenes: 1, last_scene: "" },
  ]);
  campaignMounts.length = 0;
  render(<MemoryRouter initialEntries={["/campaigns/saltmarch"]}><App /></MemoryRouter>);
  await screen.findByTestId("campaign-view");
  await waitFor(() => expect(campaignMounts).toEqual(["saltmarch"]));

  fireEvent.click(await screen.findByRole("link", { name: "Realm" }));
  await waitFor(() =>
    expect(screen.getByTestId("campaign-view-cid")).toHaveTextContent("realm"));
  // A reused component would record realm as a second entry from the SAME
  // instance; a remount is what makes the previous campaign's state
  // unreachable. The unmount/remount is observable as a fresh mount effect.
  expect(campaignMounts).toEqual(["saltmarch", "realm"]);
});

test("a slow pre-mutation config response cannot revert the bar behind a newer one", async () => {
  // Two reads in flight at once — a second connection edit during the first
  // read, or a store move — with nothing ordering the responses. Retiring the
  // client's in-flight entry stops a new caller *joining* the old promise; it
  // cannot detach the .then already on it.
  let settleOld: (v: unknown) => void = () => {};
  (api.getConfig as any)
    // call 1 is App's theme read, which must resolve or nothing renders;
    // call 2 is this effect's first run, left hanging deliberately.
    .mockResolvedValueOnce(READY_OPENROUTER)
    .mockReturnValueOnce(new Promise((r) => { settleOld = r; }))
    .mockResolvedValue({
      ...READY_OPENROUTER, active_connection_id: "claude",
      active_connection: { id: "claude", kind: "claude", name: "Claude", model: "opus" },
    });
  render(<MemoryRouter initialEntries={["/"]}><App /></MemoryRouter>);

  act(() => configChanged());
  await waitFor(() =>
    expect(screen.getByTestId("status-model")).toHaveTextContent("opus"));

  // the superseded read finally answers, with the connection from before
  await act(async () => {
    settleOld({
      ...READY_OPENROUTER, active_connection_id: "openrouter",
      active_connection: { id: "openrouter", kind: "openrouter", name: "OpenRouter", model: "vendor/model-x" },
    });
  });
  expect(screen.getByTestId("status-model")).toHaveTextContent("opus");
  expect(screen.getByTestId("status-connection")).toHaveTextContent(/claude/i);
});

test("a phone-width viewport opens railed, so the main column is usable", async () => {
  // The Android shell hosts this frontend full-screen; at 360px the expanded
  // 208px sidebar leaves the page ~150px, most of which its own padding eats.
  const orig = window.innerWidth;
  Object.defineProperty(window, "innerWidth", { value: 360, configurable: true, writable: true });
  try {
    render(<MemoryRouter initialEntries={["/worlds"]}><App /></MemoryRouter>);
    await screen.findByRole("navigation", { name: /primary/i });
    expect(screen.getByRole("navigation", { name: /primary/i })).toHaveClass("rail");
  } finally {
    Object.defineProperty(window, "innerWidth", { value: orig, configurable: true, writable: true });
  }
});

test("a stated preference still wins on a phone — the toggle is not overridden", async () => {
  // Defaulting narrow to the rail must not mean the control stops working.
  localStorage.setItem("grimoire.nav.rail", "0");
  const orig = window.innerWidth;
  Object.defineProperty(window, "innerWidth", { value: 360, configurable: true, writable: true });
  try {
    render(<MemoryRouter initialEntries={["/worlds"]}><App /></MemoryRouter>);
    await screen.findByRole("navigation", { name: /primary/i });
    expect(screen.getByRole("navigation", { name: /primary/i })).not.toHaveClass("rail");
  } finally {
    Object.defineProperty(window, "innerWidth", { value: orig, configurable: true, writable: true });
  }
});

test("rotating a phone into a wide viewport relaxes the rail default", async () => {
  const orig = window.innerWidth;
  Object.defineProperty(window, "innerWidth", { value: 360, configurable: true, writable: true });
  try {
    render(<MemoryRouter initialEntries={["/worlds"]}><App /></MemoryRouter>);
    await screen.findByRole("navigation", { name: /primary/i });
    expect(screen.getByRole("navigation", { name: /primary/i })).toHaveClass("rail");

    Object.defineProperty(window, "innerWidth", { value: 1200, configurable: true, writable: true });
    act(() => { window.dispatchEvent(new Event("resize")); });
    expect(screen.getByRole("navigation", { name: /primary/i })).not.toHaveClass("rail");
  } finally {
    Object.defineProperty(window, "innerWidth", { value: orig, configurable: true, writable: true });
  }
});

// ---- first-run setup wizard (#194) ----
const FIRST_RUN = { ...READY_OPENROUTER, ready: false, active_connection: null, setup_done: "off", first_run: true };

test("a first run sends / to the setup wizard instead of the campaigns list", async () => {
  (api.getConfig as any).mockResolvedValue(FIRST_RUN);
  render(<MemoryRouter initialEntries={["/"]}><App /></MemoryRouter>);
  expect(await screen.findByTestId("setup-wizard")).toBeInTheDocument();
  expect(campaignsPage()).not.toBeInTheDocument();
});

test("an install past setup lands on the campaigns list", async () => {
  render(<MemoryRouter initialEntries={["/"]}><App /></MemoryRouter>);
  await screen.findByText(/GRIMOIRE/);
  await waitFor(() => expect(campaignsPage()).toBeInTheDocument());
  expect(screen.queryByTestId("setup-wizard")).not.toBeInTheDocument();
});

test("a first run does not hijack a route other than /", async () => {
  (api.getConfig as any).mockResolvedValue(FIRST_RUN);
  render(<MemoryRouter initialEntries={["/worlds"]}><App /></MemoryRouter>);
  await screen.findByText(/GRIMOIRE/);
  expect(screen.queryByTestId("setup-wizard")).not.toBeInTheDocument();
});

test("the per-navigation config read bypasses the cache", async () => {
  // The cache is only invalidated by this tab's own writes, so a library
  // populated in another tab or by a sync client would leave the verdict — and
  // the connection status beside it — stale for the life of the process.
  render(<MemoryRouter initialEntries={["/"]}><App /></MemoryRouter>);
  await screen.findByText(/GRIMOIRE/);
  // Library rather than Worlds: the sidebar's top level is Campaigns /
  // Library / Connections, and the library's own sections appear under it.
  // Any route change off "/" exercises the same effect.
  fireEvent.click(nav().getByRole("link", { name: /library/i }));
  await waitFor(() => expect(api.getConfig).toHaveBeenCalledWith({ fresh: true }));
});

test("a world created outside the wizard retires the redirect on the next navigation", async () => {
  // The sidebar deliberately lets a fresh user escape. If they make a world in
  // WorldsView, `/` must stop bouncing them back into setup.
  (api.getConfig as any).mockResolvedValue(FIRST_RUN);
  render(<MemoryRouter initialEntries={["/worlds"]}><App /></MemoryRouter>);
  await screen.findByText(/GRIMOIRE/);

  (api.getConfig as any).mockResolvedValue(READY_OPENROUTER);   // a world now exists
  fireEvent.click(nav().getByRole("link", { name: /campaigns/i }));
  await waitFor(() => expect(campaignsPage()).toBeInTheDocument());
  expect(screen.queryByTestId("setup-wizard")).not.toBeInTheDocument();
});

test("a different store pointed at later gets its own first run", async () => {
  // The exit latch is scoped to the library it was set in. Unscoped, finishing
  // setup once would suppress the wizard for every store this session ever
  // opens — including a brand-new empty one chosen from Config.
  (api.getConfig as any).mockResolvedValue(FIRST_RUN);
  render(<MemoryRouter initialEntries={["/"]}><App /></MemoryRouter>);
  const wizard = await screen.findByTestId("setup-wizard");
  fireEvent.click(within(wizard).getByText("finish-setup"));
  await waitFor(() => expect(campaignsPage()).toBeInTheDocument());

  // Config repoints storage at a different, empty library
  (api.getConfig as any).mockResolvedValue({ ...FIRST_RUN, data_dir: "/sync/other" });
  fireEvent.click(nav().getByRole("link", { name: /library/i }));
  await waitFor(() => expect(api.getConfig).toHaveBeenCalledWith({ fresh: true }));
  fireEvent.click(nav().getByRole("link", { name: /campaigns/i }));
  expect(await screen.findByTestId("setup-wizard")).toBeInTheDocument();
});

test("/welcome bounces to the campaigns list once the store is no longer a first run", async () => {
  // A reload part-way through the wizard: the world it created means the
  // server no longer calls this a first run, so restarting at step one (and
  // creating a second world) must not be possible.
  render(<MemoryRouter initialEntries={["/welcome"]}><App /></MemoryRouter>);
  await screen.findByText(/GRIMOIRE/);
  expect(screen.queryByTestId("setup-wizard")).not.toBeInTheDocument();
  await waitFor(() => expect(campaignsPage()).toBeInTheDocument());
});

test("/welcome still renders the wizard for a genuine first run", async () => {
  (api.getConfig as any).mockResolvedValue(FIRST_RUN);
  render(<MemoryRouter initialEntries={["/welcome"]}><App /></MemoryRouter>);
  expect(await screen.findByTestId("setup-wizard")).toBeInTheDocument();
});

test("leaving the wizard sticks even when the server still answers first_run", async () => {
  // finish() writes setup_done best-effort so a failure can't strand anyone —
  // which means the live verdict can still say first_run afterwards. The
  // session latch is what makes one exit enough instead of a loop.
  (api.getConfig as any).mockResolvedValue(FIRST_RUN);
  render(<MemoryRouter initialEntries={["/"]}><App /></MemoryRouter>);
  const wizard = await screen.findByTestId("setup-wizard");

  fireEvent.click(within(wizard).getByText("finish-setup"));
  await waitFor(() => expect(api.listCampaigns).toHaveBeenCalled());
  expect(screen.queryByTestId("setup-wizard")).not.toBeInTheDocument();
});

test("a scene URL is still the campaign route: the topbar collapses there too", async () => {
  // The play view redirects itself to /campaigns/:cid/scenes/:sid (#87), so a
  // pattern that only matched the bare campaign path would hand the topbar
  // back the moment the reader landed on a scene.
  localStorage.setItem("grimoire.topbar.collapsed", "1");
  render(<MemoryRouter initialEntries={["/campaigns/run/scenes/003--the-gate"]}><App /></MemoryRouter>);
  await screen.findByTestId("campaign-view");
  expect(screen.getByRole("banner")).toHaveClass("collapsed");
});

test("a scene URL renders one CampaignView, not a second nested one", async () => {
  // The scene segment is a CHILD route so the router keeps a single instance
  // across /campaigns/A/scenes/s1 → /campaigns/B; a sibling route would mount
  // a second view here.
  render(<MemoryRouter initialEntries={["/campaigns/run/scenes/s1"]}><App /></MemoryRouter>);
  await screen.findByTestId("campaign-view");
  expect(screen.getAllByTestId("campaign-view")).toHaveLength(1);
});
