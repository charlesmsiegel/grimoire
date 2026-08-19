import { render, screen, fireEvent, waitFor, within, act } from "@testing-library/react";
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
      getCampaignClock: vi.fn(), previewAdvance: vi.fn(), advanceTime: vi.fn(),
      listAppearances: vi.fn(), listEntityImages: vi.fn(),
      listEntities: vi.fn(), setSceneLocation: vi.fn(), sceneBriefing: vi.fn(),
      getRollingSummary: vi.fn(), refreshRollingSummary: vi.fn(),
      getSceneBreak: vi.fn(), askSceneBreak: vi.fn(), dismissSceneBreak: vi.fn(),
      addToCast: vi.fn(), removeFromCast: vi.fn(),
      getPins: vi.fn(), setPin: vi.fn(), removePin: vi.fn(),
      actorImageUrl: (_sc: { id: string }, k: string, a: string, v: string) => `/img/${k}/${a}/${v}`,
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
  (api.getCampaignClock as any).mockResolvedValue(
    { now: "2026-05-01", friendly: "1 May 2026", log: [] });
  (api.listAppearances as any).mockResolvedValue([]);
  (api.listEntityImages as any).mockResolvedValue([]);
  (api.listEntities as any).mockResolvedValue([]);
  (api.setSceneLocation as any).mockResolvedValue({ ok: true, moved: true, name: "" });
  // Empty by default, so the briefing section renders nothing and the suites
  // that predate it assert on the same rail they always did (#118).
  (api.sceneBriefing as any).mockResolvedValue(EMPTY_BRIEFING);
  (api.getRollingSummary as any).mockResolvedValue({
    summary: "", at: 0, total: 0, stale: false, every: 10, due: false });
  (api.refreshRollingSummary as any).mockResolvedValue({
    summary: "Refolded.", at: 4, total: 4, stale: false, every: 10, due: false,
    refreshed: true });
  // The scene-break detector saying nothing (#84), so every suite that predates
  // it renders the rail it always did.
  (api.getSceneBreak as any).mockResolvedValue(NO_SCENE_BREAK);
  (api.askSceneBreak as any).mockResolvedValue({ ...NO_SCENE_BREAK, asked: false });
  (api.dismissSceneBreak as any).mockResolvedValue(NO_SCENE_BREAK);
  // No rules by default (#129), so every suite that predates pins renders the
  // rail it always did -- an empty section with its hint.
  (api.getPins as any).mockResolvedValue({ pins: [] });
  (api.setPin as any).mockResolvedValue({ ok: true });
  (api.removePin as any).mockResolvedValue({ ok: true });
});

/** One pin row as the panel receives it. */
function pinRow(over: Partial<any> = {}) {
  return {
    ref: "characters:seraphine", kind: "characters", id: "seraphine",
    name: "Seraphine", missing: false, mode: "pin", scope: "scene", sid: "s",
    ttl_posts: 0, remaining: null, created: "2026-01-01T00:00:00Z", ...over,
  };
}

const NO_SCENE_BREAK = {
  verdict: "" as const, reason: "", title: "",
  posts: 0, score: 0, signals: [], every: 20, due: false,
};
const EMPTY_BRIEFING = {
  focus: [], plot: [], commitments: [], relationships: [], last_time: null };

function renderInspector(onSceneChanged: () => void = () => {}) {
  render(<SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={onSceneChanged} />);
}

// ---- the scene-break detector (#84) ----
const BREAK_YES = {
  ...NO_SCENE_BREAK, verdict: "yes" as const,
  reason: "The ledger changed hands and both sides walked away.",
  title: "The Long Walk Back", posts: 40, score: 3, due: false,
};

test("a confirmed break shows why, and what the next scene might be called", async () => {
  (api.getSceneBreak as any).mockResolvedValue(BREAK_YES);
  renderInspector();
  await screen.findByText("The ledger changed hands and both sides walked away.");
  await screen.findByText(/The Long Walk Back/);
});

test("the detector never ends the scene itself — the only actions are asking and declining", async () => {
  (api.getSceneBreak as any).mockResolvedValue(BREAK_YES);
  renderInspector();
  await screen.findByText(/The Long Walk Back/);
  // No control here writes the transcript or the scene's done flag; a reader
  // finding one would be finding an auto-split, which this feature must not do.
  expect(screen.queryByRole("button", { name: /end scene|split|start next/i })).toBeNull();
  expect(screen.getByRole("button", { name: /not here/i })).toBeInTheDocument();
});

test("a model that said no is said differently from nothing having been asked", async () => {
  (api.getSceneBreak as any).mockResolvedValue({
    ...NO_SCENE_BREAK, verdict: "no", reason: "They are still mid-argument.", posts: 40 });
  renderInspector();
  await screen.findByText(/still mid-beat/i);
  await screen.findByText("They are still mid-argument.");
  // ...and declining is not offered against an answer there is nothing to decline.
  expect(screen.queryByRole("button", { name: /not here/i })).toBeNull();
});

test("with nothing to suggest it says what it can see rather than nothing at all", async () => {
  (api.getSceneBreak as any).mockResolvedValue({
    ...NO_SCENE_BREAK, posts: 12, score: 1,
    signals: [{ kind: "length", weight: 1, detail: "12 posts since this was last considered" }] });
  renderInspector();
  await screen.findByText(/Nothing to suggest yet/);
  await screen.findByText("12 posts since this was last considered");
});

