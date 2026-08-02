import { StrictMode } from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import CampaignView from "./CampaignView";
import type { ChatEvent } from "../api/stream";

// CastPanel, NewSceneChooser, and CalendarConfig have their own tests + make their own
// API calls; stub them here.
vi.mock("../components/CastPanel", () => ({
  CastPanel: ({ initialPrompt, onSceneRenamed }: any) => (
    <div data-testid="cast-panel">
      {initialPrompt ?? ""}
      <button onClick={() => onSceneRenamed?.("s10")}>stub-datestamp</button>
    </div>
  ),
}));
vi.mock("../components/NewSceneChooser", () => ({
  NewSceneChooser: ({ onCreated, onClose }: any) => (
    <div data-testid="scene-chooser">
      <button onClick={() => onCreated("s9", "A premise")}>stub-pick</button>
      <button onClick={() => onClose()}>stub-close</button>
    </div>
  ),
}));
vi.mock("../components/CalendarConfig", () => ({ CalendarConfig: () => <div data-testid="calendar-config" /> }));
vi.mock("../components/ResponsePresetPicker", () => ({ ResponsePresetPicker: () => <div data-testid="response-preset-picker" /> }));

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    api: {
      getCampaign: vi.fn(),
      getWorld: vi.fn(),
      listScenes: vi.fn(),
      getScene: vi.fn(),
      createScene: vi.fn(),
      renameScene: vi.fn(),
      deleteScene: vi.fn(),
      chat: vi.fn(),
      retry: vi.fn(),
      regenerate: vi.fn(),
      roll: vi.fn(),
      getRollProposal: vi.fn(), resolveProposal: vi.fn(),
      getSceneChecks: vi.fn(), rollCheck: vi.fn(),
      getConfig: vi.fn(),
      editMessage: vi.fn(),
      absorbScene: vi.fn(), saveChronicle: vi.fn(), getChronicle: vi.fn(), retryAudit: vi.fn(),
      // consumed by the embedded SceneInspector
      getCast: vi.fn(), getSceneLocation: vi.fn(), getSceneContext: vi.fn(),
      // Resolves to "no weather" so the widget renders nothing: these suites
      // assert on the rest of the inspector, not the sky.
      getSceneWeather: vi.fn(() => Promise.resolve({ weather: null, location: null, native: null })),
      getCastDetail: vi.fn(), readEntity: vi.fn(),
      addToCast: vi.fn(), removeFromCast: vi.fn(),
      getCalendarConfig: vi.fn(), setCalendarConfig: vi.fn(), getCalendarProviders: vi.fn(),
      getSceneDatetime: vi.fn(), setSceneDatetime: vi.fn(), getCalendarMonths: vi.fn(),
      listStyles: vi.fn(),
      listResponsePresets: vi.fn(), getSceneResponse: vi.fn(),
      listCharacters: vi.fn(), listPCs: vi.fn(), listCampaignPCs: vi.fn(),
      campaignChanges: vi.fn(),
      listAppearances: vi.fn(), listEntityImages: vi.fn(), listEntities: vi.fn(),
      campaignImageUrl: (_c: string, char: string, v: string, n: string) => `/img/${char}/${v}/${n}`,
      entityImageUrl: () => "/loc-img",
    },
  };
});
vi.mock("../api/models", () => ({ getModels: vi.fn() }));
import { api, ApiError } from "../api/client";
import { getModels } from "../api/models";
import { LOCKED_WHILE_GENERATING } from "../components/sceneLock";

const ONE_SCENE = [{ id: "s1", title: "Old", model: "", created: "", updated: "" }];
// Stand-in `phases` for the absorb mocks that are about something else. What
// every one of them relies on is the single property named here: no phase was
// cut short by the time budget, so no budget notice renders.
const PHASES_NONE_CUT = [
  { name: "extraction", status: "ok", reason: null, attempted: true, budget_exhausted: false },
  { name: "dossiers", status: "ok", reason: null, attempted: true, budget_exhausted: false },
  { name: "audit", status: "ok", reason: null, attempted: true, budget_exhausted: false },
];

// The built-ins response_presets.py ships (templates/response_presets/*.md) —
// the chip's dropdown lists whatever listResponsePresets returns.
const RESPONSE_PRESETS = [
  { id: "standard", name: "Standard", built_in: true },
  { id: "brisk", name: "Brisk", built_in: true },
  { id: "cinematic", name: "Cinematic", built_in: true },
  { id: "terse", name: "Terse", built_in: true },
];

// What GET /api/campaigns/:cid/scenes/:sid/response returns: the scene's own
// (here: empty) fields plus the SERVER-resolved bundle and its provenance.
const RESPONSE_BUNDLE = {
  response_preset: "", style_id: "",
  length_reply_words: "", length_blocks: "", length_paragraphs: "",
  length_speakers: "", length_blocks_per_speaker: "",
  effective: { style_id: "", reply_words: 550, blocks: 5, paragraphs: 2, speakers: 4, blocks_per_speaker: 2 },
  provenance: { reply_words: { scope: "default", source: "default" } },
};

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
  (api.getCampaign as any).mockResolvedValue({ meta: { id: "run", name: "Run One", world: "w", world_name: "Saltmarch" }, body: "" });
  (api.getWorld as any).mockResolvedValue({ meta: { id: "w", name: "Saltmarch" }, body: "", counts: {} });
  (api.listScenes as any).mockResolvedValue([]);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [] });
  (api.createScene as any).mockResolvedValue({ id: "s1" });
  (api.renameScene as any).mockResolvedValue({ id: "s1", title: "New" });
  (api.deleteScene as any).mockResolvedValue({ ok: true });
  // Every streaming route ends a successful turn with a `done` frame — that is
  // how the client knows the backend finalized and persisted, rather than the
  // body merely reaching EOF. A default that resolved silently modelled a
  // truncated stream, so these mocks now send it.
  const streamsDone = async (...args: unknown[]) => {
    (args.find((a) => typeof a === "function") as ((e: ChatEvent) => void) | undefined)?.(
      { done: true });
  };
  (api.chat as any).mockImplementation(streamsDone);
  (api.retry as any).mockImplementation(streamsDone);
  (api.regenerate as any).mockImplementation(streamsDone);
  (api.getRollProposal as any).mockResolvedValue({ record: null });
  (api.resolveProposal as any).mockImplementation(streamsDone);
  (api.getSceneChecks as any).mockResolvedValue({ actors: [] });
  (api.rollCheck as any).mockResolvedValue({ ok: true, resolution: {}, message: "" });
  (api.getConfig as any).mockResolvedValue({ theme: "codex", system_prompt: "", quote_color: "off", user_label: "You", assistant_label: "Grimoire", active_connection_id: "openrouter", active_connection: { id: "openrouter", kind: "openrouter", name: "OpenRouter" }, ready: true });
  (api.editMessage as any).mockResolvedValue({ ok: true });
  (api.getCast as any).mockResolvedValue([]);
  (api.addToCast as any).mockResolvedValue({ ok: true });
  (api.removeFromCast as any).mockResolvedValue({ ok: true });
  (api.getSceneLocation as any).mockResolvedValue({ current: null, visited: [] });
  (api.getSceneContext as any).mockResolvedValue({ model: "m", total_tokens: 0,
    dropped_tokens: 0, budget_tokens: 0, sections: [] });
  (api.getCalendarConfig as any).mockResolvedValue({
    primary: { provider: "gregorian", region: "US", custom_holidays: [], anchor: null },
    secondary: null, confirmed: true });
  (api.getCalendarProviders as any).mockResolvedValue({ providers: [
    { id: "gregorian", name: "Gregorian" }, { id: "hebrew", name: "Hebrew" },
  ] });
  (api.getSceneDatetime as any).mockResolvedValue({ current: null, history: [] });
  (api.listStyles as any).mockResolvedValue([]);
  (api.getSceneResponse as any).mockResolvedValue(RESPONSE_BUNDLE);
  (api.listCharacters as any).mockResolvedValue([]);
  (api.listPCs as any).mockResolvedValue([]);
  (api.listCampaignPCs as any).mockResolvedValue([]);
  (api.listAppearances as any).mockResolvedValue([]);
  (api.listEntityImages as any).mockResolvedValue([]);
  (api.listEntities as any).mockResolvedValue([]);
  (getModels as any).mockResolvedValue([]);
  (api.absorbScene as any).mockResolvedValue({
    one_line: "They met.", summary: "A met B.", keywords: ["salt"],
    timeline_events: [], cast: [], location: "", date: "",
    mechanics: { status: "ok", reason: null, warnings: [], dropped: [] },
    commit_token: "tok",
    dossiers: { status: "skipped", reason: null, proposed: [], failed: [], skipped: [] },
    phases: PHASES_NONE_CUT,
    edits: [{ id: "character_state:seraphine", kind: "character_state",
      target: { kind: "characters", id: "seraphine" }, label: "Seraphine — current state",
      field: "current_state", before: "Wary.", after: "Loyal now.", authored: false }] });
  (api.saveChronicle as any).mockResolvedValue({ id: "s1", one_line: "They met.",
    summary: "A met B.", keywords: ["salt"], cast: [], location: "", date: "", absorbed: "t",
    applied: [], failures: [] });
  (api.getChronicle as any).mockResolvedValue([]);
  (api.campaignChanges as any).mockResolvedValue([]);
  (api.listResponsePresets as any).mockResolvedValue([]);
});

function renderCampaign() {
  return render(
    <MemoryRouter initialEntries={["/campaigns/run"]}>
      <Routes>
        <Route path="/campaigns/:cid" element={<CampaignView ready={true} />} />
      </Routes>
    </MemoryRouter>,
  );
}

test("shows the sub-header with world-copy link, scene counter, and rail date", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getSceneDatetime as any).mockResolvedValue({
    current: { native: "2026-07-03", friendly: "3 July 2026", weekday: "Friday",
               secondary_friendly: null, holidays_today: ["Independence Day"], upcoming: null, cast: [] },
    history: [],
  });
  renderCampaign();
  await screen.findByText(/‹ Campaigns/i);
  expect(screen.getByRole("link", { name: /world ▸ saltmarch/i })).toHaveAttribute("href", "/campaigns/run/world");
  expect(screen.getByText(/scenes \/ 01/i)).toBeInTheDocument();
  await screen.findByText(/Friday 3 July 2026/i);
  expect(screen.getByText(/✦ Independence Day/i)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /campaign world/i })).toBeInTheDocument();
});

test("scene rail numbers by the id's own number, not list position", async () => {
  // listScenes is sorted by `updated` descending — an earlier scene edited
  // most recently sorts first, which must not desync the displayed number
  // from the scene's actual story position (its id's leading number).
  (api.listScenes as any).mockResolvedValue([
    { id: "003--2024-09-10--day-two", title: "Day Two", model: "", created: "", updated: "2026-07-07T00:49:35Z" },
    { id: "036--2024-09-24--froot-loops", title: "Froot Loops", model: "", created: "", updated: "2026-07-06T23:26:21Z" },
  ]);
  renderCampaign();
  await screen.findByText(/‹ Campaigns/i);
  expect(screen.getByText("03 · Day Two")).toBeInTheDocument();
  expect(screen.getByText("36 · Froot Loops")).toBeInTheDocument();
});

test("scene rail is sortable by last updated, scene date, or order", async () => {
  (api.listScenes as any).mockResolvedValue([
    // API order is "updated" desc, deliberately unrelated to date or id order.
    { id: "003--2024-09-24--day-two", title: "Day Two", model: "", created: "", updated: "3", date: "2024-09-24" },
    { id: "036--2024-09-10--froot-loops", title: "Froot Loops", model: "", created: "", updated: "2", date: "2024-09-10" },
    { id: "010--2024-09-15--undated", title: "Undated", model: "", created: "", updated: "1", date: "" },
  ]);
  renderCampaign();
  await screen.findByText(/‹ Campaigns/i);

  const rowOrder = () => Array.from(document.querySelectorAll(".rail-scenes .row-name")).map((el) => el.textContent);

  // default: "updated" — API order preserved (most-recently-edited first).
  expect(rowOrder()).toEqual(["03 · Day Two", "36 · Froot Loops", "10 · Undated"]);

  // "date" — latest scene date first; undated scenes still sort last.
  fireEvent.change(screen.getByLabelText(/sort scenes by/i), { target: { value: "date" } });
  expect(rowOrder()).toEqual(["03 · Day Two", "36 · Froot Loops", "10 · Undated"]);

  // "order" — the scene id's own leading number, descending.
  fireEvent.change(screen.getByLabelText(/sort scenes by/i), { target: { value: "order" } });
  expect(rowOrder()).toEqual(["36 · Froot Loops", "10 · Undated", "03 · Day Two"]);
});

test("groups consecutive posts under one speaker plate", async () => {
  (api.getConfig as any).mockResolvedValue({
    theme: "codex", system_prompt: "", quote_color: "off", user_label: "Kestrel", assistant_label: "Grimoire",
    active_connection_id: "openrouter",
    active_connection: { id: "openrouter", kind: "openrouter", name: "OpenRouter" }, ready: true,
  });
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({
    meta: { id: "s1", title: "Old" },
    messages: [
      { role: "user", content: "I open the door." },
      { role: "assistant", content: "She waits.", speaker: "Seraphine Vale" },
      { role: "assistant", content: "She smiles.", speaker: "Seraphine Vale" },
    ],
  });
  renderCampaign();
  await screen.findByText("Kestrel");
  // one plate for the two-message Seraphine run
  expect(screen.getAllByText("Seraphine Vale")).toHaveLength(1);
  expect(document.querySelectorAll(".plate")).toHaveLength(2);
  expect(document.querySelector(".spine")).toBeNull();
  // initials fallback (no cast/roster mocked): first letters of first two words
  expect(screen.getByText("SV")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Old" })).toBeInTheDocument();
});

test("plates mark PC speakers and show avatars from the roster", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getCast as any).mockResolvedValue([
    { kind: "characters", id: "seraphine", role: "npc", name: "Seraphine Vale" },
    { kind: "pcs", id: "yara", role: "player", name: "Yara" },
  ]);
  (api.listAppearances as any).mockResolvedValue([
    { kind: "characters", id: "seraphine", version: "v1", role: "npc", scenes: ["s1"] },
  ]);
  (api.getScene as any).mockResolvedValue({
    meta: { id: "s1", title: "Old" },
    messages: [
      { role: "user", content: "Hello.", speaker: "Yara" },
      { role: "assistant", content: "She waits.", speaker: "Seraphine Vale" },
    ],
  });
  renderCampaign();
  await screen.findByText("Seraphine Vale");
  expect(document.querySelector(".plate.pc")).not.toBeNull();          // Yara run
  expect(screen.getByText("pc")).toBeInTheDocument();
  expect(screen.getAllByText("npc").length).toBeGreaterThan(0);
  expect(screen.getByAltText("Seraphine Vale portrait")).toBeInTheDocument();
});

test("clicking a plate name opens the record drawer", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getCast as any).mockResolvedValue([
    { kind: "characters", id: "seraphine", role: "npc", name: "Seraphine Vale" },
  ]);
  (api.getScene as any).mockResolvedValue({
    meta: { id: "s1", title: "Old" },
    messages: [{ role: "assistant", content: "She waits.", speaker: "Seraphine Vale" }],
  });
  (api.getCastDetail as any).mockResolvedValue({
    kind: "characters", id: "seraphine", name: "Seraphine Vale", version: "v1", body: "keeper" });
  renderCampaign();
  fireEvent.click(await screen.findByRole("button", { name: "Seraphine Vale" }));
  await screen.findByText("keeper");
});

test("shows the campaign name and loads its scenes", async () => {
  renderCampaign();
  await screen.findByText("Run One");
  await waitFor(() => expect(api.listScenes).toHaveBeenCalledWith("run"));
});

test("renders the inspector for an active scene", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  await screen.findByText(/Active characters/i);
  await screen.findByText(/^Context/);
});

test("hides Cast & scene setup once the scene has messages", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [{ role: "assistant", content: "hi" }] });
  renderCampaign();
  await screen.findByText("hi");
  expect(screen.queryByTestId("cast-panel")).toBeNull();
});

test("shows Cast & scene setup for an empty scene", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [] });
  renderCampaign();
  await screen.findByTestId("cast-panel");
});

test("editing a message saves and reloads", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [{ role: "assistant", content: "hi" }] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getAllByTitle("Edit message")[0]);
  const ta = await screen.findByLabelText(/edit message/i);
  fireEvent.change(ta, { target: { value: "hello" } });
  fireEvent.click(screen.getByRole("button", { name: /save/i }));
  await waitFor(() => expect(api.editMessage).toHaveBeenCalledWith("run", "s1", 0, "hello"));
});

test("a manual dice roll's transcript line has no Edit control", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "assistant", content: "an ordinary reply" },
    { role: "assistant", content: "🎲 2d6 = 7", speaker: "⁣Roll" }] });
  renderCampaign();
  await screen.findByText(/2d6 = 7/);
  expect(screen.getAllByTitle("Edit message")).toHaveLength(1);
});

test("Enter sends a message in the active scene", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  await screen.findByText(/01 · Old/);
  const ta = screen.getByRole("textbox");
  fireEvent.change(ta, { target: { value: "hello" } });
  fireEvent.keyDown(ta, { key: "Enter" });
  await waitFor(() =>
    expect(api.chat).toHaveBeenCalledWith("run", "s1", "hello", expect.any(Function), undefined, expect.any(AbortSignal)),
  );
});

test("Shift+Enter does not send", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  await screen.findByText(/01 · Old/);
  const ta = screen.getByRole("textbox");
  fireEvent.change(ta, { target: { value: "hello" } });
  fireEvent.keyDown(ta, { key: "Enter", shiftKey: true });
  expect(api.chat).not.toHaveBeenCalled();
});

test("the response length chip shows the scene's preset and reverts after a successful send", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({
    meta: { id: "s1", title: "Old", response_preset: "cinematic" }, messages: [] });
  (api.listResponsePresets as any).mockResolvedValue(RESPONSE_PRESETS);
  renderCampaign();
  const chip = await screen.findByRole("button", { name: /Response length/ });
  expect(chip).toHaveTextContent("Cinematic");
  fireEvent.click(chip);
  fireEvent.click(screen.getByRole("option", { name: "Terse" }));
  expect(chip).toHaveTextContent("Terse");
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Go on." } });
  fireEvent.click(screen.getByRole("button", { name: /Send/ }));
  // the chip's promise is "the next reply" — once it lands, the one-shot
  // pick is spent and the chip falls back to the scene's own setting.
  await waitFor(() => expect(chip).toHaveTextContent("Cinematic"));
});

test("Escape closes the response length dropdown", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({
    meta: { id: "s1", title: "Old", response_preset: "cinematic" }, messages: [] });
  (api.listResponsePresets as any).mockResolvedValue(RESPONSE_PRESETS);
  renderCampaign();
  const chip = await screen.findByRole("button", { name: /Response length/ });
  fireEvent.click(chip);
  expect(screen.getByRole("listbox")).toBeInTheDocument();
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
});

test("sends the one-shot override in the chat request payload", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({
    meta: { id: "s1", title: "Old", response_preset: "cinematic" }, messages: [] });
  (api.listResponsePresets as any).mockResolvedValue(RESPONSE_PRESETS);
  renderCampaign();
  const chip = await screen.findByRole("button", { name: /Response length/ });
  fireEvent.click(chip);
  fireEvent.click(screen.getByRole("option", { name: "Terse" }));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Go on." } });
  fireEvent.click(screen.getByRole("button", { name: /Send/ }));
  await waitFor(() => expect(api.chat).toHaveBeenCalledWith(
    "run", "s1", "Go on.", expect.any(Function), { response_preset: "terse" }, expect.any(AbortSignal)));
});

