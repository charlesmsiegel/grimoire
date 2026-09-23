import { useEffect } from "react";
import { render, screen, fireEvent, waitFor, within, cleanup, act } from "@testing-library/react";
import {
  MemoryRouter, Routes, Route, useLocation, useNavigate, Outlet,
  createMemoryRouter, RouterProvider,
} from "react-router-dom";
import WorldView from "./WorldView";
import { resetPrefetch } from "../api/prefetch";
import { ShellStatusProvider, useShellStatus } from "../components/ShellStatus";
import { PaletteProvider, usePalette, type PaletteItem } from "../components/palette";

// `createMemoryRouter`'s data-router navigation builds a `Request` per
// navigation, signal included. jsdom's `AbortController`/`AbortSignal` are a
// different class than the one Node's global `Request` validates a signal
// against, so that construction throws in this test environment alone -- the
// built app runs in a real browser, where there is no such split, and every
// other test in this file uses the plain (non-data) `MemoryRouter`, which
// never builds a `Request` at all. Patched once, file-wide, for the one test
// that needs a data router (to drive `router.navigate(-1)` for Back): build
// the `Request` without the offending `signal`, then reattach the original
// object afterwards, since nothing downstream reads it before the native
// constructor's typecheck would otherwise fire.
const RealRequest = globalThis.Request;
class TestRequest extends RealRequest {
  constructor(input: RequestInfo | URL, init?: RequestInit) {
    const { signal, ...rest } = init ?? {};
    super(input, rest);
    if (signal) Object.defineProperty(this, "signal", { value: signal, configurable: true });
  }
}
globalThis.Request = TestRequest;

vi.mock("../api/client", () => ({
  SECRECY_LEVELS: ["public", "secret", "gm-only"],
  SECRECY_LABELS: { public: "Public", secret: "Secret", "gm-only": "GM-only" },
  ENTITY_KINDS: ["locations", "lore", "items", "groups", "creatures"],
  ENTITY_FIELDS: {
    locations: [], lore: [],
    items: [{ key: "item_type", label: "Type", widget: "text" },
            { key: "rarity", label: "Rarity", widget: "text" }],
    groups: [{ key: "group_type", label: "Type", widget: "text" },
             { key: "leader", label: "Leader", widget: "ref", kinds: ["characters", "pcs"] }],
    creatures: [{ key: "creature_type", label: "Type", widget: "text" },
                { key: "threat", label: "Threat", widget: "text" }],
  },
  api: {
    getWorld: vi.fn(),
    worldCoverUrl: vi.fn(),
    getCampaign: vi.fn(),
    listCampaigns: vi.fn(),
    listCharacters: vi.fn(),
    // Reached through the editors' campaign-scope LibraryPanel (#52, #53);
    // "library content, unedited" renders no button, so this view is unchanged.
    libraryStatus: vi.fn().mockResolvedValue(
      { in_library: true, diverged: false, can_promote: false, can_push: false }),
    promoteToLibrary: vi.fn(),
    // World scope reaches DemotePanel; campaign scope reaches LibraryPanel.
    // Both resolve to "nothing to do", so these views render as they always did.
    libraryDependents: vi.fn().mockResolvedValue([]),
    demoteFromLibrary: vi.fn(),
    pushToLibrary: vi.fn(),
    listUndescribedImages: vi.fn(), countUndescribedImages: vi.fn(),
    rememberedWorld: vi.fn(), rememberedCharacters: vi.fn(), rememberedAppearances: vi.fn(),
    listPCs: vi.fn(), readPC: vi.fn(),
    listPCImages: vi.fn(), getCalendarMonths: vi.fn(),
    listTags: vi.fn(),
    listEntities: vi.fn(),
    readEntity: vi.fn(),
    reclassifyEntity: vi.fn(),
    listEntityImages: vi.fn(),
    listGreetings: vi.fn(),
    readCharacter: vi.fn(),
    getCharacterTagline: vi.fn(), getCharacterVoiceAnchor: vi.fn(),
    listImageAppearances: vi.fn(),
    readGreeting: vi.fn(),
    getGreetingSubjects: vi.fn(),
    listUntaggedImages: vi.fn(),
    imageUrl: (w: string, c: string, v: string, n: string) => `/img/${w}/${c}/${v}/${n}`,
    exportUrl: (w: string, c: string, v: string, f: string) => `/export/${w}/${c}/${v}/${f}`,
    actorImageUrl: (sc: { id: string }, k: string, a: string, v: string, n: string) =>
      `/img/${sc.id}/${k}/${a}/${v}/${n}`,
    listAppearances: vi.fn(), markGreeting: vi.fn(), pickVersion: vi.fn(), importVersion: vi.fn(),
    listModules: vi.fn(), setWorldModule: vi.fn(),
    getCalendarConfig: vi.fn(), setCalendarConfig: vi.fn(), getCalendarProviders: vi.fn(),
    getCampaignModule: vi.fn(), readModule: vi.fn(), getWorldSheetsIndex: vi.fn(), getSheet: vi.fn(),
    worldCampaigns: vi.fn(), listWorldImages: vi.fn(),
  },
}));
import { api } from "../api/client";
import type { ModuleDetail } from "../api/client";

/** Reads back what the page under test offered the palette.
 *
 *  A source is registered rather than returned, so the only way to see one
 *  page's items is to share its provider and read the registry — which is
 *  exactly what `CommandPalette` does. `rev` is the registry's own "something
 *  registered" signal, so this re-reads when the page's source lands. */
function PaletteSpy({ onItems }: { onItems: (items: PaletteItem[]) => void }) {
  const { sources, rev } = usePalette();
  useEffect(() => { onItems([...sources].flatMap((s) => s(""))); }, [sources, rev, onItems]);
  return null;
}

const POOL_BASIC: ModuleDetail = {
  id: "pool-basic",
  source: "builtin",
  manifest: { id: "pool-basic", name: "Pool Basic" },
  sheets: {
    groups: {},
    sheet_types: { medium: { label: "Medium", kind: "characters", groups: [], fields: [] } },
  },
  checks: {},
  rules: [],
  content: [],
  errors: [],
};

beforeEach(() => {
  vi.clearAllMocks();
  resetPrefetch();
  // The world read carries how many campaigns play the world, which is the
  // only place the page reads that number from.
  (api.getWorld as any).mockResolvedValue(
    { meta: { id: "w", name: "Drowned Realm" }, body: "", counts: {}, campaigns: 2 });
  // A first visit, unless a test remembers something.
  (api.rememberedWorld as any).mockReturnValue(undefined);
  (api.rememberedCharacters as any).mockReturnValue(undefined);
  (api.rememberedAppearances as any).mockReturnValue(undefined);
  (api.countUndescribedImages as any).mockResolvedValue(0);
  (api.getCampaign as any).mockResolvedValue({ meta: { id: "c1", name: "Ashes of the Verdigris Crown", world: "w" } });
  (api.listCampaigns as any).mockResolvedValue([
    { id: "c1", name: "Ashes of the Verdigris Crown", world: "w" },
    { id: "c2", name: "The Saltmarch Winter", world: "w" },
    { id: "c3", name: "Elsewhere", world: "other" },
  ]);
  (api.listCharacters as any).mockResolvedValue([]);
  (api.listUndescribedImages as any).mockResolvedValue([]);
  // campaign scope reads the roster to drive the Characters grid's appeared filter
  (api.listAppearances as any).mockResolvedValue([]);
  (api.listPCs as any).mockResolvedValue([]);
  (api.listTags as any).mockResolvedValue({});
  (api.listEntities as any).mockResolvedValue([]);
  (api.readEntity as any).mockResolvedValue({
    meta: { id: "the-salt-pact", name: "The Salt Pact", keys: "", owners: "" },
    body: "Debts written in salt.",
  });
  (api.listEntityImages as any).mockResolvedValue([]);
  (api.reclassifyEntity as any).mockResolvedValue({ id: "the-salt-pact", campaigns: [] });
  (api.listGreetings as any).mockResolvedValue([]);
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "mira", name: "Mira", default_version: "main" },
    versions: [{ id: "main", name: "main", images: [],
                 card: { spec: "chara_card_v3", spec_version: "3.0",
                         data: { name: "Mira", description: "", alternate_greetings: [], extensions: {} } } }],
  });
  (api.getCharacterTagline as any).mockResolvedValue({ tagline: "" });
  (api.getCharacterVoiceAnchor as any).mockResolvedValue({ voice_anchor: "" });
  (api.listImageAppearances as any).mockResolvedValue([]);
  (api.readGreeting as any).mockResolvedValue({
    meta: { id: "sol-2", name: "SoL 2", character: "other", version: "main", present: ["mira"], requires_tags: [], predecessor_join: "all" },
    body: "hi", edges: { leads_to: [], excludes: [] }, predecessors: [],
  });
  (api.getGreetingSubjects as any).mockResolvedValue({});
  (api.listUntaggedImages as any).mockResolvedValue([]);
  (api.listWorldImages as any).mockResolvedValue([]);
  (api.getCalendarConfig as any).mockResolvedValue({
    primary: { provider: "gregorian", region: "US", custom_holidays: [], anchor: null },
    secondary: null, confirmed: false, stale_after_days: 30 });
  (api.setCalendarConfig as any).mockResolvedValue({ ok: true });
  (api.getCalendarProviders as any).mockResolvedValue({ providers: [
    { id: "gregorian", name: "Gregorian" }, { id: "hebrew", name: "Hebrew" }] });
  (api.listModules as any).mockResolvedValue([]);
  (api.setWorldModule as any).mockResolvedValue({ ok: true });
  (api.getCampaignModule as any).mockResolvedValue({ setting: "", resolved: null, source: null });
  (api.readModule as any).mockResolvedValue(POOL_BASIC);
  (api.getWorldSheetsIndex as any).mockResolvedValue({ modules: [], default: "" });
  (api.getSheet as any).mockResolvedValue({ sheet: null });
  (api.worldCampaigns as any).mockResolvedValue([
    { id: "c1", name: "Ashes of the Verdigris Crown", pending: { new: 1, update: 0, conflict: 2 } },
  ]);
});