test("switched off, the panel says so instead of reporting a score of zero", async () => {
  (api.getSceneBreak as any).mockResolvedValue({ ...NO_SCENE_BREAK, every: 0, posts: 400 });
  renderInspector();
  await screen.findByText(/Turned off/);
});

test("Ask now forces a question and installs whatever comes back", async () => {
  (api.askSceneBreak as any).mockResolvedValue({ ...BREAK_YES, asked: true });
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /ask now/i }));
  await waitFor(() => expect(api.askSceneBreak).toHaveBeenCalledWith("c", "s", true));
  await screen.findByText("The ledger changed hands and both sides walked away.");
});

test("a failed question reports itself and never blanks a standing proposal", async () => {
  (api.getSceneBreak as any).mockResolvedValue(BREAK_YES);
  (api.askSceneBreak as any).mockRejectedValue({ detail: "OpenRouter key not set" });
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /ask now/i }));
  await screen.findByText("OpenRouter key not set");
  expect(screen.getByText("The ledger changed hands and both sides walked away."))
    .toBeInTheDocument();
});

test("Not here goes to the server, because the watermark it moves lives there", async () => {
  // A local dismissal would leave the same posts re-earning the same
  // suggestion on the very next turn.
  (api.getSceneBreak as any).mockResolvedValue(BREAK_YES);
  (api.dismissSceneBreak as any).mockResolvedValue(NO_SCENE_BREAK);
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /not here/i }));
  await waitFor(() => expect(api.dismissSceneBreak).toHaveBeenCalledWith("c", "s"));
  await screen.findByText(/Nothing to suggest yet/);
});

test("switching scenes never shows the previous scene's proposal", async () => {
  // A proposal is prose ABOUT a story, so showing one under another scene reads
  // as fact rather than as lag — `rolling`'s rule, for a sharper reason.
  (api.getSceneBreak as any).mockResolvedValueOnce(BREAK_YES);
  const { rerender } = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  await screen.findByText("The ledger changed hands and both sides walked away.");
  (api.getSceneBreak as any).mockResolvedValue(NO_SCENE_BREAK);
  rerender(<SceneInspector cid="c" sid="s2" refreshKey={0} onSceneChanged={() => {}} />);
  await waitFor(() => expect(
    screen.queryByText("The ledger changed hands and both sides walked away.")).toBeNull());
});

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
  expect(screen.getByText("Y")).toBeInTheDocument();                     // no roster row: initials
  expect(screen.getByText("player", { selector: ".role-chip" })).toBeInTheDocument();
  expect(screen.getByText("npc", { selector: ".role-chip" })).toBeInTheDocument();
});