test("a failed stream keeps the override, and retry carries it", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({
    meta: { id: "s1", title: "Old", response_preset: "cinematic" }, messages: [] });
  (api.listResponsePresets as any).mockResolvedValue(RESPONSE_PRESETS);
  (api.chat as any).mockRejectedValueOnce(new Error("stream failed"));
  renderCampaign();
  const chip = await screen.findByRole("button", { name: /Response length/ });
  fireEvent.click(chip);
  fireEvent.click(screen.getByRole("option", { name: "Terse" }));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Go on." } });
  fireEvent.click(screen.getByRole("button", { name: /Send/ }));
  await waitFor(() => expect(chip).toHaveTextContent("Terse")); // NOT cleared by the failure
  fireEvent.click(screen.getByRole("button", { name: /Retry/ }));
  await waitFor(() => expect(api.retry).toHaveBeenCalledWith(
    "run", "s1", expect.any(Function), { response_preset: "terse" }, expect.any(AbortSignal)));
});

test("sending with no scene creates one first", async () => {
  (api.listScenes as any).mockResolvedValue([]);
  renderCampaign();
  await waitFor(() => expect(api.listScenes).toHaveBeenCalled());
  const ta = screen.getByRole("textbox");
  fireEvent.change(ta, { target: { value: "hi" } });
  fireEvent.keyDown(ta, { key: "Enter" });
  await waitFor(() => expect(api.createScene).toHaveBeenCalledWith("run"));
  await waitFor(() => expect(api.chat).toHaveBeenCalledWith("run", "s1", "hi", expect.any(Function), undefined, expect.any(AbortSignal)));
});

test("+ New Scene opens the chooser without creating a scene", async () => {
  renderCampaign();
  await screen.findByText(/Run One/);
  fireEvent.click(screen.getByRole("button", { name: /\+ new scene/i }));
  expect(await screen.findByTestId("scene-chooser")).toBeInTheDocument();
  expect(api.createScene).not.toHaveBeenCalled();
});

test("a chooser pick refreshes the rail, selects the scene, and seeds the prompt", async () => {
  (api.listScenes as any)
    .mockResolvedValueOnce([])                       // initial load
    .mockResolvedValue([{ id: "s9", title: "New", model: "", created: "", updated: "" }]);
  renderCampaign();
  await screen.findByText(/Run One/);
  fireEvent.click(screen.getByRole("button", { name: /\+ new scene/i }));
  fireEvent.click(await screen.findByText("stub-pick"));
  await waitFor(() => expect(api.getScene).toHaveBeenCalledWith("run", "s9", { limit: 60 }));
  expect(screen.queryByTestId("scene-chooser")).toBeNull();
  // the premise reaches the empty scene's CastPanel
  expect(await screen.findByText("A premise")).toBeInTheDocument();
});

test("a seeded premise survives the rename from the first date set", async () => {
  (api.listScenes as any)
    .mockResolvedValueOnce([])                       // initial load
    .mockResolvedValueOnce([{ id: "s9", title: "New", model: "", created: "", updated: "" }])
    .mockResolvedValue([{ id: "s10", title: "New", model: "", created: "", updated: "" }]);
  renderCampaign();
  await screen.findByText(/Run One/);
  fireEvent.click(screen.getByRole("button", { name: /\+ new scene/i }));
  fireEvent.click(await screen.findByText("stub-pick"));
  await screen.findByText("A premise");
  fireEvent.click(screen.getByText("stub-datestamp"));   // first date set renames s9 -> s10
  await waitFor(() => expect(api.getScene).toHaveBeenCalledWith("run", "s10", { limit: 60 }));
  expect(screen.getByTestId("cast-panel")).toHaveTextContent("A premise");
});

test("closing the chooser creates nothing", async () => {
  renderCampaign();
  await screen.findByText(/Run One/);
  fireEvent.click(screen.getByRole("button", { name: /\+ new scene/i }));
  fireEvent.click(await screen.findByText("stub-close"));
  expect(screen.queryByTestId("scene-chooser")).toBeNull();
  expect(api.createScene).not.toHaveBeenCalled();
  expect(api.getScene).not.toHaveBeenCalled();
});

test("the edit button renames a scene", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  await screen.findByText(/01 · Old/);
  fireEvent.click(screen.getByRole("button", { name: /rename/i }));
  const input = screen.getByDisplayValue("Old");
  fireEvent.change(input, { target: { value: "New" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(api.renameScene).toHaveBeenCalledWith("run", "s1", "New"));
});

test("the delete button deletes a scene after confirm", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  renderCampaign();
  await screen.findByText(/01 · Old/);
  fireEvent.click(screen.getByRole("button", { name: /delete/i }));
  await waitFor(() => expect(api.deleteScene).toHaveBeenCalledWith("run", "s1"));
});

test("declining the delete confirm does nothing", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  vi.spyOn(window, "confirm").mockReturnValue(false);
  renderCampaign();
  await screen.findByText(/01 · Old/);
  fireEvent.click(screen.getByRole("button", { name: /delete/i }));
  expect(api.deleteScene).not.toHaveBeenCalled();
});

test("an error shows a Retry button that retries the scene", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  // the server persisted the user turn even though the stream errored —
  // the post-stream re-fetch returns it
  (api.getScene as any)
    .mockResolvedValueOnce({ meta: {}, messages: [] })
    .mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hello" }] });
  (api.chat as any).mockImplementation(async (_c: string, _s: string, _t: string, onEvent: any) => {
    onEvent({ error: { detail: "boom" } });
  });
  renderCampaign();
  await screen.findByText(/01 · Old/);
  const ta = screen.getByRole("textbox");
  fireEvent.change(ta, { target: { value: "hello" } });
  fireEvent.keyDown(ta, { key: "Enter" });
  const retryBtn = await screen.findByRole("button", { name: /retry/i });
  fireEvent.click(retryBtn);
  await waitFor(() => expect(api.retry).toHaveBeenCalledWith("run", "s1", expect.any(Function), undefined, expect.any(AbortSignal)));
  expect(screen.getAllByText("hello")).toHaveLength(1);
});

// ---- cancelling a turn (#95) ----

/** api.chat that streams `deltas` and then hangs until its signal aborts,
 *  rejecting the way fetch does — the shape a real in-flight turn has. */
function hangingChat(deltas: string[] = []) {
  return async (_c: string, _s: string, _t: string, onEvent: any,
                _r: unknown, signal: AbortSignal) => {
    deltas.forEach((d) => onEvent({ delta: d }));
    await new Promise<void>((_resolve, reject) => {
      signal.addEventListener("abort", () => {
        const err = new Error("The operation was aborted.");
        err.name = "AbortError";
        reject(err);
      });
    });
  };
}

test("a turn in flight offers Stop in place of Send", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.chat as any).mockImplementation(hangingChat(["The tide "]));
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  const stop = await screen.findByRole("button", { name: /stop ■/i });
  expect(screen.queryByRole("button", { name: /send ▸/i })).toBeNull();
  fireEvent.click(stop);
  // back to a composer that can send again, with no error banner: the player
  // asked for this, and the partial the backend kept arrives with the re-fetch
  await screen.findByRole("button", { name: /continue ▶/i });
  expect(screen.queryByText(/aborted/i)).toBeNull();
  expect(api.getScene).toHaveBeenCalled();
});

test("a cancelled turn's partial appears even when the backend flush lands late", async () => {
  // The abort rejects the fetch as soon as the socket is torn down client-side;
  // the backend only then notices the disconnect and runs its shielded flush.
  // The refresh that follows Stop can therefore read a transcript the partial
  // has not reached yet, and without the poll the text sits on disk while the
  // screen denies it exists. Here the flush lands 100ms after the abort — after
  // the immediate refresh, before the first retry.
  //
  // The streamed text and the persisted message are deliberately different
  // strings. They are the same in life, but asserting on the streamed one here
  // proves nothing: the live preview renders it too, so the assertion would
  // pass against a node the refresh is about to tear down.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  let flushed = false;
  (api.getScene as any).mockImplementation(async () => ({
    meta: {},
    messages: flushed ? [{ role: "assistant", content: "the whole persisted partial" }] : [],
  }));
  (api.chat as any).mockImplementation(hangingChat(["a streamed fragment"]));
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  setTimeout(() => { flushed = true; }, 100);   // lands after the immediate refresh
  await waitFor(() =>
    expect(screen.getByText("the whole persisted partial")).toBeInTheDocument());
});

test("a cancel that streamed nothing still waits for the backend's flush", async () => {
  // What reached the client is not what the backend has to persist:
  // FenceWatcher emits nothing for a reply that opens with a roll fence, yet
  // the server still writes a proposal (and can write narration held back
  // behind a possible opener). Gating the poll on "did we see a delta" left
  // that invisible.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  let flushed = false;
  (api.getScene as any).mockImplementation(async () => ({
    meta: {},
    messages: flushed ? [{ role: "assistant", content: "held back all along" }] : [],
  }));
  (api.chat as any).mockImplementation(hangingChat());   // not one delta
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  setTimeout(() => { flushed = true; }, 100);
  await waitFor(() =>
    expect(screen.getByText("held back all along")).toBeInTheDocument());
});

test("StrictMode's mount cycle does not switch the flush poll off", async () => {
  // main.tsx renders the app inside StrictMode, so in development React runs
  // setup / cleanup / setup on mount. A cleanup-only mounted flag is left false
  // by that middle step, and `owns()` reads it: every post-cancel poll bows out
  // before its first look and a late flush stays invisible. Same scenario as
  // the late-flush test above, rendered the way development renders it.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  let flushed = false;
  (api.getScene as any).mockImplementation(async () => ({
    meta: {},
    messages: flushed ? [{ role: "assistant", content: "the whole persisted partial" }] : [],
  }));
  (api.chat as any).mockImplementation(hangingChat(["a streamed fragment"]));
  render(
    <StrictMode>
      <MemoryRouter initialEntries={["/campaigns/run"]}>
        <Routes>
          <Route path="/campaigns/:cid" element={<CampaignView ready={true} />} />
        </Routes>
      </MemoryRouter>
    </StrictMode>,
  );
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  setTimeout(() => { flushed = true; }, 100);
  await waitFor(() =>
    expect(screen.getByText("the whole persisted partial")).toBeInTheDocument());
});

test("a send on a scene still loading is measured against that scene", async () => {
  // Send stays enabled while a freshly clicked scene loads, and the cached
  // length belongs to whichever scene was read last. Measuring the new scene's
  // growth against the old scene's length answers by which transcript happened
  // to be longer: here the post did land, but the scene left behind is longer
  // than the one it landed in, so the stale baseline reads that as "nothing
  // stored" and hands back a prompt the player would then send twice.
  (api.listScenes as any).mockResolvedValue([
    { id: "s1", title: "Old", model: "", created: "", updated: "" },
    { id: "s2", title: "Later", model: "", created: "", updated: "" },
  ]);
  let postLanded = false;
  let pagesOfS2 = 0;
  (api.getScene as any).mockImplementation(async (_c: string, sid: string, w?: any) => {
    if (sid !== "s2") {
      return { meta: {}, total: 4, messages: [
        { role: "user", content: "a" }, { role: "assistant", content: "b" },
        { role: "user", content: "c" }, { role: "assistant", content: "d" }] };
    }
    // s2's first page never arrives, so its length is still unknown when the
    // turn starts — the window the stale baseline is reachable through.
    if (w?.limit !== 1 && pagesOfS2++ === 0) return new Promise(() => {});
    return postLanded
      ? { meta: {}, total: 1, messages: [{ role: "user", content: "I draw my blade." }] }
      : { meta: {}, total: 0, messages: [] };
  });
  (api.chat as any).mockImplementation(async () => {
    postLanded = true;          // post_chat appended, then the abort beat the headers
    const err: Error & { beforeResponse?: boolean } = new Error("The operation was aborted.");
    err.name = "AbortError";
    err.beforeResponse = true;
    throw err;
  });
  renderCampaign();
  fireEvent.click(await screen.findByText(/· Later/));
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByText("I draw my blade.");   // the refreshed transcript has it
  await new Promise((r) => setTimeout(r, 50));
  expect(screen.getByRole("textbox")).toHaveValue("");
});

test("a refresh that fails with the send still gives the prompt back", async () => {
  // The verification read fails for the same reason the send did — the server
  // is unreachable — so the one case that most needs the player's words back is
  // the case that cannot confirm anything. Throwing out of the refresh skipped
  // the restore entirely; now an unverifiable turn restores, because a visible
  // duplicate is recoverable and a destroyed prompt is not.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  let loaded = false;
  (api.getScene as any).mockImplementation(async () => {
    if (loaded) throw new Error("Failed to fetch");
    loaded = true;
    return { meta: {}, total: 0, messages: [] };
  });
  (api.chat as any).mockImplementation(async () => {
    const err: Error & { beforeResponse?: boolean } = new Error("Failed to fetch");
    err.beforeResponse = true;
    throw err;
  });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("I draw my blade."));
});

test("a rolled-back prompt comes back beside a draft typed since", async () => {
  // The composer stays editable while a turn runs, so the player can be typing
  // the next line when this one fails. `cur || content` dropped the failed
  // prompt in exactly that case — it is in no transcript and no composer, which
  // is the loss the restore exists to prevent. Both texts survive.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  let fail: (() => void) | null = null;
  (api.chat as any).mockImplementation(
    async (_c: string, _s: string, _t: string, onEvent: any) => {
      await new Promise<void>((r) => { fail = r; });
      onEvent({ error: { detail: "OpenRouter API key is not set", kind: "missing_key",
                         post_returned: true } });
    });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByRole("button", { name: /stop ■/i });
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Wait, I hesitate." } });
  fail!();
  await screen.findByText(/OpenRouter API key is not set/);
  await waitFor(() => expect(screen.getByRole("textbox"))
    .toHaveValue("I draw my blade.\n\nWait, I hesitate."));
});

test("a pre-response abort whose post landed still waits for the flush", async () => {
  // `beforeResponse` says no response came back, not that nothing was written:
  // the server can append the post, start generating, and have the abort beat
  // its headers home. Growth in the refresh proves that happened, so there is a
  // turn on the server and its abort write is still coming — skipping the poll
  // on `unreached` alone left that partial invisible.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  let landed = false;
  let flushed = false;
  (api.getScene as any).mockImplementation(async () => ({
    meta: {},
    total: flushed ? 2 : landed ? 1 : 0,
    messages: flushed
      ? [{ role: "user", content: "I draw my blade." },
         { role: "assistant", content: "the whole persisted partial" }]
      : landed ? [{ role: "user", content: "I draw my blade." }] : [],
  }));
  (api.chat as any).mockImplementation(async () => {
    landed = true;              // post_chat appended and began generating
    const err: Error & { beforeResponse?: boolean } = new Error("The operation was aborted.");
    err.name = "AbortError";
    err.beforeResponse = true;
    throw err;
  });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByText("I draw my blade.");
  setTimeout(() => { flushed = true; }, 100);   // the shielded write lands after
  await waitFor(() =>
    expect(screen.getByText("the whole persisted partial")).toBeInTheDocument());
});

test("a refused turn neither polls for a flush nor loses the prompt", async () => {
  // A non-2xx is the whole outcome: `streamPost` throws it before any body
  // exists, so no stream was cut short and no abort write is coming. The poll
  // ran anyway — twelve seconds of refreshes for a turn that never started.
  // And every 4xx `post_chat` raises comes from a check that runs before the
  // post is appended, so the prompt was cleared and stored nowhere.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, total: 0, messages: [] });
  (api.chat as any).mockImplementation(async () => {
    throw new ApiError(409, "a turn is already running on this scene", "busy");
  });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByText(/a turn is already running/);
  await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("I draw my blade."));
  const readsAfterSettling = (api.getScene as any).mock.calls.length;
  await new Promise((r) => setTimeout(r, 400));   // past the first two poll ticks
  expect((api.getScene as any).mock.calls.length).toBe(readsAfterSettling);
});

test("the scene being generated into cannot be renamed mid-turn", async () => {
  // A scene's id is its filename and renaming re-slugs it, so a rename mid-turn
  // moves the file out from under the stream: the abort write that saves the
  // partial fails with SceneNotFound and is swallowed during teardown.
  (api.listScenes as any).mockResolvedValue([
    { id: "s1", title: "Old", model: "", created: "", updated: "" },
    { id: "s2", title: "Later", model: "", created: "", updated: "" },
  ]);
  // The lock outlives the turn's `busy`, so the flush has to land for it to be
  // released — see the sibling test below.
  let flushed = false;
  (api.getScene as any).mockImplementation(async () => ({
    meta: {}, total: flushed ? 1 : 0,
    messages: flushed ? [{ role: "assistant", content: "The tide turns." }] : [],
  }));
  (api.chat as any).mockImplementation(hangingChat(["The tide "]));
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByRole("button", { name: /stop ■/i });
  const [activeRename, otherRename] = screen.getAllByRole("button", { name: /rename/i });
  expect(activeRename).toBeDisabled();
  expect(otherRename).not.toBeDisabled();   // only the scene being written to
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  await screen.findByRole("button", { name: /continue ▶/i });
  flushed = true;                            // the shielded write lands
  await waitFor(() => expect(
    screen.getAllByRole("button", { name: /rename/i })[0]).not.toBeDisabled());
});

test("the scene stays locked while the cancelled turn's flush is still coming", async () => {
  // `busy` clears as soon as the socket dies, but `on_abort` writes seconds
  // later — that gap is exactly what the flush poll waits out. Releasing the
  // lock with `busy` re-enabled rename and delete for the whole of it, so the
  // file could move out from under the very write being waited for.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, total: 0, messages: [] });
  (api.chat as any).mockImplementation(hangingChat(["The tide "]));
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  // Send is back — the turn is over as far as the composer is concerned —
  // but the write it is waiting for has not landed, so the scene stays locked.
  await screen.findByRole("button", { name: /continue ▶/i });
  await new Promise((r) => setTimeout(r, 400));   // past the first two poll ticks
  expect(screen.getByRole("button", { name: /rename/i })).toBeDisabled();
});

test("a manual roll cannot land in the window a cancelled reroll restores into", async () => {
  // The worst of the `busy`-instead-of-`streamingId` misses, because unlike
  // the others it destroys something outright. A reroll deletes the old reply
  // up front; cancelled before its first token, `on_abort` puts it back — but
  // `restore_trailing_assistant_run` steps over trailing *transitions* only
  // and refuses behind a manual roll, whose line must stay in lockstep with
  // rolls.json. So a roll in the flush window makes the restore refuse and the
  // reply is gone: nothing else holds it, and no backend hook can rescue it.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({
    meta: {}, total: 2,
    messages: [{ role: "user", content: "and then?" },
               { role: "assistant", content: "The tide turns." }],
  });
  (api.regenerate as any).mockImplementation(hangingChat([]));   // no first token
  renderCampaign();
  await screen.findByText("The tide turns.");
  // The roll is available before the turn — this is the control being locked,
  // not one that happened to be disabled anyway.
  expect(screen.getByRole("button", { name: /roll dice/i })).not.toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: /reroll/i }));
  fireEvent.click(screen.getByRole("button", { name: /reroll ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  // Send is back, so `busy` has cleared — but the abort write that restores
  // the deleted reply has not landed yet, and a roll now would defeat it.
  await screen.findByRole("button", { name: /continue ▶/i });
  await new Promise((r) => setTimeout(r, 400));   // past the first two poll ticks
  const roll = screen.getByRole("button", { name: /roll dice/i });
  expect(roll).toBeDisabled();
  expect(roll).toHaveAttribute("title", LOCKED_WHILE_GENERATING);
});

test("a lost error frame still gives the rolled-back prompt back", async () => {
  // The backend rolls the post back and *then* yields the error frame, so a
  // connection dropped in between leaves a rollback that happened and a client
  // never told. Nothing is set — not errored, not unreached, not refused — and
  // the poll cannot help, since it watches for growth and a rollback shrinks.
  // The refreshed transcript is the only witness left.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  let rolledBack = false;
  (api.getScene as any).mockImplementation(async () => ({
    meta: {}, total: rolledBack ? 0 : 0, messages: [],
  }));
  (api.chat as any).mockImplementation(async () => {
    rolledBack = true;   // headers arrived, post appended, then taken back off
    // resolves with no `done` and no error frame: the body just ended
  });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("I draw my blade."));
});