/** Where the router ended up. A character is a page of its own now, so several
 *  of this page's records LEAVE it, and "did the click go to the right place"
 *  is the assertion those tests can still make here — what happens on arrival
 *  belongs to `CharacterPage.test.tsx`. Pathname and search are tracked
 *  separately: a redirect can carry a query string through unchanged, and a
 *  test asserting on the combined string could not tell that apart from one
 *  that dropped it. */
let lastPath = "";
let lastSearch = "";
function PathSpy() {
  const loc = useLocation();
  lastPath = loc.pathname;
  lastSearch = loc.search;
  return null;
}

function renderAt() {
  lastPath = ""; lastSearch = "";
  render(
    <MemoryRouter initialEntries={["/worlds/w"]}>
      <PathSpy />
      <Routes>
        <Route path="/worlds/:wid/*" element={<WorldView />} />
        <Route path="*" element={<div>away</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

/** Navigates the surrounding router without remounting the page under test —
 *  `initialEntries` is read only on mount, so a scope change has to come from
 *  inside. */
function GoTo({ to }: { to: string }) {
  const navigate = useNavigate();
  return <button onClick={() => navigate(to)}>go</button>;
}

function renderAtUrl(url: string) {
  lastPath = ""; lastSearch = "";
  render(
    <MemoryRouter initialEntries={[url]}>
      <PathSpy />
      <Routes>
        <Route path="/worlds/:wid/*" element={<WorldView />} />
        <Route path="*" element={<div>away</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

function renderCampaign() {
  lastPath = ""; lastSearch = "";
  render(
    <MemoryRouter initialEntries={["/campaigns/c1/world"]}>
      <PathSpy />
      <Routes><Route path="/campaigns/:cid/world/*" element={<WorldView campaign />} />
        <Route path="*" element={<div>away</div>} /></Routes>
    </MemoryRouter>,
  );
}

function renderCampaignAtUrl(url: string) {
  lastPath = ""; lastSearch = "";
  render(
    <MemoryRouter initialEntries={[url]}>
      <PathSpy />
      <Routes><Route path="/campaigns/:cid/world/*" element={<WorldView campaign />} />
        <Route path="*" element={<div>away</div>} /></Routes>
    </MemoryRouter>,
  );
}

/** `window.history.back()` does not drive a `MemoryRouter` -- it has its own
 *  in-memory stack and ignores the browser's -- so the one test that steps
 *  Back needs the data router instead, which exposes `router.navigate(-1)`. */
function renderWithRouter(url: string) {
  const router = createMemoryRouter(
    [{ path: "/worlds/:wid/*", element: <><PathSpy /><WorldView /></> }],
    { initialEntries: [url] },
  );
  render(<RouterProvider router={router} />);
  return router;
}

/** The column's row for a section. Its accessible name is the label and its
 *  count, so every lookup here is a prefix match rather than an exact one.
 *  The rows are links now, which is what lets one open in a new tab. */
function indexRow(label: string) {
  return screen.getByRole("link", { name: new RegExp(`^${label}\\b`) });
}

test("shows the world name and opens on the Overview", async () => {
  renderAt();
  await screen.findByText("Drowned Realm");
  expect(indexRow("Overview")).toHaveClass("active");
  // The column's rows are navigation links now, not tab buttons -- they owe
  // the same `aria-current` convention `AppRail`/`PhoneTabs` already use, so
  // the open section is announced, not just colored.
  expect(indexRow("Overview")).toHaveAttribute("aria-current", "page");
  expect(indexRow("Push to campaigns")).not.toHaveAttribute("aria-current");
  expect(screen.getByRole("heading", { name: "Overview" })).toBeInTheDocument();
});

/** A world read whose stored counts are the ones given, every other kind 0. */
function worldWithCounts(counts: Record<string, number>) {
  return {
    meta: { id: "w", name: "Drowned Realm" }, body: "",
    counts: { characters: 0, pcs: 0, creatures: 0, groups: 0, locations: 0,
              items: 0, lore: 0, greetings: 0, ...counts },
    campaigns: 2,
  };
}

test("the index is grouped who / where & what / writing, each row counted", async () => {
  (api.getWorld as any).mockResolvedValue(
    worldWithCounts({ characters: 2, locations: 1, lore: 7, greetings: 5 }));
  // The lists say something else on purpose: on the world shape the numbers
  // come from the world read the header already makes, and a list that
  // disagreed would be answering a question nobody asked it.
  (api.listCharacters as any).mockResolvedValue([]);
  (api.listTags as any).mockResolvedValue({ tide: "Tide", dusk: "Dusk", salt: "Salt" });
  renderAt();
  await screen.findByText("Drowned Realm");

  for (const label of ["Who", "Where & what", "Writing"]) {
    expect(screen.getByText(label)).toBeInTheDocument();
  }
  await waitFor(() => expect(indexRow("Characters")).toHaveTextContent("2"));
  expect(indexRow("Locations")).toHaveTextContent("1");
  expect(indexRow("Lore")).toHaveTextContent("7");
  expect(indexRow("Greetings")).toHaveTextContent("5");
  expect(indexRow("Items")).toHaveTextContent("0");
  expect(indexRow("Tags")).toHaveTextContent("3");
  // ...and the facts a world has that are not records in it
  expect(screen.getByText("3 tags · 2 campaigns")).toBeInTheDocument();
});

test("opening a world counts it without listing a single section", async () => {
  // Nine full lists -- every character card parsed, every entity token-counted
  // -- were downloaded on every visit just to be counted, while the world read
  // beside them already carried the counts as directory tallies.
  // Opened on Items rather than the overview, whose setup checklist makes
  // reads of its own; the Items editor lists items and nothing else.
  (api.getWorld as any).mockResolvedValue(worldWithCounts({ creatures: 4 }));
  renderAtUrl("/worlds/w/items");
  await waitFor(() => expect(indexRow("Creatures")).toHaveTextContent("4"));
  expect(api.getWorld).toHaveBeenCalledTimes(1);
  expect((api.listEntities as any).mock.calls).toEqual([[{ kind: "world", id: "w" }, "items"]]);
  expect(api.listCharacters).not.toHaveBeenCalled();
  expect(api.listPCs).not.toHaveBeenCalled();
  expect(api.listGreetings).not.toHaveBeenCalled();
});

test("a kind the world read did not count is a dash, not a zero", async () => {
  (api.getWorld as any).mockResolvedValue({
    meta: { id: "w", name: "Drowned Realm" }, body: "", counts: { characters: 3 },
  });
  renderAt();
  await waitFor(() => expect(indexRow("Characters")).toHaveTextContent("3"));
  expect(indexRow("Items")).toHaveTextContent("—");
  expect(indexRow("Items")).not.toHaveTextContent("0");
});

test("picking a section swaps main and leaves the index standing", async () => {
  renderAt();
  await screen.findByText("Drowned Realm");
  fireEvent.click(indexRow("Locations"));
  await waitFor(() =>
    expect(api.listEntities).toHaveBeenCalledWith({ kind: "world", id: "w" }, "locations"));

  expect(screen.getByRole("heading", { name: "Locations" })).toBeInTheDocument();
  expect(indexRow("Locations")).toHaveClass("active");
  expect(indexRow("Overview")).not.toHaveClass("active");
  // the whole index is still there to move on to
  for (const label of ["Characters", "PCs", "Creatures", "Groups", "Items", "Lore", "Greetings", "Tags"]) {
    expect(indexRow(label)).toBeInTheDocument();
  }
});

test("picking Characters renders the character editor", async () => {
  renderAt();
  await screen.findByText("Drowned Realm");
  fireEvent.click(indexRow("Characters"));
  await waitFor(() => expect(api.listCharacters).toHaveBeenCalledWith({ kind: "world", id: "w" }));
  expect(screen.getByRole("button", { name: /new character/i })).toBeInTheDocument();
});

test("picking PCs renders the PC editor", async () => {
  renderAt();
  await screen.findByText("Drowned Realm");
  fireEvent.click(indexRow("PCs"));
  await waitFor(() => expect(api.listPCs).toHaveBeenCalledWith({ kind: "world", id: "w" }));
  expect(screen.getByRole("button", { name: /new pc/i })).toBeInTheDocument();
});

test("opening a record swaps only main — the column keeps its selection", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "the-pact", name: "The Pact" }]);
  renderAt();
  await screen.findByText("Drowned Realm");
  fireEvent.click(indexRow("Lore"));
  fireEvent.click(await screen.findByText("The Pact"));           // list -> detail

  expect(indexRow("Lore")).toHaveClass("active");
  expect(screen.getByRole("heading", { name: "Lore" })).toBeInTheDocument();
  expect(indexRow("Characters")).toBeInTheDocument();
});

test("a character leaves this page — they own a screen, not a third of one", async () => {
  (api.listCharacters as any).mockResolvedValue([
    { id: "mira", name: "Mira", default_version: "main", versions: [{ id: "main", name: "main" }] },
  ]);
  renderAt();
  await screen.findByText("Drowned Realm");
  fireEvent.click(indexRow("Characters"));
  fireEvent.click(await screen.findByText("Mira"));
  await waitFor(() => expect(lastPath).toBe("/worlds/w/characters/mira"));
});

test("world-copy mode shows the fork banner, campaign back link, and campaign entity scope", async () => {
  renderCampaign();
  await screen.findByText(/ashes of the verdigris crown \/ world copy/i);
  expect(screen.getByText(/campaign view/i)).toBeInTheDocument();
  // entity sections read from the campaign fork, not the source world
  fireEvent.click(indexRow("Locations"));
  await waitFor(() =>
    expect(api.listEntities).toHaveBeenCalledWith({ kind: "campaign", id: "c1" }, "locations"));
});

test("the Lore section hosts the lorebook importer, and the pinned row opens it", async () => {
  renderAt();
  await screen.findByText("Drowned Realm");
  fireEvent.click(screen.getByRole("button", { name: /import lorebook/i }));
  await screen.findByRole("heading", { name: "Lore" });
  expect(screen.getByText(/import lorebook \/ world-info/i).parentElement)
    .toHaveAttribute("open");
  expect(screen.getByRole("button", { name: /parse/i })).toBeInTheDocument();
});

test("the Overview hosts the scenario importer, and the pinned row opens it", async () => {
  renderAt();
  await screen.findByText("Drowned Realm");
  fireEvent.click(screen.getByRole("button", { name: /import scenario card/i }));
  await screen.findByRole("heading", { name: "Overview" });
  const summary = screen.getAllByText(/^import scenario card$/i)
    .find((el) => el.tagName === "SUMMARY");
  expect(summary!.parentElement).toHaveAttribute("open");
  expect(screen.getByRole("button", { name: /read card/i })).toBeInTheDocument();
});

test("arriving at a greeting by URL opens it, not merely its section", async () => {
  // The World-greetings chip that used to drive this from a character's detail
  // pane is on the character's own page now, and it navigates here by URL --
  // so this is the half WorldView still owns.
  (api.listGreetings as any).mockResolvedValue([
    { id: "sol-2", name: "SoL 2", character: "other", version: "main", present: ["mira"], requires_tags: [], predecessor_join: "all" },
  ]);
  renderAtUrl("/worlds/w?section=greetings&id=sol-2");
  await waitFor(() => expect(api.readGreeting).toHaveBeenCalledWith({ kind: "world", id: "w" }, "sol-2"));
  expect(indexRow("Greetings")).toHaveClass("active");
});

test("campaign mode passes campaign scope and hides Tags and the Overview", async () => {
  (api.listAppearances as any).mockResolvedValue([]);
  renderCampaign();
  await screen.findByText(/World Copy/);
  await waitFor(() => expect(api.listCharacters).toHaveBeenCalledWith({ kind: "campaign", id: "c1" }));
  expect(screen.queryByRole("link", { name: /^Tags\b/ })).toBeNull();
  expect(screen.queryByRole("link", { name: /^Overview\b/ })).toBeNull();
  // a campaign has no tag vocabulary of its own, so nothing asks for one
  expect(api.listTags).not.toHaveBeenCalled();
  expect(indexRow("Greetings")).toBeInTheDocument();
  // the fork's way back to what it forked from
  expect(screen.getByRole("link", { name: /source world/i })).toHaveAttribute("href", "/worlds/w");
});

test("campaign path resolves module context for the sections that take one", async () => {
  (api.getCampaignModule as any).mockResolvedValue({ setting: "pool-basic", resolved: "pool-basic", source: "campaign" });
  (api.readModule as any).mockResolvedValue(POOL_BASIC);
  (api.listCharacters as any).mockResolvedValue([
    { id: "mira", name: "Mira", default_version: "main", versions: [{ id: "main", name: "main" }] },
  ]);
  // she has to have appeared, or the campaign grid's default filter hides her
  (api.listAppearances as any).mockResolvedValue(
    [{ kind: "characters", id: "mira", version: "main", role: "npc", scenes: ["01"] }]);
  renderCampaign();
  await screen.findByText(/World Copy/);
  await waitFor(() => expect(api.getCampaignModule).toHaveBeenCalledWith("c1"));
  await waitFor(() => expect(api.readModule).toHaveBeenCalledWith("pool-basic"));
  // The character's own Sheet panel is on the character's page, which resolves
  // the same chain for itself; what the grid spends the module on is the
  // sheet-aware create.
  await screen.findByText("Mira");
});

test("editing a campaign's world keeps the campaign in the status bar", async () => {
  // CampaignView unmounts on the way here and clears the shell context, so
  // without WorldView publishing, the bar drops the campaign for the whole
  // world-editing workflow.
  const seen: unknown[] = [];
  function Probe() {
    seen.push(useShellStatus().context);
    return null;
  }
  render(
    <ShellStatusProvider>
      <MemoryRouter initialEntries={["/campaigns/c1/world"]}>
        <Routes>
          <Route path="/campaigns/:cid/world/*" element={<WorldView campaign />} />
        </Routes>
      </MemoryRouter>
      <Probe />
    </ShellStatusProvider>,
  );
  await waitFor(() =>
    expect(seen[seen.length - 1]).toEqual({ campaign: "Ashes of the Verdigris Crown", scene: "" }));
});

test("the standalone world route publishes no campaign — there isn't one", async () => {
  const seen: unknown[] = [];
  function Probe() {
    seen.push(useShellStatus().context);
    return null;
  }
  render(
    <ShellStatusProvider>
      <MemoryRouter initialEntries={["/worlds/w"]}>
        <Routes><Route path="/worlds/:wid/*" element={<WorldView />} /></Routes>
      </MemoryRouter>
      <Probe />
    </ShellStatusProvider>,
  );
  await waitFor(() => expect(api.getWorld).toHaveBeenCalled());
  expect(seen.every((c) => c === null)).toBe(true);
});


// ---- deep links (#33): a search hit is a record, and following one has to
// land on that record rather than on the section it lives in.

test("?section=&id= opens the entity the URL names", async () => {
  (api.listEntities as any).mockResolvedValue([
    { id: "the-salt-pact", name: "The Salt Pact" },
    { id: "the-tide-table", name: "The Tide Table" },
  ]);
  renderAtUrl("/worlds/w?section=lore&id=the-salt-pact");
  expect(await screen.findByRole("heading", { name: "Lore" })).toBeInTheDocument();
  // The record is open, not merely listed: its detail view is on screen.
  await waitFor(() =>
    expect(screen.getByRole("heading", { level: 3, name: "The Salt Pact" })).toBeInTheDocument());
});

test("a nav aimed at one kind is not consumed by another editor", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "the-salt-pact", name: "The Salt Pact" }]);
  renderAtUrl("/worlds/w?section=items&id=the-salt-pact");
  // Items is what the URL asked for, so that is the section that opens --
  // every EntityEditor is the same component, and only the kind tells them
  // apart.
  expect(await screen.findByRole("heading", { name: "Items" })).toBeInTheDocument();
  expect(indexRow("Items")).toHaveClass("active");
});

