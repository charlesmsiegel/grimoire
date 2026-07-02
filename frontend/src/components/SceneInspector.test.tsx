import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SceneInspector } from "./SceneInspector";

vi.mock("../api/client", () => ({
  api: {
    getCast: vi.fn(), getCampaign: vi.fn(), listCharacters: vi.fn(), listPCs: vi.fn(),
    listCampaignPCs: vi.fn(), getSceneLocation: vi.fn(), getSceneContext: vi.fn(),
    getCastDetail: vi.fn(), readEntity: vi.fn(), getChronicle: vi.fn(),
    getCalendarConfig: vi.fn(), setCalendarConfig: vi.fn(),
    getSceneDatetime: vi.fn(), setSceneDatetime: vi.fn(),
    campaignImageUrl: () => "/img",
  },
}));
vi.mock("../api/models", () => ({ fetchModels: vi.fn() }));
import { api } from "../api/client";
import { fetchModels } from "../api/models";

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
  (fetchModels as any).mockResolvedValue([{ id: "m", name: "M", context: 1000, prompt: "0", completion: "0" }]);
  (api.getChronicle as any).mockResolvedValue([
    { id: "s0", one_line: "They first met.", summary: "", keywords: [],
      cast: [], location: "", date: "", absorbed: "t" }]);
  (api.getCalendarConfig as any).mockResolvedValue({
    primary: { provider: "gregorian", region: "US", custom_holidays: [], anchor: null },
    secondary: null, confirmed: true });
  (api.setCalendarConfig as any).mockResolvedValue({ ok: true });
  (api.getSceneDatetime as any).mockResolvedValue({ current: null, history: [] });
  (api.setSceneDatetime as any).mockResolvedValue({ ok: true, advanced: false, friendly: "", id: "s" });
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