test("an interrupted stream whose post is still there keeps the composer clear", async () => {
  // The other side of the same gate. Headers arrived, so `post_chat` appended —
  // and the post is still in the transcript, which means the backend did NOT
  // roll it back and the reply may still be flushing. Restoring here would put
  // the text in the composer and the transcript at once.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  let posted = false;
  (api.getScene as any).mockImplementation(async () => ({
    meta: {}, total: posted ? 1 : 0,
    messages: posted ? [{ role: "user", content: "I draw my blade." }] : [],
  }));
  (api.chat as any).mockImplementation(async () => {
    posted = true;   // post_chat appended; the body then ends with no frame
  });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByText("I draw my blade.");
  await new Promise((r) => setTimeout(r, 50));
  expect(screen.getByRole("textbox")).toHaveValue("");
});

test("the lock follows the scene being written to, not the one on screen", async () => {
  // Scene selection stays live during a turn, and the write still lands in the
  // scene the stream captured. A lock keyed on `activeId` unlocked the row
  // still being written to and locked an unrelated one.
  (api.listScenes as any).mockResolvedValue([
    { id: "s1", title: "Old", model: "", created: "", updated: "" },
    { id: "s2", title: "Later", model: "", created: "", updated: "" },
  ]);
  (api.chat as any).mockImplementation(hangingChat(["The tide "]));
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByRole("button", { name: /stop ■/i });
  fireEvent.click(screen.getByText(/· Later/));          // navigate away mid-turn
  await waitFor(() => {
    const [s1Rename, s2Rename] = screen.getAllByRole("button", { name: /rename/i });
    expect(s1Rename).toBeDisabled();        // still streaming into it
    expect(s2Rename).not.toBeDisabled();    // merely being looked at
  });
});

test("Stop during the preflight read does not strand the turn", async () => {
  // The baseline read runs before the POST exists, so the turn's controller has
  // nothing to abort yet. A stalled read left `runStream` parked outside its
  // try/finally with `busy` set: no Send, no Stop that works, no prompt back.
  // No scene selected, so Send creates one and streams into it with no read in
  // between — the window where the baseline has to be fetched before the POST.
  (api.listScenes as any).mockResolvedValue([]);
  (api.getScene as any).mockImplementation(async (_c: string, _s: string, w?: any) => {
    if (w?.limit === 1) return new Promise(() => {});   // the preflight never answers
    return { meta: {}, total: 0, messages: [] };
  });
  (api.createScene as any).mockResolvedValue({ id: "s2" });
  (api.chat as any).mockImplementation(async (_c: string, _s: string, _t: string,
                                              _e: any, _r: unknown, signal: AbortSignal) => {
    const err: Error & { beforeResponse?: boolean } = new Error("The operation was aborted.");
    err.name = "AbortError";
    err.beforeResponse = true;
    if (signal.aborted) throw err;
    await new Promise<void>((_res, rej) => signal.addEventListener("abort", () => rej(err)));
  });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  // the turn unwinds instead of hanging: Send comes back and so does the prompt
  await screen.findByRole("button", { name: /continue ▶/i });
  await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("I draw my blade."));
});

test("Retry after a failed reroll rerolls again, with its guidance", async () => {
  // `/retry` continues from the transcript as it stands. A failed reroll now
  // puts the old reply back, so retrying through `/retry` would generate a
  // continuation of the very reply the player asked to replace — and drop the
  // guidance they typed with it.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({
    meta: {}, total: 2, messages: [
      { role: "user", content: "and then?" },
      { role: "assistant", content: "The tide turns." }],
  });
  (api.regenerate as any).mockImplementation(
    async (_c: string, _s: string, onEvent: any) => {
      onEvent({ error: { detail: "OpenRouter API key is not set", kind: "missing_key" } });
    });
  renderCampaign();
  await screen.findByText("The tide turns.");
  fireEvent.click(screen.getByRole("button", { name: /reroll/i }));
  fireEvent.change(screen.getByPlaceholderText(/reroll/i),
                   { target: { value: "darker this time" } });
  fireEvent.click(screen.getByRole("button", { name: /reroll ▸/i }));
  await screen.findByText(/OpenRouter API key is not set/);
  expect(api.regenerate).toHaveBeenCalledTimes(1);

  fireEvent.click(screen.getByRole("button", { name: /^retry$/i }));
  await waitFor(() => expect(api.regenerate).toHaveBeenCalledTimes(2));
  expect(api.retry).not.toHaveBeenCalled();
  expect((api.regenerate as any).mock.calls[1][3]).toBe("darker this time");
});

test("a remembered reroll does not follow the player to another scene", async () => {
  // The remembered operation had no scene identity, and Retry acts on whatever
  // scene is open — so a reroll that failed in one scene, retried after
  // switching, would replace a reply in the *other* scene with guidance written
  // for the first. Switching also clears the banner now; the scene check is
  // what makes that airtight rather than merely likely.
  (api.listScenes as any).mockResolvedValue([
    { id: "s1", title: "Old", model: "", created: "", updated: "" },
    { id: "s2", title: "Later", model: "", created: "", updated: "" },
  ]);
  (api.getScene as any).mockResolvedValue({
    meta: {}, total: 2, messages: [
      { role: "user", content: "and then?" },
      { role: "assistant", content: "The tide turns." }],
  });
  (api.regenerate as any).mockImplementation(
    async (_c: string, _s: string, onEvent: any) => {
      onEvent({ error: { detail: "OpenRouter API key is not set", kind: "missing_key" } });
    });
  renderCampaign();
  await screen.findByText("The tide turns.");
  fireEvent.click(screen.getAllByRole("button", { name: /reroll/i })[0]);
  fireEvent.change(screen.getByPlaceholderText(/reroll/i),
                   { target: { value: "darker this time" } });
  fireEvent.click(screen.getByRole("button", { name: /reroll ▸/i }));
  await screen.findByText(/OpenRouter API key is not set/);

  fireEvent.click(screen.getByText(/· Later/));       // leave the failed scene
  // the banner belonged to the scene being left, so it goes with it
  await waitFor(() => expect(screen.queryByText(/OpenRouter API key is not set/)).toBeNull());

  // Now fail something in the new scene, so a banner — and a Retry — exist here
  // on their own account. The remembered reroll is still in the ref, and this
  // is where a Retry with no scene identity would have rerolled the wrong scene.
  (api.chat as any).mockImplementation(
    async (_c: string, _s: string, _t: string, onEvent: any) => {
      onEvent({ error: { detail: "connection reset", kind: "network" } });
    });
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "onward" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByText(/connection reset/);
  fireEvent.click(screen.getByRole("button", { name: /^retry$/i }));

  await waitFor(() => expect(api.retry).toHaveBeenCalled());
  expect((api.retry as any).mock.calls[0][1]).toBe("s2");   // this scene
  expect(api.regenerate).toHaveBeenCalledTimes(1);          // not s1's reroll again
});

test("End scene stays disabled while the cancelled turn's flush is still coming", async () => {
  // Absorption reads the transcript and commits a chronicle against it. `busy`
  // clears when the socket dies, but the backend's shielded write lands seconds
  // later — absorb inside that window and the summary describes a transcript
  // the partial has not reached, then the partial lands under a scene already
  // marked absorbed. Unlike the other flush races, that one does not heal.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, total: 0, messages: [] });
  (api.chat as any).mockImplementation(hangingChat(["The tide "]));
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  await screen.findByRole("button", { name: /continue ▶/i });   // busy is clear
  await new Promise((r) => setTimeout(r, 400));                 // still flushing
  expect(screen.getByRole("button", { name: /end scene/i })).toBeDisabled();
});

test("a scene rename in flight holds off the next turn", async () => {
  // Renaming is a PUT that moves the scene file. Until it answers, which id is
  // current is genuinely unknown — so a turn started inside that window can be
  // handed the old one and have its reply written to a path that no longer
  // exists. The lock covers rename-during-a-turn; this is the other direction.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  let finishRename: ((v: any) => void) | null = null;
  (api.renameScene as any).mockImplementation(
    () => new Promise((res) => { finishRename = res; }));
  renderCampaign();
  await screen.findByText(/01 · Old/);
  fireEvent.click(screen.getByRole("button", { name: /rename/i }));
  const input = screen.getByDisplayValue("Old");
  fireEvent.change(input, { target: { value: "Renamed" } });
  fireEvent.keyDown(input, { key: "Enter" });

  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  await waitFor(() =>
    expect(screen.getByRole("button", { name: /send ▸/i })).toBeDisabled());
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  expect(api.chat).not.toHaveBeenCalled();

  finishRename!({ id: "s1", title: "Renamed" });     // the PUT answers
  await waitFor(() =>
    expect(screen.getByRole("button", { name: /send ▸/i })).not.toBeDisabled());
});

test("a verification read retired by a scene switch still gives the prompt back", async () => {
  // `selectScene` returns -1 when a newer owner takes the view: it read nothing
  // and applied nothing. The await did not throw, though, so `refreshed` was
  // true and the turn counted as verified — with no growth to point at, an
  // undelivered prompt went unrestored. "It did not look" is unverifiable.
  (api.listScenes as any).mockResolvedValue([
    { id: "s1", title: "Old", model: "", created: "", updated: "" },
    { id: "s2", title: "Later", model: "", created: "", updated: "" },
  ]);
  let releaseVerify: (() => void) | null = null;
  let loaded = false;
  (api.getScene as any).mockImplementation(async (_c: string, sid: string) => {
    // the verification read for s1 hangs until the player has moved to s2
    if (sid === "s1" && loaded) {
      await new Promise<void>((r) => { releaseVerify = r; });
    }
    loaded = true;
    return { meta: {}, total: 0, messages: [] };
  });
  (api.chat as any).mockImplementation(async () => {
    const err: Error & { beforeResponse?: boolean } = new Error("Failed to fetch");
    err.beforeResponse = true;      // nothing reached the server
    throw err;
  });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await waitFor(() => expect(releaseVerify).not.toBeNull());
  fireEvent.click(screen.getByText(/· Later/));   // retires the pending read
  releaseVerify!();
  await new Promise((r) => setTimeout(r, 60));
  // Recovered, but not into the composer the player is looking at: these are
  // scene s1's words and Send here would post them to s2. The prompt is held
  // under the scene it was written for (review, #95) …
  expect(document.querySelector(".row.active .row-name")?.textContent).toMatch(/Later/);
  expect(screen.getByRole("textbox")).toHaveValue("");
  // … and handed back when that scene is on screen again. Recovered, not lost,
  // which is the whole point of counting a retired read as unverifiable.
  fireEvent.click(screen.getByText(/. Old/));
  await waitFor(() =>
    expect(screen.getByRole("textbox")).toHaveValue("I draw my blade."));
});

test("a pending rename also blocks a proposal continuation", async () => {
  // Resolving a roll streams a continuation through `runStream` without passing
  // `send`/`retry`/`reroll`, so the per-call-site rename checks all missed it.
  // The guard belongs where every stream enters.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getRollProposal as any).mockResolvedValue({
    record: { id: "p1", status: "pending", resolution: null,
              payload: { id: "p1", check: "wits", check_label: "Wits", problems: [] } },
  });
  let finishRename: ((v: any) => void) | null = null;
  (api.renameScene as any).mockImplementation(
    () => new Promise((res) => { finishRename = res; }));
  renderCampaign();
  await screen.findByText(/01 · Old/);
  fireEvent.click(screen.getByRole("button", { name: /rename/i }));
  const input = screen.getByDisplayValue("Old");
  fireEvent.change(input, { target: { value: "Renamed" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(api.renameScene).toHaveBeenCalled());

  const decline = await screen.findByRole("button", { name: /decline/i });
  fireEvent.click(decline);
  await new Promise((r) => setTimeout(r, 50));
  expect(api.resolveProposal).not.toHaveBeenCalled();   // the file may be moving
  // and the chip is still there: refusing to send must not also hide the
  // decision, or the roll becomes unreachable until some later refresh
  expect(screen.getByRole("button", { name: /decline/i })).toBeInTheDocument();

  finishRename!({ id: "s1", title: "Renamed" });
  await waitFor(() => expect(screen.queryByDisplayValue("Renamed")).toBeNull());
});

test("a poll fetch already in flight cannot clear a new turn's preview", async () => {
  // The check-then-await window: the poll verifies it owns the view, then
  // awaits getScene, and a turn starting during that await would otherwise have
  // the stale response run setStreaming("") over the new stream's text.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  let releaseFetch: (() => void) | null = null;
  (api.getScene as any).mockImplementation(async () => {
    if (releaseFetch) await new Promise<void>((r) => { releaseFetch = r; });
    return { meta: {}, messages: [] };
  });
  (api.chat as any)
    .mockImplementationOnce(hangingChat(["first fragment"]))
    .mockImplementation(hangingChat(["second fragment"]));
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  await screen.findByRole("button", { name: /continue ▶/i });

  releaseFetch = () => {};                       // the next getScene blocks
  const before = (api.getScene as any).mock.calls.length;
  await waitFor(() => expect((api.getScene as any).mock.calls.length).toBeGreaterThan(before));

  fireEvent.change(screen.getByRole("textbox"), { target: { value: "again" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByText("second fragment");
  (releaseFetch as () => void)();                // the stale poll's fetch lands
  await new Promise((r) => setTimeout(r, 50));
  expect(screen.getByText("second fragment")).toBeInTheDocument();
});

test("a cancelled fence's proposal survives a stale proposal read", async () => {
  // selectScene fires getRollProposal and awaits only getScene, so on the tick
  // that catches the flush the two race it independently. finalize writes the
  // proposal before the narration, so the scene read that saw growth is after
  // both writes while the proposal read beside it can be before either — and
  // its late null would clear a chip that does exist, with the poll already
  // stopped. Here the first proposal read returns null and the second (awaited,
  // after growth) returns the record.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  let flushed = false;
  let sceneSawGrowth = false;
  (api.getScene as any).mockImplementation(async () => {
    if (flushed) sceneSawGrowth = true;
    return {
      meta: {}, total: flushed ? 1 : 0,
      messages: flushed ? [{ role: "assistant", content: "She lunges" }] : [],
    };
  });
  // Evaluated when the call is made, not when it resolves — so the read
  // selectScene fires before awaiting getScene still sees the pre-flush world,
  // which is precisely the stale answer that used to win.
  (api.getRollProposal as any).mockImplementation(async () => ({
    record: sceneSawGrowth
      ? { id: "pr-1", status: "pending", payload: PROPOSAL_PAYLOAD, resolution: null }
      : null,
  }));
  (api.chat as any).mockImplementation(hangingChat(["She lunges"]));
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I punch him" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  setTimeout(() => { flushed = true; }, 100);
  expect(await screen.findByRole("button", { name: "Roll it" })).toBeInTheDocument();
});

test("a slow pre-flush proposal read cannot undo the settling read", async () => {
  // selectScene's proposal read is fired and not awaited. If it resolves after
  // settleProposal has installed the record, last-write-wins puts its pre-flush
  // null back — chip gone, poll already finished. The settling read is issued
  // later, so it must win regardless of which lands first.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  let flushed = false;
  let sceneSawGrowth = false;
  let releaseStale: (() => void) | null = null;
  (api.getScene as any).mockImplementation(async () => {
    if (flushed) sceneSawGrowth = true;
    return {
      meta: {}, total: flushed ? 1 : 0,
      messages: flushed ? [{ role: "assistant", content: "She lunges" }] : [],
    };
  });
  // The two reads are told apart by when they are *made*, not by an argument:
  // selectScene fires its one before awaiting getScene, so it still sees the
  // pre-growth world; settleProposal's comes after. That is the real ordering,
  // and it is the only thing left distinguishing them now the endpoint opts out
  // of coalescing for every caller.
  (api.getRollProposal as any).mockImplementation(async () => {
    if (sceneSawGrowth) {
      return { record: { id: "pr-1", status: "pending", payload: PROPOSAL_PAYLOAD, resolution: null } };
    }
    // the unawaited read: parked, so it lands *after* the settling one
    if (flushed) await new Promise<void>((r) => { releaseStale = r; });
    return { record: null };
  });
  (api.chat as any).mockImplementation(hangingChat(["She lunges"]));
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I punch him" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  setTimeout(() => { flushed = true; }, 100);
  await screen.findByRole("button", { name: "Roll it" });
  (releaseStale as unknown as () => void)();   // the stale null finally arrives
  await new Promise((r) => setTimeout(r, 50));
  expect(screen.getByRole("button", { name: "Roll it" })).toBeInTheDocument();
});

test("the refresh right after a cancel cannot wipe the next turn's preview", async () => {
  // Stop clears `busy` before the immediate refresh resolves, so the next turn
  // can begin while that fetch is in flight — and its response would otherwise
  // run setMessages/setStreaming("") straight over the new turn's state.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  let holdRefresh: (() => void) | null = null;
  (api.getScene as any).mockImplementation(async () => {
    if (holdRefresh) await new Promise<void>((r) => { holdRefresh = r; });
    return { meta: {}, total: 0, messages: [] };
  });
  (api.chat as any)
    .mockImplementationOnce(hangingChat(["first fragment"]))
    .mockImplementation(hangingChat(["second fragment"]));
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));

  holdRefresh = () => {};                       // the post-cancel refresh parks
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  await waitFor(() => expect(holdRefresh).not.toBe(null));
  await screen.findByRole("button", { name: /continue ▶/i });   // Send is live again

  fireEvent.change(screen.getByRole("textbox"), { target: { value: "again" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByText("second fragment");
  (holdRefresh as unknown as () => void)();     // the stale refresh finally lands
  await new Promise((r) => setTimeout(r, 50));
  expect(screen.getByText("second fragment")).toBeInTheDocument();
});

test("Stop after the done frame is a finished turn, not a cancellation", async () => {
  // `done` is parsed off the stream before the body reports EOF, and Stop stays
  // live until it does. A press in that gap used to be classed as a cancel,
  // which handed back a one-shot response length the reply had already
  // consumed — and spent it again on the next turn.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.listResponsePresets as any).mockResolvedValue(RESPONSE_PRESETS);
  (api.getScene as any).mockResolvedValue({ meta: { id: "s1", title: "Old" }, total: 0, messages: [] });
  (api.chat as any).mockImplementation(
    async (_c: string, _s: string, _t: string, onEvent: any, _r: unknown, signal: AbortSignal) => {
      onEvent({ delta: "All told." });
      onEvent({ done: true });          // persisted server-side from here on
      await new Promise<void>((_res, reject) => {   // body still open
        signal.addEventListener("abort", () => {
          const err = new Error("The operation was aborted.");
          err.name = "AbortError";
          reject(err);
        });
      });
    });
  renderCampaign();
  const chip = await screen.findByRole("button", { name: /Response length/ });
  fireEvent.click(chip);
  fireEvent.click(screen.getByRole("option", { name: "Terse" }));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  await screen.findByRole("button", { name: /continue ▶/i });
  // spent by the reply that did land, so it must not ride the next turn
  await waitFor(() => expect(chip).not.toHaveTextContent("Terse"));
});

test("a failed send that was rolled back gives the player their words back", async () => {
  // The backend removes the post when a turn fails having produced nothing, so
  // without this the text exists nowhere: the composer was cleared on send and
  // the refresh drops the optimistic copy. Retry cannot recover it either — it
  // calls /retry, which has no prompt of its own.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.chat as any).mockImplementation(
    async (_c: string, _s: string, _t: string, onEvent: any) => {
      onEvent({ error: { detail: "OpenRouter API key is not set", kind: "missing_key",
                         post_returned: true } });
    });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByText(/OpenRouter API key is not set/);
  await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("I draw my blade."));
});

test("a Stop before the request lands gives the prompt back too", async () => {
  // The abort beats the request to the server, so there is no post to roll back
  // and no error frame to carry `post_returned` — but the player is in exactly
  // the same position, with the composer cleared and nothing durable anywhere.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.chat as any).mockImplementation(async () => {
    const err: Error & { beforeResponse?: boolean } = new Error("The operation was aborted.");
    err.name = "AbortError";
    err.beforeResponse = true;   // set by streamPost when fetch never resolved
    throw err;
  });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("I draw my blade."));
});