test("a PC in the roster gets a portrait like anyone else", async () => {
  // The roster lookup used to be gated on `kind === "characters"`, so a PC fell
  // back to initials even with a locked version to build a URL from (#219).
  (api.getCast as any).mockResolvedValue([
    { kind: "pcs", id: "yara", role: "player", name: "Yara" }]);
  (api.listAppearances as any).mockResolvedValue([
    { kind: "pcs", id: "yara", version: "v3", role: "player", scenes: ["s"] }]);
  (api.listPCs as any).mockResolvedValue([
    { id: "yara", name: "Yara", tags: [], default_version: "default", versions: [] }]);
  renderInspector();
  const portrait = await screen.findByAltText("yara portrait");
  expect(portrait.getAttribute("src")).toBe("/img/pcs/yara/v3");
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

test("the Campaign clock section is shut until asked for, then mounts the panel", async () => {
  // The one campaign-scoped section on a scene-scoped rail (#100): collapsed by
  // default so it does not push the scene's own state down, and — because it is
  // collapsed — the panel must not fetch the clock until a reader opens it.
  renderInspector();
  await screen.findByText("Active characters");
  expect(api.getCampaignClock).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: /campaign clock/i }));
  expect(await screen.findByText(/Now: 1 May 2026/)).toBeInTheDocument();
  expect(api.getCampaignClock).toHaveBeenCalledWith("c");
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
  fireEvent.click(screen.getByRole("button", { name: "+ Add" }));
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
  fireEvent.click(screen.getByRole("button", { name: "+ Add" }));
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
  fireEvent.click(screen.getByRole("button", { name: "+ Add" }));
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
  fireEvent.click(screen.getByRole("button", { name: "+ Add" }));
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

test("the two off-scene cast tiers read as separate rows with their own numbers", async () => {
  // The backend splits the directory into a section per tier (#2) precisely so
  // the unbounded one can be priced on its own; the panel has to show them as
  // two readouts rather than collapsing them back into a single row.
  (api.getSceneContext as any).mockResolvedValue({
    model: "m", total_tokens: 100, dropped_tokens: 0, budget_tokens: 200,
    sections: [
      { label: "Off-scene cast · active elsewhere", text: "Winifred: she counts the tide.",
        tokens: 20, tier: "background", dropped: false, trimmed: 0 },
      { label: "Off-scene cast · known to exist", text: "Mara: a courier with cold hands.",
        tokens: 80, tier: "background", dropped: false, trimmed: 0 },
    ],
  });
  renderInspector();
  const active = await screen.findByText("Off-scene cast · active elsewhere");
  const known = await screen.findByText("Off-scene cast · known to exist");
  expect(active.closest("details")).not.toBe(known.closest("details"));
  await screen.findByText("20 · 10%");
  await screen.findByText("80 · 40%");
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

test("a turn list arriving after a scene change is dropped, not listed", async () => {
  // Worse than a stale label: entry ids are per-campaign counters, so they
  // collide across campaigns and clicking a stale row would fetch a different
  // campaign's prompt under it.
  (api.listScenePrompts as any).mockResolvedValue({ entries: TURNS });
  const { rerender } = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  await screen.findByText("Regenerate");

  let release: (v: any) => void = () => {};
  (api.listScenePrompts as any).mockReturnValue(new Promise((r) => { release = r; }));
  rerender(<SceneInspector cid="c2" sid="s" refreshKey={0} onSceneChanged={() => {}} />);

  // the previous campaign's rows are gone the moment the scene changes, not
  // when the replacement request settles
  await screen.findByText("No captured turns yet.");
  release({ entries: TURNS });
  await screen.findByText("Regenerate");   // c2's own rows, once they arrive
});

test("the last turn clicked wins even if an earlier request resolves after it", async () => {
  // The scene guard does not cover this: both requests are for the same scene,
  // so without a per-request check the slower first click overwrites the second.
  (api.listScenePrompts as any).mockResolvedValue({ entries: TURNS });
  const pending: Record<string, (v: any) => void> = {};
  (api.getScenePrompt as any).mockImplementation((_c: string, _s: string, eid: string) =>
    new Promise((r) => { pending[eid] = r; }));

  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));        // 000001
  fireEvent.click(await screen.findByRole("button", { name: /^Regenerate/ }));  // 000002

  pending["000002"]({ ...FROZEN, id: "000002", task: "regenerate",
                      sections: [{ label: "World info", text: "the regenerate prompt",
                                   tokens: 90, tier: "spotlight", dropped: false, trimmed: 0 }] });
  await screen.findByText("the regenerate prompt");

  pending["000001"](FROZEN);          // the superseded click lands late
  await new Promise((r) => setTimeout(r, 0));
  await screen.findByText("the regenerate prompt");
  expect(screen.queryByText("the lore as it stood then")).toBeNull();
});


test("a superseded turn-list response does not overwrite a newer one", async () => {
  // `fresh: true` stops the refresh JOINING a pre-turn request; it does not
  // order two independent ones. If the newer answers first, the older `.then`
  // would put the pre-turn rows back.
  const pending: ((v: any) => void)[] = [];
  (api.listScenePrompts as any).mockImplementation(
    () => new Promise((r) => { pending.push(r); }));

  const { rerender } = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  rerender(<SceneInspector cid="c" sid="s" refreshKey={1} onSceneChanged={() => {}} />);
  await waitFor(() => expect(pending.length).toBe(2));

  pending[1]({ entries: TURNS });                    // the post-generation read
  await screen.findByText("Regenerate");
  pending[0]({ entries: [] });                       // the stale one, late
  await new Promise((r) => setTimeout(r, 0));

  await screen.findByText("Regenerate");             // still there
  expect(screen.queryByText("No captured turns yet.")).toBeNull();
});

test("going back to live discards a detail fetch still in flight", async () => {
  // Click B, change your mind, go back — B must not reopen the panel over an
  // explicit choice to leave it.
  (api.listScenePrompts as any).mockResolvedValue({ entries: TURNS });
  (api.getScenePrompt as any).mockResolvedValueOnce(FROZEN);
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));
  await screen.findByText("the lore as it stood then");

  let release: (v: any) => void = () => {};
  (api.getScenePrompt as any).mockReturnValue(new Promise((r) => { release = r; }));
  fireEvent.click(screen.getByRole("button", { name: /^Regenerate/ }));
  fireEvent.click(screen.getByRole("button", { name: /Back to live context/ }));
  await screen.findByText("lore text");

  release({ ...FROZEN, id: "000002",
            sections: [{ label: "World info", text: "the regenerate prompt", tokens: 90,
                         tier: "spotlight", dropped: false, trimmed: 0 }] });
  await new Promise((r) => setTimeout(r, 0));

  await screen.findByText("lore text");              // still live
  expect(screen.queryByText("the regenerate prompt")).toBeNull();
  expect(screen.queryByText(/What the model saw/)).toBeNull();
});

test("a frozen turn is not painted under a scene it does not belong to", async () => {
  // The clearing effect runs AFTER render, so scoping has to happen during it —
  // otherwise the first paint under the new scene still shows the old prompt.
  (api.listScenePrompts as any).mockResolvedValue({ entries: TURNS });
  (api.getScenePrompt as any).mockResolvedValue(FROZEN);
  const { rerender } = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));
  await screen.findByText("the lore as it stood then");

  rerender(<SceneInspector cid="c" sid="s2" refreshKey={0} onSceneChanged={() => {}} />);
  // synchronously after the re-render, before effects have settled
  expect(screen.queryByText("the lore as it stood then")).toBeNull();
  expect(screen.queryByText(/What the model saw/)).toBeNull();
});

test("switching campaigns does not paint the other campaign's frozen turn", async () => {
  // Scene ids repeat across campaigns, so `sid` alone would match here.
  (api.listScenePrompts as any).mockResolvedValue({ entries: TURNS });
  (api.getScenePrompt as any).mockResolvedValue(FROZEN);
  const { rerender } = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));
  await screen.findByText("the lore as it stood then");

  rerender(<SceneInspector cid="c2" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  expect(screen.queryByText("the lore as it stood then")).toBeNull();
});

