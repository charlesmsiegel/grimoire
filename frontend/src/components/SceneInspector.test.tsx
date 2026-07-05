import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SceneInspector } from "./SceneInspector";

vi.mock("../api/client", () => ({
  api: {
    getCast: vi.fn(), getCampaign: vi.fn(), listCharacters: vi.fn(), listPCs: vi.fn(),
    listCampaignPCs: vi.fn(), getSceneLocation: vi.fn(), getSceneContext: vi.fn(),
    getCastDetail: vi.fn(), readEntity: vi.fn(), getChronicle: vi.fn(),
    getCalendarConfig: vi.fn(), setCalendarConfig: vi.fn(),
    getSceneDatetime: vi.fn(), setSceneDatetime: vi.fn(),
    listAppearances: vi.fn(), listEntityImages: vi.fn(),
    listEntities: vi.fn(), setSceneLocation: vi.fn(),
    campaignImageUrl: () => "/img",
    entityImageUrl: () => "/loc-img",
  },
}));
vi.mock("../api/models", () => ({ getModels: vi.fn() }));
import { api } from "../api/client";
import { getModels } from "../api/models";

beforeEach(() => {
  vi.clearAllMocks();
  (api.getCast as any).mockResolvedValue([{ kind: "characters", id: "seraphine", role: "npc" }]);
  (api.getCampaign as any).mockResolvedValue({ meta: { id: "c", world: "w" }, body: "" });
  (api.listCharacters as any).mockResolvedValue([{ id: "seraphine", name: "Seraphine", default_version: "default", versions: [] }]);
  (api.listPCs as any).mockResolvedValue([]);
  (api.listCampaignPCs as any).mockResolvedValue([]);
  (api.getSceneLocation as any).mockResolvedValue({ current: { id: "crypt", name: "The Crypt" }, visited: [] });
  (api.getSceneContext as any).mockResolvedValue({
    model: "m", total_tokens: 100,
    sections: [{ label: "World info", text: "lore text", tokens: 100 }],
  });
  (api.getCastDetail as any).mockResolvedValue({ kind: "characters", id: "seraphine", name: "Seraphine", version: "default", body: "keeper" });
  (getModels as any).mockResolvedValue([{ id: "m", name: "M", context: 1000, prompt: "0", completion: "0" }]);
  (api.getChronicle as any).mockResolvedValue([
    { id: "s0", one_line: "They first met.", summary: "", keywords: [],
      cast: [], location: "", date: "", absorbed: "t" }]);
  (api.getCalendarConfig as any).mockResolvedValue({
    primary: { provider: "gregorian", region: "US", custom_holidays: [], anchor: null },
    secondary: null, confirmed: true });
  (api.setCalendarConfig as any).mockResolvedValue({ ok: true });
  (api.getSceneDatetime as any).mockResolvedValue({ current: null, history: [], suggested: null });
  (api.setSceneDatetime as any).mockResolvedValue({ ok: true, advanced: false, friendly: "", id: "s" });
  (api.listAppearances as any).mockResolvedValue([]);
  (api.listEntityImages as any).mockResolvedValue([]);
  (api.listEntities as any).mockResolvedValue([]);
  (api.setSceneLocation as any).mockResolvedValue({ ok: true, moved: true, name: "" });
});

function renderInspector(onSceneChanged: () => void = () => {}) {
  render(<SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={onSceneChanged} />);
}

test("lists cast names and the location and a context section", async () => {
  renderInspector();
  await screen.findByText("Seraphine");
  await screen.findByText("The Crypt");
  await screen.findByText(/World info/);
});

test("clicking a cast row opens the drawer", async () => {
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /Seraphine/ }));
  await waitFor(() => expect(api.getCastDetail).toHaveBeenCalledWith("c", "s", "characters", "seraphine"));
  await screen.findByText("keeper");
});

test("cast rows show portraits with roster versions and role chips", async () => {
  (api.getCast as any).mockResolvedValue([
    { kind: "characters", id: "seraphine", role: "npc", name: "Seraphine" },
    { kind: "pcs", id: "yara", role: "player", name: "Yara" },
  ]);
  (api.listAppearances as any).mockResolvedValue([
    { kind: "characters", id: "seraphine", version: "v2", role: "npc", scenes: ["s"] },
  ]);
  (api.listPCs as any).mockResolvedValue([
    { id: "yara", name: "Yara", tags: [], default_version: "default", versions: [] }]);
  renderInspector();
  await screen.findByText("Seraphine");
  expect(screen.getByAltText("Seraphine portrait")).toBeInTheDocument(); // roster version found
  expect(screen.getByText("Y")).toBeInTheDocument();                     // PC initials fallback
  expect(screen.getByText("player")).toBeInTheDocument();
  expect(screen.getByText("npc")).toBeInTheDocument();
});