test("an abort whose post did land does not duplicate the prompt", async () => {
  // `beforeResponse` only means no response arrived. The server can have
  // appended the post and had the abort beat its headers back — restoring then
  // puts the text in the composer *and* the transcript, and the next Send
  // sends it twice. Growth in the refreshed transcript is what settles it.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  let landedOnServer = false;
  (api.getScene as any).mockImplementation(async () => ({
    meta: {}, total: landedOnServer ? 1 : 0,
    messages: landedOnServer ? [{ role: "user", content: "I draw my blade." }] : [],
  }));
  (api.chat as any).mockImplementation(async () => {
    landedOnServer = true;      // it did reach post_chat, headers just never came back
    const err: Error & { beforeResponse?: boolean } = new Error("The operation was aborted.");
    err.name = "AbortError";
    err.beforeResponse = true;
    throw err;
  });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByText("I draw my blade.");   // the post is really there
  await new Promise((r) => setTimeout(r, 50));
  expect(screen.getByRole("textbox")).toHaveValue("");
});

test("a cancel after the request landed leaves the composer alone", async () => {
  // The post is durably stored by then — a cancel keeps it — so restoring would
  // have the player send the same line twice.
  //
  // The refreshed transcript has to actually contain that post, or the test
  // asserts its opposite: this used to run against the default empty-scene
  // mock, so it modelled a cancel whose post was NOT stored and passed only
  // because nothing restored on this path at all. Growth is now what tells the
  // two apart (review, #95).
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  let posted = false;
  (api.getScene as any).mockImplementation(async () => ({
    meta: {}, total: posted ? 1 : 0,
    messages: posted ? [{ role: "user", content: "I draw my blade." }] : [],
  }));
  (api.chat as any).mockImplementation((...args: any[]) => {
    posted = true;   // post_chat appended before returning the stream
    return (hangingChat(["The tide "]) as any)(...args);
  });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  await screen.findByRole("button", { name: /continue ▶/i });
  expect(screen.getByRole("textbox")).toHaveValue("");
});

test("a failure the backend did not roll back leaves the composer alone", async () => {
  // The post is still in the transcript, so restoring it would have the player
  // send the same line twice. Only the backend knows which happened.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.chat as any).mockImplementation(
    async (_c: string, _s: string, _t: string, onEvent: any) => {
      onEvent({ delta: "The tide " });
      onEvent({ error: { detail: "connection reset", kind: "network" } });
    });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByText(/connection reset/);
  expect(screen.getByRole("textbox")).toHaveValue("");
});

test("a body that ends before the done frame is an interrupted turn", async () => {
  // `reader.read()` reporting EOF resolves streamPost normally, so a proxy
  // cutting the body short used to look identical to a completed turn — the
  // one-shot override was spent and the flush never waited for.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.listResponsePresets as any).mockResolvedValue(RESPONSE_PRESETS);
  // The truncated turn is treated as interrupted, so the flush poll runs; let
  // the partial land so it exits on its first tick rather than sitting out the
  // whole budget — `runStream` awaits it before the override is settled either
  // way, and a `waitFor` that ran before that would pass on any implementation.
  let flushed = false;
  (api.getScene as any).mockImplementation(async () => ({
    meta: { id: "s1", title: "Old" },
    total: flushed ? 1 : 0,
    messages: flushed ? [{ role: "assistant", content: "The tide" }] : [],
  }));
  (api.chat as any).mockImplementation(
    async (_c: string, _s: string, _t: string, onEvent: any) => {
      onEvent({ delta: "The tide " });   // ...and then the body just ends
    });
  renderCampaign();
  const chip = await screen.findByRole("button", { name: /Response length/ });
  fireEvent.click(chip);
  fireEvent.click(screen.getByRole("option", { name: "Terse" }));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  setTimeout(() => { flushed = true; }, 100);
  await screen.findByText("The tide");          // poll caught the flush and ended
  await new Promise((r) => setTimeout(r, 600)); // let the poll exit and send() settle
  // unspent: the reply never confirmed, so the override rides the retry
  expect(chip).toHaveTextContent("Terse");
});

test("the post-cancel poll stops once a new turn owns the view", async () => {
  // Stop clears `busy` before the poll finishes, so the player can send again
  // while it is still running. Left alone it would keep calling selectScene,
  // clearing the new stream's live preview on every tick.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [] });
  (api.chat as any).mockImplementation(hangingChat(["a streamed fragment"]));
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  await screen.findByRole("button", { name: /continue ▶/i });   // cancel settled
  const afterCancel = (api.getScene as any).mock.calls.length;

  fireEvent.change(screen.getByRole("textbox"), { target: { value: "again" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByRole("button", { name: /stop ■/i });       // second turn in flight
  await new Promise((r) => setTimeout(r, 700));                 // past two poll ticks
  // The live preview of the second turn survives, and the stale poll made no
  // further fetches of its own.
  expect(screen.getByText("a streamed fragment")).toBeInTheDocument();
  expect((api.getScene as any).mock.calls.length).toBe(afterCancel);
});

test("cancelling keeps a one-shot response override for the retry", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.listResponsePresets as any).mockResolvedValue(RESPONSE_PRESETS);
  (api.chat as any).mockImplementation(hangingChat());
  renderCampaign();
  const chip = await screen.findByRole("button", { name: /Response length/ });
  fireEvent.click(chip);
  fireEvent.click(screen.getByRole("option", { name: "Terse" }));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  await waitFor(() => expect(chip).toHaveTextContent("Terse")); // unspent, like a failure
});

test("Reroll on the last assistant post replaces it with a fresh reply", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any)
    .mockResolvedValueOnce({ meta: {}, messages: [
      { role: "user", content: "hi" }, { role: "assistant", content: "old reply" }] })
    .mockResolvedValue({ meta: {}, messages: [
      { role: "user", content: "hi" }, { role: "assistant", content: "fresh reply" }] });
  (api.regenerate as any).mockImplementation(async (_c: string, _s: string, onEvent: any) => {
    onEvent({ delta: "fresh reply" });
  });
  renderCampaign();
  await screen.findByText("old reply");
  fireEvent.click(screen.getByTitle("Reroll"));
  // clicking Reroll opens the popover instead of firing immediately
  expect(api.regenerate).not.toHaveBeenCalled();
  expect(screen.getByTitle("Reroll")).toBeInTheDocument(); // hovertext present
  fireEvent.click(screen.getByRole("button", { name: /reroll ▸/i })); // empty = plain reroll
  await waitFor(() => expect(api.regenerate).toHaveBeenCalledWith(
    "run", "s1", expect.any(Function), undefined, undefined, expect.any(AbortSignal)));
  await screen.findByText("fresh reply");
  expect(screen.queryByText("old reply")).toBeNull();
});

test("typed guidance is passed to regenerate", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "old reply" }] });
  renderCampaign();
  await screen.findByText("old reply");
  fireEvent.click(screen.getByTitle("Reroll"));
  const input = screen.getByPlaceholderText(/guide the reroll/i);
  fireEvent.change(input, { target: { value: "make her angrier" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(api.regenerate).toHaveBeenCalledWith(
    "run", "s1", expect.any(Function), "make her angrier", undefined, expect.any(AbortSignal)));
});

test("Escape closes the reroll popover without firing", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "old reply" }] });
  renderCampaign();
  await screen.findByText("old reply");
  fireEvent.click(screen.getByTitle("Reroll"));
  fireEvent.keyDown(screen.getByPlaceholderText(/guide the reroll/i), { key: "Escape" });
  expect(screen.queryByPlaceholderText(/guide the reroll/i)).toBeNull();
  expect(api.regenerate).not.toHaveBeenCalled();
});

test("regenerate carries a pending override", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({
    meta: { id: "s1", title: "Old", response_preset: "cinematic" },
    messages: [{ role: "user", content: "hi" }, { role: "assistant", content: "old reply" }],
  });
  (api.listResponsePresets as any).mockResolvedValue(RESPONSE_PRESETS);
  renderCampaign();
  await screen.findByText("old reply");
  const chip = await screen.findByRole("button", { name: /Response length/ });
  fireEvent.click(chip);
  fireEvent.click(screen.getByRole("option", { name: "Terse" }));
  fireEvent.click(screen.getByTitle("Reroll"));
  fireEvent.click(screen.getByRole("button", { name: /reroll ▸/i })); // empty guidance = plain reroll
  await waitFor(() => expect(api.regenerate).toHaveBeenCalledWith(
    "run", "s1", expect.any(Function), undefined, { response_preset: "terse" }, expect.any(AbortSignal)));
});

test("no Reroll when a manual dice roll trails the assistant reply", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" },
    { role: "assistant", content: "🎲 2d6 = 7", speaker: "⁣Roll" }] });
  renderCampaign();
  await screen.findByText(/2d6 = 7/);
  expect(screen.queryByRole("button", { name: /reroll/i })).toBeNull();
});

test("Reroll is offered when the last post is merely spoken by a character actually named Roll", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "hello", speaker: "Roll" }] });
  renderCampaign();
  await screen.findByText("hello");
  expect(screen.getByRole("button", { name: /reroll/i })).toBeInTheDocument();
});

test("no Reroll when the last post is the user's", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "assistant", content: "a reply" }, { role: "user", content: "hi" }] });
  renderCampaign();
  await screen.findByText("hi");
  expect(screen.queryByRole("button", { name: /reroll/i })).toBeNull();
});

test("no Reroll on a sole opening post", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "assistant", content: "the greeting" }] });
  renderCampaign();
  await screen.findByText("the greeting");
  expect(screen.queryByRole("button", { name: /reroll/i })).toBeNull();
});

test("only the last assistant post shows Reroll", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "one" }, { role: "assistant", content: "first reply" },
    { role: "user", content: "two" }, { role: "assistant", content: "second reply" }] });
  renderCampaign();
  await screen.findByText("second reply");
  expect(screen.getAllByRole("button", { name: /reroll/i })).toHaveLength(1);
});

