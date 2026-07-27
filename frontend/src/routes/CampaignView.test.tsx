import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import CampaignView from "./CampaignView";

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
      getCastDetail: vi.fn(), readEntity: vi.fn(),
      addToCast: vi.fn(), removeFromCast: vi.fn(),
      getCalendarConfig: vi.fn(), setCalendarConfig: vi.fn(), getCalendarProviders: vi.fn(),
      getSceneDatetime: vi.fn(), setSceneDatetime: vi.fn(), getCalendarMonths: vi.fn(),
      listStyles: vi.fn(), getSceneStyle: vi.fn(), setSceneStyle: vi.fn(),
      listCharacters: vi.fn(), listPCs: vi.fn(), listCampaignPCs: vi.fn(),
      campaignChanges: vi.fn(),
      listAppearances: vi.fn(), listEntityImages: vi.fn(), listEntities: vi.fn(),
      campaignImageUrl: (_c: string, char: string, v: string, n: string) => `/img/${char}/${v}/${n}`,
      entityImageUrl: () => "/loc-img",
    },
  };
});
vi.mock("../api/models", () => ({ getModels: vi.fn() }));
import { api } from "../api/client";
import { getModels } from "../api/models";

const ONE_SCENE = [{ id: "s1", title: "Old", model: "", created: "", updated: "" }];

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
  (api.chat as any).mockResolvedValue(undefined);
  (api.retry as any).mockResolvedValue(undefined);
  (api.regenerate as any).mockResolvedValue(undefined);
  (api.getRollProposal as any).mockResolvedValue({ record: null });
  (api.resolveProposal as any).mockResolvedValue(undefined);
  (api.getSceneChecks as any).mockResolvedValue({ actors: [] });
  (api.rollCheck as any).mockResolvedValue({ ok: true, resolution: {}, message: "" });
  (api.getConfig as any).mockResolvedValue({ theme: "codex", system_prompt: "", quote_color: "off", user_label: "You", assistant_label: "Grimoire", default_style_id: "", active_connection_id: "openrouter", active_connection: { id: "openrouter", kind: "openrouter", name: "OpenRouter" }, ready: true });
  (api.editMessage as any).mockResolvedValue({ ok: true });
  (api.getCast as any).mockResolvedValue([]);
  (api.addToCast as any).mockResolvedValue({ ok: true });
  (api.removeFromCast as any).mockResolvedValue({ ok: true });
  (api.getSceneLocation as any).mockResolvedValue({ current: null, visited: [] });
  (api.getSceneContext as any).mockResolvedValue({ model: "m", total_tokens: 0, sections: [] });
  (api.getCalendarConfig as any).mockResolvedValue({
    primary: { provider: "gregorian", region: "US", custom_holidays: [], anchor: null },
    secondary: null, confirmed: true });
  (api.getCalendarProviders as any).mockResolvedValue({ providers: [
    { id: "gregorian", name: "Gregorian" }, { id: "hebrew", name: "Hebrew" },
  ] });
  (api.getSceneDatetime as any).mockResolvedValue({ current: null, history: [] });
  (api.listStyles as any).mockResolvedValue([]);
  (api.getSceneStyle as any).mockResolvedValue({ style_id: "" });
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
    edits: [{ id: "character_state:seraphine", kind: "character_state",
      target: { kind: "characters", id: "seraphine" }, label: "Seraphine — current state",
      field: "current_state", before: "Wary.", after: "Loyal now.", authored: false }] });
  (api.saveChronicle as any).mockResolvedValue({ id: "s1", one_line: "They met.",
    summary: "A met B.", keywords: ["salt"], cast: [], location: "", date: "", absorbed: "t",
    applied: [], sheet_failures: [] });
  (api.getChronicle as any).mockResolvedValue([]);
  (api.campaignChanges as any).mockResolvedValue([]);
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
    default_style_id: "", active_connection_id: "openrouter",
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
    expect(api.chat).toHaveBeenCalledWith("run", "s1", "hello", expect.any(Function)),
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

