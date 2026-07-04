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

vi.mock("../api/client", () => ({
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
    getConfig: vi.fn(),
    editMessage: vi.fn(),
    absorbScene: vi.fn(), saveChronicle: vi.fn(), getChronicle: vi.fn(),
    // consumed by the embedded SceneInspector
    getCast: vi.fn(), getSceneLocation: vi.fn(), getSceneContext: vi.fn(),
    getCastDetail: vi.fn(), readEntity: vi.fn(),
    getCalendarConfig: vi.fn(), setCalendarConfig: vi.fn(),
    getSceneDatetime: vi.fn(), setSceneDatetime: vi.fn(),
    listCharacters: vi.fn(), listPCs: vi.fn(), listCampaignPCs: vi.fn(),
    campaignChanges: vi.fn(),
    listAppearances: vi.fn(), listEntityImages: vi.fn(),
    campaignImageUrl: (_c: string, char: string, v: string, n: string) => `/img/${char}/${v}/${n}`,
    entityImageUrl: () => "/loc-img",
  },
}));
vi.mock("../api/models", () => ({ fetchModels: vi.fn() }));
import { api } from "../api/client";
import { fetchModels } from "../api/models";

const ONE_SCENE = [{ id: "s1", title: "Old", model: "", created: "", updated: "" }];

beforeEach(() => {
  vi.clearAllMocks();
  (api.getCampaign as any).mockResolvedValue({ meta: { id: "run", name: "Run One", world: "w" }, body: "" });
  (api.getWorld as any).mockResolvedValue({ meta: { id: "w", name: "Saltmarch" }, body: "", counts: {} });
  (api.listScenes as any).mockResolvedValue([]);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [] });
  (api.createScene as any).mockResolvedValue({ id: "s1" });
  (api.renameScene as any).mockResolvedValue({ id: "s1", title: "New" });
  (api.deleteScene as any).mockResolvedValue({ ok: true });
  (api.chat as any).mockResolvedValue(undefined);
  (api.retry as any).mockResolvedValue(undefined);
  (api.regenerate as any).mockResolvedValue(undefined);
  (api.getConfig as any).mockResolvedValue({ model: "m", theme: "codex", key_set: true, system_prompt: "", quote_color: "off" });
  (api.editMessage as any).mockResolvedValue({ ok: true });
  (api.getCast as any).mockResolvedValue([]);
  (api.getSceneLocation as any).mockResolvedValue({ current: null, visited: [] });
  (api.getSceneContext as any).mockResolvedValue({ model: "m", total_tokens: 0, sections: [] });
  (api.getCalendarConfig as any).mockResolvedValue({
    primary: { provider: "gregorian", region: "US", custom_holidays: [], anchor: null },
    secondary: null, confirmed: true });
  (api.getSceneDatetime as any).mockResolvedValue({ current: null, history: [] });
  (api.listCharacters as any).mockResolvedValue([]);
  (api.listPCs as any).mockResolvedValue([]);
  (api.listCampaignPCs as any).mockResolvedValue([]);
  (api.listAppearances as any).mockResolvedValue([]);
  (api.listEntityImages as any).mockResolvedValue([]);
  (fetchModels as any).mockResolvedValue([]);
  (api.absorbScene as any).mockResolvedValue({
    one_line: "They met.", summary: "A met B.", keywords: ["salt"],
    timeline_events: [], cast: [], location: "", date: "",
    edits: [{ id: "character_state:seraphine", kind: "character_state",
      target: { kind: "characters", id: "seraphine" }, label: "Seraphine — current state",
      field: "current_state", before: "Wary.", after: "Loyal now.", authored: false }] });
  (api.saveChronicle as any).mockResolvedValue({ id: "s1", one_line: "They met.",
    summary: "A met B.", keywords: ["salt"], cast: [], location: "", date: "", absorbed: "t" });
  (api.getChronicle as any).mockResolvedValue([]);
  (api.campaignChanges as any).mockResolvedValue([]);
});

function renderCampaign() {
  render(
    <MemoryRouter initialEntries={["/campaigns/run"]}>
      <Routes>
        <Route path="/campaigns/:cid" element={<CampaignView keySet={true} />} />
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

test("groups consecutive posts under one speaker plate", async () => {
  (api.getConfig as any).mockResolvedValue({
    model: "m", theme: "codex", key_set: true, system_prompt: "", quote_color: "off",
    user_label: "Kestrel", assistant_label: "Grimoire",
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

test("relationship rows are read-only and sent with payload on save", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  (api.absorbScene as any).mockResolvedValue({
    one_line: "o", summary: "s", keywords: [], timeline_events: [], cast: [], location: "", date: "",
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