test("End scene fetches a preview, edits, and saves the chronicle", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  renderCampaign();
  await screen.findByText("hi"); // scene loaded → activeId set → button enabled
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  const summary = await screen.findByLabelText("Scene summary");
  expect((summary as HTMLTextAreaElement).value).toContain("A met B.");
  fireEvent.change(summary, { target: { value: "Edited summary." } });
  fireEvent.click(screen.getByRole("button", { name: /Save summary/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalledWith("run", "s1",
    expect.objectContaining({ summary: "Edited summary.", one_line: "They met." })));
});

test("End scene review sends approved edits with the summary", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("Seraphine — current state");
  fireEvent.click(screen.getByRole("button", { name: /Save summary/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalledWith("run", "s1",
    expect.objectContaining({
      edits: [expect.objectContaining({ id: "character_state:seraphine", after: "Loyal now." })] })));
});

test("re-absorbing a scene asks for confirmation, then retries with force", async () => {
  const { ApiError } = await vi.importActual<typeof import("../api/client")>("../api/client");
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  (api.absorbScene as any)
    .mockRejectedValueOnce(new ApiError(409, "this scene has already been absorbed",
                                        "already_absorbed"))
    .mockResolvedValueOnce({
      one_line: "Again.", summary: "s", keywords: [], timeline_events: [],
      cast: [], location: "", date: "", edits: [],
      mechanics: { status: "ok", reason: null, warnings: [], dropped: [] },
    commit_token: "tok",
      dossiers: { status: "skipped", reason: null, proposed: [], failed: [] },
      phases: PHASES_NONE_CUT });
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await waitFor(() => expect(api.absorbScene).toHaveBeenCalledTimes(2));
  // the FIRST attempt must be unforced -- otherwise the guard is bypassed outright
  expect((api.absorbScene as any).mock.calls[0][2]).toBeFalsy();
  expect((api.absorbScene as any).mock.calls[1]).toEqual(["run", "s1", true]);
  expect(confirm).toHaveBeenCalled();
  expect(await screen.findByLabelText("Scene one-line")).toHaveValue("Again.");
  confirm.mockRestore();
});

test("declining the re-absorb confirmation leaves the scene alone", async () => {
  const { ApiError } = await vi.importActual<typeof import("../api/client")>("../api/client");
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  (api.absorbScene as any).mockRejectedValue(
    new ApiError(409, "this scene has already been absorbed", "already_absorbed"));
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await waitFor(() => expect(confirm).toHaveBeenCalled());
  expect(api.absorbScene).toHaveBeenCalledTimes(1);
  expect(screen.queryByLabelText("Scene one-line")).toBeNull();
  confirm.mockRestore();
});

test("double-clicking Save summary commits once", async () => {
  // PUT /chronicle is replayable and plot movements append a beat per apply, so a
  // second commit of the same review duplicates them (#235).
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  let release: (v: any) => void = () => {};
  (api.saveChronicle as any).mockReturnValue(new Promise((res) => { release = res; }));
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  const save = await screen.findByRole("button", { name: /Save summary/ });
  fireEvent.click(save);
  fireEvent.click(save);
  expect(api.saveChronicle).toHaveBeenCalledTimes(1);
  release({ id: "s1", one_line: "o", summary: "s", keywords: [], cast: [], location: "",
            date: "", absorbed: "t", applied: [], failures: [] });
  await waitFor(() => expect(screen.queryByLabelText("Scene summary")).toBeNull());
});

test("a review saves to the scene it was absorbed from, not the selected one", async () => {
  // Switching scenes leaves the review panel open, so a save issued afterwards
  // would otherwise be routed at the newly selected scene (#235).
  (api.listScenes as any).mockResolvedValue([
    { id: "s1", title: "One", model: "", created: "", updated: "", date: "" },
    { id: "s2", title: "Two", model: "", created: "", updated: "", date: "" }]);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByLabelText("Scene summary");
  fireEvent.click(screen.getByText(/Two/));                        // switch scenes
  await waitFor(() => expect(api.getScene).toHaveBeenCalledWith("run", "s2", { limit: 60 }));
  fireEvent.click(screen.getByRole("button", { name: /Save summary/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalled());
  expect((api.saveChronicle as any).mock.calls[0][1]).toBe("s1");
});

test("a failed save offers a retry that saves, not one that generates a reply", async () => {
  // The shared error banner's Retry calls api.retry (chat generation). Routing a
  // save failure there would invite the user to generate another reply with the
  // unsaved review still open.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  (api.saveChronicle as any).mockRejectedValueOnce(
    Object.assign(new Error("boom"), { detail: "disk full" }));
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  fireEvent.click(await screen.findByRole("button", { name: /Save summary/ }));
  const again = await screen.findByRole("button", { name: /Try saving again/ });
  (api.saveChronicle as any).mockResolvedValueOnce({
    id: "s1", one_line: "o", summary: "s", keywords: [], cast: [], location: "",
    date: "", absorbed: "t", applied: [], failures: [] });
  fireEvent.click(again);
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalledTimes(2));
  expect(api.retry).not.toHaveBeenCalled();
  // the same token both times, so a first PUT that landed cannot commit twice
  const tokens = (api.saveChronicle as any).mock.calls.map((c: any) => c[2].commit_token);
  expect(tokens).toEqual(["tok", "tok"]);
});

test("a failed save keeps the review open and shows the error", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  (api.saveChronicle as any).mockRejectedValue(
    Object.assign(new Error("boom"), { detail: "disk full" }));
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  fireEvent.click(await screen.findByRole("button", { name: /Save summary/ }));
  expect(await screen.findByText(/disk full/)).toBeTruthy();
  expect(screen.getByLabelText("Scene summary")).toBeTruthy();  // review survives to retry
});

// The default absorb mock stages one lore edit, so these drive #111's whole
// review loop: a save refused because the target moved, then keep / replace /
// merge on the row that moved.
const LORE_REVIEW = {
  one_line: "They met.", summary: "A met B.", keywords: [], timeline_events: [],
  cast: [], location: "", date: "",
  mechanics: { status: "ok", reason: null, warnings: [], dropped: [] },
  commit_token: "tok",
  dossiers: { status: "skipped", reason: null, proposed: [], failed: [], skipped: [] },
  phases: PHASES_NONE_CUT,
  edits: [{ id: "lore:the-pact", kind: "lore", target: { kind: "lore", id: "the-pact" },
    label: "The Pact — lore", field: "body", authored: false,
    before: "Signed at dusk.", after: "Signed at dusk.\n\nBroken by morning." }],
};
const PACT_CONFLICT = {
  id: "lore:the-pact", label: "The Pact — lore", kind: "lore", field: "body",
  before: "Signed at dusk.", after: "Signed at dusk.\n\nBroken by morning.",
  stored: "Witnessed by the watch.",
  reason: "this entry changed since the scene was absorbed",
  mergeable: true, merged: "Witnessed by the watch.\n\nBroken by morning.",
  index: 0,
};

/** Absorb the scene, hit Save, and have the server refuse the batch. */
async function reviewIntoConflict() {
  const { ApiError } = await vi.importActual<typeof import("../api/client")>("../api/client");
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  (api.absorbScene as any).mockResolvedValue(LORE_REVIEW);
  (api.saveChronicle as any).mockRejectedValueOnce(new ApiError(
    409, "some proposed changes no longer match what is stored", "edit_conflicts",
    { conflicts: [PACT_CONFLICT] }));
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  fireEvent.click(await screen.findByRole("button", { name: /Save summary/ }));
  await screen.findByText(/no longer match/);
}

test("a refused save keeps the review open and shows what the record now says", async () => {
  await reviewIntoConflict();
  expect(screen.getByText("Witnessed by the watch.")).toBeTruthy();
  expect(screen.getByText(/this entry changed since the scene was absorbed/)).toBeTruthy();
  // The review survives untouched -- nothing was written, so it is savable again.
  expect(screen.getByLabelText("Scene summary")).toBeTruthy();
  expect(screen.getByRole("button", { name: /Keep stored The Pact/ })).toBeTruthy();
  expect(screen.getByRole("button", { name: /Replace stored The Pact/ })).toBeTruthy();
  expect(screen.getByRole("button", { name: /Merge stored The Pact/ })).toBeTruthy();
});

test("Replace authorizes the staged text and the next save carries it", async () => {
  await reviewIntoConflict();
  fireEvent.click(screen.getByRole("button", { name: /Replace stored The Pact/ }));
  fireEvent.click(screen.getByRole("button", { name: /Save summary/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalledTimes(2));
  expect((api.saveChronicle as any).mock.calls[1][2].edits).toEqual([
    expect.objectContaining({ id: "lore:the-pact", resolve: "replace",
                              // the value that was on screen, so a record that
                              // moves again is refused rather than overwritten
                              resolve_from: "Witnessed by the watch.",
                              after: "Signed at dusk.\n\nBroken by morning." })]);
});

test("answering one row leaves its duplicate-id sibling unanswered", async () => {
  // `materialize` dedupes only plot threads, so two lore proposals naming one
  // entry can share an edit id. Answering by id would silently answer both.
  const { ApiError } = await vi.importActual<typeof import("../api/client")>("../api/client");
  const twin = { ...LORE_REVIEW.edits[0], after: "Signed at dusk.\n\nSealed at noon." };
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  (api.absorbScene as any).mockResolvedValue({
    ...LORE_REVIEW, edits: [LORE_REVIEW.edits[0], twin] });
  (api.saveChronicle as any).mockRejectedValueOnce(new ApiError(
    409, "some proposed changes no longer match what is stored", "edit_conflicts",
    { conflicts: [PACT_CONFLICT, { ...PACT_CONFLICT, after: twin.after, index: 1 }] }));
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  fireEvent.click(await screen.findByRole("button", { name: /Save summary/ }));
  await screen.findByText(/2 proposed changes no longer match/);

  fireEvent.click(screen.getAllByRole("button", { name: /Replace stored The Pact/ })[0]);

  // one answered, one still waiting -- not both
  expect(await screen.findByText(/One proposed change no longer matches/)).toBeTruthy();
  expect(screen.getAllByRole("button", { name: /Replace stored The Pact/ })).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: /Save summary/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalledTimes(2));
  const sent = (api.saveChronicle as any).mock.calls[1][2].edits;
  expect(sent.map((e: any) => e.resolve)).toEqual(["replace", undefined]);
});

test("a conflict on the later of two same-id rows lands on that row", async () => {
  // The server drops the rows that were fine, so the conflict list is not
  // positionally aligned with the edits. Matching on id alone put the second
  // row's verdict on the first — answering a proposal nobody looked at while
  // the drifted one stayed unanswered.
  const { ApiError } = await vi.importActual<typeof import("../api/client")>("../api/client");
  const twin = { ...LORE_REVIEW.edits[0], after: "Signed at dusk.\n\nSealed at noon." };
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  (api.absorbScene as any).mockResolvedValue({
    ...LORE_REVIEW, edits: [LORE_REVIEW.edits[0], twin] });
  (api.saveChronicle as any).mockRejectedValueOnce(new ApiError(
    409, "some proposed changes no longer match what is stored", "edit_conflicts",
    // only the SECOND row conflicts; the first was fine and is not in the list
    { conflicts: [{ ...PACT_CONFLICT, after: twin.after, index: 1,
                    merged: "Witnessed by the watch.\n\nSealed at noon." }] }));
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  fireEvent.click(await screen.findByRole("button", { name: /Save summary/ }));
  await screen.findByText(/One proposed change no longer matches/);

  fireEvent.click(screen.getByRole("button", { name: /Merge stored The Pact/ }));

  // the merged draft went into the SECOND row's box, not the first's
  const boxes = screen.getAllByLabelText("After The Pact — lore");
  expect((boxes[0] as HTMLTextAreaElement).value).toBe("Signed at dusk.\n\nBroken by morning.");
  expect((boxes[1] as HTMLTextAreaElement).value).toBe("Witnessed by the watch.\n\nSealed at noon.");
  fireEvent.click(screen.getByRole("button", { name: /Save summary/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalledTimes(2));
  expect((api.saveChronicle as any).mock.calls[1][2].edits.map((e: any) => e.resolve))
    .toEqual([undefined, "merge"]);
});

test("a row that moves again after being answered comes back for a second answer", async () => {
  const { ApiError } = await vi.importActual<typeof import("../api/client")>("../api/client");
  await reviewIntoConflict();
  fireEvent.click(screen.getByRole("button", { name: /Replace stored The Pact/ }));
  (api.saveChronicle as any).mockRejectedValueOnce(new ApiError(
    409, "some proposed changes no longer match what is stored", "edit_conflicts",
    { conflicts: [{ ...PACT_CONFLICT, stored: "Rewritten by hand.",
                    reason: "this changed again after you answered — the value you were "
                            + "shown is not what is stored now",
                    merged: "Rewritten by hand.\n\nBroken by morning." }] }));

  fireEvent.click(screen.getByRole("button", { name: /Save summary/ }));

  expect(await screen.findByText(/changed again after you answered/)).toBeTruthy();
  expect(screen.getByText("Rewritten by hand.")).toBeTruthy();
  // answering again re-stamps the snapshot with what is on screen NOW
  fireEvent.click(screen.getByRole("button", { name: /Replace stored The Pact/ }));
  fireEvent.click(screen.getByRole("button", { name: /Save summary/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalledTimes(3));
  expect((api.saveChronicle as any).mock.calls[2][2].edits).toEqual([
    expect.objectContaining({ resolve: "replace", resolve_from: "Rewritten by hand." })]);
});

test("Merge prefills the editable text with the server's draft", async () => {
  await reviewIntoConflict();
  fireEvent.click(screen.getByRole("button", { name: /Merge stored The Pact/ }));
  expect(screen.getByLabelText("After The Pact — lore")).toHaveValue(
    "Witnessed by the watch.\n\nBroken by morning.");
  fireEvent.click(screen.getByRole("button", { name: /Save summary/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalledTimes(2));
  expect((api.saveChronicle as any).mock.calls[1][2].edits).toEqual([
    expect.objectContaining({ id: "lore:the-pact", resolve: "merge",
                              after: "Witnessed by the watch.\n\nBroken by morning." })]);
});

test("Keep stored drops the row from the batch entirely", async () => {
  await reviewIntoConflict();
  fireEvent.click(screen.getByRole("button", { name: /Keep stored The Pact/ }));
  expect(screen.queryByText("Witnessed by the watch.")).toBeNull();       // answered
  expect(screen.queryByText(/no longer match/)).toBeNull();               // and counted as such
  fireEvent.click(screen.getByRole("button", { name: /Save summary/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalledTimes(2));
  expect((api.saveChronicle as any).mock.calls[1][2].edits).toEqual([]);
});

test("a staged dossier is editable and sent with the save", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  (api.absorbScene as any).mockResolvedValue({
    one_line: "o", summary: "s", keywords: [], timeline_events: [], cast: [], location: "", date: "",
    mechanics: { status: "ok", reason: null, warnings: [], dropped: [] },
    commit_token: "tok",
    dossiers: { status: "ok", reason: null, proposed: ["seraphine"], failed: [] },
    phases: PHASES_NONE_CUT,
    edits: [{ id: "dossier:seraphine", kind: "dossier",
      target: { kind: "characters", id: "seraphine" }, label: "Seraphine — campaign dossier",
      field: "dossier", authored: false,
      before: "Seraphine is wary.", after: "Seraphine now rides with the party." }] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  const ta = await screen.findByLabelText("After Seraphine — campaign dossier");
  fireEvent.change(ta, { target: { value: "Seraphine rides ahead." } });
  fireEvent.click(screen.getByRole("button", { name: /Save summary/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalledWith("run", "s1",
    expect.objectContaining({ edits: [expect.objectContaining({
      id: "dossier:seraphine", after: "Seraphine rides ahead." })] })));
});

test("unchecking an edit excludes it from the save", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  fireEvent.click(await screen.findByLabelText("Approve Seraphine — current state"));
  fireEvent.click(screen.getByRole("button", { name: /Save summary/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalledWith("run", "s1",
    expect.objectContaining({ edits: [] })));
});

test("character_state row renders a multi-section knowledge body in its textarea", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  (api.absorbScene as any).mockResolvedValue({
    one_line: "o", summary: "s", keywords: [], timeline_events: [], cast: [], location: "", date: "",
    mechanics: { status: "ok", reason: null, warnings: [], dropped: [] },
    commit_token: "tok",
    dossiers: { status: "skipped", reason: null, proposed: [], failed: [], skipped: [] },
    phases: PHASES_NONE_CUT,
    edits: [{ id: "character_state:seraphine", kind: "character_state",
      target: { kind: "characters", id: "seraphine" }, label: "Seraphine — current state",
      field: "current_state", authored: false,
      before: "Wary.", after: "## Current state\nHurt.\n\n## Knows\nmap is fake" }] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  const ta = await screen.findByLabelText("After Seraphine — current state");
  expect((ta as HTMLTextAreaElement).value).toContain("## Knows");
  expect((ta as HTMLTextAreaElement).value).toContain("map is fake");
  fireEvent.click(screen.getByRole("button", { name: /Save summary/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalledWith("run", "s1",
    expect.objectContaining({ edits: [expect.objectContaining({
      id: "character_state:seraphine", after: "## Current state\nHurt.\n\n## Knows\nmap is fake" })] })));
});

test("plot rows are editable and sent with payload on save", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  (api.absorbScene as any).mockResolvedValue({
    one_line: "o", summary: "s", keywords: [], timeline_events: [], cast: [], location: "", date: "",
    mechanics: { status: "ok", reason: null, warnings: [], dropped: [] },
    commit_token: "tok",
    dossiers: { status: "skipped", reason: null, proposed: [], failed: [], skipped: [] },
    phases: PHASES_NONE_CUT,
    edits: [{ id: "plot:the-map", kind: "plot",
      target: { kind: "plot", id: "the-map" }, label: "The map — advanced",
      field: "beat", before: "open — Elara got it.", after: "It is a forgery.",
      authored: false, payload: { id: "the-map", title: "The map", status: "advanced", scene: "s1" } }] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  const ta = await screen.findByLabelText("After The map — advanced");
  expect((ta as HTMLTextAreaElement).value).toBe("It is a forgery.");
  fireEvent.change(ta, { target: { value: "It is a clever forgery." } });
  fireEvent.click(screen.getByRole("button", { name: /Save summary/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalledWith("run", "s1",
    expect.objectContaining({ edits: expect.arrayContaining([
      expect.objectContaining({ id: "plot:the-map", after: "It is a clever forgery.",
        payload: expect.objectContaining({ status: "advanced" }) })]) })));
});

test("new_character proposal renders editable card and provenance fields and saves them", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  (api.absorbScene as any).mockResolvedValue({
    one_line: "o", summary: "s", keywords: [], timeline_events: [], cast: [], location: "", date: "",
    mechanics: { status: "ok", reason: null, warnings: [], dropped: [] },
    commit_token: "tok",
    dossiers: { status: "skipped", reason: null, proposed: [], failed: [], skipped: [] },
    phases: PHASES_NONE_CUT,
    edits: [{ id: "new_character:old-bram", kind: "new_character",
      target: { kind: "characters", id: "" }, label: "New character — Old Bram",
      field: "description", before: "", after: "[character(\"Old Bram\") {}]", authored: false,
      payload: { name: "Old Bram", sd_prompt: "an old innkeeper",
        personality: "gruff but kind", mes_example: "<START>\n{{user}}: A room?\n{{char}}: Aye.",
        evidence: "Bram rented the party a room.", confidence: "thin",
        open_questions: "Why does he fear the pier?" } }] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  const nameInput = await screen.findByLabelText("Name New character — Old Bram");
  expect((nameInput as HTMLInputElement).value).toBe("Old Bram");
  const desc = await screen.findByLabelText("After New character — Old Bram");
  expect((desc as HTMLTextAreaElement).value).toBe("[character(\"Old Bram\") {}]");
  const personality = await screen.findByLabelText("Personality New character — Old Bram");
  expect((personality as HTMLTextAreaElement).value).toBe("gruff but kind");
  const dialogue = await screen.findByLabelText("Example dialogue New character — Old Bram");
  expect((dialogue as HTMLTextAreaElement).value).toBe("<START>\n{{user}}: A room?\n{{char}}: Aye.");
  const prompt = await screen.findByLabelText("Suggested image prompt New character — Old Bram");
  expect((prompt as HTMLInputElement).value).toBe("an old innkeeper");
  const evidence = await screen.findByLabelText(/Evidence New character.*Old Bram/);
  expect((evidence as HTMLTextAreaElement).value).toBe("Bram rented the party a room.");
  const confidence = await screen.findByLabelText(/Confidence New character.*Old Bram/);
  expect((confidence as HTMLSelectElement).value).toBe("thin");
  const questions = await screen.findByLabelText(/Open questions New character.*Old Bram/);
  expect((questions as HTMLTextAreaElement).value).toBe("Why does he fear the pier?");
  fireEvent.change(nameInput, { target: { value: "Old Man Bram" } });
  fireEvent.change(personality, { target: { value: "gruff, secretly gentle" } });
  fireEvent.change(prompt, { target: { value: "a grizzled innkeeper" } });
  fireEvent.change(evidence, { target: { value: "Bram warned the party away from the pier." } });
  fireEvent.change(confidence, { target: { value: "sketched" } });
  fireEvent.change(questions, { target: { value: "Who pays Bram for rumors?" } });
  fireEvent.click(screen.getByRole("button", { name: /Save summary/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalledWith("run", "s1",
    expect.objectContaining({ edits: [expect.objectContaining({
      id: "new_character:old-bram",
      payload: { name: "Old Man Bram", sd_prompt: "a grizzled innkeeper",
        personality: "gruff, secretly gentle",
        mes_example: "<START>\n{{user}}: A room?\n{{char}}: Aye.",
        evidence: "Bram warned the party away from the pier.",
        confidence: "sketched",
        open_questions: "Who pays Bram for rumors?" } })] })));
});

test("new_location shows the setting checkbox only when the scene has no location", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  (api.absorbScene as any).mockResolvedValue({
    one_line: "o", summary: "s", keywords: [], timeline_events: [], cast: [], location: "", date: "",
    mechanics: { status: "ok", reason: null, warnings: [], dropped: [] },
    commit_token: "tok",
    dossiers: { status: "skipped", reason: null, proposed: [], failed: [], skipped: [] },
    phases: PHASES_NONE_CUT,
    edits: [{ id: "new_location:the-crypt", kind: "new_location",
      target: { kind: "locations", id: "" }, label: "New location — The Crypt",
      field: "body", before: "", after: "A cold crypt.", authored: false,
      payload: { name: "The Crypt", keys: "crypt", sd_prompt: "a dark crypt", current_setting: false } }] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  const setting = await screen.findByLabelText("This is where the scene happened New location — The Crypt");
  fireEvent.click(setting);
  fireEvent.click(screen.getByRole("button", { name: /Save summary/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalledWith("run", "s1",
    expect.objectContaining({ edits: [expect.objectContaining({
      payload: expect.objectContaining({ current_setting: true }) })] })));
});

test("new_location hides the setting checkbox when the scene already has a location", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  (api.absorbScene as any).mockResolvedValue({
    one_line: "o", summary: "s", keywords: [], timeline_events: [], cast: [], location: "Old Dock", date: "",
    mechanics: { status: "ok", reason: null, warnings: [], dropped: [] },
    commit_token: "tok",
    dossiers: { status: "skipped", reason: null, proposed: [], failed: [], skipped: [] },
    phases: PHASES_NONE_CUT,
    edits: [{ id: "new_location:the-crypt", kind: "new_location",
      target: { kind: "locations", id: "" }, label: "New location — The Crypt",
      field: "body", before: "", after: "A cold crypt.", authored: false,
      payload: { name: "The Crypt", keys: "crypt", sd_prompt: "", current_setting: false } }] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByLabelText("After New location — The Crypt");
  expect(screen.queryByLabelText("This is where the scene happened New location — The Crypt")).toBeNull();
});

test("relationship rows are read-only and sent with payload on save", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  (api.absorbScene as any).mockResolvedValue({
    one_line: "o", summary: "s", keywords: [], timeline_events: [], cast: [], location: "", date: "",
    mechanics: { status: "ok", reason: null, warnings: [], dropped: [] },
    commit_token: "tok",
    dossiers: { status: "skipped", reason: null, proposed: [], failed: [], skipped: [] },
    phases: PHASES_NONE_CUT,
    edits: [{ id: "feeling:characters:a->characters:b", kind: "relationship",
      target: { kind: "relationships", id: "characters:a->characters:b" }, label: "Ann → Bo",
      field: "feeling", before: "trust 1, affection 1, tension 3", after: "trust 4, affection 3, tension 1",
      authored: false, payload: { from: "characters:a", to: "characters:b", trust: 4, affection: 3, tension: 1, note: "" } }] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("Ann → Bo");
  expect(screen.queryByLabelText("After Ann → Bo")).toBeNull();
  expect(screen.getByText(/trust 4, affection 3, tension 1/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: /Save summary/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalledWith("run", "s1",
    expect.objectContaining({ edits: expect.arrayContaining([
      expect.objectContaining({ id: "feeling:characters:a->characters:b",
        payload: expect.objectContaining({ trust: 4 }) })]) })));
});

const SHEET_EDIT = { id: "sheet:characters:mara:hp", kind: "sheet",
  target: { kind: "characters", id: "mara" }, label: "Mara — HP", field: "hp",
  before: "hp 6/10", after: "hp 4/10", authored: false, payload: { note: "took a hit" } };

test("mechanics: warnings render with a ⚠ prefix; a clean run shows the hint instead", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  (api.absorbScene as any).mockResolvedValue({
    one_line: "o", summary: "s", keywords: [], timeline_events: [], cast: [], location: "", date: "",
    mechanics: { status: "ok", reason: null, warnings: ["Mara claimed a hit with no roll"], dropped: [] },
    commit_token: "tok",
    dossiers: { status: "skipped", reason: null, proposed: [], failed: [], skipped: [] },
    phases: PHASES_NONE_CUT,
    edits: [] });
  const { unmount } = renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("⚠ Mara claimed a hit with no roll");
  expect(screen.queryByText("mechanics audited clean")).toBeNull();
  unmount();

  (api.absorbScene as any).mockResolvedValue({
    one_line: "o", summary: "s", keywords: [], timeline_events: [], cast: [], location: "", date: "",
    mechanics: { status: "ok", reason: null, warnings: [], dropped: [] },
    commit_token: "tok",
    dossiers: { status: "skipped", reason: null, proposed: [], failed: [], skipped: [] },
    phases: PHASES_NONE_CUT,
    edits: [] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("mechanics audited clean");
});

test("skipped mechanics renders no mechanics section", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  (api.absorbScene as any).mockResolvedValue({
    one_line: "o", summary: "s", keywords: [], timeline_events: [], cast: [], location: "", date: "",
    mechanics: { status: "skipped", reason: null, warnings: [], dropped: [] },
    commit_token: "tok",
    dossiers: { status: "skipped", reason: null, proposed: [], failed: [], skipped: [] },
    phases: PHASES_NONE_CUT,
    edits: [] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("Review scene summary");
  expect(screen.queryByText("mechanics audited clean")).toBeNull();
  expect(screen.queryByText(/⚠/)).toBeNull();
  expect(screen.queryByText(/Mechanics validation failed/)).toBeNull();
  expect(screen.queryByText(/could not be validated/)).toBeNull();
});

test("failed mechanics shows a notice with Retry validation; retry replaces sheet rows and clears the notice", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  (api.absorbScene as any).mockResolvedValue({
    one_line: "o", summary: "s", keywords: [], timeline_events: [], cast: [], location: "", date: "",
    mechanics: { status: "failed", reason: "boom", warnings: [], dropped: [] },
    commit_token: "tok",
    dossiers: { status: "skipped", reason: null, proposed: [], failed: [], skipped: [] },
    phases: PHASES_NONE_CUT,
    edits: [] });
  (api.retryAudit as any).mockResolvedValue({
    mechanics: { status: "ok", reason: null, warnings: [], dropped: [] },
    commit_token: "tok",
    edits: [SHEET_EDIT] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("Mechanics validation failed: boom");
  fireEvent.click(screen.getByRole("button", { name: /Retry validation/ }));
  await waitFor(() => expect(screen.queryByText(/Mechanics validation failed/)).toBeNull());
  expect(await screen.findByText("Mara — HP")).toBeInTheDocument();
  expect(api.retryAudit).toHaveBeenCalledWith("run", "s1");
});

test("a rejected retryAudit surfaces an error and leaves the mechanics notice/rows untouched", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  (api.absorbScene as any).mockResolvedValue({
    one_line: "o", summary: "s", keywords: [], timeline_events: [], cast: [], location: "", date: "",
    mechanics: { status: "failed", reason: "boom", warnings: [], dropped: [] },
    commit_token: "tok",
    dossiers: { status: "skipped", reason: null, proposed: [], failed: [], skipped: [] },
    phases: PHASES_NONE_CUT,
    edits: [] });
  (api.retryAudit as any).mockRejectedValue({ detail: "audit retry blew up" });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("Mechanics validation failed: boom");
  fireEvent.click(screen.getByRole("button", { name: /Retry validation/ }));
  await screen.findByText("audit retry blew up");
  // the failed-mechanics panel state is untouched by the rejection
  expect(screen.getByText("Mechanics validation failed: boom")).toBeInTheDocument();
  expect(screen.queryByText("Mara — HP")).toBeNull();
});

test("unapproved non-sheet rows survive Retry validation without duplicating", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  const LORE_EDIT = { id: "lore:old-dock", kind: "lore",
    target: { kind: "lore", id: "old-dock" }, label: "Old Dock — lore",
    field: "body", before: "quiet.", after: "quiet, but watched.", authored: false };
  (api.absorbScene as any).mockResolvedValue({
    one_line: "o", summary: "s", keywords: [], timeline_events: [], cast: [], location: "", date: "",
    mechanics: { status: "failed", reason: "boom", warnings: [], dropped: [] },
    commit_token: "tok",
    dossiers: { status: "skipped", reason: null, proposed: [], failed: [], skipped: [] },
    phases: PHASES_NONE_CUT,
    edits: [LORE_EDIT] });
  (api.retryAudit as any).mockResolvedValue({
    mechanics: { status: "ok", reason: null, warnings: [], dropped: [] },
    commit_token: "tok",
    edits: [SHEET_EDIT] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("Mechanics validation failed: boom");
  const checkbox = screen.getByLabelText(`Approve ${LORE_EDIT.label}`) as HTMLInputElement;
  expect(checkbox.checked).toBe(true);
  fireEvent.click(checkbox);
  expect(checkbox.checked).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: /Retry validation/ }));
  await waitFor(() => expect(screen.queryByText(/Mechanics validation failed/)).toBeNull());
  expect(await screen.findByText("Mara — HP")).toBeInTheDocument();
  const loreCheckboxes = screen.getAllByLabelText(`Approve ${LORE_EDIT.label}`);
  expect(loreCheckboxes).toHaveLength(1);
  expect((loreCheckboxes[0] as HTMLInputElement).checked).toBe(false);
});

test("degraded mechanics shows a notice listing dropped findings", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  (api.absorbScene as any).mockResolvedValue({
    one_line: "o", summary: "s", keywords: [], timeline_events: [], cast: [], location: "", date: "",
    mechanics: { status: "degraded", reason: null, warnings: [],
      dropped: [{ id: "characters:mara", field: "athletics", reason: "static tamper" }] },
    dossiers: { status: "skipped", reason: null, proposed: [], failed: [], skipped: [] },
    phases: PHASES_NONE_CUT,
    edits: [] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("Some mechanics findings could not be validated");
  expect(screen.getByText(/characters:mara athletics: static tamper/)).toBeInTheDocument();
});

const absorbWithDossiers = (dossiers: unknown) =>
  (api.absorbScene as any).mockResolvedValue({
    one_line: "o", summary: "s", keywords: [], timeline_events: [], cast: [], location: "", date: "",
    mechanics: { status: "skipped", reason: null, warnings: [], dropped: [] },
    commit_token: "tok",
    dossiers, phases: PHASES_NONE_CUT, edits: [] });

test("failed dossier refreshes are listed per NPC instead of passing silently", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  absorbWithDossiers({ status: "degraded", reason: "some dossiers could not be prepared",
    proposed: ["mara"], failed: [{ id: "winifred", reason: "rate limited" }], skipped: [] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("Some NPC dossiers could not be prepared");
  expect(screen.getByText(/winifred: rate limited/)).toBeInTheDocument();
});

test("dossiers the absorb budget skipped are named, not silently missing", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  absorbWithDossiers({ status: "degraded",
    reason: "the absorb time budget ran out before the rest could be prepared",
    proposed: ["mara"], failed: [], skipped: ["winifred"] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText(/the absorb time budget ran out/);
  expect(screen.getByText(/skipped: winifred/)).toBeInTheDocument();
});

test("a partly-prepared dossier phase does not call itself failed", async () => {
  // mara's dossier was prepared; only winifred's was dropped. Calling that
  // "refresh failed" contradicts the edit sitting in the list beside it.
  absorbWithPhases(
    phasesFor({ dossiers: { status: "degraded",
                            reason: "the absorb time budget ran out before the rest could be prepared",
                            attempted: true, budget_exhausted: true } }),
    { dossiers: { status: "degraded",
                  reason: "the absorb time budget ran out before the rest could be prepared",
                  proposed: ["mara"], failed: [], skipped: ["winifred"],
                  attempted: true, budget_exhausted: true } });
  await openAbsorb();

  await screen.findByText(/Some NPC dossiers were not prepared: the absorb time budget ran out/);
  expect(screen.queryByText(/dossier refresh failed/)).toBeNull();
});

test("every NPC failing reads as total failure, not partial", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  absorbWithDossiers({ status: "failed", reason: "no dossier could be prepared",
    proposed: [], failed: [{ id: "winifred", reason: "LLMError: rate limited" }], skipped: [] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("No NPC dossier could be prepared");
  expect(screen.queryByText(/Some NPC dossiers/)).toBeNull();
  expect(screen.getByText(/winifred: LLMError: rate limited/)).toBeInTheDocument();
});

test("a whole-phase dossier failure shows its reason", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  absorbWithDossiers({ status: "failed", reason: "could not read the scene cast: boom",
    proposed: [], failed: [], skipped: [] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("NPC dossier refresh failed: could not read the scene cast: boom");
});

test("clean and skipped dossier phases render no notice", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  absorbWithDossiers({ status: "ok", reason: null, proposed: ["mara"], failed: [], skipped: [] });
  const { unmount } = renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("Review scene summary");
  expect(screen.queryByText(/dossier/i)).toBeNull();
  unmount();

  absorbWithDossiers({ status: "skipped", reason: "no npcs present", proposed: [], failed: [], skipped: [] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("Review scene summary");
  expect(screen.queryByText(/dossier/i)).toBeNull();
});

// ---- absorb phases: a run the time budget cut short says so ----

const absorbWithPhases = (phases: unknown, over: Record<string, unknown> = {}) =>
  (api.absorbScene as any).mockResolvedValue({
    one_line: "o", summary: "s", keywords: [], timeline_events: [], cast: [], location: "", date: "",
    mechanics: { status: "skipped", reason: null, warnings: [], dropped: [],
                 attempted: false, budget_exhausted: false },
    dossiers: { status: "skipped", reason: null, proposed: [], failed: [], skipped: [],
                attempted: false, budget_exhausted: false },
    commit_token: "tok", phases, edits: [], ...over });

const openAbsorb = async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("Review scene summary");
};

test("an absorb the budget cut short names the steps that never ran", async () => {
  // The reported failure mode: extraction eats the clock, so the review panel
  // shows fewer proposed changes and nothing says why.
  absorbWithPhases([
    { name: "extraction", status: "ok", reason: null, attempted: true, budget_exhausted: false },
    { name: "dossiers", status: "failed", reason: "the absorb time budget ran out",
      attempted: false, budget_exhausted: true },
    { name: "audit", status: "failed", reason: "the absorb time budget ran out",
      attempted: false, budget_exhausted: true },
  ]);
  await openAbsorb();

  await screen.findByText(/only partly absorbed/);
  expect(screen.getByText(/NPC dossiers, mechanics audit/)).toBeInTheDocument();
});

test("a phase that ran and failed on its own merits is not blamed on the clock", async () => {
  absorbWithPhases([
    { name: "extraction", status: "ok", reason: null, attempted: true, budget_exhausted: false },
    { name: "dossiers", status: "ok", reason: null, attempted: true, budget_exhausted: false },
    { name: "audit", status: "failed", reason: "audit failed: boom",
      attempted: true, budget_exhausted: false },
  ], { mechanics: { status: "failed", reason: "audit failed: boom", warnings: [], dropped: [],
                    attempted: true, budget_exhausted: false } });
  await openAbsorb();

  await screen.findByText("Mechanics validation failed: audit failed: boom");
  expect(screen.queryByText(/only partly absorbed/)).toBeNull();
});

/** Phase rows that agree with the blocks, the way the backend's projection
 *  guarantees — a row claiming the clock while its block claims otherwise is a
 *  state the API cannot produce, so no test should assert against it. */
const phasesFor = (over: Record<string, any>) =>
  [{ name: "extraction", status: "ok", reason: null, attempted: true, budget_exhausted: false },
   { name: "dossiers", ...(over.dossiers ?? { status: "ok", reason: null, attempted: true, budget_exhausted: false }) },
   { name: "audit", ...(over.mechanics ?? { status: "ok", reason: null, attempted: true, budget_exhausted: false }) }]
    .map(({ name, status, reason, attempted, budget_exhausted }) =>
      ({ name, status, reason, attempted, budget_exhausted }));

test("a budget-cut audit reads as never run, and still offers the retry", async () => {
  const over = {
    mechanics: { status: "failed", reason: "the absorb time budget ran out before the audit could run",
                 warnings: [], dropped: [], attempted: false, budget_exhausted: true },
  };
  absorbWithPhases(phasesFor(over), over);
  await openAbsorb();

  await screen.findByText(/Mechanics validation never ran: the absorb time budget ran out/);
  expect(screen.queryByText(/Mechanics validation failed/)).toBeNull();
  expect(screen.getByRole("button", { name: /Retry validation/ })).toBeInTheDocument();
});

test("a successful audit retry clears the budget notice it was offered for", async () => {
  // Retry replaces `mechanics`; the phase row it was projected from has to
  // move with it, or the panel keeps warning about a step that has since run.
  const over = {
    mechanics: { status: "failed", reason: "the absorb time budget ran out before the audit could run",
                 warnings: [], dropped: [], attempted: false, budget_exhausted: true },
  };
  absorbWithPhases(phasesFor(over), over);
  (api.retryAudit as any).mockResolvedValue({
    mechanics: { status: "ok", reason: null, warnings: [], dropped: [],
                 attempted: true, budget_exhausted: false },
    edits: [] });
  await openAbsorb();

  await screen.findByText(/only partly absorbed/);
  fireEvent.click(screen.getByRole("button", { name: /Retry validation/ }));
  await waitFor(() => expect(screen.queryByText(/only partly absorbed/)).toBeNull());
});

test("Retry validation audits the review's scene, not whichever is on screen", async () => {
  // A review outlives a scene switch (only Discard and a successful save clear
  // it), so the retry has to follow `absorbSid` the way `saveAbsorb` already
  // does — otherwise it audits the scene the user has since opened and writes
  // that verdict, its sheet edits and its phase row into the other scene's
  // review.
  (api.listScenes as any).mockResolvedValue([
    { id: "s1", title: "One", model: "", created: "", updated: "", date: "" },
    { id: "s2", title: "Two", model: "", created: "", updated: "", date: "" }]);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  const over = {
    mechanics: { status: "failed", reason: "the absorb time budget ran out before the audit could run",
                 warnings: [], dropped: [], attempted: false, budget_exhausted: true },
  };
  absorbWithPhases(phasesFor(over), over);
  (api.retryAudit as any).mockResolvedValue({
    mechanics: { status: "ok", reason: null, warnings: [], dropped: [],
                 attempted: true, budget_exhausted: false },
    edits: [] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("Review scene summary");

  fireEvent.click(screen.getByText(/Two/));                        // switch scenes
  await waitFor(() => expect(api.getScene).toHaveBeenCalledWith("run", "s2", { limit: 60 }));
  fireEvent.click(screen.getByRole("button", { name: /Retry validation/ }));

  await waitFor(() => expect(api.retryAudit).toHaveBeenCalled());
  expect((api.retryAudit as any).mock.calls[0][1]).toBe("s1");
});

test("renaming the reviewed scene moves the review's id with it", async () => {
  // A scene's id is derived from its title, so a rename mints a new one. The
  // open review still points at the old id — and both the retry and the save
  // would POST a scene that no longer exists. `renameScene` already migrates
  // `seedPrompt.sid` for this reason; the review id belongs in that list.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  (api.renameScene as any).mockResolvedValue({ id: "s1-renamed", title: "New" });
  const over = {
    mechanics: { status: "failed", reason: "boom", warnings: [], dropped: [],
                 attempted: true, budget_exhausted: false },
  };
  absorbWithPhases(phasesFor(over), over);
  (api.retryAudit as any).mockResolvedValue({
    mechanics: { status: "ok", reason: null, warnings: [], dropped: [],
                 attempted: true, budget_exhausted: false },
    edits: [] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("Review scene summary");

  fireEvent.click(screen.getByRole("button", { name: /rename/i }));
  const input = screen.getByDisplayValue("Old");
  fireEvent.change(input, { target: { value: "New" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(api.renameScene).toHaveBeenCalled());

  fireEvent.click(screen.getByRole("button", { name: /Retry validation/ }));
  await waitFor(() => expect(api.retryAudit).toHaveBeenCalled());
  expect((api.retryAudit as any).mock.calls[0][1]).toBe("s1-renamed");
});

test("renaming the reviewed scene repoints its staged plot edits too", async () => {
  // `payload.scene` is embedded by absorb.materialize and handed straight to
  // plot.set_movement on save. It lives only in this browser, so the server's
  // scene_refs.repoint pass cannot reach it — a rename that moved only
  // `absorbSid` would save beats pointing at a scene id that no longer exists.
  const PLOT_EDIT = {
    id: "plot:the-siege", kind: "plot", target: { kind: "plot", id: "the-siege" },
    label: "The Siege", field: "status", before: "open", after: "escalating",
    authored: false, payload: { id: "the-siege", title: "The Siege", status: "escalating", scene: "s1" },
  };
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  (api.renameScene as any).mockResolvedValue({ id: "s1-renamed", title: "New" });
  absorbWithPhases(PHASES_NONE_CUT, { edits: [PLOT_EDIT] });
  (api.saveChronicle as any).mockResolvedValue({ id: "s1-renamed", one_line: "o", summary: "s",
    keywords: [], cast: [], location: "", date: "", absorbed: "t", applied: [], failures: [] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("Review scene summary");

  fireEvent.click(screen.getByRole("button", { name: /rename/i }));
  const input = screen.getByDisplayValue("Old");
  fireEvent.change(input, { target: { value: "New" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(api.renameScene).toHaveBeenCalled());

  fireEvent.click(screen.getByRole("button", { name: /Save summary/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalled());
  const saved = (api.saveChronicle as any).mock.calls[0][2];
  expect(saved.edits[0].payload.scene).toBe("s1-renamed");
});

test("the budget notice never sends the reviewer back through End scene", async () => {
  // End scene posts the *active* scene and replaces the review wholesale, so
  // advising it here would tell the user to discard the edits this very notice
  // has just told them are complete.
  absorbWithPhases([
    { name: "extraction", status: "ok", reason: null, attempted: true, budget_exhausted: false },
    { name: "dossiers", status: "failed", reason: "the absorb time budget ran out",
      attempted: false, budget_exhausted: true },
    { name: "audit", status: "ok", reason: null, attempted: true, budget_exhausted: false },
  ]);
  await openAbsorb();

  await screen.findByText(/only partly absorbed/);
  expect(screen.getByText(/Raise the absorb budget/)).toBeInTheDocument();
  expect(screen.queryByText(/end the scene again/i)).toBeNull();
});

test("a budget-cut dossier phase reads as never prepared, not as a failure", async () => {
  const over = {
    dossiers: { status: "failed",
                reason: "the absorb time budget ran out before any dossier could be prepared",
                proposed: [], failed: [], skipped: ["winifred"],
                attempted: false, budget_exhausted: true },
  };
  absorbWithPhases(phasesFor(over), over);
  await openAbsorb();

  await screen.findByText(/No NPC dossier was prepared: the absorb time budget ran out/);
  expect(screen.queryByText("No NPC dossier could be prepared")).toBeNull();
  expect(screen.getByText(/skipped: winifred/)).toBeInTheDocument();
});

test("sheet edits render read-only with the note and survive save", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  (api.absorbScene as any).mockResolvedValue({
    one_line: "o", summary: "s", keywords: [], timeline_events: [], cast: [], location: "", date: "",
    mechanics: { status: "ok", reason: null, warnings: [], dropped: [] },
    commit_token: "tok",
    dossiers: { status: "skipped", reason: null, proposed: [], failed: [], skipped: [] },
    phases: PHASES_NONE_CUT,
    edits: [SHEET_EDIT] });
  (api.saveChronicle as any).mockResolvedValue({ id: "s1", one_line: "o", summary: "s", keywords: [],
    cast: [], location: "", date: "", absorbed: "t",
    applied: ["sheet:characters:mara:hp"], failures: [] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("Mara — HP");
  expect(screen.getByText("hp 6/10")).toBeInTheDocument();
  expect(screen.getByText("hp 4/10")).toBeInTheDocument();
  expect(screen.getByText("took a hit")).toBeInTheDocument();
  expect(screen.queryByLabelText("After Mara — HP")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /Save summary/ }));
  await waitFor(() => expect(api.saveChronicle).toHaveBeenCalled());
  expect(screen.queryByText(/did not apply/)).toBeNull();
});

test("failures from save render a notice", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  (api.absorbScene as any).mockResolvedValue({
    one_line: "o", summary: "s", keywords: [], timeline_events: [], cast: [], location: "", date: "",
    mechanics: { status: "ok", reason: null, warnings: [], dropped: [] },
    commit_token: "tok",
    dossiers: { status: "skipped", reason: null, proposed: [], failed: [], skipped: [] },
    phases: PHASES_NONE_CUT,
    edits: [SHEET_EDIT] });
  (api.saveChronicle as any).mockResolvedValue({ id: "s1", one_line: "o", summary: "s", keywords: [],
    cast: [], location: "", date: "", absorbed: "t", applied: [],
    failures: [{ id: "sheet:characters:mara:hp", reason: "changed", kind: "conflict" }] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("Mara — HP");
  fireEvent.click(screen.getByRole("button", { name: /Save summary/ }));
  await screen.findByText("1 change did not apply");
  expect(screen.getByText(/Mara — HP/)).toBeInTheDocument();
  expect(screen.getByText("Mara — HP: changed (conflict)")).toBeInTheDocument();

  // A stale failures notice must not survive into the next scene's
  // absorb panel -- opening a new one (End scene) clears it immediately.
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await waitFor(() => expect(screen.queryByText(/did not apply/)).toBeNull());
});

test("Changes tab reveals the changes panel", async () => {
  renderCampaign();
  fireEvent.click(await screen.findByRole("button", { name: /^Changes$/ }));
  expect(await screen.findByText(/No record changes yet/)).toBeInTheDocument();
});

test("an unstamped user line renders the sole player's name", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getCast as any).mockResolvedValue([
    { kind: "pcs", id: "elara-vane", role: "player", name: "Elara Vane" },
    { kind: "characters", id: "seraphine", role: "npc", name: "Seraphine Vale" },
  ]);
  (api.getScene as any).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "user", content: "I open the door." },
    { role: "assistant", content: "She waits.", speaker: "Seraphine Vale" },
  ] });
  renderCampaign();
  await screen.findByText("Elara Vane");
  expect(screen.getByText("Seraphine Vale")).toBeInTheDocument();
});

test("a stored speaker beats the player-name fallback", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getCast as any).mockResolvedValue([
    { kind: "pcs", id: "elara-vane", role: "player", name: "Elara Vane" }]);
  (api.getScene as any).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "user", content: "spoken as someone else", speaker: "Old Name" }] });
  renderCampaign();
  await screen.findByText("Old Name");
  expect(screen.queryByText("Elara Vane")).toBeNull();
});

test("after a stream completes the scene is re-fetched", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any)
    .mockResolvedValueOnce({ meta: {}, messages: [] })
    .mockResolvedValue({ meta: {}, messages: [
      { role: "user", content: "hello" },
      { role: "assistant", content: "Thunder rolls." },
      { role: "assistant", content: "Who goes there?", speaker: "Seraphine Vale" },
    ] });
  (api.chat as any).mockImplementation(async (_c: string, _s: string, _t: string, onEvent: any) => {
    onEvent({ delta: "**Grimoire:** Thunder rolls." });
  });
  renderCampaign();
  await screen.findByText(/01 · Old/);
  const ta = screen.getByRole("textbox");
  fireEvent.change(ta, { target: { value: "hello" } });
  fireEvent.keyDown(ta, { key: "Enter" });
  await screen.findByText("Who goes there?");
  expect(api.getScene).toHaveBeenCalledTimes(2);
});

test("no Reroll when every message is assistant-side (multi-post opener)", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "assistant", content: "opener one" },
    { role: "assistant", content: "opener two", speaker: "Seraphine Vale" }] });
  renderCampaign();
  await screen.findByText("opener two");
  expect(screen.queryByRole("button", { name: /reroll/i })).toBeNull();
});

test("world name comes from the campaign payload, with no world fetch", async () => {
  (api.getCampaign as any).mockResolvedValue({
    meta: { id: "run", name: "Run One", world: "w", world_name: "Saltmarch" }, body: "" });
  renderCampaign();
  expect(await screen.findByText(/World ▸ Saltmarch/)).toBeInTheDocument();
  expect(api.getWorld).not.toHaveBeenCalled();
});

test("a first-name speaker matches its cast member (fuzzy, unique prefix)", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getCast as any).mockResolvedValue([
    { kind: "characters", id: "winifred", role: "npc", name: "Winifred Vance" },
    { kind: "pcs", id: "yara", role: "player", name: "Yara Vane" },
  ]);
  (api.getScene as any).mockResolvedValue({
    meta: { id: "s1", title: "Old" },
    messages: [
      { role: "assistant", content: "She smiles.", speaker: "Winifred" },
      { role: "user", content: "Hello.", speaker: "Yara" },
    ],
  });
  renderCampaign();
  // both short labels resolve to cast members: clickable plates, pc coloring
  const winifred = await screen.findByRole("button", { name: "Winifred" });
  expect(winifred).toBeInTheDocument();
  expect(document.querySelector(".plate.pc")).not.toBeNull(); // "Yara" -> player Yara Vane
});

const OFFSCREEN_SCENE = [{ id: "s1", title: "Cabal", model: "", created: "", updated: "", pcless: true }];

test("offscreen scene: director composer, Continue button, badges", async () => {
  (api.listScenes as any).mockResolvedValue(OFFSCREEN_SCENE);
  renderCampaign();
  await screen.findByPlaceholderText(/direct the scene/i);
  expect(screen.getByRole("button", { name: /continue ▶/i })).toBeInTheDocument();
  // one "Offscreen" chip by the title + one subtitle on the rail row
  expect(screen.getAllByText("Offscreen")).toHaveLength(2);
});

test("offscreen scene: empty Continue sends an empty note", async () => {
  (api.listScenes as any).mockResolvedValue(OFFSCREEN_SCENE);
  renderCampaign();
  fireEvent.click(await screen.findByRole("button", { name: /continue ▶/i }));
  await waitFor(() => expect(api.chat).toHaveBeenCalledWith("run", "s1", "", expect.any(Function), undefined, expect.any(AbortSignal)));
});

test("offscreen scene: typed note shows transiently, never lands in messages", async () => {
  (api.listScenes as any).mockResolvedValue(OFFSCREEN_SCENE);
  let release: () => void = () => {};
  (api.chat as any).mockReturnValue(new Promise<void>((r) => { release = () => r(); }));
  renderCampaign();
  const box = await screen.findByPlaceholderText(/direct the scene/i);
  fireEvent.change(box, { target: { value: "the guard grows suspicious" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByText(/🎬 the guard grows suspicious/);
  release();
  await waitFor(() => expect(screen.queryByText(/🎬/)).toBeNull());
});

test("normal scene: plain placeholder, Continue on empty input, Send once typed", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  const box = await screen.findByPlaceholderText(/speak your intent/i);
  expect(screen.getByRole("button", { name: /continue ▶/i })).toBeInTheDocument();
  fireEvent.change(box, { target: { value: "I draw my blade." } });
  expect(screen.getByRole("button", { name: /send ▸/i })).toBeInTheDocument();
});

test("normal scene: empty Continue sends an ephemeral round, no user message added", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  fireEvent.click(await screen.findByRole("button", { name: /continue ▶/i }));
  await waitFor(() => expect(api.chat).toHaveBeenCalledWith("run", "s1", "", expect.any(Function), undefined, expect.any(AbortSignal)));
});

test("Roll dice is disabled on a fresh scene until the opener/cast setup produces a message", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  const rollBtn = await screen.findByRole("button", { name: "Roll dice" });
  expect(rollBtn).toBeDisabled();
});

test("renders an export menu with a download link per format", async () => {
  renderCampaign();
  const epub = await screen.findByRole("link", { name: /^epub$/i });
  expect(epub).toHaveAttribute("href", "/api/campaigns/run/export.epub");
  expect(epub).toHaveAttribute("download");
  expect(screen.getByRole("link", { name: /markdown/i }))
    .toHaveAttribute("href", "/api/campaigns/run/export.md.zip");
  expect(screen.getByRole("link", { name: /^html$/i }))
    .toHaveAttribute("href", "/api/campaigns/run/export.html");
  expect(screen.getByRole("link", { name: /plain text/i }))
    .toHaveAttribute("href", "/api/campaigns/run/export.txt");
  expect(screen.getByRole("link", { name: /^json$/i }))
    .toHaveAttribute("href", "/api/campaigns/run/export.json");
});

test("rolls dice from the input bar popover and refreshes the scene", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  (api.roll as any).mockResolvedValue({ ok: true, roll: { id: "r1" }, message: "🎲" });
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(screen.getByRole("button", { name: "Roll dice" }));
  fireEvent.change(screen.getByLabelText("Dice notation"), { target: { value: "2d6+1" } });
  fireEvent.change(screen.getByLabelText("Roll label"), { target: { value: "Perception" } });
  fireEvent.click(screen.getByRole("button", { name: "Roll ▸" }));
  await waitFor(() => expect(api.roll).toHaveBeenCalledWith("run", "s1", "2d6+1", "Perception"));
  // popover closes and the scene re-fetches to show the roll line
  await waitFor(() => expect(screen.queryByLabelText("Dice notation")).toBeNull());
  expect((api.getScene as any).mock.calls.length).toBeGreaterThan(1);
});

test("disables roll submission while a roll is in flight, so repeated clicks send only one", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  let resolveRoll: (v: unknown) => void;
  (api.roll as any).mockReturnValue(new Promise((resolve) => { resolveRoll = resolve; }));
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(screen.getByRole("button", { name: "Roll dice" }));
  fireEvent.change(screen.getByLabelText("Dice notation"), { target: { value: "2d6+1" } });
  const rollBtn = screen.getByRole("button", { name: "Roll ▸" });
  fireEvent.click(rollBtn);
  await waitFor(() => expect(rollBtn).toBeDisabled());
  fireEvent.click(rollBtn);
  fireEvent.click(rollBtn);
  expect(api.roll).toHaveBeenCalledTimes(1);
  resolveRoll!({ ok: true, roll: { id: "r1" }, message: "🎲" });
  await waitFor(() => expect(screen.queryByLabelText("Dice notation")).toBeNull());
});

test("shows a roll error and keeps the popover open", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  (api.roll as any).mockRejectedValue({ detail: "can't read dice notation 'garbage'" });
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(screen.getByRole("button", { name: "Roll dice" }));
  fireEvent.change(screen.getByLabelText("Dice notation"), { target: { value: "garbage" } });
  fireEvent.click(screen.getByRole("button", { name: "Roll ▸" }));
  await screen.findByText(/can't read dice notation/);
  expect(screen.getByLabelText("Dice notation")).toBeInTheDocument();
});

test("toggles an in-app dice notation syntax reference from the roll popover", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(screen.getByRole("button", { name: "Roll dice" }));
  expect(screen.queryByText(/exploding dice/i)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Dice notation syntax" }));
  expect(screen.getByText(/exploding dice/i)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Dice notation syntax" }));
  expect(screen.queryByText(/exploding dice/i)).toBeNull();
});

const PROPOSAL_PAYLOAD = {
  id: "pr-1", check: "brawl", check_label: "Vigor + Brawl",
  actor: "characters:mara", actor_label: "Mara", difficulty: 6,
  available: { "characters:mara": [["brawl", "Vigor + Brawl"]] },
  problems: [],
};

test("an SSE proposal event mounts the roll-proposal chip", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  (api.chat as any).mockImplementation(async (_c: string, _s: string, _t: string, onEvent: any) => {
    onEvent({ proposal: PROPOSAL_PAYLOAD });
  });
  // the SSE event mounts the chip immediately; runStream's finally then
  // re-fetches via selectScene — mock the backend as having durably
  // persisted the same pending record by then (its real behavior).
  (api.getRollProposal as any)
    .mockResolvedValueOnce({ record: null }) // initial scene load
    .mockResolvedValue({ record: { id: "pr-1", status: "pending", payload: PROPOSAL_PAYLOAD, resolution: null } });
  renderCampaign();
  await screen.findByText("a reply");
  const ta = screen.getByRole("textbox");
  fireEvent.change(ta, { target: { value: "I punch him" } });
  fireEvent.keyDown(ta, { key: "Enter" });
  expect(await screen.findByRole("button", { name: "Roll it" })).toBeInTheDocument();
  expect(screen.getByText(/Vigor \+ Brawl — Mara/)).toBeInTheDocument();
});

test("resolving a roll-proposal chip calls api.resolveProposal and clears the chip", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  (api.chat as any).mockImplementation(async (_c: string, _s: string, _t: string, onEvent: any) => {
    onEvent({ proposal: PROPOSAL_PAYLOAD });
  });
  (api.getRollProposal as any)
    .mockResolvedValueOnce({ record: null }) // initial scene load
    .mockResolvedValueOnce({ record: { id: "pr-1", status: "pending", payload: PROPOSAL_PAYLOAD, resolution: null } }) // after send()
    .mockResolvedValue({ record: null }); // after resolve — the backend supersedes it
  renderCampaign();
  await screen.findByText("a reply");
  const ta = screen.getByRole("textbox");
  fireEvent.change(ta, { target: { value: "I punch him" } });
  fireEvent.keyDown(ta, { key: "Enter" });
  const rollIt = await screen.findByRole("button", { name: "Roll it" });
  fireEvent.click(rollIt);
  await waitFor(() => expect(api.resolveProposal).toHaveBeenCalledWith(
    "run", "s1",
    { proposal: "pr-1", action: "accept", check: "brawl", actor: "characters:mara", difficulty: 6, modifier: 0 },
    expect.any(Function), expect.any(AbortSignal)));
  await waitFor(() => expect(screen.queryByRole("button", { name: "Roll it" })).toBeNull());
});

test("a declined roll whose narration never landed stays retryable", async () => {
  // Stopping (or an upstream failure on) a declined record's continuation
  // leaves it `declined` with nothing persisted. The backend re-streams that
  // continuation on request, but the chip used to be filtered out of the scene
  // load, so the decline narration had no way back.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "assistant", content: "a reply" }] });
  (api.getRollProposal as any).mockResolvedValue({
    record: { id: "pr-1", status: "declined", payload: PROPOSAL_PAYLOAD, resolution: null } });
  renderCampaign();
  await screen.findByText(/Roll declined, narration pending/);
  fireEvent.click(screen.getByRole("button", { name: "Continue narration" }));
  await waitFor(() => expect(api.resolveProposal).toHaveBeenCalledWith(
    "run", "s1", { proposal: "pr-1", action: "decline" },
    expect.any(Function), expect.any(AbortSignal)));
});

test("selecting a scene re-hydrates a pending roll-proposal record", async () => {
  (api.listScenes as any).mockResolvedValue([
    { id: "001--2024-01-01--one", title: "One", model: "", created: "", updated: "" },
    { id: "002--2024-01-02--two", title: "Two", model: "", created: "", updated: "" },
  ]);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "assistant", content: "a reply" }] });
  (api.getRollProposal as any)
    .mockResolvedValueOnce({ record: null }) // initial select of "one"
    .mockResolvedValue({ record: {
      id: "pr-2", status: "pending", payload: {
        id: "pr-2", check: "stealth", check_label: "Wits + Stealth",
        actor: "characters:mara", actor_label: "Mara", available: {}, problems: [] },
      resolution: null,
    } });
  renderCampaign();
  await screen.findByText("a reply");
  expect(screen.queryByRole("button", { name: "Roll it" })).toBeNull();
  fireEvent.click(screen.getByText(/Two/));
  expect(await screen.findByRole("button", { name: "Roll it" })).toBeInTheDocument();
});

test("popover Check mode with difficulty left empty posts rollCheck without difficulty", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  (api.getSceneChecks as any).mockResolvedValue({ actors: [
    { ref: "characters:mara", label: "Mara", sheet_type: "vampire",
      checks: [["brawl", "Vigor + Brawl"], ["stealth", "Wits + Stealth"]] },
  ] });
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(screen.getByRole("button", { name: "Roll dice" }));
  fireEvent.click(screen.getByRole("button", { name: "Check" }));
  await waitFor(() => expect(api.getSceneChecks).toHaveBeenCalledWith("run", "s1"));
  fireEvent.change(await screen.findByLabelText("Check actor"), { target: { value: "characters:mara" } });
  fireEvent.change(screen.getByLabelText("Check"), { target: { value: "brawl" } });
  fireEvent.click(screen.getByRole("button", { name: "Roll ▸" }));
  await waitFor(() => expect(api.rollCheck).toHaveBeenCalledWith("run", "s1",
    { check: "brawl", actor: "characters:mara", modifier: 0 }));
  const [, , rollBody] = (api.rollCheck as any).mock.calls[0];
  expect(rollBody).not.toHaveProperty("difficulty");
  await waitFor(() => expect(screen.queryByLabelText("Check actor")).toBeNull());
});

test("popover Check mode with a typed difficulty posts it", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  (api.getSceneChecks as any).mockResolvedValue({ actors: [
    { ref: "characters:mara", label: "Mara", sheet_type: "vampire",
      checks: [["brawl", "Vigor + Brawl"], ["stealth", "Wits + Stealth"]] },
  ] });
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(screen.getByRole("button", { name: "Roll dice" }));
  fireEvent.click(screen.getByRole("button", { name: "Check" }));
  await waitFor(() => expect(api.getSceneChecks).toHaveBeenCalledWith("run", "s1"));
  fireEvent.change(await screen.findByLabelText("Check actor"), { target: { value: "characters:mara" } });
  fireEvent.change(screen.getByLabelText("Check"), { target: { value: "brawl" } });
  fireEvent.change(screen.getByLabelText("Difficulty"), { target: { value: "7" } });
  fireEvent.click(screen.getByRole("button", { name: "Roll ▸" }));
  await waitFor(() => expect(api.rollCheck).toHaveBeenCalledWith("run", "s1",
    { check: "brawl", actor: "characters:mara", difficulty: 7, modifier: 0 }));
  await waitFor(() => expect(screen.queryByLabelText("Check actor")).toBeNull());
});

test("switching between two scenes that both have pending proposals shows the new scene's chip and rolls its own check, never the previous scene's", async () => {
  (api.listScenes as any).mockResolvedValue([
    { id: "001--2024-01-01--one", title: "One", model: "", created: "", updated: "" },
    { id: "002--2024-01-02--two", title: "Two", model: "", created: "", updated: "" },
  ]);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "assistant", content: "a reply" }] });
  const PROPOSAL_A = {
    id: "pr-a", check: "brawl", check_label: "Vigor + Brawl",
    actor: "characters:mara", actor_label: "Mara", difficulty: 6,
    available: { "characters:mara": [["brawl", "Vigor + Brawl"]] }, problems: [],
  };
  const PROPOSAL_B = {
    id: "pr-b", check: "stealth", check_label: "Wits + Stealth",
    actor: "characters:borys", actor_label: "Borys", difficulty: 4,
    available: { "characters:borys": [["stealth", "Wits + Stealth"]] }, problems: [],
  };
  // scenes each have their own live pending proposal — keyed by scene id, not call order.
  (api.getRollProposal as any).mockImplementation((_c: string, sid: string) => {
    if (sid.endsWith("--one")) return Promise.resolve({ record: { id: "pr-a", status: "pending", payload: PROPOSAL_A, resolution: null } });
    if (sid.endsWith("--two")) return Promise.resolve({ record: { id: "pr-b", status: "pending", payload: PROPOSAL_B, resolution: null } });
    return Promise.resolve({ record: null });
  });
  renderCampaign();
  await screen.findByText("a reply");
  expect(await screen.findByText(/Vigor \+ Brawl — Mara/)).toBeInTheDocument();
  fireEvent.click(screen.getByText(/Two/));
  expect(await screen.findByText(/Wits \+ Stealth — Borys/)).toBeInTheDocument();
  expect(screen.queryByText(/Vigor \+ Brawl — Mara/)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Roll it" }));
  await waitFor(() => expect(api.resolveProposal).toHaveBeenCalledWith(
    "run", "002--2024-01-02--two",
    { proposal: "pr-b", action: "accept", check: "stealth", actor: "characters:borys", difficulty: 4, modifier: 0 },
    expect.any(Function), expect.any(AbortSignal)));
  expect(api.resolveProposal).not.toHaveBeenCalledWith(
    "run", expect.anything(),
    expect.objectContaining({ proposal: "pr-a" }),
    expect.anything());
});

test("collapsing the scene rail hides it and shows an edge tab; clicking the tab restores it", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  await screen.findByRole("button", { name: "+ New Scene" });
  fireEvent.click(screen.getByRole("button", { name: /collapse scene list/i }));
  expect(screen.queryByRole("button", { name: "+ New Scene" })).not.toBeInTheDocument();
  const tab = screen.getByRole("button", { name: /expand scene list/i });
  fireEvent.click(tab);
  await screen.findByRole("button", { name: "+ New Scene" });
});

test("collapsing the inspector hides it and shows an edge tab; clicking the tab restores it", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  await screen.findByText("Active characters");
  fireEvent.click(screen.getByRole("button", { name: /collapse sidebar/i }));
  expect(screen.queryByText("Active characters")).not.toBeInTheDocument();
  const tab = screen.getByRole("button", { name: /expand sidebar/i });
  fireEvent.click(tab);
  await screen.findByText("Active characters");
});

test("rail and inspector collapse state persist across a remount", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  const { unmount } = renderCampaign();
  await screen.findByRole("button", { name: "+ New Scene" });
  fireEvent.click(screen.getByRole("button", { name: /collapse scene list/i }));
  await screen.findByText("Active characters");
  fireEvent.click(screen.getByRole("button", { name: /collapse sidebar/i }));
  expect(localStorage.getItem("grimoire.rail.collapsed")).toBe("1");
  expect(localStorage.getItem("grimoire.inspector.collapsed")).toBe("1");
  unmount();

  renderCampaign();
  await screen.findByRole("button", { name: /expand scene list/i }); // rail stayed collapsed
  await screen.findByRole("button", { name: /expand sidebar/i }); // inspector stayed collapsed too
  expect(screen.queryByRole("button", { name: "+ New Scene" })).not.toBeInTheDocument();
  expect(screen.queryByText("Active characters")).not.toBeInTheDocument();
});

test("the chrome bar toggles the subheader independently of the topbar toggle", async () => {
  renderCampaign();
  await screen.findByText("Run One");
  fireEvent.click(screen.getByRole("button", { name: "▴ Bar" }));
  expect(screen.queryByText("Run One")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "▾ Bar" }));
  await screen.findByText("Run One");
});

test("the chip names the preset resolved at campaign scope, not a hardcoded Standard", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  // the scene itself names no preset — the campaign does, so nothing in the
  // scene's frontmatter can tell the chip what the reply will actually be
  (api.getScene as any).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [] });
  (api.getSceneResponse as any).mockResolvedValue({
    ...RESPONSE_BUNDLE,
    effective: { ...RESPONSE_BUNDLE.effective, reply_words: 900 },
    provenance: { reply_words: { scope: "campaign", source: "preset" } },
  });
  (api.listResponsePresets as any).mockResolvedValue(RESPONSE_PRESETS);
  renderCampaign();
  const chip = await screen.findByRole("button", { name: /Response length/ });
  await waitFor(() => expect(chip).toHaveTextContent("900 words"));
  expect(chip).toHaveTextContent("this campaign");
  expect(chip).not.toHaveTextContent("Standard");
});

test("a pending one-shot pick is badged and can be cancelled without sending", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({
    meta: { id: "s1", title: "Old", response_preset: "cinematic" }, messages: [] });
  (api.listResponsePresets as any).mockResolvedValue(RESPONSE_PRESETS);
  renderCampaign();
  const chip = await screen.findByRole("button", { name: /Response length/ });
  // an inherited/scene setting carries no badge...
  expect(chip).toHaveTextContent("Cinematic");
  expect(chip).not.toHaveTextContent(/next reply only/i);
  expect(screen.queryByLabelText(/cancel the one-shot/i)).toBeNull();
  // ...a one-shot pick does, and is distinguishable from it
  fireEvent.click(chip);
  fireEvent.click(screen.getByRole("option", { name: "Terse" }));
  expect(chip).toHaveTextContent("Terse");
  expect(chip).toHaveTextContent(/next reply only/i);
  // cancelling reverts to the scene's own setting without sending anything
  fireEvent.click(screen.getByLabelText(/cancel the one-shot/i));
  expect(chip).toHaveTextContent("Cinematic");
  expect(chip).not.toHaveTextContent(/next reply only/i);
  expect(api.chat).not.toHaveBeenCalled();
});

test("a scene transition renders as unlabelled narration, with no Scene plate", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" },
    { role: "assistant", content: "a reply" },
    { role: "assistant", content: "*Time passes. It is now dusk.*", speaker: "⁣Scene" }] });
  const { container } = renderCampaign();
  await screen.findByText(/Time passes/);
  expect(screen.queryByText(/⁣Scene/)).toBeNull();
  // the tagged transition joins the reply's run instead of opening its own
  // plate — exactly how an untagged transition rendered before the tag existed
  const names = [...container.querySelectorAll(".plate-name")].map((n) => n.textContent);
  expect(names).toEqual(["You", "Grimoire"]);
});