test("sending with no scene creates one first", async () => {
  (api.listScenes as any).mockResolvedValue([]);
  renderCampaign();
  await waitFor(() => expect(api.listScenes).toHaveBeenCalled());
  const ta = screen.getByRole("textbox");
  fireEvent.change(ta, { target: { value: "hi" } });
  fireEvent.keyDown(ta, { key: "Enter" });
  await waitFor(() => expect(api.createScene).toHaveBeenCalledWith("run"));
  await waitFor(() => expect(api.chat).toHaveBeenCalledWith("run", "s1", "hi", expect.any(Function)));
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
  await waitFor(() => expect(api.getScene).toHaveBeenCalledWith("run", "s9"));
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
  await waitFor(() => expect(api.getScene).toHaveBeenCalledWith("run", "s10"));
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
  await waitFor(() => expect(api.retry).toHaveBeenCalledWith("run", "s1", expect.any(Function)));
  expect(screen.getAllByText("hello")).toHaveLength(1);
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
  await waitFor(() => expect(api.regenerate).toHaveBeenCalledWith("run", "s1", expect.any(Function)));
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
    "run", "s1", expect.any(Function), "make her angrier"));
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
    edits: [] });
  (api.retryAudit as any).mockResolvedValue({
    mechanics: { status: "ok", reason: null, warnings: [], dropped: [] },
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
    edits: [LORE_EDIT] });
  (api.retryAudit as any).mockResolvedValue({
    mechanics: { status: "ok", reason: null, warnings: [], dropped: [] },
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
    edits: [] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("Some mechanics findings could not be validated");
  expect(screen.getByText(/characters:mara athletics: static tamper/)).toBeInTheDocument();
});

test("sheet edits render read-only with the note and survive save", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  (api.absorbScene as any).mockResolvedValue({
    one_line: "o", summary: "s", keywords: [], timeline_events: [], cast: [], location: "", date: "",
    mechanics: { status: "ok", reason: null, warnings: [], dropped: [] },
    edits: [SHEET_EDIT] });
  (api.saveChronicle as any).mockResolvedValue({ id: "s1", one_line: "o", summary: "s", keywords: [],
    cast: [], location: "", date: "", absorbed: "t",
    applied: ["sheet:characters:mara:hp"], sheet_failures: [] });
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

test("sheet_failures from save render a notice", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  (api.absorbScene as any).mockResolvedValue({
    one_line: "o", summary: "s", keywords: [], timeline_events: [], cast: [], location: "", date: "",
    mechanics: { status: "ok", reason: null, warnings: [], dropped: [] },
    edits: [SHEET_EDIT] });
  (api.saveChronicle as any).mockResolvedValue({ id: "s1", one_line: "o", summary: "s", keywords: [],
    cast: [], location: "", date: "", absorbed: "t", applied: [],
    sheet_failures: [{ id: "sheet:characters:mara:hp", reason: "changed", kind: "conflict" }] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByText("Mara — HP");
  fireEvent.click(screen.getByRole("button", { name: /Save summary/ }));
  await screen.findByText("1 sheet change did not apply");
  expect(screen.getByText(/Mara — HP/)).toBeInTheDocument();
  expect(screen.getByText("Mara — HP: changed (conflict)")).toBeInTheDocument();

  // A stale sheet_failures notice must not survive into the next scene's
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
  await waitFor(() => expect(api.chat).toHaveBeenCalledWith("run", "s1", "", expect.any(Function)));
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
  await waitFor(() => expect(api.chat).toHaveBeenCalledWith("run", "s1", "", expect.any(Function)));
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
    expect.any(Function)));
  await waitFor(() => expect(screen.queryByRole("button", { name: "Roll it" })).toBeNull());
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
    expect.any(Function)));
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
