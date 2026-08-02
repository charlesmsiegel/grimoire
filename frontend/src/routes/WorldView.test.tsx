import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import WorldView from "./WorldView";

vi.mock("../api/client", () => ({
  ENTITY_FIELDS: {
    locations: [], lore: [],
    items: [{ key: "item_type", label: "Type" }, { key: "rarity", label: "Rarity" }],
    groups: [{ key: "group_type", label: "Type" }],
    creatures: [{ key: "creature_type", label: "Type" }, { key: "threat", label: "Threat" }],
  },
  api: {
    getWorld: vi.fn(),
    getCampaign: vi.fn(),
    listCharacters: vi.fn(),
    listPCs: vi.fn(),
    listTags: vi.fn(),
    listEntities: vi.fn(),
    listGreetings: vi.fn(),
    readCharacter: vi.fn(),
    getCharacterTagline: vi.fn(), getCharacterVoiceAnchor: vi.fn(),
    listImageAppearances: vi.fn(),
    readGreeting: vi.fn(),
    getGreetingSubjects: vi.fn(),
    listUntaggedImages: vi.fn(),
    imageUrl: (w: string, c: string, v: string, n: string) => `/img/${w}/${c}/${v}/${n}`,
    actorImageUrl: (sc: { id: string }, c: string, v: string, n: string) => `/img/${sc.id}/${c}/${v}/${n}`,
    listAppearances: vi.fn(), markGreeting: vi.fn(), pickVersion: vi.fn(), importVersion: vi.fn(),
    listModules: vi.fn(), setWorldModule: vi.fn(),
    getCampaignModule: vi.fn(), readModule: vi.fn(), getWorldSheetsIndex: vi.fn(), getSheet: vi.fn(),
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
  (api.listCharacters as any).mockResolvedValue([]);
  (api.listPCs as any).mockResolvedValue([]);
  (api.listTags as any).mockResolvedValue({});
  (api.listEntities as any).mockResolvedValue([]);
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
  (api.listModules as any).mockResolvedValue([]);
  (api.setWorldModule as any).mockResolvedValue({ ok: true });
  (api.getCampaignModule as any).mockResolvedValue({ setting: "", resolved: null, source: null });
  (api.readModule as any).mockResolvedValue(POOL_BASIC);
  (api.getWorldSheetsIndex as any).mockResolvedValue({ modules: [], default: "" });
  (api.getSheet as any).mockResolvedValue({ sheet: null });
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

test("shows the world name and defaults to the Overview tab", async () => {
  renderAt();
  await screen.findByText("Drowned Realm");
  expect(screen.getByRole("button", { name: "Overview" })).toHaveClass("active");
});

test("switching to the Characters tab renders the character editor", async () => {
  renderAt();
  await screen.findByText("Drowned Realm");
  fireEvent.click(screen.getByRole("button", { name: "Characters" }));
  await waitFor(() => expect(api.listCharacters).toHaveBeenCalledWith({ kind: "world", id: "w" }));
  expect(screen.getByRole("button", { name: /new character/i })).toBeInTheDocument();
});

test("switching to the PCs tab renders the PC editor", async () => {
  renderAt();
  await screen.findByText("Drowned Realm");
  fireEvent.click(screen.getByRole("button", { name: "PCs" }));
  await waitFor(() => expect(api.listPCs).toHaveBeenCalledWith({ kind: "world", id: "w" }));
  expect(screen.getByRole("button", { name: /new pc/i })).toBeInTheDocument();
});

test("world-copy mode shows the fork banner, campaign back link, and campaign entity scope", async () => {
  render(
    <MemoryRouter initialEntries={["/campaigns/c1/world"]}>
      <Routes>
        <Route path="/campaigns/:cid/world" element={<WorldView campaign />} />
      </Routes>
    </MemoryRouter>,
  );
  await screen.findByText(/ashes of the verdigris crown \/ world copy/i);
  expect(screen.getByText(/campaign view/i)).toBeInTheDocument();
  // entity tabs read from the campaign fork, not the source world
  fireEvent.click(screen.getByRole("button", { name: "Locations" }));
  await waitFor(() =>
    expect(api.listEntities).toHaveBeenCalledWith({ kind: "campaign", id: "c1" }, "locations"));
});

test("the Lore tab hosts the lorebook importer", async () => {
  renderAt();
  await screen.findByText("Drowned Realm");
  fireEvent.click(screen.getByRole("button", { name: "Lore" }));
  fireEvent.click(screen.getByText(/import lorebook/i)); // expand the details
  expect(screen.getByRole("button", { name: /parse/i })).toBeInTheDocument();
});

test("openGreeting switches to the greetings tab and focuses the greeting", async () => {
  (api.listCharacters as any).mockResolvedValue([
    { id: "mira", name: "Mira", default_version: "main", versions: [{ id: "main", name: "main" }] },
  ]);
  (api.listGreetings as any).mockResolvedValue([
    { id: "sol-2", name: "SoL 2", character: "other", version: "main", present: ["mira"], requires_tags: [], predecessor_join: "all" },
  ]);
  renderAt();
  await screen.findByText("Drowned Realm");
  fireEvent.click(screen.getByRole("button", { name: "Characters" }));
  fireEvent.click(await screen.findByText("Mira"));               // grid -> detail
  const wg = await screen.findByText("World greetings");
  fireEvent.click(within(wg.parentElement as HTMLElement).getByText("SoL 2"));
  await waitFor(() => expect(api.readGreeting).toHaveBeenCalledWith({ kind: "world", id: "w" }, "sol-2"));
  expect(screen.getByRole("button", { name: "Greetings" })).toHaveClass("active");
});


test("campaign mode passes campaign scope and hides the Tags tab", async () => {
  (api.listAppearances as any).mockResolvedValue([]);
  render(
    <MemoryRouter initialEntries={["/campaigns/c1/world"]}>
      <Routes><Route path="/campaigns/:cid/world" element={<WorldView campaign />} /></Routes>
    </MemoryRouter>,
  );
  await screen.findByText(/World Copy/);
  await waitFor(() => expect(api.listCharacters).toHaveBeenCalledWith({ kind: "campaign", id: "c1" }));
  expect(screen.queryByRole("button", { name: "Tags" })).toBeNull();
  expect(screen.getByRole("button", { name: "Greetings" })).toBeInTheDocument();
});

test("campaign path resolves module context and threads it into the character editor's Sheet section", async () => {
  (api.getCampaignModule as any).mockResolvedValue({ setting: "pool-basic", resolved: "pool-basic", source: "campaign" });
  (api.readModule as any).mockResolvedValue(POOL_BASIC);
  (api.listCharacters as any).mockResolvedValue([
    { id: "mira", name: "Mira", default_version: "main", versions: [{ id: "main", name: "main" }] },
  ]);
  render(
    <MemoryRouter initialEntries={["/campaigns/c1/world"]}>
      <Routes><Route path="/campaigns/:cid/world" element={<WorldView campaign />} /></Routes>
    </MemoryRouter>,
  );
  await screen.findByText(/World Copy/);
  await waitFor(() => expect(api.getCampaignModule).toHaveBeenCalledWith("c1"));
  await waitFor(() => expect(api.readModule).toHaveBeenCalledWith("pool-basic"));
  fireEvent.click(await screen.findByText("Mira"));
  await screen.findByText("Sheet");
});
