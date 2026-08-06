import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SceneInspector } from "./SceneInspector";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    api: {
      getCast: vi.fn(), getCampaign: vi.fn(), listCharacters: vi.fn(), listPCs: vi.fn(),
      listCampaignPCs: vi.fn(), getSceneLocation: vi.fn(), getSceneContext: vi.fn(),
      listScenePrompts: vi.fn(), getScenePrompt: vi.fn(),
      // Resolves to "no weather" so the widget renders nothing: these suites
      // assert on the rest of the inspector, not the sky.
      getSceneWeather: vi.fn(() => Promise.resolve({ weather: null, location: null, native: null })),
      getCastDetail: vi.fn(), readEntity: vi.fn(), getChronicle: vi.fn(),
      getCalendarConfig: vi.fn(), setCalendarConfig: vi.fn(), getCalendarProviders: vi.fn(),
      getSceneDatetime: vi.fn(), setSceneDatetime: vi.fn(), getCalendarMonths: vi.fn(),
      listAppearances: vi.fn(), listEntityImages: vi.fn(),
      listEntities: vi.fn(), setSceneLocation: vi.fn(), sceneBriefing: vi.fn(),
      addToCast: vi.fn(), removeFromCast: vi.fn(),
      campaignImageUrl: () => "/img",
      entityImageUrl: () => "/loc-img",
    },
  };
});
vi.mock("../api/models", () => ({ getModels: vi.fn() }));
vi.mock("./ResponsePresetPicker", () => ({
  ResponsePresetPicker: ({ scope, cid, sid }: any) => (
    <div data-testid="response-preset-picker" data-scope={scope} data-cid={cid} data-sid={sid} />
  ),
}));
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
    model: "m", total_tokens: 100, dropped_tokens: 0, budget_tokens: 0,
    sections: [{ label: "World info", text: "lore text", tokens: 100,
                 tier: "spotlight", dropped: false, trimmed: 0 }],
  });
  (api.listScenePrompts as any).mockResolvedValue({ entries: [] });
  (api.getScenePrompt as any).mockResolvedValue(null);
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
  // Empty by default, so the briefing section renders nothing and the suites
  // that predate it assert on the same rail they always did (#118).
  (api.sceneBriefing as any).mockResolvedValue(EMPTY_BRIEFING);
});

const EMPTY_BRIEFING = {
  focus: [], plot: [], commitments: [], relationships: [], last_time: null };

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

test("the date actions are locked while a turn streams into this scene", async () => {
  // Setting a date for the first time re-slugs the scene file, and a scene's id
  // is its filename — so this is a rename control, and moving the file mid-turn
  // strands `finalize`, `_persist_reply` and the abort write on the old id.
  // Review caught the rail and the cast panel being locked while this one, the
  // always-mounted surface, was not (#95).
  (api.getSceneDatetime as any).mockResolvedValue(
    { current: null, history: [], suggested: "2026-07-06" });
  render(<SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} sceneLocked />);
  const button = await screen.findByRole("button", { name: /set date/i });
  await waitFor(() => expect(screen.getByLabelText("Scene date year")).toHaveValue(2026));
  expect(button).toBeDisabled();          // even with a date filled in
  expect(button).toHaveAttribute("title", "Not while this scene is generating");
  fireEvent.click(button);
  expect(api.setSceneDatetime).not.toHaveBeenCalled();
});

test("Advance to is locked for the same reason", async () => {
  // The dated branch renders a different button through the same handler.
  (api.getSceneDatetime as any).mockResolvedValue({
    current: { friendly: "6 July 2026", weekday: "Monday", holidays_today: [] },
    history: [], suggested: "2026-07-07",
  });
  const { rerender } = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} sceneLocked />);
  const button = await screen.findByRole("button", { name: /advance to/i });
  expect(button).toBeDisabled();
  // On the title, not on `disabled` alone: a dated scene does not prefill the
  // picker, so this button is disabled for want of a date either way and
  // `toBeDisabled` would pass without the lock existing. The title is set only
  // when locked, so it is the one assertion that distinguishes the two.
  expect(button).toHaveAttribute("title", "Not while this scene is generating");
  rerender(<SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  await waitFor(() => expect(
    screen.getByRole("button", { name: /advance to/i })).not.toHaveAttribute("title"));
});