// ---- the live rolling summary (#85) ----
test("shows the running summary and what it covers", async () => {
  (api.getRollingSummary as any).mockResolvedValue({
    summary: "Mara reaches the salt gate; the ledger is still missing.",
    at: 12, total: 14, stale: false, every: 10, due: false });
  renderInspector();
  await screen.findByText("Mara reaches the salt gate; the ledger is still missing.");
  await screen.findByText(/12 of 14 posts/);
});

test("says so when nothing has been summarized yet, rather than showing an empty box", async () => {
  renderInspector();
  await screen.findByText(/No summary yet/);
});

test("flags a summary whose posts have since been rerolled or edited", async () => {
  (api.getRollingSummary as any).mockResolvedValue({
    summary: "An older account of the scene.", at: 12, total: 12,
    stale: true, every: 10, due: false });
  renderInspector();
  await screen.findByText(/out of date/i);
});

test("Refresh now forces a refold and shows the result", async () => {
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /refresh now/i }));
  await waitFor(() => expect(api.refreshRollingSummary).toHaveBeenCalledWith("c", "s", true));
  await screen.findByText("Refolded.");
});

test("a refresh that spends nothing leaves the panel as it was", async () => {
  (api.getRollingSummary as any).mockResolvedValue({
    summary: "Standing summary.", at: 4, total: 4, stale: false, every: 10, due: false });
  (api.refreshRollingSummary as any).mockResolvedValue({
    summary: "Standing summary.", at: 4, total: 4, stale: false, every: 10, due: false,
    refreshed: false });
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /refresh now/i }));
  await waitFor(() => expect(api.refreshRollingSummary).toHaveBeenCalled());
  await screen.findByText("Standing summary.");
});

test("a failed refresh reports itself and never blanks the summary", async () => {
  (api.getRollingSummary as any).mockResolvedValue({
    summary: "Standing summary.", at: 4, total: 4, stale: false, every: 10, due: false });
  (api.refreshRollingSummary as any).mockRejectedValue({ detail: "OpenRouter key not set" });
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /refresh now/i }));
  await screen.findByText("OpenRouter key not set");
  expect(screen.getByText("Standing summary.")).toBeInTheDocument();
});

test("a summary that cannot be read is not an empty summary", async () => {
  // The panel reads on every scene select, and a failed GET must not be
  // indistinguishable from a scene nobody has summarized.
  (api.getRollingSummary as any).mockRejectedValue(new Error("offline"));
  renderInspector();
  await screen.findByText(/could not be read/i);
});

test("switching scenes never shows the previous scene's summary", async () => {
  // Prose under the wrong scene reads as fact, where a stale token count reads
  // as lag — and two selects in a row can answer out of order.
  (api.getRollingSummary as any).mockResolvedValueOnce({
    summary: "Scene one: Mara reaches the salt gate.", at: 4, total: 4,
    stale: false, every: 10, due: false });
  const view = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  await screen.findByText("Scene one: Mara reaches the salt gate.");

  (api.getRollingSummary as any).mockReturnValueOnce(new Promise(() => {}));  // never answers
  view.rerender(
    <SceneInspector cid="c" sid="s2" refreshKey={0} onSceneChanged={() => {}} />);
  await waitFor(() =>
    expect(screen.queryByText("Scene one: Mara reaches the salt gate.")).toBeNull());
});

test("a late read for a scene the reader has left does not evict the current one", async () => {
  // Stamping the response keeps A's prose off B's panel, but on its own it does
  // not stop A's *later* answer from replacing B's — after which the stamp
  // rejects it and the panel says "No summary yet" about a scene that has one.
  let answerA: ((v: any) => void) | undefined;
  (api.getRollingSummary as any).mockImplementationOnce(
    () => new Promise((resolve) => { answerA = resolve; }));
  const view = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);

  (api.getRollingSummary as any).mockResolvedValueOnce({
    summary: "Scene two: the ledger surfaces.", at: 2, total: 2,
    stale: false, every: 10, due: false });
  view.rerender(
    <SceneInspector cid="c" sid="s2" refreshKey={0} onSceneChanged={() => {}} />);
  await screen.findByText("Scene two: the ledger surfaces.");

  // A answers last, and must be dropped rather than overwrite B's state
  await act(async () => {
    answerA!({ summary: "Scene one: Mara reaches the salt gate.", at: 4, total: 4,
               stale: false, every: 10, due: false });
  });
  expect(screen.getByText("Scene two: the ledger surfaces.")).toBeInTheDocument();
  expect(screen.queryByText(/No summary yet/)).toBeNull();
});