test("Reroll is offered past a trailing scene transition and keeps it", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" },
    { role: "assistant", content: "a reply" },
    { role: "assistant", content: "*Time passes. It is now dusk.*", speaker: "⁣Scene" }] });
  renderCampaign();
  await screen.findByText(/Time passes/);
  fireEvent.click(screen.getByTitle("Reroll"));
  fireEvent.click(screen.getByRole("button", { name: /reroll ▸/i }));
  // the optimistic trim drops the reply but leaves the transition standing
  expect(screen.queryByText("a reply")).toBeNull();
  expect(screen.getByText(/Time passes/)).toBeInTheDocument();
  await waitFor(() => expect(api.regenerate).toHaveBeenCalled());
});

// ---- paginated scene history (#94) ----

// jsdom has no layout: scrollTop is a no-op setter and every metric reads 0.
// These stubs give the stream just enough geometry for the scroll handler and
// the restore to be exercised for real. scrollHeight grows with the number of
// rendered posts, which is what makes the prepend's height change observable.
function stubStreamGeometry(el: HTMLElement, clientHeight = 300, pxPerPost = 500) {
  let top = 0;
  Object.defineProperty(el, "scrollTop", {
    configurable: true, get: () => top, set: (v: number) => { top = v; },
  });
  Object.defineProperty(el, "clientHeight", { configurable: true, get: () => clientHeight });
  Object.defineProperty(el, "scrollHeight", {
    configurable: true, get: () => el.querySelectorAll(".msg").length * pxPerPost,
  });
}