test("offscreen scene shows the offscreen side-section", async () => {
  render(<SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} pcless />);
  await screen.findByText("Offscreen scene");
  expect(screen.getByText(/no player character/i)).toBeInTheDocument();
});

test("mounts the response preset picker scoped to this scene", async () => {
  renderInspector();
  const picker = await screen.findByTestId("response-preset-picker");
  expect(picker).toHaveAttribute("data-scope", "scene");
  expect(picker).toHaveAttribute("data-cid", "c");
  expect(picker).toHaveAttribute("data-sid", "s");
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

test("a section the budget dropped is shown as dropped, not hidden", async () => {
  // The whole point of reporting drops is that the user can see them; a
  // dropped section that simply vanished would be the silent truncation the
  // packer replaced.
  (api.getSceneContext as any).mockResolvedValue({
    model: "m", total_tokens: 100, dropped_tokens: 40, budget_tokens: 120,
    sections: [
      { label: "World info", text: "lore text", tokens: 100, tier: "spotlight", dropped: false, trimmed: 0 },
      { label: "Earlier scenes", text: "old scene", tokens: 40, tier: "archive", dropped: true, trimmed: 0 },
    ],
  });
  renderInspector();
  const dropped = await screen.findByText("Earlier scenes");
  expect(dropped.closest("details")!.className).toContain("dropped");
  await screen.findByText("dropped");
  await screen.findByText(/40 tok dropped to fit the budget/i);
});

test("percentages measure against the configured budget, not the model window", async () => {
  (api.getSceneContext as any).mockResolvedValue({
    model: "m", total_tokens: 100, dropped_tokens: 0, budget_tokens: 200,
    sections: [{ label: "World info", text: "lore text", tokens: 100,
                 tier: "spotlight", dropped: false, trimmed: 0 }],
  });
  renderInspector();
  // 100 of a 200-token budget is 50%, not 10% of the model's 1000-token window
  await screen.findByText("50%");
  await screen.findByText(/100 \/ 200 tok/);
});

test("a trimmed history says how many turns went", async () => {
  (api.getSceneContext as any).mockResolvedValue({
    model: "m", total_tokens: 100, dropped_tokens: 0, budget_tokens: 200,
    sections: [{ label: "Conversation history", text: "turns", tokens: 100,
                 tier: "history", dropped: false, trimmed: 3 }],
  });
  renderInspector();
  await screen.findByText("3 trimmed");
});

test("percentages use the smaller of the budget and the model window", async () => {
  // A 32k budget left over from a bigger model would otherwise report a full
  // 8k window as a quarter used — hiding the overflow this panel exists to show.
  (getModels as any).mockResolvedValue([
    { id: "m", name: "M", context: 200, prompt: "0", completion: "0" }]);
  (api.getSceneContext as any).mockResolvedValue({
    model: "m", total_tokens: 100, dropped_tokens: 0, budget_tokens: 1000,
    sections: [{ label: "World info", text: "lore text", tokens: 100,
                 tier: "spotlight", dropped: false, trimmed: 0 }],
  });
  renderInspector();
  await screen.findByText("50%");              // 100 of the model's 200, not of 1000
  await screen.findByText(/100 \/ 200 tok/);
});

// ---- the pre-scene briefing (#118) ----------------------------------------

const BRIEFING = {
  focus: ["Winifred Vance"],
  plot: [{ id: "the-ledger", title: "Find the ledger", status: "open",
           last_scene: "s0", latest_beat: "She named it aloud.",
           involves: ["Winifred Vance"] },
         { id: "the-tide", title: "The tide turns", status: "advanced",
           last_scene: "s0", latest_beat: "", involves: [] }],
  commitments: [{ id: "the-deadline", title: "Seraphine's midnight deadline",
                  kind: "threat", status: "open", due: "midnight",
                  last_scene: "s0", latest_beat: "Sworn in front of her.",
                  involves: ["Winifred Vance"] }],
  relationships: ["Winifred Vance distrusts Seraphine."],
  last_time: { id: "s0", one_line: "They argued.", title: "First Night",
               date: "5 Harvestmoon" },
};

function renderWithBriefing(posts?: number) {
  (api.sceneBriefing as any).mockResolvedValue(BRIEFING);
  render(<SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}}
                         posts={posts} />);
}