test("the same scene id in another campaign does not inherit the summary", async () => {
  // Scene ids are campaign-local and collide freely — `001--saltmarch` is what
  // every campaign's first scene by that title is called. `App.tsx` renders
  // CampaignView without a key, so navigating between campaigns reuses this
  // inspector rather than remounting it.
  (api.getRollingSummary as any).mockResolvedValueOnce({
    summary: "Campaign one: Mara reaches the salt gate.", at: 4, total: 4,
    stale: false, every: 10, due: false });
  const view = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  await screen.findByText("Campaign one: Mara reaches the salt gate.");

  (api.getRollingSummary as any).mockReturnValueOnce(new Promise(() => {}));
  view.rerender(
    <SceneInspector cid="c2" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  await waitFor(() =>
    expect(screen.queryByText("Campaign one: Mara reaches the salt gate.")).toBeNull());
});

test("an older read of the same scene cannot overwrite a newer one", async () => {
  // This effect re-runs on `refreshKey` for the SAME scene — twice per turn, in
  // fact — so two reads are routinely in flight together. Scene identity cannot
  // order them.
  let answerFirst: ((v: any) => void) | undefined;
  (api.getRollingSummary as any).mockImplementationOnce(
    () => new Promise((resolve) => { answerFirst = resolve; }));
  const view = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);

  (api.getRollingSummary as any).mockResolvedValueOnce({
    summary: "The refreshed summary.", at: 12, total: 12,
    stale: false, every: 10, due: false });
  view.rerender(
    <SceneInspector cid="c" sid="s" refreshKey={1} onSceneChanged={() => {}} />);
  await screen.findByText("The refreshed summary.");

  await act(async () => {
    answerFirst!({ summary: "The summary as it was before the refresh.", at: 4,
                   total: 4, stale: false, every: 10, due: false });
  });
  expect(screen.getByText("The refreshed summary.")).toBeInTheDocument();
});

test("a refresh that fails after the reader moves on does not banner the new scene", async () => {
  // `error` is shared by every action in this panel and the scene-change effect
  // does not clear it, so an error installed for the scene the reader left
  // sits over the scene they are on until something else happens to clear it.
  let rejectA: ((e: any) => void) | undefined;
  (api.refreshRollingSummary as any).mockImplementationOnce(
    () => new Promise((_resolve, reject) => { rejectA = reject; }));
  const view = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: /refresh now/i }));

  view.rerender(
    <SceneInspector cid="c" sid="s2" refreshKey={0} onSceneChanged={() => {}} />);
  await act(async () => { rejectA!({ detail: "OpenRouter key not set" }); });

  expect(screen.queryByText("OpenRouter key not set")).toBeNull();
});

test("switching scenes frees the new scene's Refresh button", async () => {
  // `rollingBusy` was a bare boolean, so scene A's in-flight refold left B's
  // button disabled and reading "Summarizing…" for as long as A's provider call
  // took — for a result B can no longer use.
  (api.refreshRollingSummary as any).mockReturnValueOnce(new Promise(() => {}));
  const view = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: /refresh now/i }));
  await screen.findByRole("button", { name: /summarizing/i });

  view.rerender(
    <SceneInspector cid="c" sid="s2" refreshKey={0} onSceneChanged={() => {}} />);
  const button = await screen.findByRole("button", { name: /refresh now/i });
  expect(button).not.toBeDisabled();
});

test("a failed reread is reported even when older prose is on screen", async () => {
  // Same scene, `refreshKey` bumped: the cached value still matches the key, so
  // the failure used to be suppressed entirely and the panel kept presenting
  // pre-turn coverage as current with nothing to say otherwise.
  (api.getRollingSummary as any).mockResolvedValueOnce({
    summary: "Mara reaches the salt gate.", at: 10, total: 10,
    stale: false, every: 10, due: false });
  const view = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  await screen.findByText("Mara reaches the salt gate.");

  (api.getRollingSummary as any).mockRejectedValueOnce(new Error("offline"));
  view.rerender(
    <SceneInspector cid="c" sid="s" refreshKey={1} onSceneChanged={() => {}} />);

  // A failed read beside cached prose reads differently from a failed read with
  // nothing to show: the prose is not gone, it just cannot claim to be current.
  await screen.findByText(/latest read failed/i);
  // the prose is still the best thing anyone has, so it stays
  expect(screen.getByText("Mara reaches the salt gate.")).toBeInTheDocument();
  // ...but its coverage must not be presented as current
  expect(screen.queryByText(/Covers 10 of 10 posts/)).toBeNull();
});

test("a read issued before a manual refresh cannot undo it", async () => {
  // The GET and the POST shared one token, so a read issued while the refold was
  // in flight bumped it, and the POST's authoritative answer was then thrown
  // away as superseded — leaving the panel on the old summary although the new
  // one is durable on the server.
  let answerRead: ((v: any) => void) | undefined;
  let answerPost: ((v: any) => void) | undefined;
  (api.getRollingSummary as any).mockResolvedValueOnce({
    summary: "The old summary.", at: 4, total: 4, stale: false, every: 10, due: false });
  (api.refreshRollingSummary as any).mockImplementationOnce(
    () => new Promise((resolve) => { answerPost = resolve; }));
  const view = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  await screen.findByText("The old summary.");
  fireEvent.click(screen.getByRole("button", { name: /refresh now/i }));

  // a routine reread starts while the refold is still out, and answers first
  (api.getRollingSummary as any).mockImplementationOnce(
    () => new Promise((resolve) => { answerRead = resolve; }));
  view.rerender(
    <SceneInspector cid="c" sid="s" refreshKey={1} onSceneChanged={() => {}} />);
  await waitFor(() => expect(answerRead).toBeDefined());
  await act(async () => {
    answerRead!({ summary: "The old summary.", at: 4, total: 4,
                  stale: false, every: 10, due: false });
  });
  await act(async () => {
    answerPost!({ summary: "The refolded summary.", at: 9, total: 9,
                  stale: false, every: 10, due: false, refreshed: true });
  });

  await screen.findByText("The refolded summary.");
});