test("reclassifying a record opens it in the section it moved to", async () => {
  // The move takes it out of Lore entirely, so leaving the user on Lore would
  // read as a delete. `openEntity` is the same deep-link path a search hit uses.
  (api.listEntities as any).mockResolvedValue([{ id: "the-salt-pact", name: "The Salt Pact" }]);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  renderAtUrl("/worlds/w?section=lore&id=the-salt-pact");
  const picker = await screen.findByLabelText("Reclassify as");
  fireEvent.change(picker, { target: { value: "locations" } });
  expect(await screen.findByRole("heading", { name: "Locations" })).toBeInTheDocument();
  expect(indexRow("Locations")).toHaveClass("active");
});


test("an old character link keeps its version", async () => {
  // Kept working rather than chased down: SearchView builds these links
  // generically, and the hub and the palette carry them too.
  (api.listCharacters as any).mockResolvedValue([{ id: "mira", name: "Mira", versions: 1 }]);
  renderAtUrl("/worlds/w?section=characters&id=mira&v=main");
  await waitFor(() => expect(lastPath + lastSearch).toBe("/worlds/w/characters/mira?v=main"));
});

test("section=characters with no id is still the grid, not an empty character", async () => {
  (api.listCharacters as any).mockResolvedValue([{ id: "mira", name: "Mira", versions: 1 }]);
  renderAtUrl("/worlds/w?section=characters");
  await waitFor(() => expect(lastPath).toBe("/worlds/w/characters"));
  await screen.findByText("Mira");
  expect(indexRow("Characters")).toHaveClass("active");
});

