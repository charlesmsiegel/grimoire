import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SceneInspector } from "./SceneInspector";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    api: {
      getCast: vi.fn(), getCampaign: vi.fn(), listCharacters: vi.fn(), listPCs: vi.fn(),
      listCampaignPCs: vi.fn(), getSceneLocation: vi.fn(), getSceneContext: vi.fn(),
      getCastDetail: vi.fn(), readEntity: vi.fn(), getChronicle: vi.fn(),
      getCalendarConfig: vi.fn(), setCalendarConfig: vi.fn(), getCalendarProviders: vi.fn(),
      getSceneDatetime: vi.fn(), setSceneDatetime: vi.fn(), getCalendarMonths: vi.fn(),
      listAppearances: vi.fn(), listEntityImages: vi.fn(),
      listEntities: vi.fn(), setSceneLocation: vi.fn(),
      getSceneStyle: vi.fn(), setSceneStyle: vi.fn(), listStyles: vi.fn(),
      addToCast: vi.fn(), removeFromCast: vi.fn(),
      campaignImageUrl: () => "/img",
      entityImageUrl: () => "/loc-img",
    },
  };
});
vi.mock("../api/models", () => ({ getModels: vi.fn() }));
import { api } from "../api/client";
import { getModels } from "../api/models";

const GREG_MONTHS = [
  { key: "01", name: "January", days: 31 },
  { key: "02", name: "February", days: 28 },
  { key: "03", name: "March", days: 31 },
  { key: "04", name: "April", days: 30 },
  { key: "05", name: "May", days: 31 },
  { key: "06", name: "June", days: 30 },
  { key: "07", name: "July", days: 31 },
  { key: "08", name: "August", days: 31 },
  { key: "09", name: "September", days: 30 },
  { key: "10", name: "October", days: 31 },
  { key: "11", name: "November", days: 30 },
  { key: "12", name: "December", days: 31 },
];

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
  (api.getCast as any).mockResolvedValue([{ kind: "characters", id: "seraphine", role: "npc" }]);
  (api.addToCast as any).mockResolvedValue({ ok: true });
  (api.removeFromCast as any).mockResolvedValue({ ok: true });
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
  (api.getCalendarProviders as any).mockResolvedValue({ providers: [
    { id: "gregorian", name: "Gregorian" }, { id: "hebrew", name: "Hebrew" },
  ] });
  (api.getSceneDatetime as any).mockResolvedValue({ current: null, history: [], suggested: null });
  (api.setSceneDatetime as any).mockResolvedValue({ ok: true, advanced: false, friendly: "", id: "s" });
  (api.getCalendarMonths as any).mockResolvedValue({ months: GREG_MONTHS });
  (api.listAppearances as any).mockResolvedValue([]);
  (api.listEntityImages as any).mockResolvedValue([]);
  (api.listEntities as any).mockResolvedValue([]);
  (api.setSceneLocation as any).mockResolvedValue({ ok: true, moved: true, name: "" });
  (api.getSceneStyle as any).mockResolvedValue({ style_id: "" });
  (api.setSceneStyle as any).mockResolvedValue({ ok: true });
  (api.listStyles as any).mockResolvedValue([
    { id: "gothic-horror", name: "Gothic Horror", description: "", tags: [], built_in: true },
    { id: "noir-detective", name: "Noir Detective", description: "", tags: [], built_in: true },
  ]);
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
  fireEvent.click(await screen.findByRole("button", { name: /^Seraphine/ }));
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
  expect(screen.getByText("player", { selector: ".role-chip" })).toBeInTheDocument();
  expect(screen.getByText("npc", { selector: ".role-chip" })).toBeInTheDocument();
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
  fireEvent.change(await screen.findByLabelText("Scene date year"), { target: { value: "2026" } });
  const monthSelect = await screen.findByLabelText("Scene date month");
  await waitFor(() => expect(monthSelect).not.toBeDisabled());
  fireEvent.change(monthSelect, { target: { value: "07" } });
  const daySelect = screen.getByLabelText("Scene date day");
  await waitFor(() => expect(daySelect).not.toBeDisabled());
  fireEvent.change(daySelect, { target: { value: "4" } });
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
  fireEvent.change(await screen.findByLabelText("Scene date year"), { target: { value: "2026" } });
  const monthSelect = await screen.findByLabelText("Scene date month");
  await waitFor(() => expect(monthSelect).not.toBeDisabled());
  fireEvent.change(monthSelect, { target: { value: "07" } });
  const daySelect = screen.getByLabelText("Scene date day");
  await waitFor(() => expect(daySelect).not.toBeDisabled());
  fireEvent.change(daySelect, { target: { value: "4" } });
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
  await screen.findByLabelText("Scene date year");
  // the picker's visible fields show the prefill once it arrives...
  await waitFor(() =>
    expect(screen.getByLabelText("Scene date year")).toHaveValue(2026));
  await waitFor(() =>
    expect(screen.getByLabelText("Scene date month")).toHaveValue("07"));
  expect(screen.getByLabelText("Scene date day")).toHaveValue("6");
  // ...and "Set date" is immediately enabled and submits the suggestion
  const button = await screen.findByRole("button", { name: /set date/i });
  await waitFor(() => expect(button).not.toBeDisabled());
  fireEvent.click(button);
  await waitFor(() => expect(api.setSceneDatetime).toHaveBeenCalledWith("c", "s", "2026-07-06"));
});

test("offscreen scene shows the offscreen side-section", async () => {
  render(<SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} pcless />);
  await screen.findByText("Offscreen scene");
  expect(screen.getByText(/no player character/i)).toBeInTheDocument();
});