test("Refresh is held while a turn is streaming into the scene", async () => {
  // A chat appends the player post before streaming and the reply only when it
  // lands, so a refold in between covers an unanswered post — and the reply is
  // an append, which does not invalidate the digest, so it can stay out of the
  // "current" summary until the next threshold.
  render(<SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}}
                         sceneLocked />);
  expect(await screen.findByRole("button", { name: /refresh now/i })).toBeDisabled();
});

test("a refresh error does not follow the reader to the next scene", async () => {
  // The token guard only retires a rejection that arrives AFTER navigation. One
  // that lands while the scene is still selected is stored, and nothing on the
  // scene-change path clears the panel's shared banner.
  (api.refreshRollingSummary as any).mockRejectedValueOnce({ detail: "OpenRouter key not set" });
  const view = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: /refresh now/i }));
  await screen.findByText("OpenRouter key not set");

  view.rerender(
    <SceneInspector cid="c" sid="s2" refreshKey={0} onSceneChanged={() => {}} />);
  await waitFor(() =>
    expect(screen.queryByText("OpenRouter key not set")).toBeNull());
});

test("a later successful read clears a settled refresh failure", async () => {
  // A manual refold fails; a later automatic one succeeds and bumps refreshKey.
  // The banner must not go on reporting a failure the panel recovered from.
  (api.refreshRollingSummary as any).mockRejectedValueOnce({ detail: "OpenRouter key not set" });
  const view = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: /refresh now/i }));
  await screen.findByText("OpenRouter key not set");

  (api.getRollingSummary as any).mockResolvedValueOnce({
    summary: "Recovered, and current.", at: 12, total: 12,
    stale: false, every: 10, due: false });
  view.rerender(
    <SceneInspector cid="c" sid="s" refreshKey={1} onSceneChanged={() => {}} />);

  await screen.findByText("Recovered, and current.");
  expect(screen.queryByText("OpenRouter key not set")).toBeNull();
});

test("moving the scene asks whether the summary is due", async () => {
  // A location move appends a transition post, which counts toward the
  // threshold like any other — and `onSceneChanged` only re-reads (#85).
  (api.listEntities as any).mockResolvedValue([{ id: "dock", name: "The Night Dock" }]);
  renderInspector();
  fireEvent.change(await screen.findByLabelText("Move to location"),
                   { target: { value: "dock" } });
  fireEvent.click(screen.getByRole("button", { name: "Move to" }));
  await waitFor(() => expect(api.setSceneLocation).toHaveBeenCalled());
  // Without `force` — the server still decides whether to spend a call — and
  // bounded by the transcript length the panel just read, so the fold cannot
  // swallow a post the transition did not include.
  await waitFor(() => expect(api.refreshRollingSummary).toHaveBeenCalledWith(
    "c", "s", false, 0));
});

test("the first date set asks for a refold under the scene's NEW id", async () => {
  // Setting a date for the first time renames the scene file, and this branch
  // returns early to let the sid prop change re-run the load effects. Those
  // effects only READ, and a read never evaluates the gate — while the write
  // itself changed the scene's date FACT, which is part of the fold's validity
  // key. So the ask belongs here, against the id the rename produced: the old
  // one no longer names a file (#85).
  (api.setSceneDatetime as any).mockResolvedValue(
    { ok: true, advanced: false, friendly: "4 July 2026", id: "001--2026-07-04--s" });
  render(<SceneInspector cid="c" sid="s" refreshKey={0}
                         onSceneChanged={() => {}} onSceneRenamed={() => {}} />);
  fireEvent.change(await screen.findByLabelText("Scene date year"), { target: { value: "2026" } });
  const monthSelect = await screen.findByLabelText("Scene date month");
  await waitFor(() => expect(monthSelect).not.toBeDisabled());
  fireEvent.change(monthSelect, { target: { value: "07" } });
  const daySelect = screen.getByLabelText("Scene date day");
  await waitFor(() => expect(daySelect).not.toBeDisabled());
  fireEvent.change(daySelect, { target: { value: "4" } });
  fireEvent.click(screen.getByRole("button", { name: /set date/i }));

  await waitFor(() => expect(api.getRollingSummary).toHaveBeenCalledWith(
    "c", "001--2026-07-04--s"));
  await waitFor(() => expect(api.refreshRollingSummary).toHaveBeenCalledWith(
    "c", "001--2026-07-04--s", false, expect.any(Number)));
});