test("the index offers Push to campaigns, which lists what each campaign owes", async () => {
  renderAt();
  await screen.findByText("Drowned Realm");
  fireEvent.click(indexRow("Push to campaigns"));
  expect(await screen.findByRole("heading", { name: "Push to campaigns" })).toBeInTheDocument();
  const row = await screen.findByRole("link", { name: /Ashes of the Verdigris Crown/ });
  expect(row.textContent).toContain("2 conflict");
  expect(row).toHaveAttribute("href", "/campaigns/c1");
});

test("a campaign's fork of a world is not offered the push panel", async () => {
  renderCampaign();
  await screen.findByText(/Campaign view/);
  expect(screen.queryByRole("link", { name: /^Push to campaigns/ })).not.toBeInTheDocument();
});

// ---- Images (#200) ----

test("the world index opens the Images view", async () => {
  renderAt();
  await screen.findByText("Drowned Realm");
  fireEvent.click(indexRow("Images"));
  expect(await screen.findByRole("tab", { name: /Gallery/ })).toBeInTheDocument();
  expect(api.listWorldImages).toHaveBeenCalledWith("w", false);
});

test("the campaign fork has no Images row", async () => {
  // The subjects sidecar the queue writes is world-side, and a fork browses its
  // own diverged art in the editor that owns it.
  renderCampaign();
  await screen.findByText(/World Copy/);
  expect(screen.queryByRole("link", { name: /^Images\b/ })).toBeNull();
});

test("Images is offered in the command palette, like every other world section", async () => {
  // The column is not the only way in: a reader on ⌘K should reach Images the
  // way they reach Overview and Push.
  const items: PaletteItem[] = [];
  render(
    <PaletteProvider>
      <PaletteSpy onItems={(got) => items.splice(0, items.length, ...got)} />
      <MemoryRouter initialEntries={["/worlds/w"]}>
        <Routes><Route path="/worlds/:wid/*" element={<WorldView />} /></Routes>
      </MemoryRouter>
    </PaletteProvider>,
  );
  await screen.findByText("Drowned Realm");
  await waitFor(() => expect(items.some((i) => i.id === "world-section:images")).toBe(true));
  const images = items.find((i) => i.id === "world-section:images")!;
  expect(images.label).toBe("Images");
  expect(items.map((i) => i.id)).toContain("world-section:push");
});

test("Greetings switches between the chip list and the plot map, and a node opens the greeting", async () => {
  (api.listGreetings as any).mockResolvedValue([
    { id: "dawn", name: "Saltmarch Dawn", character: "", version: "", present: [], requires_tags: [], predecessor_join: "all" },
    { id: "sol-2", name: "SoL 2", character: "", version: "", present: [], requires_tags: [], predecessor_join: "all" },
  ]);
  (api.readGreeting as any).mockImplementation(async (_s: unknown, gid: string) => ({
    meta: { id: gid, name: gid, character: "", version: "", present: [], requires_tags: [], predecessor_join: "all" },
    body: "hi", rev: "r1", predecessors: [],
    edges: gid === "dawn" ? { leads_to: ["sol-2"], excludes: [] } : { leads_to: [], excludes: [] },
  }));
  renderAt();
  await screen.findByText("Drowned Realm");
  fireEvent.click(indexRow("Greetings"));

  // the chip-list editor is what a section opens on; the graph is the alternate
  await screen.findByRole("link", { name: /new greeting/i });
  fireEvent.click(screen.getByRole("button", { name: "Plot map" }));

  const node = await screen.findByRole("button", { name: "Open Saltmarch Dawn" });
  expect(screen.getByRole("button", { name: "Unlocks: Saltmarch Dawn → SoL 2" })).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: /new greeting/i })).toBeNull();

  // a node is a way into the editor, so it lands back on the list with that
  // greeting open rather than opening a second detail pane on the graph
  fireEvent.click(node);
  await screen.findByRole("link", { name: /new greeting/i });
  await waitFor(() => expect(api.readGreeting).toHaveBeenCalledWith({ kind: "world", id: "w" }, "dawn"));
});

test("switching to the plot map keeps a half-written greeting, and a save reloads the map", async () => {
  (api.listGreetings as any).mockResolvedValue([
    { id: "dawn", name: "Saltmarch Dawn", character: "", version: "", present: [], requires_tags: [], predecessor_join: "all" },
  ]);
  (api.readGreeting as any).mockResolvedValue({
    meta: { id: "dawn", name: "Saltmarch Dawn", character: "", version: "", present: [], requires_tags: [], predecessor_join: "all" },
    body: "hi", rev: "r1", predecessors: [], edges: { leads_to: [], excludes: [] },
  });
  renderAt();
  await screen.findByText("Drowned Realm");
  fireEvent.click(indexRow("Greetings"));

  // start a new greeting, then look at the graph without saving
  await screen.findByRole("link", { name: /new greeting/i });
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Half-written" } });
  fireEvent.click(screen.getByRole("button", { name: "Plot map" }));
  await screen.findByRole("button", { name: "Open Saltmarch Dawn" });

  // Unmounting the editor would take the draft with it -- no Save, no Cancel,
  // no warning. It is hidden, so coming back finds the words still there.
  fireEvent.click(screen.getByRole("button", { name: "List" }));
  await waitFor(() => expect(screen.getByLabelText("Name")).toHaveValue("Half-written"));
});