test("a scene opens at its most recent page, not its whole history", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "assistant", content: "recent" }], offset: 40, total: 41, has_older: true });
  renderCampaign();
  await screen.findByText("recent");
  expect(api.getScene).toHaveBeenCalledWith("run", "s1", { limit: 60 });
});

test("a windowed post is edited by its absolute index, not its position on the page", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "page two" }], offset: 40, total: 41, has_older: true });
  renderCampaign();
  await screen.findByText("page two");
  fireEvent.click(screen.getAllByTitle("Edit message")[0]);
  fireEvent.change(await screen.findByLabelText(/edit message/i), { target: { value: "fixed" } });
  fireEvent.click(screen.getByRole("button", { name: /save/i }));
  await waitFor(() => expect(api.editMessage).toHaveBeenCalledWith("run", "s1", 40, "fixed"));
});

test("scrolling to the top of the stream prepends the previous page", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockImplementation((_c: string, _s: string, w?: any) =>
    Promise.resolve(w?.before === 2
      ? { meta: {}, messages: [{ role: "user", content: "older post" }], offset: 1, total: 3, has_older: true }
      : { meta: {}, messages: [{ role: "user", content: "newer post" }], offset: 2, total: 3, has_older: true }));
  const { container } = renderCampaign();
  await screen.findByText("newer post");
  fireEvent.scroll(container.querySelector(".stream")!);
  await screen.findByText("older post");
  expect(api.getScene).toHaveBeenCalledWith("run", "s1", { limit: 60, before: 2 });
  // prepended, so the older post reads first
  const posts = [...container.querySelectorAll(".msg-body")].map((n) => n.textContent);
  expect(posts).toEqual(["older post", "newer post"]);
});