test("an automatic refold that answers late cannot undo a newer one", async () => {
  // Two transitions in quick succession each ask. The first fold finishes on the
  // server and releases its claim, so the second is not coalesced and really
  // does refold — and if the FIRST one's response is the slower of the two it
  // lands second, passes the scene-key check, and overwrites the newer summary
  // and coverage with its own older ones. Nothing is scheduled behind it to put
  // that right (#85).
  // Two places that are not where the scene already is: the picker offers
  // somewhere to GO, so the current location is not among its options.
  (api.listEntities as any).mockResolvedValue([
    { id: "dock", name: "The Night Dock" }, { id: "gate", name: "The Salt Gate" }]);
  let landFirst: ((v: any) => void) | undefined;
  (api.refreshRollingSummary as any)
    .mockImplementationOnce(() => new Promise((resolve) => { landFirst = resolve; }))
    .mockResolvedValueOnce({ summary: "The newer fold.", at: 12, total: 12,
                             stale: false, every: 10, due: false, refreshed: true });
  render(<SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);

  const picker = await screen.findByLabelText("Move to location");
  fireEvent.change(picker, { target: { value: "dock" } });
  fireEvent.click(screen.getByRole("button", { name: "Move to" }));
  await waitFor(() => expect(api.refreshRollingSummary).toHaveBeenCalledTimes(1));
  fireEvent.change(picker, { target: { value: "gate" } });
  fireEvent.click(screen.getByRole("button", { name: "Move to" }));

  await screen.findByText("The newer fold.");
  // ...and only now does the FIRST ask answer, describing less of the scene.
  await act(async () => {
    landFirst!({ summary: "The older fold.", at: 10, total: 10, stale: false,
                 every: 10, due: false, refreshed: true });
  });

  expect(screen.getByText("The newer fold.")).toBeInTheDocument();
  expect(screen.queryByText("The older fold.")).toBeNull();
});

test("a successful automatic refold retires the warnings it just disproved", async () => {
  // The panel has two ways of saying "what you are reading may be behind", and a
  // fold the server reconciled makes both false. Leaving them up left an earlier
  // provider failure sitting beside prose the POST had just made current, with
  // nothing later scheduled to retire it (#85).
  (api.listEntities as any).mockResolvedValue([{ id: "dock", name: "The Night Dock" }]);
  (api.refreshRollingSummary as any).mockRejectedValueOnce(
    { detail: "OpenRouter key not set" });
  render(<SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: /refresh now/i }));
  await screen.findByText("OpenRouter key not set");

  (api.refreshRollingSummary as any).mockResolvedValueOnce({
    summary: "Mara reaches the salt gate.", at: 12, total: 12, stale: false,
    every: 10, due: false, refreshed: true });
  fireEvent.change(await screen.findByLabelText("Move to location"),
                   { target: { value: "dock" } });
  fireEvent.click(screen.getByRole("button", { name: "Move to" }));

  await screen.findByText("Mara reaches the salt gate.");
  expect(screen.queryByText("OpenRouter key not set")).toBeNull();
  expect(screen.queryByText(/latest read failed/i)).toBeNull();
});

// --- pins & excludes (#129) --------------------------------------------------

test("an empty rule set shows the hint, not a list", async () => {
  renderInspector();
  await screen.findByText(/Nothing pinned/);
  expect(api.getPins).toHaveBeenCalledWith("c", "s");
});

test("a cast row's pin toggle files a scene-scoped rule and reloads", async () => {
  const changed = vi.fn();
  renderInspector(changed);
  fireEvent.click(await screen.findByRole("button", { name: "Pin Seraphine in the prompt" }));
  await waitFor(() => expect(api.setPin).toHaveBeenCalledWith("c", {
    ref: "characters:seraphine", mode: "pin", scope: "scene", sid: "s" }));
  // The prompt moved, so the context panel has to be re-read.
  await waitFor(() => expect(changed).toHaveBeenCalled());
  expect(api.getPins).toHaveBeenCalledTimes(2);
});

test("the toggle reads as pressed while its rule stands, and lifts it on a second click", async () => {
  (api.getPins as any).mockResolvedValue({ pins: [pinRow()] });
  renderInspector();
  const pin = await screen.findByRole("button", { name: "Pin Seraphine in the prompt" });
  await waitFor(() => expect(pin.getAttribute("aria-pressed")).toBe("true"));

  fireEvent.click(pin);
  await waitFor(() => expect(api.removePin).toHaveBeenCalledWith("c", "characters:seraphine", "scene", "s"));
  expect(api.setPin).not.toHaveBeenCalled();
});

test("excluding someone already pinned replaces the rule rather than removing it", async () => {
  (api.getPins as any).mockResolvedValue({ pins: [pinRow()] });
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: "Exclude Seraphine from the prompt" }));
  await waitFor(() => expect(api.setPin).toHaveBeenCalledWith("c", {
    ref: "characters:seraphine", mode: "exclude", scope: "scene", sid: "s" }));
  expect(api.removePin).not.toHaveBeenCalled();
});

test("the panel lists each rule with its scope, countdown and a way to lift it", async () => {
  (api.getPins as any).mockResolvedValue({ pins: [
    pinRow({ remaining: 3, ttl_posts: 3 }),
    pinRow({ ref: "lore:tide-oath", kind: "lore", id: "tide-oath", name: "Tide oath",
             mode: "exclude", scope: "campaign", sid: "" }),
  ] });
  renderInspector();
  await screen.findByText(/3 posts left/);
  await screen.findByText(/lore · campaign/);
  expect(screen.getByText("excluded")).toBeTruthy();

  fireEvent.click(screen.getByRole("button", { name: "Lift the rule on Tide oath" }));
  await waitFor(() => expect(api.removePin).toHaveBeenCalledWith("c", "lore:tide-oath", "campaign", ""));
});

test("a rule whose target the campaign lost is shown as deleted rather than hidden", async () => {
  (api.getPins as any).mockResolvedValue({ pins: [
    pinRow({ ref: "lore:gone", kind: "lore", id: "gone", name: "gone", missing: true })] });
  renderInspector();
  await screen.findByText(/deleted/);
});