test("the briefing lists what is open, what came before, and who it involves", async () => {
  renderWithBriefing();
  await screen.findByText("Find the ledger");
  expect(api.sceneBriefing).toHaveBeenCalledWith("c", "s");
  expect(screen.getByText("Seraphine's midnight deadline")).toBeInTheDocument();
  expect(screen.getByText("due midnight")).toBeInTheDocument();
  expect(screen.getByText("They argued.")).toBeInTheDocument();
  expect(screen.getByText(/First Night/)).toBeInTheDocument();
  expect(screen.getByText("Winifred Vance distrusts Seraphine.")).toBeInTheDocument();
  // The flag names who, so a scene with two players can tell whose thread it is.
  expect(screen.getAllByText("involves Winifred Vance")).toHaveLength(2);
});

test("an unflagged row still lists — narrowing is ordering, not filtering", async () => {
  renderWithBriefing();
  await screen.findByText("The tide turns");
});

test("the briefing section is absent when there is nothing to brief", async () => {
  render(<SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  await screen.findByText("Seraphine");                  // the rail has loaded...
  expect(screen.queryByText("Briefing")).not.toBeInTheDocument();   // ...without it
});

test("the briefing survives a failed load as the empty state", async () => {
  (api.sceneBriefing as any).mockRejectedValue(new Error("nope"));
  render(<SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  await screen.findByText("Seraphine");
  expect(screen.queryByText("Briefing")).not.toBeInTheDocument();
});

test("the briefing opens itself on a fresh scene and not on a long one", async () => {
  renderWithBriefing(0);
  await screen.findByText("Find the ledger");
  expect(screen.getByRole("button", { name: /Briefing/ })).toHaveAttribute("aria-expanded", "true");
});

test("a scene already several posts in gets the briefing collapsed", async () => {
  renderWithBriefing(20);
  const head = await screen.findByRole("button", { name: /Briefing/ });
  expect(head).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByText("Find the ledger")).not.toBeInTheDocument();
});

test("an explicit toggle outlives the post count that set the default", async () => {
  renderWithBriefing(20);
  fireEvent.click(await screen.findByRole("button", { name: /Briefing/ }));
  await screen.findByText("Find the ledger");           // opened by hand...
  expect(JSON.parse(localStorage.getItem("grimoire.inspector.sections")!).briefing)
    .toBe(false);                                       // ...and remembered as open
});

test("a briefing never renders under a different campaign's scene of the same id", async () => {
  // The route is /campaigns/:cid with no `key`, so React Router reuses
  // CampaignView across campaigns, and scene ids are per-campaign — so two
  // campaigns sitting on "s" would have matched on sid alone, showing one
  // game's commitments under the other's name (Codex review).
  (api.sceneBriefing as any).mockResolvedValue(BRIEFING);
  const { rerender } = render(
    <SceneInspector cid="a" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  await screen.findByText("Find the ledger");

  // Campaign b's request never settles, so anything on screen is a's.
  (api.sceneBriefing as any).mockReturnValue(new Promise(() => {}));
  rerender(<SceneInspector cid="b" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  await waitFor(() =>
    expect(screen.queryByText("Find the ledger")).not.toBeInTheDocument());
});

// ---- turn history: what the model saw for a PAST turn (#157) ----

const TURNS = [
  { id: "000002", scene: "s", ts: "2026-08-06T12:00:00Z", task: "regenerate",
    model: "m", total_tokens: 90, dropped_tokens: 0, budget_tokens: 0 },
  { id: "000001", scene: "s", ts: "2026-08-06T11:00:00Z", task: "chat",
    model: "m", total_tokens: 80, dropped_tokens: 0, budget_tokens: 0 },
];

const FROZEN = {
  id: "000001", ts: "2026-08-06T11:00:00Z", task: "chat", model: "m",
  total_tokens: 80, dropped_tokens: 0, budget_tokens: 0,
  sections: [{ label: "World info", text: "the lore as it stood then", tokens: 80,
               tier: "spotlight", dropped: false, trimmed: 0 }],
};

test("with no captured turns the history section says so", async () => {
  renderInspector();
  await screen.findByText("No captured turns yet.");
});

test("captured turns are listed by what kind of turn they were", async () => {
  (api.listScenePrompts as any).mockResolvedValue({ entries: TURNS });
  renderInspector();
  await screen.findByText("Regenerate");
  await screen.findByText("Send");
});

test("clicking a turn shows that turn's frozen prompt instead of the live one", async () => {
  (api.listScenePrompts as any).mockResolvedValue({ entries: TURNS });
  (api.getScenePrompt as any).mockResolvedValue(FROZEN);
  renderInspector();

  // the live composition is what shows first
  await screen.findByText("lore text");

  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));

  await waitFor(() => expect(api.getScenePrompt).toHaveBeenCalledWith("c", "s", "000001"));
  await screen.findByText("the lore as it stood then");
  await screen.findByText(/What the model saw/);
  // and the live text is gone — showing both would be the confusion this fixes
  expect(screen.queryByText("lore text")).toBeNull();
});

test("going back restores the live context", async () => {
  (api.listScenePrompts as any).mockResolvedValue({ entries: TURNS });
  (api.getScenePrompt as any).mockResolvedValue(FROZEN);
  renderInspector();

  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));
  await screen.findByText("the lore as it stood then");

  fireEvent.click(screen.getByRole("button", { name: /Back to live context/ }));
  await screen.findByText("lore text");
  expect(screen.queryByText(/What the model saw/)).toBeNull();
});