test("loading older posts holds the viewport instead of jumping to the bottom", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockImplementation((_c: string, _s: string, w?: any) =>
    Promise.resolve(w?.before === 2
      ? { meta: {}, messages: [{ role: "user", content: "older post" }], offset: 0, total: 3, has_older: false }
      : { meta: {}, messages: [{ role: "user", content: "newer post" }, { role: "assistant", content: "a reply" }],
          offset: 2, total: 4, has_older: true }));
  const { container } = renderCampaign();
  await screen.findByText("newer post");
  const stream = container.querySelector(".stream") as HTMLElement;
  stubStreamGeometry(stream);
  const scrollTo = vi.fn();
  stream.scrollTo = scrollTo as any;

  fireEvent.scroll(stream); // scrollTop 0 with two posts on screen: at the top
  await screen.findByText("older post");
  // two posts (1000px) with the viewport at the top means 1000px sat below the
  // fold; after the prepend (1500px) the same 1000px must still sit below it
  expect(stream.scrollTop).toBe(500);
  expect(scrollTo).not.toHaveBeenCalled();
});

test("the older-history button loads the previous page and disappears at the top", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockImplementation((_c: string, _s: string, w?: any) =>
    Promise.resolve(w?.before === 1
      ? { meta: {}, messages: [{ role: "user", content: "the opener" }], offset: 0, total: 2, has_older: false }
      : { meta: {}, messages: [{ role: "assistant", content: "newer post" }], offset: 1, total: 2, has_older: true }));
  renderCampaign();
  await screen.findByText("newer post");
  fireEvent.click(screen.getByRole("button", { name: /load 1 older post/i }));
  await screen.findByText("the opener");
  expect(screen.queryByRole("button", { name: /older posts/i })).toBeNull();
});

test("no older-history button when the whole transcript is loaded", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "assistant", content: "only post" }], offset: 0, total: 1, has_older: false });
  renderCampaign();
  await screen.findByText("only post");
  expect(screen.queryByRole("button", { name: /older posts/i })).toBeNull();
});

test("Reroll survives the opening user post being off-window", async () => {
  // the run's own user turn is older than the loaded page — unloaded, not
  // absent, so this is still a reply to something and still rerollable
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "assistant", content: "a reply" }],
    offset: 12, total: 13, has_older: true, has_user_message: true });
  renderCampaign();
  await screen.findByText("a reply");
  expect(screen.getByTitle("Reroll")).toBeInTheDocument();
});

test("no Reroll on an all-assistant transcript, however much history is above", async () => {
  // an offscreen scene never stores a player turn, so unloaded history above
  // the window is not evidence of one — and regenerate 400s on that transcript
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "assistant", content: "narration" }],
    offset: 12, total: 13, has_older: true, has_user_message: false });
  renderCampaign();
  await screen.findByText("narration");
  expect(screen.queryByTitle("Reroll")).toBeNull();
});

test("an older page that lands after a scene switch is dropped", async () => {
  // otherwise scene A's posts prepend onto B and install A's offset, after
  // which an edit sends B's id with an A-derived index — onto an unrelated post
  (api.listScenes as any).mockResolvedValue([
    { id: "s1", title: "First", model: "", created: "", updated: "" },
    { id: "s2", title: "Second", model: "", created: "", updated: "" }]);
  let releaseOlder: (() => void) | null = null;
  (api.getScene as any).mockImplementation((_c: string, sid: string, w?: any) => {
    if (sid === "s1" && w?.before === 5) {
      return new Promise((resolve) => {
        releaseOlder = () => resolve({ meta: {}, messages: [{ role: "user", content: "scene one, older" }],
                                       offset: 4, total: 6, has_older: true, has_user_message: true });
      });
    }
    return Promise.resolve(sid === "s2"
      ? { meta: {}, messages: [{ role: "assistant", content: "scene two" }],
          offset: 0, total: 1, has_older: false, has_user_message: false }
      : { meta: {}, messages: [{ role: "assistant", content: "scene one, newest" }],
          offset: 5, total: 6, has_older: true, has_user_message: true });
  });
  renderCampaign();
  await screen.findByText("scene one, newest");
  fireEvent.click(screen.getByRole("button", { name: /load .* older post/i }));
  fireEvent.click(screen.getByText(/· Second$/));
  await screen.findByText("scene two");
  releaseOlder!();
  await waitFor(() => expect(screen.getByText("scene two")).toBeInTheDocument());
  expect(screen.queryByText("scene one, older")).toBeNull();
  // and the retired page did not install scene one's offset on scene two
  expect(screen.queryByRole("button", { name: /older post/i })).toBeNull();
});

test("a refresh of the open scene re-reads everything already on screen", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockImplementation((_c: string, _s: string, w?: any) =>
    Promise.resolve(w?.before === 60
      ? { meta: {}, messages: [{ role: "user", content: "older post" }], offset: 59, total: 61, has_older: true }
      : { meta: {}, messages: [{ role: "assistant", content: "newer post" }], offset: 60, total: 61, has_older: true }));
  renderCampaign();
  await screen.findByText("newer post");
  fireEvent.click(screen.getByRole("button", { name: /load .* older posts/i }));
  await screen.findByText("older post");
  // editing forces a re-select; it must not collapse the reader back to one page
  fireEvent.click(screen.getAllByTitle("Edit message")[0]);
  // exact label: the OTHER post's gutter button is "Edit message <n>"
  fireEvent.change(await screen.findByLabelText("Edit message"), { target: { value: "fixed" } });
  fireEvent.click(screen.getByRole("button", { name: /save/i }));
  await waitFor(() => expect(api.getScene).toHaveBeenLastCalledWith("run", "s1", { limit: 61 }));
});

test("a recovered prompt is never shown against the scene the player moved to", async () => {
  // The composer is one shared box that survives a scene switch, so restoring
  // a rolled-back prompt straight into it puts scene A's words in front of a
  // player looking at scene B, and Send there posts them to B.
  //
  // The window is short but real: `runStream`'s finally refreshes the turn's
  // scene and `selectScene` sets `activeId` synchronously, so the player is
  // pulled back to A a couple of microtasks later. Measured against the old
  // code, the DOM does commit in between — scene B on screen, A's prompt in
  // the composer — and in the browser the stream read that sits between the
  // error frame and the body ending is a task boundary, so that state can
  // paint and be clicked. Relying on an unrelated navigation side effect to
  // close it is also the kind of accident this PR keeps finding.
  //
  // So the invariant is sampled across the whole window rather than at one
  // instant: the composer must never hold text while a scene other than the
  // turn's own is the active one.
  (api.listScenes as any).mockResolvedValue([
    { id: "s1", title: "Old", model: "", created: "", updated: "" },
    { id: "s2", title: "Later", model: "", created: "", updated: "" },
  ]);
  (api.getScene as any).mockResolvedValue({ meta: {}, total: 0, messages: [] });
  let fail: (() => void) | null = null;
  (api.chat as any).mockImplementation(
    async (_c: string, _s: string, _m: string, onEvent: any) => {
      await new Promise<void>((r) => {
        fail = () => {
          onEvent({ error: { detail: "OpenRouter API key is not set",
                             post_returned: true } });
          r();
        };
      });
    });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue(""));

  fireEvent.click(screen.getByText(/. Later/));          // leave before it fails
  await waitFor(() => expect(
    document.querySelector(".row.active .row-name")?.textContent).toMatch(/Later/));

  const wrong: string[] = [];
  fail!();
  for (let i = 0; i < 8; i++) {
    await Promise.resolve();
    const row = document.querySelector(".row.active .row-name")?.textContent ?? "";
    const composer = (screen.getByRole("textbox") as HTMLTextAreaElement).value;
    if (composer && !/Old/.test(row)) wrong.push(`${row} | ${composer}`);
  }
  expect(wrong).toEqual([]);

  // And it is not dropped: it comes back with the scene it was written for.
  await waitFor(() => expect(
    document.querySelector(".row.active .row-name")?.textContent).toMatch(/Old/));
  await waitFor(() =>
    expect(screen.getByRole("textbox")).toHaveValue("I draw my blade."));
});

test("renaming a scene carries its parked prompt to the new id", async () => {
  // A scene's id is its filename, so a rename mints a new one. A recovered
  // prompt parked under the old id is then looked up under the new one, found
  // missing, and lost when the view unmounts — and it is the only copy of what
  // the player wrote.
  //
  // Built on the retired-read scenario rather than an error frame, because that
  // is the one that provably leaves the player on the other scene: the error
  // frame path has `runStream`'s finally pull them back to the turn's scene,
  // which hands the prompt over before a rename can strand it. A first attempt
  // written that way passed against the unfixed code.
  (api.listScenes as any).mockResolvedValue([
    { id: "s1", title: "Old", model: "", created: "", updated: "" },
    { id: "s2", title: "Later", model: "", created: "", updated: "" },
  ]);
  let releaseVerify: (() => void) | null = null;
  let loaded = false;
  (api.getScene as any).mockImplementation(async (_c: string, sid: string) => {
    if (sid === "s1" && loaded) await new Promise<void>((r) => { releaseVerify = r; });
    loaded = true;
    return { meta: {}, total: 0, messages: [] };
  });
  (api.chat as any).mockImplementation(async () => {
    const err: Error & { beforeResponse?: boolean } = new Error("Failed to fetch");
    err.beforeResponse = true;
    throw err;
  });
  (api.renameScene as any).mockResolvedValue({ id: "s1-renamed" });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await waitFor(() => expect(releaseVerify).not.toBeNull());
  fireEvent.click(screen.getByText(/. Later/));
  releaseVerify!();
  await new Promise((r) => setTimeout(r, 60));
  expect(screen.getByRole("textbox")).toHaveValue("");   // parked under s1

  // Rename the scene it is parked under, while sitting on another one.
  (api.listScenes as any).mockResolvedValue([
    { id: "s1-renamed", title: "Renamed", model: "", created: "", updated: "" },
    { id: "s2", title: "Later", model: "", created: "", updated: "" },
  ]);
  // By row, not by index — and only once the scene lock has let go. s1 is still
  // `streamingId` while the flush is outstanding, so its Rename is disabled;
  // that is the lock doing its job, and the rename this test needs comes after.
  const oldRename = () => Array.from(document.querySelectorAll(".row"))
    .find((r) => /Old/.test(r.textContent ?? ""))!
    .querySelector('button[aria-label="Rename"]') as HTMLButtonElement;
  await waitFor(() => expect(oldRename()).not.toBeDisabled(), { timeout: 15000 });
  fireEvent.click(oldRename());
  const nameInput = screen.getByDisplayValue("Old");
  fireEvent.change(nameInput, { target: { value: "Renamed" } });
  fireEvent.keyDown(nameInput, { key: "Enter" });
  await waitFor(() => expect(screen.getByText(/. Renamed/)).toBeInTheDocument());

  // Re-opening it under its new id still hands the prompt back.
  fireEvent.click(screen.getByText(/. Renamed/));
  await waitFor(() =>
    expect(screen.getByRole("textbox")).toHaveValue("I draw my blade."));
});

test("renaming the scene keeps a failed reroll's Retry a reroll", async () => {
  // The remembered reroll carries the scene it belongs to so Retry cannot act
  // on a different one. A rename mints a new id, and leaving the ref on the old
  // one makes that same check misfire in the other direction: Retry decides
  // this is not the reroll's scene, falls back to `/retry`, and continues from
  // the restored old reply — dropping the guidance the player wrote.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({
    meta: {}, total: 2, messages: [
      { role: "user", content: "and then?" },
      { role: "assistant", content: "The tide turns." }],
  });
  (api.renameScene as any).mockResolvedValue({ id: "s1-renamed" });
  (api.regenerate as any).mockImplementation(
    async (_c: string, _s: string, onEvent: any) => {
      onEvent({ error: { detail: "OpenRouter API key is not set", kind: "missing_key" } });
    });
  renderCampaign();
  await screen.findByText("The tide turns.");
  fireEvent.click(screen.getByRole("button", { name: /reroll/i }));
  fireEvent.change(screen.getByPlaceholderText(/reroll/i),
                   { target: { value: "darker this time" } });
  fireEvent.click(screen.getByRole("button", { name: /reroll ▸/i }));
  await screen.findByText(/OpenRouter API key is not set/);

  // Rename the active scene. The banner stays up, so Retry is still offered.
  (api.listScenes as any).mockResolvedValue(
    [{ id: "s1-renamed", title: "Renamed", model: "", created: "", updated: "" }]);
  const rename = () => document.querySelector('button[aria-label="Rename"]') as HTMLButtonElement;
  await waitFor(() => expect(rename()).not.toBeDisabled(), { timeout: 15000 });
  fireEvent.click(rename());
  const nameInput = screen.getByDisplayValue("Old");
  fireEvent.change(nameInput, { target: { value: "Renamed" } });
  fireEvent.keyDown(nameInput, { key: "Enter" });
  await waitFor(() => expect(api.renameScene).toHaveBeenCalled());

  fireEvent.click(screen.getByRole("button", { name: /^retry$/i }));
  await waitFor(() => expect(api.regenerate).toHaveBeenCalledTimes(2));
  expect(api.retry).not.toHaveBeenCalled();
  expect((api.regenerate as any).mock.calls[1][1]).toBe("s1-renamed");
  expect((api.regenerate as any).mock.calls[1][3]).toBe("darker this time");
});