test("a save that lands after the map is open makes the map re-read", async () => {
  (api.listGreetings as any).mockResolvedValue([
    { id: "dawn", name: "Saltmarch Dawn", character: "", version: "", present: [], requires_tags: [], predecessor_join: "all" },
  ]);
  (api.readGreeting as any).mockResolvedValue({
    meta: { id: "dawn", name: "Saltmarch Dawn", character: "", version: "", present: [], requires_tags: [], predecessor_join: "all" },
    body: "hi", rev: "r1", predecessors: [], edges: { leads_to: [], excludes: [] },
  });
  // The save is still in flight when the reader switches views -- which is the
  // whole race: the map mounts and reads the edges as they were, and the save
  // lands afterwards with nothing to tell it.
  let landSave = () => {};
  (api.updateGreeting as any) = vi.fn(() => new Promise<void>((res) => { landSave = res; }));
  (api.setEdges as any) = vi.fn().mockResolvedValue({ ok: true });
  renderAt();
  await screen.findByText("Drowned Realm");
  fireEvent.click(indexRow("Greetings"));

  const rail = await waitFor(() => document.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Saltmarch Dawn"));
  fireEvent.click(await screen.findByRole("button", { name: /^edit$/i }));
  fireEvent.click(screen.getByRole("button", { name: "Save greeting" }));
  await waitFor(() => expect(api.updateGreeting).toHaveBeenCalled());

  fireEvent.click(screen.getByRole("button", { name: "Plot map" }));
  await screen.findByRole("button", { name: "Open Saltmarch Dawn" });
  const readsBefore = (api.readGreeting as any).mock.calls.length;

  landSave();
  // Without the re-read the map would keep serving a snapshot from before the
  // save, and its next whole-array write would send those edges back.
  await waitFor(() =>
    expect((api.readGreeting as any).mock.calls.length).toBeGreaterThan(readsBefore));
});

test("the map holds still while a list save is in flight", async () => {
  (api.listGreetings as any).mockResolvedValue([
    { id: "dawn", name: "Saltmarch Dawn", character: "", version: "", present: [], requires_tags: [], predecessor_join: "all" },
    { id: "vow", name: "Vow of Silence", character: "", version: "", present: [], requires_tags: [], predecessor_join: "all" },
  ]);
  (api.readGreeting as any).mockImplementation(async (_s: unknown, gid: string) => ({
    meta: { id: gid, name: gid, character: "", version: "", present: [], requires_tags: [], predecessor_join: "all" },
    body: "hi", rev: "r1", predecessors: [], edges: { leads_to: [], excludes: [] },
  }));
  let landSave = () => {};
  (api.updateGreeting as any) = vi.fn(() => new Promise<void>((res) => { landSave = res; }));
  (api.setEdges as any) = vi.fn().mockResolvedValue({ ok: true });
  renderAt();
  await screen.findByText("Drowned Realm");
  fireEvent.click(indexRow("Greetings"));

  const rail = await waitFor(() => document.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Saltmarch Dawn"));
  fireEvent.click(await screen.findByRole("button", { name: /^edit$/i }));
  fireEvent.click(screen.getByRole("button", { name: "Save greeting" }));
  await waitFor(() => expect(api.updateGreeting).toHaveBeenCalled());

  fireEvent.click(screen.getByRole("button", { name: "Plot map" }));
  // That save still owes a whole-array setEdges of its own; a graph write
  // landing first would be the older payload's to overwrite.
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Link from Vow of Silence" })).toBeDisabled());
  landSave();
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Link from Vow of Silence" })).toBeEnabled());
});

test("a graph edit re-reads the chip list behind it", async () => {
  (api.listGreetings as any).mockResolvedValue([
    { id: "dawn", name: "Saltmarch Dawn", character: "", version: "", present: [], requires_tags: [], predecessor_join: "all" },
    { id: "vow", name: "Vow of Silence", character: "", version: "", present: [], requires_tags: [], predecessor_join: "all" },
  ]);
  (api.readGreeting as any).mockImplementation(async (_s: unknown, gid: string) => ({
    meta: { id: gid, name: gid, character: "", version: "", present: [], requires_tags: [], predecessor_join: "all" },
    body: "hi", rev: "r1", predecessors: [], edges: { leads_to: [], excludes: [] },
  }));
  (api.setEdges as any) = vi.fn().mockResolvedValue({ ok: true });
  renderAt();
  await screen.findByText("Drowned Realm");
  fireEvent.click(indexRow("Greetings"));

  const rail = await waitFor(() => document.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Saltmarch Dawn"));   // open it in the list
  fireEvent.click(await screen.findByRole("button", { name: "Plot map" }));
  await screen.findByRole("button", { name: "Open Saltmarch Dawn" });
  const readsBefore = (api.readGreeting as any).mock.calls.length;

  fireEvent.click(screen.getByRole("button", { name: "Link from Saltmarch Dawn" }));
  fireEvent.click(screen.getByRole("button", { name: "Link Saltmarch Dawn to Vow of Silence" }));

  // The chip list is still mounted with this greeting's edges as they were.
  // Left alone, Edit-then-Save there would send that stale array back.
  await waitFor(() =>
    expect((api.readGreeting as any).mock.calls.length).toBeGreaterThan(readsBefore));
});

test("a map remounted mid-write is held behind the write the last one left", async () => {
  (api.listGreetings as any).mockResolvedValue([
    { id: "dawn", name: "Saltmarch Dawn", character: "", version: "", present: [], requires_tags: [], predecessor_join: "all" },
    { id: "vow", name: "Vow of Silence", character: "", version: "", present: [], requires_tags: [], predecessor_join: "all" },
  ]);
  (api.readGreeting as any).mockImplementation(async (_s: unknown, gid: string) => ({
    meta: { id: gid, name: gid, character: "", version: "", present: [], requires_tags: [], predecessor_join: "all" },
    body: "hi", rev: "r1", predecessors: [], edges: { leads_to: [], excludes: [] },
  }));
  const gate: (() => void)[] = [];
  (api.setEdges as any) = vi.fn(() => new Promise<void>((res) => gate.push(res)));
  renderAt();
  await screen.findByText("Drowned Realm");
  fireEvent.click(indexRow("Greetings"));
  fireEvent.click(await screen.findByRole("button", { name: "Plot map" }));

  fireEvent.click(await screen.findByRole("button", { name: "Link from Saltmarch Dawn" }));
  fireEvent.click(screen.getByRole("button", { name: "Link Saltmarch Dawn to Vow of Silence" }));
  await waitFor(() => expect(api.setEdges).toHaveBeenCalledTimes(1));

  // away and straight back: the first map is gone, its PUT is not
  fireEvent.click(screen.getByRole("button", { name: "List" }));
  fireEvent.click(screen.getByRole("button", { name: "Plot map" }));

  // A fresh map with an empty queue of its own would happily write over it.
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Link from Vow of Silence" })).toBeDisabled());
  gate.forEach((res) => res());
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Link from Vow of Silence" })).toBeEnabled());
});

test("the plot map is an address, and Back returns to the list", async () => {
  const router = renderWithRouter("/worlds/w/greetings");
  fireEvent.click(await screen.findByRole("button", { name: "Plot map" }));
  await waitFor(() => expect(lastPath + lastSearch).toBe("/worlds/w/greetings?view=graph"));
  fireEvent.click(screen.getByRole("button", { name: "List" }));
  await waitFor(() => expect(lastSearch).toBe(""));
  await router.navigate(-1);
  await waitFor(() => expect(lastSearch).toBe("?view=graph"));
  expect(await screen.findByTestId("plot-map")).toBeInTheDocument();
});

test("a greeting's own address honours ?view=graph, and the record stays selected behind it", async () => {
  (api.listGreetings as any).mockResolvedValue([
    { id: "tide-watch", name: "Tide Watch", character: "", version: "", present: [], requires_tags: [], predecessor_join: "all" },
  ]);
  (api.readGreeting as any).mockResolvedValue({
    meta: { id: "tide-watch", name: "Tide Watch", character: "", version: "", present: [], requires_tags: [], predecessor_join: "all" },
    body: "hi", rev: "r1", predecessors: [], edges: { leads_to: [], excludes: [] },
  });
  renderAtUrl("/worlds/w/greetings/tide-watch?view=graph");
  // the graph has no detail pane -- the record is hidden, not rendered here
  expect(await screen.findByTestId("plot-map")).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Tide Watch" })).toBeNull();

  // switching back to List does not lose it -- unlike a fresh navigation to
  // the section root, the address kept naming this record the whole time
  fireEvent.click(screen.getByRole("button", { name: "List" }));
  await screen.findByRole("heading", { name: "Tide Watch" });
});

test("switching to the map and back does not lose a half-written greeting", async () => {
  renderAtUrl("/worlds/w/greetings");
  fireEvent.change(await screen.findByLabelText(/Name/), { target: { value: "Half written" } });
  fireEvent.click(screen.getByRole("button", { name: "Plot map" }));
  await waitFor(() => expect(lastSearch).toBe("?view=graph"));
  fireEvent.click(screen.getByRole("button", { name: "List" }));
  expect(await screen.findByLabelText(/Name/)).toHaveValue("Half written");
});

// Not a new-greeting draft: an EXISTING greeting, opened by its own address
// and then edited. The chip's navigation has to keep the record segment
// (not just drop back to the section root) or the address changes under the
// edit, `selected` goes null, and the effect's `else resetForm()` throws the
// draft away -- proven a real regression against ce2c1fc1f.
test("editing an existing greeting also keeps its draft across the map switch", async () => {
  (api.listGreetings as any).mockResolvedValue([
    { id: "dawn", name: "Saltmarch Dawn", character: "", version: "", present: [], requires_tags: [], predecessor_join: "all" },
  ]);
  (api.readGreeting as any).mockResolvedValue({
    meta: { id: "dawn", name: "Saltmarch Dawn", character: "", version: "", present: [], requires_tags: [], predecessor_join: "all" },
    body: "hi", rev: "r1", predecessors: [], edges: { leads_to: [], excludes: [] },
  });
  renderAtUrl("/worlds/w/greetings/dawn");
  fireEvent.click(await screen.findByRole("button", { name: /^edit$/i }));
  fireEvent.change(screen.getByLabelText(/Name/), { target: { value: "Saltmarch Dusk" } });
  fireEvent.click(screen.getByRole("button", { name: "Plot map" }));
  await waitFor(() => expect(lastPath + lastSearch).toBe("/worlds/w/greetings/dawn?view=graph"));
  fireEvent.click(screen.getByRole("button", { name: "List" }));
  await waitFor(() => expect(lastPath).toBe("/worlds/w/greetings/dawn"));
  expect(await screen.findByLabelText(/Name/)).toHaveValue("Saltmarch Dusk");
});

// `save()` used to end a create with `select(id)` directly, opening the new
// record without touching the address -- so `+ New greeting` (a `Link` to
// `sectionPath`) pointed at the screen already showing and did nothing.
test("+ New greeting clears the form after a create", async () => {
  (api.listGreetings as any).mockResolvedValue([]);
  (api.createGreeting as any) = vi.fn().mockResolvedValue({ id: "new-greeting" });
  (api.setEdges as any) = vi.fn().mockResolvedValue({ ok: true });
  (api.readGreeting as any).mockResolvedValue({
    meta: { id: "new-greeting", name: "New greeting", character: "", version: "", present: [], requires_tags: [], predecessor_join: "all" },
    body: "", rev: "r1", predecessors: [], edges: { leads_to: [], excludes: [] },
  });
  renderAtUrl("/worlds/w/greetings");
  fireEvent.change(await screen.findByLabelText(/Name/), { target: { value: "New greeting" } });
  fireEvent.click(screen.getByRole("button", { name: /create greeting/i }));
  await waitFor(() => expect(lastPath).toBe("/worlds/w/greetings/new-greeting"));
  await screen.findByRole("button", { name: /^edit$/i });   // opened read-only, not the form

  fireEvent.click(screen.getByRole("link", { name: /new greeting/i }));
  await waitFor(() => expect(lastPath).toBe("/worlds/w/greetings"));
  expect(await screen.findByLabelText(/Name/)).toHaveValue("");
});

test("a ref chip naming a PC opens that PC, and picking PCs from the index clears it", async () => {
  // Two rules in one test because they are the same rule from both sides: a
  // chip carries a record to open, choosing the section from the column means
  // the list. Without the second, the section reopens whoever a chip pointed
  // at, possibly several navigations ago.
  (api.listPCs as any).mockResolvedValue([
    { id: "winifred", name: "Winifred", tags: [], default_version: "main", versions: [] }]);
  (api.readPC as any).mockResolvedValue({
    meta: { id: "winifred", name: "Winifred", tags: [], default_version: "main" },
    versions: [{ id: "main", name: "main",
                 persona: { name: "Winifred", pronouns: "", summary: "",
                            birthdate: "", description: "a quiet sort" } }] });
  (api.listPCImages as any).mockResolvedValue([]);
  (api.listTags as any).mockResolvedValue({});
  (api.getCalendarMonths as any).mockResolvedValue({ months: [] });
  (api.getSheet as any).mockResolvedValue({ sheet: null });
  (api.listEntities as any).mockResolvedValue([{ id: "watch", name: "The Watch" }]);
  (api.readEntity as any).mockResolvedValue({
    meta: { id: "watch", name: "The Watch", leader: "pcs:winifred" }, body: "x", rev: "r1" });
  renderAt();
  await screen.findByText("Drowned Realm");

  fireEvent.click(indexRow("Groups"));
  fireEvent.click(await screen.findByText("The Watch"));
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  fireEvent.click(screen.getByRole("button", { name: /Winifred/ }));
  await waitFor(() =>
    expect(api.readPC).toHaveBeenCalledWith({ kind: "world", id: "w" }, "winifred"));

  // ...and coming back to PCs from the column shows the list, not Winifred
  fireEvent.click(indexRow("Groups"));
  (api.readPC as any).mockClear();
  fireEvent.click(indexRow("PCs"));
  await waitFor(() => expect(api.listPCs).toHaveBeenCalled());
  expect(api.readPC).not.toHaveBeenCalled();
});

test("a focused PC is not carried into another scope's render", async () => {
  // The record now comes off the URL rather than out of React state keyed to
  // a captured scope, so there is nothing left to carry: a plain navigation to
  // another world's PCs section names no record at all, and the editor there
  // has nothing to focus.
  (api.listPCs as any).mockResolvedValue([
    { id: "winifred", name: "Winifred", tags: [], default_version: "main", versions: [] }]);
  (api.readPC as any).mockResolvedValue({
    meta: { id: "winifred", name: "Winifred", tags: [], default_version: "main" },
    versions: [{ id: "main", name: "main",
                 persona: { name: "Winifred", pronouns: "", summary: "",
                            birthdate: "", description: "a quiet sort" } }] });
  (api.listPCImages as any).mockResolvedValue([]);
  (api.listTags as any).mockResolvedValue({});
  (api.getCalendarMonths as any).mockResolvedValue({ months: [] });
  (api.getSheet as any).mockResolvedValue({ sheet: null });
  (api.listEntities as any).mockResolvedValue([{ id: "watch", name: "The Watch" }]);
  (api.readEntity as any).mockResolvedValue({
    meta: { id: "watch", name: "The Watch", leader: "pcs:winifred" }, body: "x", rev: "r1" });
  render(
    <MemoryRouter initialEntries={["/worlds/w"]}>
      <Routes>
        <Route path="/worlds/:wid/*" element={<><WorldView /><GoTo to="/worlds/w2/pcs" /></>} />
      </Routes>
    </MemoryRouter>,
  );
  await screen.findByText("Drowned Realm");
  fireEvent.click(indexRow("Groups"));
  fireEvent.click(await screen.findByText("The Watch"));
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  fireEvent.click(screen.getByRole("button", { name: /Winifred/ }));
  await waitFor(() => expect(api.readPC).toHaveBeenCalled());

  (api.readPC as any).mockClear();
  (api.listPCs as any).mockClear();
  fireEvent.click(screen.getByRole("button", { name: "go" }));
  await waitFor(() => expect(api.listPCs).toHaveBeenCalled());
  expect(api.readPC).not.toHaveBeenCalled();
});


test("the world header shows the world's cover, and drops it if it will not load", async () => {
  (api.getWorld as any).mockResolvedValue({
    meta: { id: "w", name: "Drowned Realm", cover: "v1" }, body: "", counts: {},
  });
  (api.worldCoverUrl as any).mockImplementation(
    (wid: string, o: any) => `/api/worlds/${wid}/cover?w=${o.w}&v=${o.v}`);

  renderAt();
  const img = await screen.findByAltText("Drowned Realm cover");
  // 2x of headroom for a box index.css sizes at 104px, the campaigns shelf's rule
  expect(img.getAttribute("src")).toContain("w=208");

  // A cover that will not load leaves no empty frame in a header that already
  // has a name and a section to show.
  fireEvent.error(img);
  await waitFor(() => expect(screen.queryByAltText("Drowned Realm cover")).toBeNull());
});

test("a world with no cover renders no header thumbnail", async () => {
  (api.getWorld as any).mockResolvedValue({
    meta: { id: "w", name: "Drowned Realm", cover: "" }, body: "", counts: {},
  });
  renderAt();
  // The eyebrow carries the world name on every section, so wait on something
  // unambiguous rather than the name itself.
  await screen.findByRole("heading", { level: 1 });
  expect(screen.queryByAltText("Drowned Realm cover")).toBeNull();
});

// ---- a section is a route (#addresses): the column's rows are links, an
// unknown or malformed tail redirects, and an old ?section= link is
// translated once, ahead of every other redirect.

test("each section has its own address", async () => {
  renderAtUrl("/worlds/w/items");
  expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent("Items");
});

test("the world root is the overview", async () => {
  renderAtUrl("/worlds/w");
  expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent("Overview");
});

test("a column row is a link, which is what lets it open in a new tab", async () => {
  renderAtUrl("/worlds/w");
  const column = within(await screen.findByRole("complementary"));
  const row = column.getByRole("link", { name: /Items/ });
  expect(row).toHaveAttribute("href", "/worlds/w/items");
});

test("clicking a column row changes the address", async () => {
  renderAtUrl("/worlds/w");
  const column = within(await screen.findByRole("complementary"));
  fireEvent.click(column.getByRole("link", { name: /Lore/ }));
  await waitFor(() => expect(lastPath).toBe("/worlds/w/lore"));
});

test("back steps through sections rather than leaving the world", async () => {
  const router = renderWithRouter("/worlds/w");
  const column = within(await screen.findByRole("complementary"));
  fireEvent.click(column.getByRole("link", { name: /Lore/ }));
  await waitFor(() => expect(lastPath).toBe("/worlds/w/lore"));
  fireEvent.click(column.getByRole("link", { name: /Items/ }));
  await waitFor(() => expect(lastPath).toBe("/worlds/w/items"));
  await router.navigate(-1);
  await waitFor(() => expect(lastPath).toBe("/worlds/w/lore"));
});

test("a tail spelled any other way is replaced with the one address the screen has", async () => {
  for (const odd of ["/worlds/w/items/", "/worlds/w//items", "/worlds/w/it%65ms"]) {
    renderAtUrl(odd);
    await waitFor(() => expect(lastPath).toBe("/worlds/w/items"));
    cleanup();
  }
});

test("...and the redirect keeps the modifiers the link was carrying", async () => {
  renderAtUrl("/worlds/w/lore/?owner=characters%3Asera");
  await waitFor(() => expect(lastPath).toBe("/worlds/w/lore"));
  expect(lastSearch).toBe("?owner=characters%3Asera");
});

test("an old section link lands on the record's new address, replacing it", async () => {
  renderAtUrl("/worlds/w?section=lore&id=the-salt-pact");
  await waitFor(() => expect(lastPath).toBe("/worlds/w/lore/the-salt-pact"));
  expect(lastSearch).toBe("");
});

test("Back from a legacy link lands outside the world, not bouncing forward through the redirect again", async () => {
  // The legacy translation (WorldView.tsx) fires a `<Navigate replace>`, not a
  // plain push -- without `replace` this history stack would read
  // [/away, "?section=lore&id=..." legacy URL, canonical URL], and stepping
  // Back from the canonical URL would land back on the legacy one, which
  // redirects forward again on render: Back would be permanently trapped one
  // step short of leaving the world.
  const router = createMemoryRouter(
    [
      {
        element: <><PathSpy /><Outlet /></>,
        children: [
          { path: "/worlds/:wid/*", element: <WorldView /> },
          { path: "*", element: <div>away</div> },
        ],
      },
    ],
    { initialEntries: ["/away", "/worlds/w?section=lore&id=the-salt-pact"], initialIndex: 1 },
  );
  render(<RouterProvider router={router} />);
  await waitFor(() => expect(lastPath).toBe("/worlds/w/lore/the-salt-pact"));
  await router.navigate(-1);
  await waitFor(() => expect(lastPath).toBe("/away"));
});

test("an old images link keeps the campaign it was narrowed to", async () => {
  renderAtUrl("/worlds/w?section=images&for=run");
  await waitFor(() => expect(lastPath + lastSearch).toBe("/worlds/w/images?for=run"));
});

test("a modern record path ignores a stray section param rather than obeying it", async () => {
  renderAtUrl("/worlds/w/lore/current?section=items&id=old");
  await waitFor(() => expect(lastPath).toBe("/worlds/w/lore/current"));
});

test("a legacy section this shape does not have is ignored, not translated", async () => {
  renderCampaignAtUrl("/campaigns/c/world?section=tags");
  await waitFor(() => expect(lastPath).toBe("/campaigns/c/world/characters"));
});

test("legacy translation beats the campaign root's default redirect", async () => {
  // The whole reason this task is not two tasks: a split leaves a commit in
  // which this URL lands on Characters and the lore entry is lost.
  renderCampaignAtUrl("/campaigns/c/world?section=lore&id=x");
  await waitFor(() => expect(lastPath).toBe("/campaigns/c/world/lore/x"));
});

test("an id containing a slash survives the round trip", async () => {
  // The one case a decoded splat param cannot express: react-router hands
  // `useParams()["*"]` back as `items/a/b`, which reads as three segments and
  // is not an address at all. Rendered rather than unit-tested, because the
  // decoding this guards against happens in the router, not in the parser.
  (api.readEntity as any).mockResolvedValue({
    meta: { id: "a/b", name: "A slash B", keys: "", owners: "" }, body: "Sliced clean.",
  });
  renderAtUrl("/worlds/w/items/a%2Fb");
  await waitFor(() => expect(lastPath).toBe("/worlds/w/items/a%2Fb"));
  await screen.findByRole("heading", { name: "A slash B" });
});

test("an unknown section redirects rather than rendering a page headed Overview", async () => {
  renderAtUrl("/worlds/w/garbage");
  await waitFor(() => expect(lastPath).toBe("/worlds/w"));
});

test("a campaign is sent to its cast, and cannot address a world-only section", async () => {
  renderCampaignAtUrl("/campaigns/c/world");
  await waitFor(() => expect(lastPath).toBe("/campaigns/c/world/characters"));
  cleanup();
  renderCampaignAtUrl("/campaigns/c/world/tags");
  await waitFor(() => expect(lastPath).toBe("/campaigns/c/world/characters"));
});

/** Forget every call so far, keeping what each mock answers. */
function forgetCalls() {
  for (const fn of Object.values(api)) {
    if (typeof fn === "function" && "mockClear" in fn) (fn as any).mockClear();
  }
}

test("a section click recounts in one request, and never by listing another section", async () => {
  // It used to re-download all nine lists on every click to learn one number
  // that could have moved. The section being left is where a record may have
  // been added or removed; the world read counts every kind at once, so one
  // cheap request answers it -- a reclassify's destination included.
  (api.getWorld as any).mockResolvedValue(worldWithCounts({ items: 2 }));
  renderAtUrl("/worlds/w/items");
  await waitFor(() => expect(indexRow("Items")).toHaveTextContent("2"));
  forgetCalls();
  // A record was made while Items was open.
  (api.getWorld as any).mockResolvedValue(worldWithCounts({ items: 3 }));

  fireEvent.click(indexRow("Creatures"));
  await screen.findByRole("heading", { name: "Creatures" });
  await waitFor(() => expect(indexRow("Items")).toHaveTextContent("3"));
  expect(api.getWorld).toHaveBeenCalledTimes(1);
  // The only list read is the one the Creatures editor makes for its own rows.
  expect((api.listEntities as any).mock.calls).toEqual([[{ kind: "world", id: "w" }, "creatures"]]);
  expect(api.listCharacters).not.toHaveBeenCalled();
  expect(api.listPCs).not.toHaveBeenCalled();
  expect(api.listGreetings).not.toHaveBeenCalled();
  expect(api.listTags).not.toHaveBeenCalled();
  // ...and the page itself is not re-read: the name and cover stand.
  expect(screen.getByText("Drowned Realm")).toBeInTheDocument();
});

test("a click from a section with no count asks for nothing", async () => {
  renderAtUrl("/worlds/w");
  await screen.findByRole("heading", { name: "Overview" });
  forgetCalls();
  fireEvent.click(indexRow("Groups"));
  await screen.findByRole("heading", { name: "Groups" });
  expect(api.getWorld).not.toHaveBeenCalled();
  expect(api.listTags).not.toHaveBeenCalled();
});

test("leaving Tags recounts the vocabulary, which the world read does not carry", async () => {
  (api.listTags as any).mockResolvedValue({ tide: "Tide" });
  renderAtUrl("/worlds/w/tags");
  await waitFor(() => expect(indexRow("Tags")).toHaveTextContent("1"));
  (api.listTags as any).mockResolvedValue({ tide: "Tide", dusk: "Dusk" });
  fireEvent.click(indexRow("Items"));
  await waitFor(() => expect(indexRow("Tags")).toHaveTextContent("2"));
  expect(screen.getByText("2 tags · 2 campaigns")).toBeInTheDocument();
});

test("a campaign's copy re-lists only the rows a section change could have moved", async () => {
  // The campaign shape keeps its list counts -- they are overlay unions the
  // world's tallies know nothing about -- but a click re-lists the section
  // being left and the one being entered (a reclassify lands there, and that
  // read is the one its editor is making anyway), not all eight.
  renderCampaignAtUrl("/campaigns/c1/world/items");
  await screen.findByRole("heading", { name: "Items" });
  forgetCalls();
  fireEvent.click(indexRow("Creatures"));
  await screen.findByRole("heading", { name: "Creatures" });
  await waitFor(() => expect(api.listEntities).toHaveBeenCalledWith(
    { kind: "campaign", id: "c1" }, "items"));
  const kinds = (api.listEntities as any).mock.calls.map((c: unknown[]) => c[1]);
  expect(new Set(kinds)).toEqual(new Set(["items", "creatures"]));
  expect(api.listCharacters).not.toHaveBeenCalled();
  expect(api.listPCs).not.toHaveBeenCalled();
  expect(api.listGreetings).not.toHaveBeenCalled();
});

test("a campaign is redirected before its world id has arrived", async () => {
  // getCampaign never settles: the redirect must not be waiting on it.
  (api.getCampaign as any).mockReturnValue(new Promise(() => {}));
  renderCampaignAtUrl("/campaigns/c/world");
  await waitFor(() => expect(lastPath).toBe("/campaigns/c/world/characters"));
});

// ---- the world read's campaign count ----

test("the world shape counts its campaigns off the world read, not the campaign shelf", async () => {
  // It used to fetch every campaign's shelf row -- a scene listing each -- to
  // filter down to this world's; the world read carries the number now.
  (api.getWorld as any).mockResolvedValue({ ...worldWithCounts({}), campaigns: 5 });
  renderAtUrl("/worlds/w/characters");
  await screen.findByText(/5 campaigns/);
  expect(indexRow("Push to campaigns")).toHaveTextContent("5");
  expect(api.listCampaigns).not.toHaveBeenCalled();
});

test("a campaign count the world read could not give is a dash, never a zero", async () => {
  (api.getWorld as any).mockResolvedValue({ ...worldWithCounts({}), campaigns: null });
  renderAtUrl("/worlds/w/characters");
  await screen.findByText("Drowned Realm");
  expect(screen.getByText(/— campaigns/)).toBeInTheDocument();
  expect(indexRow("Push to campaigns")).toHaveTextContent("—");
  expect(indexRow("Push to campaigns")).not.toHaveTextContent("0");
});

test("a server older than the campaign count reads as unknown too", async () => {
  const { campaigns: _omitted, ...older } = worldWithCounts({});
  (api.getWorld as any).mockResolvedValue(older);
  renderAtUrl("/worlds/w/characters");
  await screen.findByText("Drowned Realm");
  expect(screen.getByText(/— campaigns/)).toBeInTheDocument();
});

// ---- remembered header ----

test("a revisit paints the header and its numbers before the world read lands", async () => {
  (api.rememberedWorld as any).mockImplementation((wid: string) =>
    (wid === "w" ? { ...worldWithCounts({ characters: 4 }), campaigns: 3 } : undefined));
  (api.getWorld as any).mockReturnValue(new Promise(() => {}));
  renderAtUrl("/worlds/w/characters");
  // Before any await: nothing has been read, and the page already says what
  // it last said about this world.
  expect(screen.getAllByText("Drowned Realm").length).toBeGreaterThan(0);
  expect(indexRow("Characters")).toHaveTextContent("4");
  expect(indexRow("Push to campaigns")).toHaveTextContent("3");
  expect(api.getWorld).toHaveBeenCalledWith("w");
  await screen.findByRole("heading", { name: "Characters" });
});

test("a world with nothing remembered opens on dashes, not on another world's numbers", async () => {
  (api.rememberedWorld as any).mockImplementation((wid: string) =>
    (wid === "other" ? { ...worldWithCounts({ characters: 9 }), campaigns: 7 } : undefined));
  (api.getWorld as any).mockReturnValue(new Promise(() => {}));
  renderAtUrl("/worlds/w/characters");
  expect(indexRow("Characters")).toHaveTextContent("—");
  expect(indexRow("Push to campaigns")).toHaveTextContent("—");
  expect(api.rememberedWorld).not.toHaveBeenCalledWith("other");
  await screen.findByRole("heading", { name: "Characters" });
});

// ---- deferred module reads ----

const SHEETED = { modules: ["pool-basic"], default: "pool-basic" };

test("the roster asks for the module only once its list is in -- and still gets it", async () => {
  // On a single-threaded server every read started beside the character list
  // is time the list waits behind, and the grid spends the module on one
  // button.
  (api.getWorldSheetsIndex as any).mockResolvedValue(SHEETED);
  let land: (rows: unknown) => void = () => {};
  (api.listCharacters as any).mockReturnValue(new Promise((r) => { land = r; }));
  renderAtUrl("/worlds/w/characters");
  await screen.findByText("Drowned Realm");
  expect(api.getWorldSheetsIndex).not.toHaveBeenCalled();
  expect(api.listModules).not.toHaveBeenCalled();
  expect(api.readModule).not.toHaveBeenCalled();
  await act(async () => {
    land([{ id: "mira", name: "Mira", default_version: "main", versions: [{ id: "main", name: "main" }] }]);
  });
  await screen.findByText("Mira");
  await waitFor(() => expect(api.readModule).toHaveBeenCalledWith("pool-basic"));
  expect(await screen.findByRole("button", { name: /New character with sheet/ })).toBeInTheDocument();
});

test("a section that takes the module asks for it on arrival", async () => {
  (api.getWorldSheetsIndex as any).mockResolvedValue(SHEETED);
  renderAtUrl("/worlds/w/pcs");
  expect(await screen.findByRole("button", { name: /New PC with sheet/ })).toBeInTheDocument();
  expect(api.getWorldSheetsIndex).toHaveBeenCalledWith("w");
});

test("a section that takes no module never reads one", async () => {
  renderAtUrl("/worlds/w/greetings");
  await screen.findByRole("heading", { name: "Greetings" });
  expect(api.getWorldSheetsIndex).not.toHaveBeenCalled();
  expect(api.listModules).not.toHaveBeenCalled();
  expect(api.readModule).not.toHaveBeenCalled();
});

test("moving from a section without the module to one with it asks then", async () => {
  (api.getWorldSheetsIndex as any).mockResolvedValue(SHEETED);
  renderAtUrl("/worlds/w/greetings");
  await screen.findByRole("heading", { name: "Greetings" });
  fireEvent.click(indexRow("PCs"));
  expect(await screen.findByRole("button", { name: /New PC with sheet/ })).toBeInTheDocument();
});

test("a campaign's roster defers its module the same way", async () => {
  (api.getCampaignModule as any).mockResolvedValue(
    { setting: "pool-basic", resolved: "pool-basic", source: "campaign" });
  let land: (rows: unknown) => void = () => {};
  (api.listCharacters as any).mockReturnValue(new Promise((r) => { land = r; }));
  renderCampaignAtUrl("/campaigns/c1/world/characters");
  await screen.findByText(/World Copy/);
  expect(api.getCampaignModule).not.toHaveBeenCalled();
  await act(async () => { land([]); });
  await waitFor(() => expect(api.readModule).toHaveBeenCalledWith("pool-basic"));
});

// ---- intent prefetch ----

test("pressing the Characters row starts the roster's read before the click lands", async () => {
  // Tags, because it is a section that lists no characters of its own.
  renderAtUrl("/worlds/w/tags");
  await screen.findByRole("heading", { name: "Tags" });
  expect(api.listCharacters).not.toHaveBeenCalled();
  fireEvent.pointerDown(indexRow("Characters"));
  await waitFor(() => expect(api.listCharacters).toHaveBeenCalledWith({ kind: "world", id: "w" }));
});

test("the Characters row prefetches nothing while it is the page already open", async () => {
  renderAtUrl("/worlds/w/characters");
  await screen.findByRole("heading", { name: "Characters" });
  const before = (api.listCharacters as any).mock.calls.length;
  fireEvent.pointerDown(indexRow("Characters"));
  await screen.findByRole("heading", { name: "Characters" });
  expect((api.listCharacters as any).mock.calls.length).toBe(before);
});