test("location with a primary image renders a clickable thumbnail", async () => {
  (api.listEntityImages as any).mockResolvedValue([{ name: "avatar", ext: "png" }]);
  renderInspector();
  const thumb = await screen.findByAltText("The Crypt");
  expect(thumb.closest("button")).not.toBeNull();
  await waitFor(() => expect(api.listEntityImages).toHaveBeenCalledWith(
    { kind: "campaign", id: "c" }, "locations", "crypt"));
});

test("location without an image keeps the text row", async () => {
  renderInspector();
  const row = await screen.findByRole("button", { name: "The Crypt" });
  expect(row.querySelector("img")).toBeNull();
});

test("context section expands to show the text", async () => {
  renderInspector();
  const summary = await screen.findByText(/World info/);
  fireEvent.click(summary);
  await screen.findByText("lore text");
});

test("shows the story-so-far recap", async () => {
  renderInspector();
  await screen.findByText("Story so far");
  await screen.findByText("They first met.");
});

test("no calendar selected: choosing one confirms the calendar", async () => {
  (api.getCalendarConfig as any).mockResolvedValue({
    primary: { provider: "gregorian", region: "US", custom_holidays: [], anchor: null },
    secondary: null, confirmed: false });
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /use this calendar/i }));
  await waitFor(() => expect(api.setCalendarConfig).toHaveBeenCalledWith(
    "c", expect.objectContaining({ confirmed: true })));
});

test("calendar but no date: setting a date calls setSceneDatetime and notifies", async () => {
  const onChanged = vi.fn();
  renderInspector(onChanged);
  const input = await screen.findByLabelText("Scene date");
  fireEvent.change(input, { target: { value: "2026-07-04" } });
  fireEvent.click(screen.getByRole("button", { name: /set date/i }));
  await waitFor(() => expect(api.setSceneDatetime).toHaveBeenCalledWith("c", "s", "2026-07-04"));
  await waitFor(() => expect(onChanged).toHaveBeenCalled());
});

test("first date set renames the scene: adopts the new id via onSceneRenamed", async () => {
  (api.setSceneDatetime as any).mockResolvedValue(
    { ok: true, advanced: false, friendly: "4 July 2026", id: "001--2026-07-04--s" });
  const onRenamed = vi.fn();
  render(<SceneInspector cid="c" sid="s" refreshKey={0}
                         onSceneChanged={() => {}} onSceneRenamed={onRenamed} />);
  const input = await screen.findByLabelText("Scene date");
  fireEvent.change(input, { target: { value: "2026-07-04" } });
  fireEvent.click(screen.getByRole("button", { name: /set date/i }));
  await waitFor(() => expect(onRenamed).toHaveBeenCalledWith("001--2026-07-04--s"));
});

test("shows the current date when one is set", async () => {
  (api.getSceneDatetime as any).mockResolvedValue({
    current: { native: "2026-07-04", friendly: "4 July 2026", weekday: "Saturday",
               secondary_friendly: null, holidays_today: [], upcoming: null, cast: [] },
    history: ["2026-07-04"] });
  renderInspector();
  await screen.findByText(/4 July 2026/);
});

test("Move to sets the scene location, reloads it, and refreshes the stream", async () => {
  (api.listEntities as any).mockResolvedValue([
    { id: "crypt", name: "The Crypt" }, { id: "docks", name: "The Docks" }]);
  const onSceneChanged = vi.fn();
  renderInspector(onSceneChanged);
  await screen.findByText("The Crypt");
  fireEvent.change(await screen.findByLabelText(/move to location/i), { target: { value: "docks" } });
  fireEvent.click(screen.getByRole("button", { name: /move to/i }));
  await waitFor(() => expect(api.setSceneLocation).toHaveBeenCalledWith("c", "s", "docks"));
  await waitFor(() => expect(onSceneChanged).toHaveBeenCalled());
  expect((api.getSceneLocation as any).mock.calls.length).toBeGreaterThan(1); // reloaded after the move
});

test("Move to is disabled until a location is chosen", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "docks", name: "The Docks" }]);
  renderInspector();
  await screen.findByText("The Crypt");
  expect(await screen.findByRole("button", { name: /move to/i })).toBeDisabled();
});

test("a dateless scene with a suggestion pre-fills the date input", async () => {
  (api.getSceneDatetime as any).mockResolvedValue(
    { current: null, history: [], suggested: "2026-07-06" });
  renderInspector();
  const input = await screen.findByLabelText("Scene date");
  await waitFor(() => expect((input as HTMLInputElement).value).toBe("2026-07-06"));
  fireEvent.click(screen.getByRole("button", { name: /set date/i }));
  await waitFor(() => expect(api.setSceneDatetime).toHaveBeenCalledWith("c", "s", "2026-07-06"));
});
