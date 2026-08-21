import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import WorldView from "./WorldView";
import { ShellStatusProvider, useShellStatus } from "../components/ShellStatus";

vi.mock("../api/client", () => ({
  SECRECY_LEVELS: ["public", "secret", "gm-only"],
  SECRECY_LABELS: { public: "Public", secret: "Secret", "gm-only": "GM-only" },
  ENTITY_FIELDS: {
    locations: [], lore: [],
    items: [{ key: "item_type", label: "Type" }, { key: "rarity", label: "Rarity" }],
    groups: [{ key: "group_type", label: "Type" }],
    creatures: [{ key: "creature_type", label: "Type" }, { key: "threat", label: "Threat" }],
  },
  api: {
    getWorld: vi.fn(),
    getCampaign: vi.fn(),
    listCampaigns: vi.fn(),
    listCharacters: vi.fn(),
    listUndescribedImages: vi.fn(),
    listPCs: vi.fn(),
    listTags: vi.fn(),
    listEntities: vi.fn(),
    readEntity: vi.fn(),
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
    worldCampaigns: vi.fn(),
    // Both import panels live on this page and ask what a row may be filed
    // under (#138). Declared rather than left off: the hook falls back to the
    // build's own kinds when the read fails, so an absent mock would pass for
    // the wrong reason.
    entityKinds: vi.fn(async () => ({
      kinds: ["locations", "lore", "items", "groups", "creatures"],
    })),
  },
}));
import { api } from "../api/client";
import type { ModuleDetail } from "../api/client";

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
  (api.getWorld as any).mockResolvedValue({ meta: { id: "w", name: "Drowned Realm" }, body: "", counts: {} });
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

function renderAt() {
  render(
    <MemoryRouter initialEntries={["/worlds/w"]}>
      <Routes>
        <Route path="/worlds/:wid" element={<WorldView />} />
      </Routes>
    </MemoryRouter>,
  );
}

function renderAtUrl(url: string) {
  render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path="/worlds/:wid" element={<WorldView />} />
      </Routes>
    </MemoryRouter>,
  );
}

function renderCampaign() {
  render(
    <MemoryRouter initialEntries={["/campaigns/c1/world"]}>
      <Routes><Route path="/campaigns/:cid/world" element={<WorldView campaign />} /></Routes>
    </MemoryRouter>,
  );
}

/** The column's row for a section. Its accessible name is the label and its
 *  count, so every lookup here is a prefix match rather than an exact one. */
function indexRow(label: string) {
  return screen.getByRole("button", { name: new RegExp(`^${label}\\b`) });
}

test("shows the world name and opens on the Overview", async () => {
  renderAt();
  await screen.findByText("Drowned Realm");
  expect(indexRow("Overview")).toHaveClass("active");
  expect(screen.getByRole("heading", { name: "Overview" })).toBeInTheDocument();
});

test("the index is grouped who / where & what / writing, each row counted", async () => {
  (api.listCharacters as any).mockResolvedValue([
    { id: "mira", name: "Mira", default_version: "main", versions: [{ id: "main", name: "main" }] },
    { id: "aud", name: "Aud", default_version: "main", versions: [{ id: "main", name: "main" }] },
  ]);
  (api.listEntities as any).mockImplementation((_scope: unknown, kind: string) =>
    Promise.resolve(kind === "locations" ? [{ id: "the-wall", name: "The Wall" }] : []));
  (api.listTags as any).mockResolvedValue({ tide: "Tide", dusk: "Dusk", salt: "Salt" });
  renderAt();
  await screen.findByText("Drowned Realm");

  for (const label of ["Who", "Where & what", "Writing"]) {
    expect(screen.getByText(label)).toBeInTheDocument();
  }
  // A live count, not the world's stored one: it is the same read the section's
  // own editor makes, so both shapes of the route can use it.
  await waitFor(() => expect(indexRow("Characters")).toHaveTextContent("2"));
  expect(indexRow("Locations")).toHaveTextContent("1");
  expect(indexRow("Items")).toHaveTextContent("0");
  expect(indexRow("Tags")).toHaveTextContent("3");
  // ...and the facts a world has that are not records in it
  expect(screen.getByText("3 tags · 2 campaigns")).toBeInTheDocument();
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
  (api.listCharacters as any).mockResolvedValue([
    { id: "mira", name: "Mira", default_version: "main", versions: [{ id: "main", name: "main" }] },
  ]);
  renderAt();
  await screen.findByText("Drowned Realm");
  fireEvent.click(indexRow("Characters"));
  fireEvent.click(await screen.findByText("Mira"));               // grid -> detail
  await waitFor(() => expect(api.readCharacter).toHaveBeenCalled());

  expect(indexRow("Characters")).toHaveClass("active");
  expect(screen.getByRole("heading", { name: "Characters" })).toBeInTheDocument();
  expect(indexRow("Lore")).toBeInTheDocument();
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

test("openGreeting switches to Greetings and focuses the greeting", async () => {
  (api.listCharacters as any).mockResolvedValue([
    { id: "mira", name: "Mira", default_version: "main", versions: [{ id: "main", name: "main" }] },
  ]);
  (api.listGreetings as any).mockResolvedValue([
    { id: "sol-2", name: "SoL 2", character: "other", version: "main", present: ["mira"], requires_tags: [], predecessor_join: "all" },
  ]);
  renderAt();
  await screen.findByText("Drowned Realm");
  fireEvent.click(indexRow("Characters"));
  fireEvent.click(await screen.findByText("Mira"));               // grid -> detail
  const wg = await screen.findByText("World greetings");
  fireEvent.click(within(wg.parentElement as HTMLElement).getByText("SoL 2"));
  await waitFor(() => expect(api.readGreeting).toHaveBeenCalledWith({ kind: "world", id: "w" }, "sol-2"));
  expect(indexRow("Greetings")).toHaveClass("active");
});

test("campaign mode passes campaign scope and hides Tags and the Overview", async () => {
  (api.listAppearances as any).mockResolvedValue([]);
  renderCampaign();
  await screen.findByText(/World Copy/);
  await waitFor(() => expect(api.listCharacters).toHaveBeenCalledWith({ kind: "campaign", id: "c1" }));
  expect(screen.queryByRole("button", { name: /^Tags\b/ })).toBeNull();
  expect(screen.queryByRole("button", { name: /^Overview\b/ })).toBeNull();
  // a campaign has no tag vocabulary of its own, so nothing asks for one
  expect(api.listTags).not.toHaveBeenCalled();
  expect(indexRow("Greetings")).toBeInTheDocument();
  // the fork's way back to what it forked from
  expect(screen.getByRole("link", { name: /source world/i })).toHaveAttribute("href", "/worlds/w");
});

test("campaign path resolves module context and threads it into the character editor's Sheet section", async () => {
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
  fireEvent.click(await screen.findByText("Mira"));
  await screen.findByText("Sheet");
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
          <Route path="/campaigns/:cid/world" element={<WorldView campaign />} />
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
        <Routes><Route path="/worlds/:wid" element={<WorldView />} /></Routes>
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

test("?section=characters&v= opens that character's version", async () => {
  (api.listCharacters as any).mockResolvedValue([{ id: "mira", name: "Mira", versions: 1 }]);
  renderAtUrl("/worlds/w?section=characters&id=mira&v=main");
  await waitFor(() => expect(api.readCharacter).toHaveBeenCalledWith(
    expect.objectContaining({ id: "w" }), "mira"));
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
  expect(screen.queryByRole("button", { name: /^Push to campaigns/ })).not.toBeInTheDocument();
});