test("picking a scene prose style saves it immediately", async () => {
  render(<SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={vi.fn()} />);
  const sel = await screen.findByLabelText("Prose style");
  fireEvent.change(sel, { target: { value: "noir-detective" } });
  await waitFor(() => expect(api.setSceneStyle).toHaveBeenCalledWith("c", "s", "noir-detective"));
});

test("clicking a section header collapses its body and toggles aria-expanded", async () => {
  renderInspector();
  await screen.findByText("They first met.");
  const header = screen.getByRole("button", { name: /story so far/i });
  expect(header).toHaveAttribute("aria-expanded", "true");
  fireEvent.click(header);
  expect(header).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByText("They first met.")).not.toBeInTheDocument();
  fireEvent.click(header);
  expect(header).toHaveAttribute("aria-expanded", "true");
  await screen.findByText("They first met.");
});

test("section collapse state persists across a remount", async () => {
  const { unmount } = render(<SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  await screen.findByText("They first met.");
  fireEvent.click(screen.getByRole("button", { name: /story so far/i }));
  expect(screen.queryByText("They first met.")).not.toBeInTheDocument();
  expect(JSON.parse(localStorage.getItem("grimoire.inspector.sections")!)).toEqual({ story: true });
  unmount();

  render(<SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  await screen.findByText("Active characters"); // sanity: the inspector rendered
  expect(screen.queryByText("They first met.")).not.toBeInTheDocument(); // stayed collapsed
});

test("the Context section header still shows the percentage badge and collapses as a whole", async () => {
  renderInspector();
  await screen.findByText(/World info/);
  await screen.findByText("10%");
  const header = screen.getByRole("button", { name: /context/i });
  fireEvent.click(header);
  expect(screen.queryByText(/World info/)).not.toBeInTheDocument();
});

test("removing a cast member calls removeFromCast, reloads cast, and notifies the scene changed", async () => {
  const onSceneChanged = vi.fn();
  render(<SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={onSceneChanged} />);
  await screen.findByText("Seraphine");
  fireEvent.click(screen.getByRole("button", { name: /remove seraphine from scene/i }));
  await waitFor(() => expect(api.removeFromCast).toHaveBeenCalledWith("c", "s", "characters", "seraphine"));
  await waitFor(() => expect(onSceneChanged).toHaveBeenCalled());
  expect(api.getCast).toHaveBeenCalledTimes(2); // initial load + reload after remove
});

test("adding a character posts kind + id + role, reloads cast, and notifies the scene changed", async () => {
  (api.listCharacters as any).mockResolvedValue([
    { id: "seraphine", name: "Seraphine", default_version: "default", versions: [] },
    { id: "mara", name: "Mara", default_version: "default", versions: [] },
  ]);
  const onSceneChanged = vi.fn();
  render(<SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={onSceneChanged} />);
  await screen.findByRole("option", { name: "Mara" });
  fireEvent.change(screen.getByLabelText("Character or PC to add"), { target: { value: "mara" } });
  fireEvent.change(screen.getByLabelText("Role for new cast member"), { target: { value: "player" } });
  fireEvent.click(screen.getByRole("button", { name: /\+ add/i }));
  await waitFor(() => expect(api.addToCast).toHaveBeenCalledWith(
    "c", "s", { kind: "characters", id: "mara", role: "player" }));
  await waitFor(() => expect(onSceneChanged).toHaveBeenCalled());
});

test("adding a PC omits the role picker and forces role=player", async () => {
  (api.listCampaignPCs as any).mockResolvedValue([
    { id: "elara", name: "Elara", tags: [], default_version: "default", versions: [] }]);
  render(<SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  fireEvent.change(await screen.findByLabelText("Cast kind to add"), { target: { value: "pcs" } });
  await screen.findByRole("option", { name: "Elara" });
  expect(screen.queryByLabelText("Role for new cast member")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Character or PC to add"), { target: { value: "elara" } });
  fireEvent.click(screen.getByRole("button", { name: /\+ add/i }));
  await waitFor(() => expect(api.addToCast).toHaveBeenCalledWith(
    "c", "s", { kind: "pcs", id: "elara", role: "player" }));
});

test("offscreen scene hides the kind and role pickers, forcing npc characters only", async () => {
  (api.getCast as any).mockResolvedValue([]);
  render(<SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} pcless />);
  await screen.findByLabelText("Character or PC to add");
  expect(screen.queryByLabelText("Cast kind to add")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Role for new cast member")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Character or PC to add"), { target: { value: "seraphine" } });
  fireEvent.click(screen.getByRole("button", { name: /\+ add/i }));
  await waitFor(() => expect(api.addToCast).toHaveBeenCalledWith(
    "c", "s", { kind: "characters", id: "seraphine", role: "npc" }));
});

test("a failed add surfaces the error banner instead of silently failing", async () => {
  (api.addToCast as any).mockRejectedValue({ detail: "already cast" });
  (api.listCharacters as any).mockResolvedValue([
    { id: "mara", name: "Mara", default_version: "default", versions: [] }]);
  render(<SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  await screen.findByRole("option", { name: "Mara" });
  fireEvent.change(screen.getByLabelText("Character or PC to add"), { target: { value: "mara" } });
  fireEvent.click(screen.getByRole("button", { name: /\+ add/i }));
  await screen.findByText("already cast");
});

test("a failed remove surfaces the error banner instead of silently failing", async () => {
  (api.removeFromCast as any).mockRejectedValue({ detail: "actor kind not found" });
  renderInspector();
  await screen.findByText("Seraphine");
  fireEvent.click(screen.getByRole("button", { name: /remove seraphine from scene/i }));
  await screen.findByText("actor kind not found");
});
