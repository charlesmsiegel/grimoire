import { render, screen, within, fireEvent, waitFor, act } from "@testing-library/react";
import { MemoryRouter, useNavigate, useParams } from "react-router-dom";
import { useEffect } from "react";
import App from "./App";
import { configChanged } from "./appEvents";

vi.mock("./api/client", () => ({
  api: {
    getConfig: vi.fn(),
    // The rail reads this on every navigation. A campaign of `null` is the
    // shape for "nothing open", which is what these tests are about.
    getShell: vi.fn().mockResolvedValue({ campaigns: 0, campaign: null, todo: null }),
    putConfig: vi.fn().mockResolvedValue({ theme: "system" }),
    listCampaigns: vi.fn().mockResolvedValue([]),
    listWorlds: vi.fn().mockResolvedValue([]),
    listModules: vi.fn().mockResolvedValue([]),
    listStyles: vi.fn().mockResolvedValue([]),
    listResponsePresets: vi.fn().mockResolvedValue([]),
    listClimates: vi.fn().mockResolvedValue({ climates: [] }),
    listConnections: vi.fn().mockResolvedValue([]),
    exportWorldUrl: (wid: string) => `/api/worlds/${wid}/export`,
  },
}));
import { api } from "./api/client";

/** jsdom reports 1024px, which is *below* the rail's breakpoint -- so by
 *  default the rail is a drawer and renders nothing until it is opened. Tests
 *  about the docked rail have to say so. Restored in `afterEach`, or a test
 *  that widened the window would change the answer for every test after it. */
const REAL_WIDTH = window.innerWidth;
function widthOf(px: number) {
  Object.defineProperty(window, "innerWidth", { value: px, configurable: true, writable: true });
}
afterEach(() => widthOf(REAL_WIDTH));