test("adding a rule from the picker sends the kind, mode, scope and post window", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "tide-oath", name: "Tide oath" }]);
  renderInspector();
  await screen.findByText(/Nothing pinned/);

  fireEvent.change(screen.getByLabelText("What to pin or exclude"), { target: { value: "lore" } });
  await waitFor(() => expect(api.listEntities).toHaveBeenCalledWith({ kind: "campaign", id: "c" }, "lore"));
  fireEvent.change(await screen.findByLabelText("Record to pin or exclude"),
                   { target: { value: "tide-oath" } });
  fireEvent.change(screen.getByLabelText("Pin or exclude"), { target: { value: "exclude" } });
  fireEvent.change(screen.getByLabelText("Posts to keep the rule for"), { target: { value: "4" } });
  fireEvent.click(screen.getByRole("button", { name: "+ Add rule" }));

  await waitFor(() => expect(api.setPin).toHaveBeenCalledWith("c", {
    ref: "lore:tide-oath", mode: "exclude", scope: "scene", sid: "s", ttl_posts: 4 }));
});

test("a campaign-wide rule hides the TTL box, since posts have no scene to run in", async () => {
  renderInspector();
  await screen.findByText(/Nothing pinned/);
  expect(screen.getByLabelText("Posts to keep the rule for")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Rule scope"), { target: { value: "campaign" } });
  expect(screen.queryByLabelText("Posts to keep the rule for")).toBeNull();
});

test("Add is disabled until a record is picked", async () => {
  renderInspector();
  await screen.findByText(/Nothing pinned/);
  expect((screen.getByRole("button", { name: "+ Add rule" }) as HTMLButtonElement).disabled).toBe(true);
});

test("a refused rule surfaces the server's reason", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "tide-oath", name: "Tide oath" }]);
  (api.setPin as any).mockRejectedValue({ detail: "a campaign-scoped rule cannot carry a post TTL" });
  renderInspector();
  fireEvent.change(await screen.findByLabelText("What to pin or exclude"), { target: { value: "lore" } });
  fireEvent.change(await screen.findByLabelText("Record to pin or exclude"),
                   { target: { value: "tide-oath" } });
  fireEvent.click(screen.getByRole("button", { name: "+ Add rule" }));
  await screen.findByText(/cannot carry a post TTL/);
});

test("a pinned section is marked in the context breakdown", async () => {
  (api.getSceneContext as any).mockResolvedValue({
    model: "m", total_tokens: 100, dropped_tokens: 0, budget_tokens: 0,
    sections: [{ label: "World info", text: "lore text", tokens: 100,
                 tier: "spotlight", dropped: false, trimmed: 0, pinned: true }],
  });
  renderInspector();
  await screen.findByText("pinned");
});

test("a campaign-wide rule shows on the row but is not toggled from it", async () => {
  // The row toggles only ever write scene scope, so leaving them live here made
  // a pressed button whose click changed nothing a reader could see.
  (api.getPins as any).mockResolvedValue({ pins: [pinRow({ scope: "campaign", sid: "" })] });
  renderInspector();
  const pin = await screen.findByRole("button", { name: "Pin Seraphine in the prompt" });
  await waitFor(() => expect(pin.getAttribute("aria-pressed")).toBe("true"));
  expect((pin as HTMLButtonElement).disabled).toBe(true);

  fireEvent.click(pin);
  expect(api.setPin).not.toHaveBeenCalled();
  expect(api.removePin).not.toHaveBeenCalled();
});

test("switching scenes does not leave the previous scene's rules driving the toggles", async () => {
  // The component survives a scene switch, so an unscoped rule list left the
  // new scene's row showing the old scene's pin — and a click on it would have
  // asked the server to lift a rule that scene never had.
  (api.getPins as any).mockResolvedValue({ pins: [pinRow()] });
  const { rerender } = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  await waitFor(() => expect(
    screen.getByRole("button", { name: "Pin Seraphine in the prompt" }).getAttribute("aria-pressed"),
  ).toBe("true"));

  // The next scene's read is still in flight when it repaints.
  (api.getPins as any).mockReturnValue(new Promise(() => {}));
  rerender(<SceneInspector cid="c" sid="s2" refreshKey={0} onSceneChanged={() => {}} />);
  expect(
    screen.getByRole("button", { name: "Pin Seraphine in the prompt" }).getAttribute("aria-pressed"),
  ).toBe("false");
});

test("the picker's options do not survive a campaign switch", async () => {
  // Same reason, one cache over: offering another campaign's lore here would
  // file a rule naming an entry this campaign has never had.
  (api.listEntities as any).mockResolvedValue([{ id: "tide-oath", name: "Tide oath" }]);
  const { rerender } = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  fireEvent.change(await screen.findByLabelText("What to pin or exclude"), { target: { value: "lore" } });
  // Scoped to the picker: `listEntities` also feeds the location list, so a
  // bare option query would find the Move-to select's copy of the same name.
  const picker = () => within(screen.getByLabelText("Record to pin or exclude") as HTMLElement);
  await waitFor(() => expect(picker().queryByRole("option", { name: "Tide oath" })).not.toBeNull());

  (api.listEntities as any).mockReturnValue(new Promise(() => {}));
  rerender(<SceneInspector cid="c2" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  await waitFor(() => expect(picker().queryByRole("option", { name: "Tide oath" })).toBeNull());
});