test("a turn that has aged out of the log says so rather than blanking", async () => {
  (api.listScenePrompts as any).mockResolvedValue({ entries: TURNS });
  (api.getScenePrompt as any).mockRejectedValue({ status: 404, detail: "not found" });
  renderInspector();

  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));
  await screen.findByText(/aged out of the log/);
  await screen.findByText("lore text");        // still on the live view
});

test("a frozen turn is measured against the budget it was captured under", async () => {
  // Not today's: the live budget has since been raised, and reporting the past
  // turn against it would show a prompt that overran as comfortably inside.
  (api.listScenePrompts as any).mockResolvedValue({ entries: TURNS });
  (api.getScenePrompt as any).mockResolvedValue({ ...FROZEN, budget_tokens: 100 });
  (api.getSceneContext as any).mockResolvedValue({
    model: "m", total_tokens: 100, dropped_tokens: 0, budget_tokens: 10000,
    sections: [{ label: "World info", text: "lore text", tokens: 100,
                 tier: "spotlight", dropped: false, trimmed: 0 }],
  });
  renderInspector();

  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));
  await screen.findByText(/80 \/ 100 tok/);    // the frozen budget, not 10000
  await screen.findByText("80%");
});

test("a snapshot arriving after a scene change is dropped, not shown", async () => {
  // The guard is worth a test because the obvious version of it — comparing the
  // callback's own captured sid against itself — is always satisfied and looks
  // right on the page.
  (api.listScenePrompts as any).mockResolvedValue({ entries: TURNS });
  let release: (v: any) => void = () => {};
  (api.getScenePrompt as any).mockReturnValue(new Promise((r) => { release = r; }));

  const { rerender } = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));

  // the reader moves to another scene while the fetch is still in flight
  rerender(<SceneInspector cid="c" sid="s2" refreshKey={0} onSceneChanged={() => {}} />);
  release(FROZEN);

  await screen.findByText("lore text");                       // still the live view
  expect(screen.queryByText("the lore as it stood then")).toBeNull();
  expect(screen.queryByText(/What the model saw/)).toBeNull();
});