const campaignMounts: string[] = [];
vi.mock("./routes/CampaignView", () => ({
  // Named, and capitalized, so `react-hooks/rules-of-hooks` can see that the
  // hooks below sit inside a component. An anonymous arrow assigned to
  // `default` is a component to vitest and an ordinary function to eslint.
  default: function CampaignViewStub() {
    // Mount-only deps on purpose: this must record one entry per *mount*, so
    // a test can tell a remount (fresh state) from a re-render with a new
    // param (stale state kept). Keyed on [cid] it would fire either way and
    // the test below would pass without the fix.
    const { cid } = useParams();
    // eslint-disable-next-line react-hooks/exhaustive-deps
    useEffect(() => { campaignMounts.push(cid ?? ""); }, []);
    return (
      <div data-testid="campaign-view">
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
  default: function SetupWizardStub({ onDone }: any) {
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

/** A button that moves the router without going through the palette — the
 *  palette's campaign rows land on the hub, and the remount hazard below is
 *  about two PLAY urls that differ only in `:cid`. */
function Jump({ to }: { to: string }) {
  const navigate = useNavigate();
  return <button onClick={() => navigate(to)}>jump</button>;
}

const READY_OPENROUTER = {
  theme: "codex", system_prompt: "", quote_color: "off", user_label: "You", assistant_label: "Grimoire",
  active_connection_id: "openrouter",
  active_connection: { id: "openrouter", kind: "openrouter", name: "OpenRouter", model: "vendor/model-x" },
  ready: true, setup_done: "on", first_run: false, data_dir: "/home/u/.grimoire",
  health: { state: "ok", kind: "", detail: "", at: "2026-08-21T09:00:00Z" },
};

const column = () => within(screen.getByRole("complementary"));
const header = () => within(screen.getByRole("banner"));

/** Open ⌘K, type `query`, and take the first offer whose name matches. The
 *  palette is the app's only navigation surface, so every route-change test
 *  goes through it — which is also how they prove it really is one. */
async function goVia(query: string, name: RegExp) {
  fireEvent.keyDown(window, { key: "k", metaKey: true });
  const input = await screen.findByRole("combobox", { name: /search/i });
  fireEvent.change(input, { target: { value: query } });
  // All, not one: the palette's last offer is "Search for <query>", whose
  // label necessarily contains what was typed, so a name matcher aimed at a
  // record matches that row too. The record is first; the search row is the
  // fallback for when nothing named it.
  fireEvent.click((await screen.findAllByRole("option", { name }))[0]);
}

// CampaignsView's own heading, NOT `api.listCampaigns`. The palette lists
// campaigns from every route, so the call is no longer evidence that the
// campaigns *page* rendered -- and asserting on it would let these pass with
// the wizard on screen (or with nothing on screen at all).
const campaignsPage = () => screen.queryByRole("heading", { level: 1, name: "Campaigns" });

beforeEach(() => {
  localStorage.clear();
  // clearAllMocks forgets calls, not implementations, so every list a test
  // stocks has to be put back or it leaks into the next one — which is how a
  // library test that expects an empty shelf ends up rendering someone else's.
  vi.clearAllMocks();
  (api.getConfig as any).mockResolvedValue(READY_OPENROUTER);
  (api.listCampaigns as any).mockResolvedValue([]);
  (api.listWorlds as any).mockResolvedValue([]);
});

test("the header keeps the brand and the pill; Configuration moved to the rail", async () => {
  widthOf(1400);
  render(<MemoryRouter><App /></MemoryRouter>);
  expect(await screen.findByText(/GRIMOIRE/)).toBeInTheDocument();
  expect(header().getByRole("link", { name: /grimoire/i })).toHaveAttribute("href", "/");
  // The pill still names where you are and opens the palette that goes
  // anywhere; the rail lists the places worth a permanent row.
  expect(header().getByRole("button", { name: /go anywhere/i })).toBeInTheDocument();

  // CONFIG is no longer a header link -- it is a rail row, which is the first
  // half of "config not linking to connections is terrible": Configuration and
  // Connections are now reachable from the same surface.
  expect(header().queryByRole("link", { name: /^config$/i })).not.toBeInTheDocument();
  const rail = screen.getByRole("navigation", { name: /^main$/i });
  expect(within(rail).getByRole("link", { name: /configuration/i }))
    .toHaveAttribute("href", "/config");
});

test("a rail row whose page does not exist yet is absent, not disabled", async () => {
  widthOf(1400);
  // The rail ships complete in shape and sparse in fact: most campaign-tier
  // pages are later slices. A row with nowhere to go renders nothing at all,
  // so the rail never offers a destination that is not there.
  render(<MemoryRouter><App /></MemoryRouter>);
  await screen.findByText(/GRIMOIRE/);
  // Wrap-up is the row still waiting on its page; To do has one now.
  const camp = screen.queryByRole("navigation", { name: /open campaign/i });
  expect(camp === null || within(camp).queryByText(/^Wrap-up$/) === null).toBe(true);
});

test("with no campaign remembered the rail opens on the one the shell resolved", async () => {
  widthOf(1400);
  // A fresh browser -- a new device, cleared storage -- has no id to ask with,
  // and the rail used to stay one tier tall until the reader navigated into a
  // campaign. `GET /api/shell` answers an empty ask with the campaign last
  // played, and the chrome is about THAT campaign: not just its heading, but
  // the hrefs its rows are built from. A row whose `to()` is null is absent
  // from the DOM entirely, so a tier taking its name from the payload and its
  // links from the (empty) remembered id would draw a heading over nothing.
  (api.getShell as any).mockResolvedValue({
    campaigns: 1,
    campaign: {
      id: "last-played", name: "Tidewrack", world: "w1", world_name: "Saltmarch",
      scenes: 2, open: [], ledger_open: 0, sheets: null, unreviewed: null,
      pending: [], images_undescribed: null,
    },
    todo: null,
  });
  render(<MemoryRouter><App /></MemoryRouter>);
  await screen.findByText(/GRIMOIRE/);
  const camp = await screen.findByRole("navigation", { name: /open campaign/i });
  expect(within(camp).getByText("Tidewrack")).toBeInTheDocument();
  expect(within(camp).getByRole("link", { name: /^overview$/i }))
    .toHaveAttribute("href", "/campaigns/last-played");
  expect(within(camp).getByRole("link", { name: /^scenes/i }))
    .toHaveAttribute("href", "/campaigns/last-played/scenes");
});

test("the rail is not rendered beside either wizard", async () => {
  widthOf(1400);
  // `PlainShell` calls these one centred question at a time. On a first run the
  // rail would otherwise offer Campaigns, Library and Configuration before
  // setup has been answered at all.
  render(<MemoryRouter initialEntries={["/campaigns/new"]}><App /></MemoryRouter>);
  await screen.findByText(/GRIMOIRE/);
  expect(screen.queryByRole("navigation", { name: /^main$/i })).not.toBeInTheDocument();
});

test("the ⌘K pill and the keyboard shortcut open the same palette", async () => {
  render(<MemoryRouter><App /></MemoryRouter>);
  fireEvent.click(await screen.findByRole("button", { name: /go anywhere/i }));
  expect(await screen.findByRole("dialog", { name: /go anywhere/i })).toBeInTheDocument();

  fireEvent.keyDown(screen.getByRole("combobox", { name: /search/i }), { key: "Escape" });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

  fireEvent.keyDown(window, { key: "k", metaKey: true });
  expect(await screen.findByRole("dialog", { name: /go anywhere/i })).toBeInTheDocument();
});

test("the palette offers campaigns, worlds and library sections, and goes there", async () => {
  (api.listCampaigns as any).mockResolvedValue([
    { id: "saltmarch", name: "Saltmarch", world: "w", updated: "2026-03-03", scenes: 1, last_scene: "" },
  ]);
  (api.listWorlds as any).mockResolvedValue([{ id: "w", name: "Realm", counts: {} }]);
  render(<MemoryRouter initialEntries={["/"]}><App /></MemoryRouter>);
  await screen.findByText(/GRIMOIRE/);

  fireEvent.keyDown(window, { key: "k", metaKey: true });
  expect(await screen.findByRole("option", { name: /saltmarch/i })).toBeInTheDocument();
  expect(screen.getByRole("option", { name: /realm/i })).toBeInTheDocument();
  expect(screen.getByRole("option", { name: /climates/i })).toBeInTheDocument();

  fireEvent.click(screen.getByRole("option", { name: /climates/i }));
  expect(await screen.findByRole("heading", { name: /climates/i })).toBeInTheDocument();
  // and it closes behind itself — it is not persistent nav wearing a shortcut
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

test("↑↓ moves and ⏎ opens, without the mouse", async () => {
  render(<MemoryRouter initialEntries={["/"]}><App /></MemoryRouter>);
  await screen.findByText(/GRIMOIRE/);
  fireEvent.keyDown(window, { key: "k", metaKey: true });
  const input = await screen.findByRole("combobox", { name: /search/i });
  fireEvent.change(input, { target: { value: "climates" } });
  fireEvent.keyDown(input, { key: "Enter" });
  expect(await screen.findByRole("heading", { name: /climates/i })).toBeInTheDocument();
});

test("the library's card hub is gone — /library lands in a section", async () => {
  render(<MemoryRouter initialEntries={["/library"]}><App /></MemoryRouter>);
  expect(await screen.findByRole("heading", { name: /worlds/i })).toBeInTheDocument();
  // the six sections are the column now, with their counts
  expect(column().getByRole("link", { name: /worlds/i })).toHaveAttribute("href", "/worlds");
  expect(column().getByRole("link", { name: /climates/i })).toHaveAttribute("href", "/climates");
  expect(column().getByRole("link", { name: /connections/i })).toHaveAttribute("href", "/connections");
});

test("the library column persists across a section change, keeping the lit row", async () => {
  render(<MemoryRouter initialEntries={["/worlds"]}><App /></MemoryRouter>);
  await screen.findByRole("heading", { name: /worlds/i });

  fireEvent.click(column().getByRole("link", { name: /styles/i }));
  expect(await screen.findByRole("heading", { name: /style guides/i })).toBeInTheDocument();
  expect(column().getByRole("link", { name: /styles/i })).toHaveClass("active");
  expect(column().getByRole("link", { name: /worlds/i })).not.toHaveClass("active");
});

test("the header names the connection and the active model", async () => {
  render(<MemoryRouter><App /></MemoryRouter>);
  await waitFor(() => expect(header().getByText("VENDOR/MODEL-X")).toBeInTheDocument());
  expect(header().getByTitle(/openrouter, connected/i)).toBeInTheDocument();
});

test("the header reports a configured connection whose provider is failing", async () => {
  // #146: `ready` only ever meant "a key string is present", so a revoked key
  // drew the same green dot as a working one until the first scene failed.
  (api.getConfig as any).mockResolvedValue({
    ...READY_OPENROUTER,
    health: { state: "error", kind: "auth", detail: "No auth credentials found", at: "2026-08-21T09:05:00Z" },
  });
  render(<MemoryRouter><App /></MemoryRouter>);

  const dot = await waitFor(() => header().getByTitle(/no auth credentials found/i));
  expect(dot).toHaveClass("bad");
});

test("a connection nothing has exercised yet is not reported as broken", async () => {
  // The state every app start begins in. A warning shown to everyone every
  // morning is a warning nobody reads by lunchtime — the dot stays green and
  // the tooltip says which of the two greens this is.
  (api.getConfig as any).mockResolvedValue({
    ...READY_OPENROUTER, health: { state: "unknown", kind: "", detail: "", at: "" },
  });
  render(<MemoryRouter><App /></MemoryRouter>);

  const dot = await waitFor(() => header().getByTitle(/not checked yet/i));
  expect(dot).toHaveClass("ok");
});

test("the header reports a connection that cannot be used yet", async () => {
  (api.getConfig as any).mockResolvedValue({
    ...READY_OPENROUTER, ready: false,
    active_connection: { id: "zai-glm", kind: "openai_compatible", name: "z.ai GLM", model: "" },
  });
  render(<MemoryRouter><App /></MemoryRouter>);
  await waitFor(() => expect(header().getByTitle(/z\.ai glm, not ready/i)).toBeInTheDocument());
});

test("the header refetches and updates after navigating, without a reload", async () => {
  render(<MemoryRouter initialEntries={["/worlds"]}><App /></MemoryRouter>);
  await waitFor(() => expect(header().getByTitle(/openrouter, connected/i)).toBeInTheDocument());

  // simulate the active connection having changed elsewhere (Config/Connections
  // page) — the next getConfig() call reflects it
  (api.getConfig as any).mockResolvedValue({
    ...READY_OPENROUTER, ready: false, active_connection_id: "claude",
    active_connection: { id: "claude", kind: "claude", name: "Claude", model: "vendor/model-y" },
  });
  fireEvent.click(column().getByRole("link", { name: /styles/i }));
  await waitFor(() => expect(header().getByTitle(/claude, not ready/i)).toBeInTheDocument());
  expect(header().getByText("VENDOR/MODEL-Y")).toBeInTheDocument();
});

test("the header follows a connection change made without leaving the page", async () => {
  // /config switches the active connection and /connections edits its model,
  // neither of which moves the pathname — the exact workflow whose whole point
  // is to change what the header reports.
  render(<MemoryRouter initialEntries={["/"]}><App /></MemoryRouter>);
  await waitFor(() => expect(header().getByTitle(/openrouter, connected/i)).toBeInTheDocument());

  (api.getConfig as any).mockResolvedValue({
    ...READY_OPENROUTER, ready: false, active_connection_id: "claude",
    active_connection: { id: "claude", kind: "claude", name: "Claude", model: "opus" },
  });
  act(() => configChanged());
  await waitFor(() => expect(header().getByTitle(/claude, not ready/i)).toBeInTheDocument());
  expect(header().getByText("OPUS")).toBeInTheDocument();
});

test("the wizards are the two pages with no column", async () => {
  render(<MemoryRouter initialEntries={["/campaigns/new"]}><App /></MemoryRouter>);
  await screen.findByTestId("campaign-wizard");
  expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
  // the header is still there — the palette is the way out
  expect(screen.getByRole("banner")).toBeInTheDocument();
});

test("opening another campaign from the palette remounts the view instead of reusing it", async () => {
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
  // Driven on the PLAY route, which is where the hazard lives: `/campaigns/:cid`
  // is the hub now, and moving between two hubs unmounts the play view anyway.
  // Two play URLs differing only in `:cid` are the case React would happily
  // serve from one instance.
  render(
    <MemoryRouter initialEntries={["/campaigns/saltmarch/scenes/s1"]}>
      <Jump to="/campaigns/realm/scenes/s2" />
      <App />
    </MemoryRouter>);
  await screen.findByTestId("campaign-view");
  await waitFor(() => expect(campaignMounts).toEqual(["saltmarch"]));

  fireEvent.click(screen.getByText("jump"));
  await waitFor(() =>
    expect(screen.getByTestId("campaign-view-cid")).toHaveTextContent("realm"));
  // A reused component would record realm as a second entry from the SAME
  // instance; a remount is what makes the previous campaign's state
  // unreachable. The unmount/remount is observable as a fresh mount effect.
  expect(campaignMounts).toEqual(["saltmarch", "realm"]);
});

test("a slow pre-mutation config response cannot revert the header behind a newer one", async () => {
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
  await waitFor(() => expect(header().getByText("OPUS")).toBeInTheDocument());

  // the superseded read finally answers, with the connection from before
  await act(async () => {
    settleOld({
      ...READY_OPENROUTER, active_connection_id: "openrouter",
      active_connection: { id: "openrouter", kind: "openrouter", name: "OpenRouter", model: "vendor/model-x" },
    });
  });
  expect(header().getByText("OPUS")).toBeInTheDocument();
  expect(header().getByTitle(/claude/i)).toBeInTheDocument();
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
  // Any route change off "/" exercises the same effect.
  await goVia("worlds", /worlds/i);
  await waitFor(() => expect(api.getConfig).toHaveBeenCalledWith({ fresh: true }));
});

test("a world created outside the wizard retires the redirect on the next navigation", async () => {
  // The palette deliberately lets a fresh user escape. If they make a world in
  // WorldsView, `/` must stop bouncing them back into setup.
  (api.getConfig as any).mockResolvedValue(FIRST_RUN);
  render(<MemoryRouter initialEntries={["/worlds"]}><App /></MemoryRouter>);
  await screen.findByText(/GRIMOIRE/);

  (api.getConfig as any).mockResolvedValue(READY_OPENROUTER);   // a world now exists
  await goVia("campaigns", /campaigns/i);
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
  await goVia("worlds", /worlds/i);
  await waitFor(() => expect(api.getConfig).toHaveBeenCalledWith({ fresh: true }));
  await goVia("campaigns", /campaigns/i);
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

test("a scene URL renders one CampaignView, not a second nested one", async () => {
  // The scene segment is a CHILD route so the router keeps a single instance
  // across /campaigns/A/scenes/s1 → /campaigns/B; a sibling route would mount
  // a second view here.
  render(<MemoryRouter initialEntries={["/campaigns/run/scenes/s1"]}><App /></MemoryRouter>);
  await screen.findByTestId("campaign-view");
  expect(screen.getAllByTestId("campaign-view")).toHaveLength(1);
});

test("focus mode swaps the header for the pill that puts it back", async () => {
  render(<MemoryRouter><App /></MemoryRouter>);
  await screen.findByText(/GRIMOIRE/);

  fireEvent.click(header().getByRole("button", { name: /enter focus mode/i }));

  // The whole strip, not just its contents: the 52px it costs is the point.
  expect(screen.queryByRole("banner")).not.toBeInTheDocument();
  const restore = screen.getByRole("button", { name: /leave focus mode/i });
  expect(restore).toBeInTheDocument();

  fireEvent.click(restore);
  expect(screen.getByRole("banner")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /leave focus mode/i })).toBeNull();
});

test("focus mode is remembered across a reload, and is still leavable", async () => {
  const first = render(<MemoryRouter><App /></MemoryRouter>);
  await screen.findByText(/GRIMOIRE/);
  fireEvent.click(header().getByRole("button", { name: /enter focus mode/i }));
  first.unmount();

  render(<MemoryRouter><App /></MemoryRouter>);
  // No header to find, so wait on the page underneath instead.
  await waitFor(() => expect(campaignsPage()).toBeInTheDocument());
  expect(screen.queryByRole("banner")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /leave focus mode/i }));
  expect(screen.getByRole("banner")).toBeInTheDocument();
});

test("⌘K offers focus mode, and offers the way out once it is on", async () => {
  render(<MemoryRouter><App /></MemoryRouter>);
  await screen.findByText(/GRIMOIRE/);

  fireEvent.keyDown(window, { key: "k", metaKey: true });
  fireEvent.click(await screen.findByRole("option", { name: /focus mode/i }));
  expect(screen.queryByRole("banner")).not.toBeInTheDocument();

  // The row flips rather than repeating an offer for the state you are in.
  fireEvent.keyDown(window, { key: "k", metaKey: true });
  fireEvent.click(await screen.findByRole("option", { name: /leave focus mode/i }));
  expect(screen.getByRole("banner")).toBeInTheDocument();
});
