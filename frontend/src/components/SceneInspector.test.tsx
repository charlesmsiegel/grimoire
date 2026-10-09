import { render, screen, fireEvent, waitFor, within, act } from "@testing-library/react";
import { configChanged, noticesChanged } from "../appEvents";
import { MemoryRouter } from "react-router-dom";
import { SceneInspector } from "./SceneInspector";
import { useHotkeys } from "../shortcuts/useHotkeys";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    api: {
      getCast: vi.fn(), getCampaign: vi.fn(), listCharacters: vi.fn(), listPCs: vi.fn(),
      listCampaignPCs: vi.fn(), getSceneLocation: vi.fn(), getSceneContext: vi.fn(),
      listScenePrompts: vi.fn(), getScenePrompt: vi.fn(), getScenePromptDiff: vi.fn(),
      // Resolves to "no weather" so the widget renders nothing: these suites
      // assert on the rest of the inspector, not the sky.
      getSceneWeather: vi.fn(() => Promise.resolve({ weather: null, location: null, native: null })),
      getCastDetail: vi.fn(), readEntity: vi.fn(), getChronicle: vi.fn(),
      getCalendarConfig: vi.fn(), setCalendarConfig: vi.fn(), getCalendarProviders: vi.fn(),
      getSceneDatetime: vi.fn(), setSceneDatetime: vi.fn(), getCalendarMonths: vi.fn(),
      // The pre-notice banner under the When section (#106).
      dismissNotices: vi.fn(),
      getCampaignClock: vi.fn(), previewAdvance: vi.fn(), advanceTime: vi.fn(),
      listAppearances: vi.fn(), listEntityImages: vi.fn(),
      listEntities: vi.fn(), setSceneLocation: vi.fn(), sceneBriefing: vi.fn(),
      getRollingSummary: vi.fn(), refreshRollingSummary: vi.fn(),
      getSceneBreak: vi.fn(), askSceneBreak: vi.fn(), dismissSceneBreak: vi.fn(),
      addToCast: vi.fn(), removeFromCast: vi.fn(),
      getSuggestions: vi.fn(), dismissSuggestion: vi.fn(),
      getPins: vi.fn(), setPin: vi.fn(), removePin: vi.fn(),
      // The Cost section (#153). Resolved to an empty ledger and no budget
      // so it renders its quiet state: these suites assert on the rest of
      // the inspector, not on the bill.
      getSceneUsage: vi.fn(), getCampaignBudget: vi.fn(), setCampaignBudget: vi.fn(),
      // Turn history's stored rewrites (regex output processing, spec 6.3).
      getSceneRewrites: vi.fn(), getRegex: vi.fn(), editMessage: vi.fn(),
      // The Author's notes section's count (play controls V).
      getAuthorsNotesNext: vi.fn(),
      // The Models section: the campaign's inference view and its write, and
      // what the shared pickers ask of a provider.
      getCampaignInference: vi.fn(), putCampaignInference: vi.fn(),
      readConnectionCapabilities: vi.fn(), previewControls: vi.fn(),
      actorImageUrl: (_sc: { id: string }, k: string, a: string, v: string, _n: string,
                      o?: { w?: number; v?: string | null }) =>
        `/img/${k}/${a}/${v}${o?.w ? `?w=${o.w}` : ""}${o?.v ? `${o?.w ? "&" : "?"}v=${o.v}` : ""}`,
      entityImageUrl: (_sc: unknown, _k: string, _e: string, _n: string, o?: { w?: number }) =>
        `/loc-img${o?.w ? `?w=${o.w}` : ""}`,
    },
  };
});
vi.mock("../api/models", () => ({ getModels: vi.fn() }));
vi.mock("./ResponseTargetsPicker", () => ({
  ResponseTargetsPicker: ({ scope, cid, sid }: any) => (
    <div data-testid="response-preset-picker" data-scope={scope} data-cid={cid} data-sid={sid} />
  ),
}));
vi.mock("./AuthorsNotesPanel", () => ({
  AuthorsNotesPanel: ({ cid, sid }: any) => (
    <div data-testid="authors-notes-panel" data-cid={cid} data-sid={sid} />
  ),
}));
import { ApiError, api, PRESET_CLEAR } from "../api/client";
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
  (api.getAuthorsNotesNext as any).mockResolvedValue({ turn: 1, count: 0, notes: [] });
  (api.getCast as any).mockResolvedValue([{ kind: "characters", id: "seraphine", role: "npc" }]);
  (api.getSceneUsage as any).mockResolvedValue({
    campaign: "c", scene: "s", since: "2026-08-01", until: "2026-08-14",
    generated_at: "2026-08-14T12:00:00Z",
    totals: { calls: 0, errors: 0, prompt_tokens: 0, completion_tokens: 0, total_tokens: 0,
              cache_read_tokens: 0, cache_write_tokens: 0, cost_usd: 0, estimated_usd: 0,
              priced_calls: 0, unpriced_calls: 0, duration_ms: 0 },
    by_task: [], turns: [], listed: 0, truncated: false });
  (api.getCampaignBudget as any).mockResolvedValue(
    { limit_usd: 0, period: "monthly", level: "off", warn_fraction: 0.8 });
  (api.addToCast as any).mockResolvedValue({ ok: true });
  (api.getSuggestions as any).mockResolvedValue([]);
  (api.dismissSuggestion as any).mockResolvedValue({ ok: true });
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
  (api.getScenePromptDiff as any).mockResolvedValue(null);
  (api.getCastDetail as any).mockResolvedValue({ kind: "characters", id: "seraphine", name: "Seraphine", version: "default", body: "keeper", source: "library" });
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
  (api.dismissNotices as any).mockResolvedValue({ ok: true, marked: [] });
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
  verdict: "" as const, reason: "", title: "", stale: false,
  posts: 0, score: 0, signals: [], every: 20, due: false,
};
const EMPTY_BRIEFING = {
  focus: [], plot: [], commitments: [], relationships: [], last_time: null };

function renderInspector(onSceneChanged: () => void = () => {}) {
  // Routed: a refold the model could not be reached for links to
  // Connections (#210), and a `Link` outside a router throws.
  render(<MemoryRouter><SceneInspector cid="c" sid="s" refreshKey={0}
                                       onSceneChanged={onSceneChanged} /></MemoryRouter>);
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

test("a forced answer is shown even with the automatic cadence switched off", async () => {
  // Ask now works when `every` is 0 — that is the whole point of a button that
  // says now — so an off-notice that outranked the verdict meant the player
  // pressed it, paid for a call, and watched the panel go on saying "Turned off".
  (api.getSceneBreak as any).mockResolvedValue({ ...NO_SCENE_BREAK, every: 0, posts: 400 });
  (api.askSceneBreak as any).mockResolvedValue({ ...BREAK_YES, every: 0, asked: true });
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /ask now/i }));
  await screen.findByText("The ledger changed hands and both sides walked away.");
});

test("a proposal about deleted posts is shown, but not as current", async () => {
  (api.getSceneBreak as any).mockResolvedValue({ ...BREAK_YES, stale: true });
  renderInspector();
  await screen.findByText("The ledger changed hands and both sides walked away.");
  await screen.findByText(/may no longer apply/i);
});

test("a read that failed is not a detector that found nothing", async () => {
  // "Nothing to suggest yet" is an assertion about the scene. Rendering it out
  // of a failed GET tells the player it looked and found nothing when it never
  // got an answer at all.
  (api.getSceneBreak as any).mockRejectedValue(new Error("offline"));
  renderInspector();
  await screen.findByText(/could not be read/i);
  expect(screen.queryByText(/Nothing to suggest yet/)).toBeNull();
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

test("a read issued before Ask now cannot blank the verdict it just landed", async () => {
  // The scene-select effect's GET can be in flight when the button commits, and
  // resolving second would install a pre-write answer over the one the player
  // just paid for — with nothing later scheduled to put it back.
  let releaseRead: ((v: unknown) => void) | undefined;
  (api.getSceneBreak as any).mockImplementationOnce(
    () => new Promise((resolve) => { releaseRead = resolve; }));
  (api.askSceneBreak as any).mockResolvedValue({ ...BREAK_YES, asked: true });
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /ask now/i }));
  await screen.findByText("The ledger changed hands and both sides walked away.");
  await act(async () => { releaseRead!(NO_SCENE_BREAK); });
  expect(screen.getByText("The ledger changed hands and both sides walked away."))
    .toBeInTheDocument();
});

test("both break buttons are held while a question is out on this scene", async () => {
  // What makes ordering the panel's own writes against each other unnecessary:
  // they cannot overlap on one record. A write arriving from somewhere else is
  // the server's to refuse, and `_break_commit` does.
  (api.getSceneBreak as any).mockResolvedValue(BREAK_YES);
  (api.askSceneBreak as any).mockReturnValueOnce(new Promise(() => {}));
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /ask now/i }));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: /asking/i })).toBeDisabled());
  expect(screen.getByRole("button", { name: /not here/i })).toBeDisabled();
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

test("suggested cast: mid-scene mentions can be seated or dismissed from the inspector", async () => {
  // The scan reads the cards of who is already cast, so it only pays off once
  // the scene has a cast — which is exactly where CastPanel has stopped
  // rendering (#7, #96).
  (api.getSuggestions as any).mockResolvedValue([
    { character: "mara", name: "Mara", mentioned_by: ["seraphine"] },
  ]);
  const onSceneChanged = vi.fn();
  renderInspector(onSceneChanged);
  await screen.findByText("Suggested cast");
  expect(screen.getByText("mentioned by Seraphine")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Add Mara to the scene" }));
  await waitFor(() => expect(api.addToCast).toHaveBeenCalledWith(
    "c", "s", { kind: "characters", id: "mara", role: "npc" }));
  await waitFor(() => expect(onSceneChanged).toHaveBeenCalled());
});

test("suggested cast: dismissing one drops it from this scene", async () => {
  (api.getSuggestions as any)
    .mockResolvedValueOnce([{ character: "mara", name: "Mara", mentioned_by: ["seraphine"] }])
    .mockResolvedValue([]);
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: "Dismiss Mara" }));
  await waitFor(() => expect(api.dismissSuggestion).toHaveBeenCalledWith("c", "s", "mara"));
  await waitFor(() => expect(screen.queryByText("Suggested cast")).toBeNull());
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
  // A 34px row: the face bucket, which a DPR-3 screen still fills sharply.
  expect(portrait.getAttribute("src")).toBe("/img/pcs/yara/v3?w=256");
});

test("a cast row's portrait carries the roster's avatar token", async () => {
  // Without it the row is served no-cache and revalidated on every open.
  (api.getCast as any).mockResolvedValue([
    { kind: "characters", id: "seraphine", role: "npc", name: "Seraphine" }]);
  (api.listAppearances as any).mockResolvedValue([
    { kind: "characters", id: "seraphine", version: "v2", role: "npc", scenes: ["s"],
      avatar_v: "1a-2b" }]);
  renderInspector();
  const portrait = await screen.findByAltText("Seraphine portrait");
  expect(portrait.getAttribute("src")).toBe("/img/characters/seraphine/v2?w=256&v=1a-2b");
});

test("location with a primary image renders a clickable thumbnail", async () => {
  (api.listEntityImages as any).mockResolvedValue([{ name: "avatar", ext: "png" }]);
  renderInspector();
  const thumb = await screen.findByAltText("The Crypt");
  expect(thumb.closest("button")).not.toBeNull();
  // The original: the panel draws this at the main column's full width, past
  // what the largest `?w=` bucket fills, so a downscale here is a blur.
  expect(thumb.getAttribute("src")).toBe("/loc-img");
  expect(thumb.getAttribute("srcset")).toBeNull();
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

test("world info lists each entry with its reason", async () => {
  (api.getSceneContext as any).mockResolvedValue({
    model: "m", total_tokens: 100, dropped_tokens: 0, budget_tokens: 0,
    sections: [{
      id: "world_info", label: "World info", text: "lore text", tokens: 100,
      tier: "spotlight", dropped: false, trimmed: 0,
      names: { "lore:realm-charter": "Realm charter" },
      entries: [
        { ref: "lore:saltmarch", name: "Saltmarch", kind: "lore", secrecy: "public", priority: 100,
          keep: false, level: 0, shed: false,
          reason: { type: "key", key: "Saltmarch", secondary: null, post: 41, seed: false } },
        { ref: "lore:tide-tables", name: "Tide tables", kind: "lore", secrecy: "public", priority: 100,
          keep: false, level: 1, shed: true,
          reason: { type: "recursion", via: "lore:realm-charter", key: "tide" } },
      ],
      held_back: [{ ref: "lore:winifred-rumour", name: "Winifred's rumour",
                    reason: { type: "cooldown", remaining: 3 } }],
    }],
  });
  renderInspector();
  fireEvent.click(await screen.findByText(/World info/));
  const kept = (await screen.findByText("Saltmarch")).closest("li")!;
  expect(kept.textContent).toContain("key 'Saltmarch' in post #41");
  expect(kept.className).not.toContain("shed");
  const shed = screen.getByText("Tide tables").closest("li")!;
  expect(shed.className).toContain("shed");
  expect(shed.textContent).toContain("pulled in by Realm charter");
  expect(shed.textContent).toContain("shed: budget");
  expect(screen.getByText("Held back")).toBeInTheDocument();
  expect(screen.getByText("Winifred's rumour").closest("li")!.textContent)
    .toContain("on cooldown — 3 posts");
});

test("a World info that sent nothing still lists what cooldown held back", async () => {
  (api.getSceneContext as any).mockResolvedValue({
    model: "m", total_tokens: 0, dropped_tokens: 0, budget_tokens: 0,
    sections: [{
      id: "world_info", label: "World info", text: "", tokens: 0,
      tier: "spotlight", dropped: false, trimmed: 0, names: {}, entries: [],
      held_back: [{ ref: "lore:winifred-rumour", name: "Winifred's rumour",
                    reason: { type: "cooldown", remaining: 2 } }],
    }],
  });
  renderInspector();
  fireEvent.click(await screen.findByText(/World info/));
  expect(screen.getByText("Winifred's rumour").closest("li")!.textContent)
    .toContain("on cooldown — 2 posts");
  expect(document.querySelector(".ctx-text")).toBeNull();
});

test("a capture without entries still renders", async () => {
  // A prompt recorded before rows carried entries: the section expands to its
  // text and draws no list and no "Held back" heading.
  renderInspector();
  fireEvent.click(await screen.findByText(/World info/));
  await screen.findByText("lore text");
  expect(document.querySelector(".ctx-entries")).toBeNull();
  expect(screen.queryByText("Held back")).not.toBeInTheDocument();
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
    { kind: "campaign", id: "c" }, expect.objectContaining({ confirmed: true })));
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

test("an imminent event is warned about once, and dismissing it marks the key", async () => {
  // The reader-facing half of #106. The model is told what is upcoming every
  // turn; this is the channel that says it once and then stops.
  (api.getSceneDatetime as any).mockResolvedValue({
    current: { native: "2026-07-04", friendly: "4 July 2026", weekday: "Saturday",
               secondary_friendly: null, holidays_today: [], upcoming: null, cast: [],
               notices: [{ key: "event:739437:the-envoy-arrives", kind: "event",
                           name: "The envoy arrives", in_days: 2,
                           friendly: "6 July 2026" }] },
    history: ["2026-07-04"] });
  renderInspector();
  await screen.findByText("The envoy arrives");
  fireEvent.click(screen.getByLabelText("Dismiss The envoy arrives"));
  await waitFor(() => expect(api.dismissNotices).toHaveBeenCalledWith(
    "c", ["event:739437:the-envoy-arrives"], "s"));
  await waitFor(() => expect(screen.queryByText("The envoy arrives")).toBeNull());
});

test("a notice dismissed elsewhere refreshes this panel's copy of the payload", async () => {
  // CampaignView mounts the inspector and the new-scene chooser as independent
  // siblings, so the chooser can be overlaid on a live inspector. A dismissal
  // in either left the other holding a payload from before it, showing the
  // reader the warning they just closed (#106). The api mutators emit on the
  // `notices` channel and both surfaces listen.
  (api.getSceneDatetime as any).mockResolvedValue({
    current: { native: "2026-07-04", friendly: "4 July 2026", weekday: "Saturday",
               secondary_friendly: null, holidays_today: [], upcoming: null, cast: [],
               notices: [] },
    history: ["2026-07-04"] });
  renderInspector();
  await screen.findByText(/4 July 2026/);
  const before = (api.getSceneDatetime as any).mock.calls.length;
  act(() => { noticesChanged(); });
  await waitFor(() =>
    expect((api.getSceneDatetime as any).mock.calls.length).toBeGreaterThan(before));
  // `fresh`, so the read cannot join a GET issued before the write it follows.
  const last = (api.getSceneDatetime as any).mock.calls.at(-1);
  expect(last[2]).toEqual({ fresh: true });
});

test("an older datetime read cannot replace a newer one", async () => {
  // `fresh` stops a new read JOINING an in-flight GET; it does not cancel the
  // older promise, whose `.then` is already attached. Landing late, that stale
  // payload would put the acknowledged notice back (#106).
  const withNotice = {
    current: { native: "2026-07-04", friendly: "4 July 2026", weekday: "Saturday",
               secondary_friendly: null, holidays_today: [], upcoming: null, cast: [],
               notices: [{ key: "event:739437:the-envoy-arrives", kind: "event",
                           name: "The envoy arrives", in_days: 2,
                           friendly: "6 July 2026" }] },
    history: ["2026-07-04"] };
  const withoutNotice = {
    ...withNotice,
    current: { ...withNotice.current, notices: [] } };

  // The first (stale) read is held open; the second resolves immediately.
  let landStale: (v: any) => void = () => {};
  (api.getSceneDatetime as any)
    .mockReturnValueOnce(new Promise((r: any) => { landStale = r; }))
    .mockResolvedValue(withoutNotice);
  renderInspector();
  await waitFor(() => expect(api.getSceneDatetime).toHaveBeenCalled());

  // A dismissal elsewhere triggers the fresh read, which settles first.
  act(() => { noticesChanged(); });
  await waitFor(() => expect(screen.getByText(/4 July 2026/)).toBeTruthy());

  // Now the stale one lands, still carrying the notice. It must be ignored.
  await act(async () => { landStale(withNotice); });
  expect(screen.queryByText("The envoy arrives")).toBeNull();
});

test("a scene change drops the previous scene's notices before the reload lands", async () => {
  // Until the reload settles, `when` holds the PREVIOUS scene's payload — and
  // the banner would render its notices beside the new ids, so a dismissal in
  // that window writes the old campaign's occurrence key into the new
  // campaign's ledger (#106).
  (api.getSceneDatetime as any).mockResolvedValue({
    current: { native: "2026-07-04", friendly: "4 July 2026", weekday: "Saturday",
               secondary_friendly: null, holidays_today: [], upcoming: null, cast: [],
               notices: [{ key: "event:739437:the-envoy-arrives", kind: "event",
                           name: "The envoy arrives", in_days: 2,
                           friendly: "6 July 2026" }] },
    history: ["2026-07-04"] });
  const { rerender } = render(
    <MemoryRouter><SceneInspector cid="c" sid="s" refreshKey={0}
                                  onSceneChanged={() => {}} /></MemoryRouter>);
  await screen.findByText("The envoy arrives");
  // The next scene's read never settles, so only the synchronous clear can help.
  (api.getSceneDatetime as any).mockReturnValue(new Promise(() => {}));
  rerender(
    <MemoryRouter><SceneInspector cid="c" sid="s2" refreshKey={0}
                                  onSceneChanged={() => {}} /></MemoryRouter>);
  expect(screen.queryByText("The envoy arrives")).toBeNull();
});

test("a read in flight when the scene changes cannot restore the old payload", async () => {
  // The invariant: a read issued for the previous scene never restores its
  // payload under the new ids (#106).
  //
  // Honest about what this does and does not pin. TWO things enforce it — the
  // reset advances the generation, and so does the reload that follows it — so
  // this passes with either one alone and cannot isolate the first. The window
  // the reset closes is between the render-time clear and the effect flush,
  // which a test cannot interleave. The bump is kept because it is free and
  // correct, not because this proves it.
  const withNotice = {
    current: { native: "2026-07-04", friendly: "4 July 2026", weekday: "Saturday",
               secondary_friendly: null, holidays_today: [], upcoming: null, cast: [],
               notices: [{ key: "event:739437:the-envoy-arrives", kind: "event",
                           name: "The envoy arrives", in_days: 2,
                           friendly: "6 July 2026" }] },
    history: ["2026-07-04"] };
  let landOld: (v: any) => void = () => {};
  (api.getSceneDatetime as any).mockReturnValueOnce(
    new Promise((r: any) => { landOld = r; }));
  const { rerender } = render(
    <MemoryRouter><SceneInspector cid="c" sid="s" refreshKey={0}
                                  onSceneChanged={() => {}} /></MemoryRouter>);
  await waitFor(() => expect(api.getSceneDatetime).toHaveBeenCalled());
  // The next scene has nothing imminent, and its read settles first.
  (api.getSceneDatetime as any).mockResolvedValue({
    current: { ...withNotice.current, notices: [] }, history: ["2026-07-04"] });
  rerender(
    <MemoryRouter><SceneInspector cid="c" sid="s2" refreshKey={0}
                                  onSceneChanged={() => {}} /></MemoryRouter>);
  await screen.findByText(/4 July 2026/);
  await act(async () => { landOld(withNotice); });
  expect(screen.queryByText("The envoy arrives")).toBeNull();
});

test("a scene with nothing imminent shows no warning at all", async () => {
  (api.getSceneDatetime as any).mockResolvedValue({
    current: { native: "2026-07-04", friendly: "4 July 2026", weekday: "Saturday",
               secondary_friendly: null, holidays_today: [], upcoming: null, cast: [],
               notices: [] },
    history: ["2026-07-04"] });
  renderInspector();
  await screen.findByText(/4 July 2026/);
  expect(screen.queryByLabelText("Coming up")).toBeNull();
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

// ---- the Models section: this campaign's role and route overrides ----
const RESOLVED = (provider: string, provider_name: string, model: string,
                  scope: "campaign" | "global" = "global", preset = "", preset_name = "") =>
  ({ provider, provider_name, model, preset, preset_name, via: "role" as const, scope });
const NONE = { provider: "", model: "", preset: "" };

/** A role this campaign leaves to the library: it inherits Saltmarch's model. */
function inheritedRole(model: string) {
  return { stored: NONE, fallback: NONE, problem: null, fallback_missing: [],
           fallback_problem: null, rate: null,
           resolves: RESOLVED("saltmarch", "Saltmarch Router", model),
           inherits: RESOLVED("saltmarch", "Saltmarch Router", model) };
}

function campaignInference(over: Partial<any> = {}) {
  return {
    format: "2", newer: false, migration: { state: "done", reason: "", skipped: [] },
    roles: { primary: inheritedRole("vendor/opus"), fast: inheritedRole("vendor/haiku"),
             decision: inheritedRole("vendor/haiku") },
    routes: [{ key: "scene", label: "Scene turns", hint: "Story turns.", tasks: ["chat"],
               operation: "generate", default_role: "primary", requires: [],
               campaign_scoped: true, use: "", pin: NONE, preset: "", problem: null,
               fallback_missing: [], fallback_problem: null, rate: null,
               role: "primary",
               resolves: RESOLVED("saltmarch", "Saltmarch Router", "vendor/opus"),
               inherits: RESOLVED("saltmarch", "Saltmarch Router", "vendor/opus") }],
    providers: [{ id: "saltmarch", name: "Saltmarch Router", kind: "openrouter",
                  preset: "openrouter", usable: true, problem: null },
                { id: "realm", name: "Realm Local", kind: "openai_compatible",
                  preset: "custom", usable: true, problem: null }],
    presets: [{ id: "terse", name: "Terse" }],
    preset_clear: "⁣none",
    ...over,
  };
}

function capabilities(need: string, fits: string[]) {
  return {
    provider_preset: { id: "custom", label: "Custom", kind: "openai_compatible", base_url: "",
                       url_locked: false, billing: "metered", reports_price: false,
                       always: [], possible: [], never: [] },
    need, reason: null, hidden: [],
    groups: { fits: fits.map((id) => ({ id, name: id, context: null, prompt: null,
                                        completion: null, reason: "listed",
                                        capabilities: {} })),
              unverified: [] },
  };
}

/** The inspector with its Models section opened, and that section to query. */
async function openModels() {
  renderInspector();
  const header = await screen.findByRole("button", { name: /^models$/i });
  fireEvent.click(header);
  const section = within(header.closest(".side-section") as HTMLElement);
  // The view has landed: its rows are drawn.
  await section.findByRole("button", { name: /^Primary/ });
  return section;
}

describe("the Models section", () => {
  beforeEach(() => {
    (api.getCampaignInference as any).mockResolvedValue(campaignInference());
    (api.readConnectionCapabilities as any).mockImplementation(
      (_provider: string, need: string) => Promise.resolve(capabilities(need, ["realm/small"])));
    (api.previewControls as any).mockResolvedValue({ controls: {} });
  });

  test("the inspector's Models section overrides a role for this campaign", async () => {
    const saved = campaignInference({ roles: {
      ...campaignInference().roles,
      fast: { stored: { provider: "realm", model: "realm/small", preset: "terse" },
              fallback: NONE, problem: null, fallback_missing: [], fallback_problem: null, rate: null,
              resolves: RESOLVED("realm", "Realm Local", "realm/small", "campaign", "terse",
                                 "Terse"),
              inherits: RESOLVED("saltmarch", "Saltmarch Router", "vendor/haiku") } } });
    (api.putCampaignInference as any).mockResolvedValue(saved);
    const section = await openModels();
    expect(api.getCampaignInference).toHaveBeenCalledWith("c");

    // The label is the server's `inherits`, never worked out here.
    const fast = await section.findByRole("button", { name: /^Fast/ });
    expect(fast).toHaveTextContent(
      "Inherit (resolves to Saltmarch Router ▸ vendor/haiku ▸ no preset)");
    fireEvent.click(fast);
    fireEvent.click(await section.findByRole("button", { name: "Edit" }));

    fireEvent.change(section.getByRole("combobox", { name: "Fast for this campaign" }),
                     { target: { value: "own" } });
    fireEvent.change(section.getByRole("combobox", { name: "Provider" }),
                     { target: { value: "realm" } });
    fireEvent.click(await section.findByRole("radio", { name: "realm/small" }));
    fireEvent.change(section.getByRole("combobox", { name: "Preset" }),
                     { target: { value: "terse" } });
    fireEvent.click(section.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(api.putCampaignInference).toHaveBeenCalledWith("c", {
      roles: { fast: { selection: { provider: "realm", model: "realm/small", preset: "terse" } } },
    }));
    // Back to the read-only view of what it now runs on.
    expect(await section.findByText("Runs on Realm Local ▸ realm/small ▸ Terse"))
      .toBeInTheDocument();
    expect(section.queryByRole("combobox", { name: "Provider" })).not.toBeInTheDocument();
  });

  test("a campaign role or fallback with a provider and no model is never saved", async () => {
    const section = await openModels();
    fireEvent.click(await section.findByRole("button", { name: /^Fast/ }));
    fireEvent.click(await section.findByRole("button", { name: "Edit" }));
    fireEvent.change(section.getByRole("combobox", { name: "Fast for this campaign" }),
                     { target: { value: "own" } });
    fireEvent.change(section.getByRole("combobox", { name: "Provider" }),
                     { target: { value: "realm" } });
    expect(section.getByText("Choose a model for this provider to save.")).toBeInTheDocument();
    expect(section.getByRole("button", { name: "Save" })).toBeDisabled();
    fireEvent.click(await section.findByRole("radio", { name: "realm/small" }));
    expect(section.getByRole("button", { name: "Save" })).toBeEnabled();

    fireEvent.click(section.getByRole("button", { name: "Fallback" }));
    const fallback = within(await section.findByRole("group", { name: "Fast fallback" }));
    fireEvent.change(fallback.getByRole("combobox", { name: "Provider" }),
                     { target: { value: "realm" } });
    expect(section.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(api.putCampaignInference).not.toHaveBeenCalled();
  });

  test("a fallback preset left without a provider says it is not used", async () => {
    // Saved, such a fallback is sent empty; the form must not go on showing
    // the preset as though it were kept (/models says the same of its draft).
    const section = await openModels();
    fireEvent.click(await section.findByRole("button", { name: /^Fast/ }));
    fireEvent.click(await section.findByRole("button", { name: "Edit" }));
    fireEvent.click(section.getByRole("button", { name: "Fallback" }));
    const fallback = within(await section.findByRole("group", { name: "Fast fallback" }));
    fireEvent.change(fallback.getByRole("combobox", { name: "Provider" }),
                     { target: { value: "realm" } });
    fireEvent.click(await fallback.findByRole("radio", { name: "realm/small" }));
    fireEvent.change(fallback.getByRole("combobox", { name: "Fallback preset" }),
                     { target: { value: "terse" } });
    expect(fallback.queryByText(/preset with no provider is not used/)).not.toBeInTheDocument();

    fireEvent.change(fallback.getByRole("combobox", { name: "Provider" }),
                     { target: { value: "" } });
    expect(fallback.getByText(/preset with no provider is not used/)).toBeInTheDocument();
  });

  test("an arrow on a model radio is the radio's, never the scene's variant swipe", async () => {
    // The play view binds bare ← and → to the last reply's variant swipe, and
    // the Inspector is a panel beside it, not an overlay: nothing holds those
    // bindings off while a Models form is open.
    const swipe = vi.fn();
    function Swipe() {
      useHotkeys([{ keys: "arrowleft", run: swipe }, { keys: "arrowright", run: swipe }]);
      return null;
    }
    render(<Swipe />);
    const section = await openModels();
    fireEvent.click(section.getByRole("button", { name: /^Primary/ }));
    fireEvent.click(await section.findByRole("button", { name: "Edit" }));
    fireEvent.change(section.getByRole("combobox", { name: "Primary for this campaign" }),
                     { target: { value: "own" } });
    fireEvent.change(section.getByRole("combobox", { name: "Provider" }),
                     { target: { value: "realm" } });
    const radio = await section.findByRole("radio", { name: "realm/small" });
    radio.focus();

    const left = fireEvent.keyDown(radio, { key: "ArrowLeft" });
    const right = fireEvent.keyDown(radio, { key: "ArrowRight" });

    expect(swipe).not.toHaveBeenCalled();
    // Stopped, never prevented: the browser still moves the selection.
    expect(left).toBe(true);
    expect(right).toBe(true);
    // Away from the radio, the swipe is still the scene's.
    fireEvent.keyDown(document.body, { key: "ArrowLeft" });
    expect(swipe).toHaveBeenCalledTimes(1);
  });

  test("it never offers the embedding role", async () => {
    const section = await openModels();
    await section.findByRole("button", { name: /^Primary/ });
    for (const role of ["Primary", "Fast", "Decision"]) {
      expect(section.getByRole("button", { name: new RegExp(`^${role}`) })).toBeInTheDocument();
    }
    expect(section.queryByText(/embedding/i)).not.toBeInTheDocument();
    // Nor among the routes.
    fireEvent.click(section.getByRole("button", { name: "Routes" }));
    expect(await section.findByRole("button", { name: /^Scene turns/ })).toBeInTheDocument();
    expect(section.queryByText(/embedding/i)).not.toBeInTheDocument();
  });

  test("rows are read-only until Edit", async () => {
    const section = await openModels();
    fireEvent.click(await section.findByRole("button", { name: "Routes" }));
    fireEvent.click(await section.findByRole("button", { name: /^Scene turns/ }));

    // The record opens in its view: what it runs on, and nothing to change it with.
    expect(await section.findByText(
      "Runs on Saltmarch Router ▸ vendor/opus ▸ no preset",
      { selector: "p" })).toBeInTheDocument();
    expect(section.queryByRole("combobox")).not.toBeInTheDocument();

    fireEvent.click(section.getByRole("button", { name: "Edit" }));
    expect(await section.findByRole("combobox", { name: "Role (default: Primary)" }))
      .toBeInTheDocument();
    expect(section.getByRole("combobox", { name: "Preset override" })).toBeInTheDocument();

    // Cancel goes back to the view, having sent nothing.
    fireEvent.click(section.getByRole("button", { name: "Cancel" }));
    expect(section.queryByRole("combobox")).not.toBeInTheDocument();
    expect(api.putCampaignInference).not.toHaveBeenCalled();
  });

  test("the section is shut until asked for, and asks nothing until then", async () => {
    renderInspector();
    const header = await screen.findByRole("button", { name: /^models$/i });
    expect(header).toHaveAttribute("aria-expanded", "false");
    expect(api.getCampaignInference).not.toHaveBeenCalled();
  });

  test("Edit waits for the new layout, and the upgrade banner says why", async () => {
    (api.getCampaignInference as any).mockResolvedValue(campaignInference({
      format: "1", migration: { state: "pending", reason: "", skipped: [] } }));
    const section = await openModels();
    expect(await section.findByText(/Upgrade pending/)).toBeInTheDocument();
    fireEvent.click(section.getByRole("button", { name: /^Primary/ }));
    expect(await section.findByRole("button", { name: "Edit" })).toBeDisabled();
  });

  test("Edit opens once the upgrade lands, with the section left open", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      (api.getCampaignInference as any).mockResolvedValue(campaignInference({
        format: "1", migration: { state: "running", reason: "", skipped: [] } }));
      const section = await openModels();
      fireEvent.click(section.getByRole("button", { name: /^Primary/ }));
      expect(await section.findByRole("button", { name: "Edit" })).toBeDisabled();

      (api.getCampaignInference as any).mockResolvedValue(campaignInference());
      await act(async () => { await vi.advanceTimersByTimeAsync(1000); });

      expect(await section.findByRole("button", { name: "Edit" })).toBeEnabled();
      expect(section.queryByText(/Upgrade pending/)).not.toBeInTheDocument();
      const reads = (api.getCampaignInference as any).mock.calls.length;
      await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
      expect((api.getCampaignInference as any).mock.calls.length).toBe(reads);
    } finally {
      vi.useRealTimers();
    }
  });

  test("a campaign the move has not reached says so quietly at the new layout", async () => {
    (api.getCampaignInference as any).mockResolvedValue(campaignInference({
      migration: { state: "pending", reason: "",
                   skipped: ["campaign c: busy; finished on the next start"] } }));
    const section = await openModels();
    expect(await section.findByText(
      /still in the old layout \(busy; finished on the next start\)/)).toBeInTheDocument();
    // Not a block: the server moves it inside the write.
    expect(section.queryByText(/Upgrade pending/)).not.toBeInTheDocument();
    fireEvent.click(section.getByRole("button", { name: /^Primary/ }));
    expect(await section.findByRole("button", { name: "Edit" })).toBeEnabled();
  });

  test("another campaign left behind is the library's upgrade line, worded as Settings words it", async () => {
    const other = "campaign saltmarch: busy; finished on the next start";
    (api.getCampaignInference as any).mockResolvedValue(campaignInference({
      migration: { state: "pending", reason: "", skipped: [other] } }));
    const section = await openModels();
    expect(await section.findByText(`The upgrade left 1 thing for later (${other}).`))
      .toBeInTheDocument();
    expect(section.queryByText(/still in the old layout/)).not.toBeInTheDocument();
  });

  test("a fallback the server reports as dropped is said on its role and its route", async () => {
    // `fallback_missing` is the server's: the seam never sends a fallback
    // known unable to do what it is for, and refuses nothing over it.
    const base = campaignInference();
    (api.getCampaignInference as any).mockResolvedValue(campaignInference({
      roles: { ...base.roles,
               primary: { ...inheritedRole("vendor/opus"),
                          fallback: { provider: "realm", model: "realm/embed", preset: "" },
                          fallback_missing: ["generate"] },
               fast: { ...inheritedRole("vendor/haiku"), fallback_missing: ["generate"] } },
      routes: [{ ...base.routes[0], fallback_missing: ["generate"] }] }));
    const section = await openModels();

    fireEvent.click(section.getByRole("button", { name: /^Primary/ }));
    expect(await section.findByText(
      "The fallback, Realm Local ▸ realm/embed, is known not to fit Primary "
      + "(it cannot generate text), so it is never sent.")).toBeInTheDocument();

    // A fallback this campaign does not choose itself is still named as one.
    fireEvent.click(section.getByRole("button", { name: /^Fast/ }));
    expect(await section.findByText(
      "The fallback is known not to fit Fast (it cannot generate text), so it is never sent."))
      .toBeInTheDocument();

    fireEvent.click(section.getByRole("button", { name: "Routes" }));
    fireEvent.click(await section.findByRole("button", { name: /^Scene turns/ }));
    expect(await section.findByText(
      "The fallback is known not to fit Scene turns (it cannot generate text), "
      + "so it is never sent.")).toBeInTheDocument();
  });

  test("a fallback that cannot send says why on its role and its route", async () => {
    // `fallback_problem` is the server's: a fallback with no key is left out
    // of the chain as silently as one known unable to do the work.
    const base = campaignInference();
    (api.getCampaignInference as any).mockResolvedValue(campaignInference({
      roles: { ...base.roles,
               primary: { ...inheritedRole("vendor/opus"),
                          fallback: { provider: "realm", model: "realm/small", preset: "" },
                          fallback_problem: "Endpoint base URL not set" } },
      routes: [{ ...base.routes[0], fallback_problem: "Endpoint base URL not set" }] }));
    const section = await openModels();

    fireEvent.click(section.getByRole("button", { name: /^Primary/ }));
    expect(await section.findByText(
      "The fallback, Realm Local ▸ realm/small, cannot be sent (Endpoint base URL not set), "
      + "so it is never tried.")).toBeInTheDocument();

    fireEvent.click(section.getByRole("button", { name: "Routes" }));
    fireEvent.click(await section.findByRole("button", { name: /^Scene turns/ }));
    expect(await section.findByText(
      "The fallback cannot be sent (Endpoint base URL not set), so it is never tried."))
      .toBeInTheDocument();
  });

  test("a role whose fallback is sent says nothing of a dropped one", async () => {
    const section = await openModels();
    fireEvent.click(section.getByRole("button", { name: /^Primary/ }));
    await section.findByRole("button", { name: "Edit" });
    expect(section.queryByText(/so it is never sent/)).not.toBeInTheDocument();
  });

  test("a campaign a newer build marked refuses the save, and the section says so", async () => {
    (api.putCampaignInference as any).mockRejectedValue(new ApiError(409,
      "A newer version of grimoire has changed this campaign's model settings. "
      + "Update grimoire to change them here.", "newer_format"));
    const section = await openModels();
    fireEvent.click(await section.findByRole("button", { name: /^Primary/ }));
    fireEvent.click(await section.findByRole("button", { name: "Edit" }));
    fireEvent.click(section.getByRole("button", { name: "Save" }));

    expect(await section.findByText(/newer version of grimoire has changed this campaign/))
      .toBeInTheDocument();
    expect(section.getByRole("button", { name: "Edit" })).toBeDisabled();
  });

  test("a save refused as not migrated shows the upgrade's state", async () => {
    const failed = { state: "failed", reason: "the safety backup failed: disk full", skipped: [] };
    (api.putCampaignInference as any).mockRejectedValue(new ApiError(409,
      "Model settings are being moved to the new layout. Try again once that has finished.",
      "not_migrated", { kind: "not_migrated", status: failed }));
    // Read again after the refusal, the store says the same: not moved yet.
    (api.getCampaignInference as any).mockResolvedValueOnce(campaignInference())
      .mockResolvedValue(campaignInference({ format: "1", migration: failed }));
    const section = await openModels();
    fireEvent.click(await section.findByRole("button", { name: /^Primary/ }));
    fireEvent.click(await section.findByRole("button", { name: "Edit" }));
    fireEvent.click(section.getByRole("button", { name: "Save" }));

    expect(await section.findByText(/Upgrade pending: the safety backup failed: disk full/))
      .toBeInTheDocument();
    fireEvent.click(section.getByRole("button", { name: "Cancel" }));
    expect(await section.findByRole("button", { name: "Edit" })).toBeDisabled();
  });

  test("a not-migrated refusal reads the view again, and a finished move frees Edit", async () => {
    (api.putCampaignInference as any).mockRejectedValue(new ApiError(409,
      "Model settings are being moved to the new layout. Try again once that has finished.",
      "not_migrated",
      { kind: "not_migrated", status: { state: "done", reason: "", skipped: [] } }));
    const section = await openModels();
    fireEvent.click(await section.findByRole("button", { name: /^Primary/ }));
    fireEvent.click(await section.findByRole("button", { name: "Edit" }));
    fireEvent.click(section.getByRole("button", { name: "Save" }));

    // The move finished between the read and the write: the view, read again,
    // is ready, so nothing is left blocked with no banner to say why.
    await waitFor(() => expect(api.getCampaignInference).toHaveBeenCalledTimes(2));
    fireEvent.click(await section.findByRole("button", { name: "Cancel" }));
    expect(await section.findByRole("button", { name: "Edit" })).toBeEnabled();
  });

  test("a route that inherits its role but overrides its preset shows what it runs on",
       async () => {
    const base = campaignInference();
    (api.getCampaignInference as any).mockResolvedValue(campaignInference({ routes: [{
      ...base.routes[0], use: "", preset: "terse",
      resolves: RESOLVED("saltmarch", "Saltmarch Router", "vendor/opus", "campaign", "terse",
                         "Terse"),
      // `inherits` silences every campaign key of the route, the preset too.
      inherits: RESOLVED("saltmarch", "Saltmarch Router", "vendor/opus") }] }));
    const section = await openModels();
    fireEvent.click(section.getByRole("button", { name: "Routes" }));

    const row = await section.findByRole("button", { name: /^Scene turns/ });
    expect(row).toHaveTextContent("Saltmarch Router ▸ vendor/opus ▸ Terse");
    expect(row).not.toHaveTextContent(/no preset/);
    fireEvent.click(row);
    const detail = within(await section.findByRole("region", { name: "Scene turns" }));
    expect(detail.getByText("Runs on Saltmarch Router ▸ vendor/opus ▸ Terse",
                            { selector: "p" })).toBeInTheDocument();
    // The inherit wording is what inheriting the ROLE gives, never the headline.
    expect(detail.getByText("Inherit (resolves to Saltmarch Router ▸ vendor/opus)"))
      .toBeInTheDocument();
    expect(detail.queryByText(/no preset/)).not.toBeInTheDocument();
    expect(detail.getByText("Terse", { selector: "span" })).toBeInTheDocument();
  });

  test("a model-settings change elsewhere re-reads the section's Inherit labels", async () => {
    const section = await openModels();
    const before = await section.findByRole("button", { name: /^Fast/ });
    expect(before).toHaveTextContent("vendor/haiku");

    (api.getCampaignInference as any).mockResolvedValue(campaignInference({ roles: {
      ...campaignInference().roles, fast: inheritedRole("vendor/sonnet") } }));
    act(() => { configChanged(); });

    expect(await section.findByText(
      "Inherit (resolves to Saltmarch Router ▸ vendor/sonnet ▸ no preset)")).toBeInTheDocument();
  });

  // ---- what a save sends: the named risks of a whole-selection replace ----
  /** A role this campaign chose for itself, with a fallback the library set. */
  function ownedFast() {
    return campaignInference({ roles: { ...campaignInference().roles,
      fast: { stored: { provider: "realm", model: "realm/small", preset: "terse" },
              fallback: { provider: "saltmarch", model: "vendor/haiku", preset: "" },
              problem: null, fallback_missing: [], rate: null,
              resolves: RESOLVED("realm", "Realm Local", "realm/small", "campaign", "terse",
                                 "Terse"),
              inherits: RESOLVED("saltmarch", "Saltmarch Router", "vendor/haiku") } } });
  }

  async function editRow(section: ReturnType<typeof within>, name: RegExp) {
    fireEvent.click(await section.findByRole("button", { name }));
    fireEvent.click(await section.findByRole("button", { name: "Edit" }));
    // Settled: a stored choice's picker and readout read on opening.
    await section.findByRole("button", { name: "Save" });
  }

  test("Inherit sends the empty selection", async () => {
    (api.getCampaignInference as any).mockResolvedValue(ownedFast());
    (api.putCampaignInference as any).mockResolvedValue(campaignInference());
    const section = await openModels();
    await editRow(section, /^Fast/);
    fireEvent.change(section.getByRole("combobox", { name: "Fast for this campaign" }),
                     { target: { value: "" } });
    fireEvent.click(section.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.putCampaignInference).toHaveBeenCalledWith("c", {
      roles: { fast: { selection: { provider: "", model: "", preset: "" } } },
    }));
  });

  test("a model edit keeps the stored preset and never names the fallback", async () => {
    (api.readConnectionCapabilities as any).mockImplementation(
      (_provider: string, need: string) =>
        Promise.resolve(capabilities(need, ["realm/small", "realm/large"])));
    (api.getCampaignInference as any).mockResolvedValue(ownedFast());
    (api.putCampaignInference as any).mockResolvedValue(ownedFast());
    const section = await openModels();
    await editRow(section, /^Fast/);
    // The role's own picker: the stored fallback opens with the form, and has one too.
    const choice = within(section.getByRole("group", { name: "Fast" }));
    fireEvent.click(await choice.findByRole("radio", { name: "realm/large" }));
    fireEvent.click(section.getByRole("button", { name: "Save" }));
    // Exactly this body: no `fallback` key, so the library's stays as it is.
    await waitFor(() => expect(api.putCampaignInference).toHaveBeenCalledWith("c", {
      roles: { fast: { selection: { provider: "realm", model: "realm/large", preset: "terse" } } },
    }));
  });

  test("a campaign sets its own fallback for a role, and only that is sent", async () => {
    (api.putCampaignInference as any).mockResolvedValue(campaignInference());
    const section = await openModels();
    await editRow(section, /^Primary/);
    // Left to inherit, the role can still have a fallback of this campaign's own.
    fireEvent.click(section.getByRole("button", { name: "Fallback" }));
    const fallback = within(await section.findByRole("group", { name: "Primary fallback" }));
    expect(fallback.getByRole("combobox", { name: "Fallback preset" })).toBeDisabled();
    fireEvent.change(fallback.getByRole("combobox", { name: "Provider" }),
                     { target: { value: "realm" } });
    fireEvent.click(await fallback.findByRole("radio", { name: "realm/small" }));
    fireEvent.click(section.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.putCampaignInference).toHaveBeenCalledWith("c", {
      roles: { primary: { selection: { provider: "", model: "", preset: "" },
                          fallback: { provider: "realm", model: "realm/small", preset: "" } } },
    }));
  });

  test("clearing a campaign's fallback sends it empty, back to the library's", async () => {
    (api.getCampaignInference as any).mockResolvedValue(ownedFast());
    (api.putCampaignInference as any).mockResolvedValue(campaignInference());
    const section = await openModels();
    await editRow(section, /^Fast/);
    // A stored fallback opens with the form.
    const fallback = within(await section.findByRole("group", { name: "Fast fallback" }));
    fireEvent.change(fallback.getByRole("combobox", { name: "Provider" }),
                     { target: { value: "" } });
    fireEvent.click(section.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.putCampaignInference).toHaveBeenCalledWith("c", {
      roles: { fast: { selection: { provider: "realm", model: "realm/small", preset: "terse" },
                       fallback: { provider: "", model: "", preset: "" } } },
    }));
  });

  /** The scene route, pinned to a model of this campaign's choosing. */
  function pinnedScene() {
    const base = campaignInference();
    return campaignInference({ routes: [{
      ...base.routes[0], use: "model",
      pin: { provider: "realm", model: "realm/small", preset: "" },
      resolves: RESOLVED("realm", "Realm Local", "realm/small", "campaign") }] });
  }

  async function editScene(section: ReturnType<typeof within>) {
    fireEvent.click(section.getByRole("button", { name: "Routes" }));
    await editRow(section, /^Scene turns/);
  }

  test("a route switched to a role leaves its pin out of the save", async () => {
    (api.getCampaignInference as any).mockResolvedValue(pinnedScene());
    (api.putCampaignInference as any).mockResolvedValue(campaignInference());
    const section = await openModels();
    await editScene(section);
    fireEvent.change(section.getByRole("combobox", { name: "Role (default: Primary)" }),
                     { target: { value: "fast" } });
    fireEvent.click(section.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.putCampaignInference).toHaveBeenCalledWith("c", {
      routes: { scene: { use: "fast", preset: "" } },
    }));
  });

  test("a route pinned to a specific model sends the pin", async () => {
    (api.putCampaignInference as any).mockResolvedValue(pinnedScene());
    const section = await openModels();
    await editScene(section);
    fireEvent.change(section.getByRole("combobox", { name: "Role (default: Primary)" }),
                     { target: { value: "model" } });
    fireEvent.change(section.getByRole("combobox", { name: "Provider" }),
                     { target: { value: "realm" } });
    fireEvent.click(await section.findByRole("radio", { name: "realm/small" }));
    fireEvent.click(section.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.putCampaignInference).toHaveBeenCalledWith("c", {
      routes: { scene: { use: "model",
                         pin: { provider: "realm", model: "realm/small", preset: "" },
                         preset: "" } },
    }));
  });

  test("a route's no-preset override is sent as the clear marker", async () => {
    (api.putCampaignInference as any).mockResolvedValue(campaignInference());
    const section = await openModels();
    await editScene(section);
    fireEvent.change(section.getByRole("combobox", { name: "Preset override" }),
                     { target: { value: PRESET_CLEAR } });
    fireEvent.click(section.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.putCampaignInference).toHaveBeenCalledWith("c", {
      routes: { scene: { use: "", preset: PRESET_CLEAR } },
    }));
  });
});

test("the Author's notes section counts the notes applying next turn", async () => {
  (api.getAuthorsNotesNext as any).mockResolvedValue({
    turn: 3, count: 2,
    notes: [{ level: "campaign", depth: 4, every: 1, applies: true },
            { level: "character", depth: 0, every: 1, applies: true,
              ref: "characters:seraphine", name: "Seraphine" }],
  });
  renderInspector();
  expect(await screen.findByLabelText("Notes applying next turn")).toHaveTextContent("2");
  const header = screen.getByRole("button", { name: /author's notes/i });
  expect(header).toHaveAttribute("aria-expanded", "false");
  fireEvent.click(header);
  const panel = await screen.findByTestId("authors-notes-panel");
  expect(panel).toHaveAttribute("data-cid", "c");
  expect(panel).toHaveAttribute("data-sid", "s");
});

test("no notes applying next turn draws no count", async () => {
  renderInspector();
  await screen.findByRole("button", { name: /author's notes/i });
  expect(screen.queryByLabelText("Notes applying next turn")).not.toBeInTheDocument();
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

test("the Cost section is shut until asked for, then mounts the breakdown", async () => {
  // Sibling to Context (#153) and collapsed for the same reason the clock is:
  // cost is a question a reader comes to ask, and a shut section must not spend
  // a ledger scan per scene open answering one nobody asked.
  renderInspector();
  await screen.findByText("Active characters");
  expect(api.getSceneUsage).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: /^cost$/i }));
  expect(await screen.findByText("Nothing metered in this scene yet.")).toBeInTheDocument();
  expect(api.getSceneUsage).toHaveBeenCalledWith("c", "s");
});

test("the Context section header still shows the percentage badge and collapses as a whole", async () => {
  renderInspector();
  await screen.findByText(/World info/);
  await screen.findByText("≈10%");
  const header = screen.getByRole("button", { name: /context/i });
  fireEvent.click(header);
  expect(screen.queryByText(/World info/)).not.toBeInTheDocument();
});

test("a shut Context section does not recompose the prompt after a turn; opening it does", async () => {
  // GET /context has the server assemble the whole next prompt. Read on every
  // `refreshKey` bump it cost that after every turn, for a section the reader
  // had closed.
  localStorage.setItem("grimoire.inspector.sections", JSON.stringify({ context: true }));
  const view = (key: number) => (
    <MemoryRouter><SceneInspector cid="c" sid="s" refreshKey={key}
                                  onSceneChanged={() => {}} /></MemoryRouter>);
  const { rerender } = render(view(0));
  await screen.findByText("Active characters");
  rerender(view(1));                                   // a turn landed
  await screen.findByText("Active characters");
  expect(api.getSceneContext).not.toHaveBeenCalled();
  expect(screen.queryByText("≈10%")).toBeNull();       // no figure nobody read

  fireEvent.click(screen.getByRole("button", { name: /^context/i }));
  expect(await screen.findByText(/World info/)).toBeInTheDocument();
  expect(api.getSceneContext).toHaveBeenCalledTimes(1);
  // Shut and reopened within the same turn: the answer on hand is still the
  // current one, so it is not asked for again.
  fireEvent.click(screen.getByRole("button", { name: /^context/i }));
  fireEvent.click(screen.getByRole("button", { name: /^context/i }));
  await screen.findByText(/World info/);
  expect(api.getSceneContext).toHaveBeenCalledTimes(1);
});

test("an open Context section is re-read once per turn", async () => {
  const view = (key: number) => (
    <MemoryRouter><SceneInspector cid="c" sid="s" refreshKey={key}
                                  onSceneChanged={() => {}} /></MemoryRouter>);
  const { rerender } = render(view(0));
  await screen.findByText(/World info/);
  expect(api.getSceneContext).toHaveBeenCalledTimes(1);
  rerender(view(1));
  await screen.findByText(/World info/);
  expect(api.getSceneContext).toHaveBeenCalledTimes(2);
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
  await screen.findByText("≈50%");
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
  await screen.findByText("≈50%");              // 100 of the model's 200, not of 1000
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
  await screen.findByText("≈80%");
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

test("a refold the model could not be reached for offers the recovery", async () => {
  (api.getRollingSummary as any).mockResolvedValue({
    summary: "Standing summary.", at: 4, total: 4, stale: false, every: 10, due: false });
  (api.refreshRollingSummary as any).mockRejectedValue(
    { detail: "connection refused", kind: "network" });
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /refresh now/i }));
  await screen.findByText(/Couldn.t reach the model provider/);
  expect(screen.getByRole("link", { name: /Providers/ })).toHaveAttribute("href", "/providers");
  // still never destructive
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

// ---- comparing a captured turn against another composition (#130) ----

const DIFF = {
  base: { id: "000001", task: "chat", ts: "2026-08-06T11:00:00Z", model: "m",
          total_tokens: 80, dropped_tokens: 0, budget_tokens: 0 },
  head: { id: "live", task: "live", ts: "", model: "m",
          total_tokens: 120, dropped_tokens: 0, budget_tokens: 0 },
  sections: [{
    id: "world", label: "World info", status: "changed",
    base: { label: "World info", tokens: 80, dropped: false, trimmed: 0, pinned: false },
    head: { label: "World info", tokens: 120, dropped: false, trimmed: 0, pinned: false },
    diff: [{ op: "insert", text: "the pact was signed at dusk" }],
  }],
};

async function openTurnAndCompare(value: string) {
  (api.listScenePrompts as any).mockResolvedValue({ entries: TURNS });
  (api.getScenePrompt as any).mockResolvedValue(FROZEN);
  (api.getScenePromptDiff as any).mockResolvedValue(DIFF);
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));
  await screen.findByText("the lore as it stood then");
  fireEvent.change(await screen.findByLabelText("Compare with"),
                   { target: { value } });
}

test("the comparison picker is offered only from a past turn", async () => {
  // The live composition has nothing to be the "before" of.
  (api.listScenePrompts as any).mockResolvedValue({ entries: TURNS });
  (api.getScenePrompt as any).mockResolvedValue(FROZEN);
  renderInspector();
  await screen.findByText("lore text");
  expect(screen.queryByLabelText("Compare with")).toBeNull();

  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));
  await screen.findByLabelText("Compare with");
});

test("comparing a turn with the live preview replaces the breakdown with the diff", async () => {
  await openTurnAndCompare("live");

  await waitFor(() => expect(api.getScenePromptDiff)
    .toHaveBeenCalledWith("c", "s", "000001", "live"));
  await screen.findByText("the pact was signed at dusk");
  // Neither breakdown is on screen underneath it: the reader asked what moved.
  expect(screen.queryByText("the lore as it stood then")).toBeNull();
  expect(screen.queryByText("lore text")).toBeNull();
});

test("the turn being compared against is offered, and the one being compared is not", async () => {
  (api.listScenePrompts as any).mockResolvedValue({ entries: TURNS });
  (api.getScenePrompt as any).mockResolvedValue(FROZEN);
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));

  const picker = await screen.findByLabelText<HTMLSelectElement>("Compare with");
  const values = Array.from(picker.options).map((o) => o.value);
  expect(values).toContain("live");
  expect(values).toContain("000002");
  expect(values).not.toContain("000001");     // that is the turn being compared
});

test("two captured turns compare against each other, oldest end first", async () => {
  // The rail is newest-first, so a reader picking a row means "what changed
  // since then" — and a diff whose insertions are what the OLDER prompt had is
  // one running backwards through time.
  await openTurnAndCompare("000002");
  await waitFor(() => expect(api.getScenePromptDiff)
    .toHaveBeenCalledWith("c", "s", "000001", "000002"));
  await screen.findByText("the pact was signed at dusk");
});

test("picking an OLDER turn still asks for the diff oldest end first", async () => {
  (api.listScenePrompts as any).mockResolvedValue({ entries: TURNS });
  (api.getScenePrompt as any).mockResolvedValue({ ...FROZEN, id: "000002", task: "regenerate" });
  (api.getScenePromptDiff as any).mockResolvedValue(DIFF);
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /^Regenerate/ }));
  await screen.findByText("the lore as it stood then");

  fireEvent.change(await screen.findByLabelText("Compare with"),
                   { target: { value: "000001" } });
  await waitFor(() => expect(api.getScenePromptDiff)
    .toHaveBeenCalledWith("c", "s", "000001", "000002"));
});

test("clearing the picker returns to the frozen turn", async () => {
  await openTurnAndCompare("live");
  await screen.findByText("the pact was signed at dusk");

  fireEvent.change(screen.getByLabelText("Compare with"), { target: { value: "" } });
  await screen.findByText("the lore as it stood then");
  expect(screen.queryByText("the pact was signed at dusk")).toBeNull();
});

test("comparing against the live preview survives moving to another turn", async () => {
  // "At which turn did this section change?" is walked by clicking down the
  // rail; re-picking the comparison at every step would make that unusable.
  await openTurnAndCompare("live");
  await screen.findByText("the pact was signed at dusk");
  // Each row answers with its own snapshot, or the second click would land the
  // first turn again and the assertion below would pass without moving.
  (api.getScenePrompt as any).mockImplementation((_c: string, _s: string, eid: string) =>
    Promise.resolve({ ...FROZEN, id: eid, task: eid === "000002" ? "regenerate" : "chat" }));

  fireEvent.click(screen.getByRole("button", { name: /^Regenerate/ }));
  await waitFor(() => expect(api.getScenePromptDiff)
    .toHaveBeenCalledWith("c", "s", "000002", "live"));
  expect(screen.getByLabelText<HTMLSelectElement>("Compare with").value).toBe("live");
});

test("comparing against a specific turn does NOT survive moving to another", async () => {
  // The turn just clicked can be the one being compared against, and an entry
  // diffed with itself is a panel that has quietly stopped answering.
  await openTurnAndCompare("000002");
  await screen.findByText("the pact was signed at dusk");

  fireEvent.click(screen.getByRole("button", { name: /^Regenerate/ }));
  await screen.findByText("the lore as it stood then");
  expect(screen.getByLabelText<HTMLSelectElement>("Compare with").value).toBe("");
});

test("going back to live context ends the comparison", async () => {
  await openTurnAndCompare("live");
  await screen.findByText("the pact was signed at dusk");

  fireEvent.click(screen.getByRole("button", { name: /Back to live context/ }));
  await screen.findByText("lore text");
  expect(screen.queryByText("the pact was signed at dusk")).toBeNull();
});

test("a landed turn re-reads the comparison, because the live end has moved", async () => {
  // A diff against a preview that has since changed describes a prompt nobody
  // would send. `refreshKey` is bumped by the very turn that moved it.
  (api.listScenePrompts as any).mockResolvedValue({ entries: TURNS });
  (api.getScenePrompt as any).mockResolvedValue(FROZEN);
  (api.getScenePromptDiff as any).mockResolvedValue(DIFF);
  const { rerender } = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));
  await screen.findByText("the lore as it stood then");
  fireEvent.change(await screen.findByLabelText("Compare with"),
                   { target: { value: "live" } });
  await waitFor(() => expect(api.getScenePromptDiff).toHaveBeenCalledTimes(1));

  rerender(<SceneInspector cid="c" sid="s" refreshKey={1} onSceneChanged={() => {}} />);
  await waitFor(() => expect(api.getScenePromptDiff).toHaveBeenCalledTimes(2));
  // ...and the reader is still where they were, not yanked back to live.
  await screen.findByText("the pact was signed at dusk");
});

test("a turn landing does not re-read a comparison of two frozen turns", async () => {
  // Both ends are frozen, so a completed turn cannot change the answer and the
  // request could only return what is already on screen.
  (api.listScenePrompts as any).mockResolvedValue({ entries: TURNS });
  (api.getScenePrompt as any).mockResolvedValue(FROZEN);
  (api.getScenePromptDiff as any).mockResolvedValue(DIFF);
  const { rerender } = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));
  await screen.findByText("the lore as it stood then");
  fireEvent.change(await screen.findByLabelText("Compare with"), { target: { value: "000002" } });
  await waitFor(() => expect(api.getScenePromptDiff).toHaveBeenCalledTimes(1));

  rerender(<SceneInspector cid="c" sid="s" refreshKey={1} onSceneChanged={() => {}} />);
  await screen.findByText("the pact was signed at dusk");
  expect(api.getScenePromptDiff).toHaveBeenCalledTimes(1);
});

test("a live comparison marks itself stale while the replacement is in flight", async () => {
  // Blanking it would flip the panel to this turn's breakdown — a different
  // kind of content, and one that is refetching too.
  (api.listScenePrompts as any).mockResolvedValue({ entries: TURNS });
  (api.getScenePrompt as any).mockResolvedValue(FROZEN);
  (api.getScenePromptDiff as any).mockResolvedValue(DIFF);
  const { rerender } = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));
  await screen.findByText("the lore as it stood then");
  fireEvent.change(await screen.findByLabelText("Compare with"), { target: { value: "live" } });
  await screen.findByText("the pact was signed at dusk");

  let release: (v: any) => void = () => {};
  (api.getScenePromptDiff as any).mockReturnValue(new Promise((r) => { release = r; }));
  rerender(<SceneInspector cid="c" sid="s" refreshKey={1} onSceneChanged={() => {}} />);

  await screen.findByText(/A turn has landed since this was computed/);
  await screen.findByText("the pact was signed at dusk");   // still readable, not blanked
  release(DIFF);
  await waitFor(() =>
    expect(screen.queryByText(/A turn has landed since this was computed/)).toBeNull());
});

test("moving to another turn with a live comparison selected stays in compare mode", async () => {
  // The picker keeps saying "The live preview" across the move, so the panel
  // must not drop to the ordinary breakdown underneath it while the
  // replacement diff is in flight — two controls contradicting each other, and
  // indefinitely so if that request never answers.
  (api.listScenePrompts as any).mockResolvedValue({ entries: TURNS });
  (api.getScenePrompt as any).mockImplementation((_c: string, _s: string, eid: string) =>
    Promise.resolve({ ...FROZEN, id: eid, task: eid === "000002" ? "regenerate" : "chat" }));
  (api.getScenePromptDiff as any).mockResolvedValue(DIFF);
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));
  await screen.findByText("the lore as it stood then");
  fireEvent.change(await screen.findByLabelText("Compare with"), { target: { value: "live" } });
  await screen.findByText("the pact was signed at dusk");

  let release: (v: any) => void = () => {};
  (api.getScenePromptDiff as any).mockReturnValue(new Promise((r) => { release = r; }));
  fireEvent.click(screen.getByRole("button", { name: /^Regenerate/ }));

  await screen.findByText("Comparing…");
  expect(screen.getByLabelText<HTMLSelectElement>("Compare with").value).toBe("live");
  // ...and specifically NOT the frozen breakdown the fall-through used to show
  expect(screen.queryByText("the lore as it stood then")).toBeNull();

  release(DIFF);
  await screen.findByText("the pact was signed at dusk");
});

test("the very first comparison also waits rather than showing the breakdown", async () => {
  (api.listScenePrompts as any).mockResolvedValue({ entries: TURNS });
  (api.getScenePrompt as any).mockResolvedValue(FROZEN);
  let release: (v: any) => void = () => {};
  (api.getScenePromptDiff as any).mockReturnValue(new Promise((r) => { release = r; }));
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));
  await screen.findByText("the lore as it stood then");

  fireEvent.change(await screen.findByLabelText("Compare with"), { target: { value: "live" } });
  await screen.findByText("Comparing…");
  release(DIFF);
  await screen.findByText("the pact was signed at dusk");
});

test("a failed comparison falls back to the turn rather than waiting forever", async () => {
  // The pending state must not outlive the request: the catch clears the
  // picker, so the panel has a comparison to fall back FROM.
  (api.listScenePrompts as any).mockResolvedValue({ entries: TURNS });
  (api.getScenePrompt as any).mockResolvedValue(FROZEN);
  (api.getScenePromptDiff as any).mockRejectedValue({ status: 404, detail: "gone" });
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));
  await screen.findByText("the lore as it stood then");

  fireEvent.change(await screen.findByLabelText("Compare with"), { target: { value: "live" } });
  await screen.findByText(/aged out of the log/);
  await screen.findByText("the lore as it stood then");
  expect(screen.queryByText("Comparing…")).toBeNull();
});

test("an evicted turn on either end says so rather than blanking", async () => {
  (api.listScenePrompts as any).mockResolvedValue({ entries: TURNS });
  (api.getScenePrompt as any).mockResolvedValue(FROZEN);
  (api.getScenePromptDiff as any).mockRejectedValue({ status: 404, detail: "not found" });
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));
  await screen.findByText("the lore as it stood then");

  fireEvent.change(await screen.findByLabelText("Compare with"),
                   { target: { value: "live" } });
  await screen.findByText(/aged out of the log/);
  await screen.findByText("the lore as it stood then");   // still on the frozen view
});

test("a failed comparison's banner clears when the reader tries again", async () => {
  // Otherwise the retry renders its diff underneath "those turns could not be
  // compared", which reads as a diff that failed.
  (api.listScenePrompts as any).mockResolvedValue({ entries: TURNS });
  (api.getScenePrompt as any).mockResolvedValue(FROZEN);
  (api.getScenePromptDiff as any).mockRejectedValueOnce({ status: 404, detail: "gone" });
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));
  await screen.findByText("the lore as it stood then");

  fireEvent.change(await screen.findByLabelText("Compare with"), { target: { value: "live" } });
  await screen.findByText(/aged out of the log/);

  (api.getScenePromptDiff as any).mockResolvedValue(DIFF);
  fireEvent.change(screen.getByLabelText("Compare with"), { target: { value: "live" } });
  await screen.findByText("the pact was signed at dusk");
  expect(screen.queryByText(/aged out of the log/)).toBeNull();
});

test("a diff arriving after the reader moved on is dropped, not shown", async () => {
  (api.listScenePrompts as any).mockResolvedValue({ entries: TURNS });
  (api.getScenePrompt as any).mockResolvedValue(FROZEN);
  let release: (v: any) => void = () => {};
  (api.getScenePromptDiff as any).mockReturnValue(new Promise((r) => { release = r; }));

  const { rerender } = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));
  await screen.findByText("the lore as it stood then");
  fireEvent.change(await screen.findByLabelText("Compare with"),
                   { target: { value: "live" } });

  rerender(<SceneInspector cid="c" sid="s2" refreshKey={0} onSceneChanged={() => {}} />);
  release(DIFF);

  await screen.findByText("lore text");
  expect(screen.queryByText("the pact was signed at dusk")).toBeNull();
});

test("a turn that fails to load ends the comparison rather than hiding it", async () => {
  // Clearing `frozen` returns the panel to live context, which is where "Back
  // to live context" lands — and that clears `compare`. Leaving it set made the
  // two exits differ: the choice would be invisible, and the NEXT turn clicked
  // would open straight into a comparison the reader never saw themselves ask
  // for. Sticky across turns, not across leaving.
  (api.listScenePrompts as any).mockResolvedValue({ entries: TURNS });
  (api.getScenePrompt as any).mockResolvedValue(FROZEN);
  (api.getScenePromptDiff as any).mockResolvedValue(DIFF);
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));
  await screen.findByText("the lore as it stood then");
  fireEvent.change(await screen.findByLabelText("Compare with"), { target: { value: "live" } });
  await screen.findByText("the pact was signed at dusk");

  // the next turn clicked has aged out
  (api.getScenePrompt as any).mockRejectedValue({ status: 404, detail: "gone" });
  fireEvent.click(screen.getByRole("button", { name: /^Regenerate/ }));
  await screen.findByText(/aged out of the log/);

  // ...and a later turn that DOES load opens as itself, not as a comparison
  (api.getScenePrompt as any).mockResolvedValue(FROZEN);
  fireEvent.click(screen.getByRole("button", { name: /^Send/ }));
  await screen.findByText("the lore as it stood then");
  expect(screen.getByLabelText<HTMLSelectElement>("Compare with").value).toBe("");
  expect(screen.queryByText("the pact was signed at dusk")).toBeNull();
});

test("a comparison whose turn ages out keeps its option in the picker", async () => {
  // A turn-against-turn diff is frozen at both ends and so is not re-read, but
  // retention can still evict the entry it names. Without an option the browser
  // falls back to the first one and the picker contradicts the panel below it;
  // clearing would throw away a comparison the reader is in the middle of.
  (api.listScenePrompts as any).mockResolvedValue({ entries: TURNS });
  (api.getScenePrompt as any).mockResolvedValue(FROZEN);
  (api.getScenePromptDiff as any).mockResolvedValue(DIFF);
  const { rerender } = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));
  await screen.findByText("the lore as it stood then");
  fireEvent.change(await screen.findByLabelText("Compare with"), { target: { value: "000002" } });
  await screen.findByText("the pact was signed at dusk");

  // the compared turn falls out of the retention window
  (api.listScenePrompts as any).mockResolvedValue({ entries: [TURNS[1]] });
  rerender(<SceneInspector cid="c" sid="s" refreshKey={1} onSceneChanged={() => {}} />);

  await screen.findByText(/aged out of the log/);
  expect(screen.getByLabelText<HTMLSelectElement>("Compare with").value).toBe("000002");
  await screen.findByText("the pact was signed at dusk");   // still on screen
});


// ---- Turn history: stored rewrites (regex output processing, spec 6.3) ----

const REWRITTEN_REPLY = {
  index: 3,
  message: { role: "assistant" as const, content: "Mara nods.", response_id: "resp-1",
             rewritten: true, speaker: "Mara" },
};

function renderRewrites(handlers: { onSceneChanged?: () => void; onTranscriptEdited?: () => void } = {}) {
  render(<MemoryRouter><SceneInspector cid="c" sid="s" refreshKey={0}
                                       onSceneChanged={handlers.onSceneChanged ?? (() => {})}
                                       onTranscriptEdited={handlers.onTranscriptEdited}
                                       rewritten={[REWRITTEN_REPLY]} /></MemoryRouter>);
}

function mockRewriteRecord() {
  (api.getSceneRewrites as any).mockResolvedValue({
    "resp-1": { original: "Mara nods. (OOC: she is lying)", rules: ["r-strip", "r-gone"],
                at: "2026-10-05T12:00:00Z" } });
  (api.getRegex as any).mockResolvedValue({
    layer: { rules: [{ id: "r-strip", name: "Strip asides" }], off: [] },
    inherited: [], warnings: {} });
}

test("a rewritten turn shows the original and Restore calls editMessage with restore", async () => {
  mockRewriteRecord();
  (api.editMessage as any).mockResolvedValue({ ok: true });
  const onSceneChanged = vi.fn();
  const onTranscriptEdited = vi.fn();
  renderRewrites({ onSceneChanged, onTranscriptEdited });
  const row = await screen.findByRole("button", { name: /Rewritten/ });
  expect(api.getSceneRewrites).not.toHaveBeenCalled();   // read on opening, not on mount
  fireEvent.click(row);
  const detail = await screen.findByRole("region", { name: "Rewrite of post 4" });
  expect(api.getSceneRewrites).toHaveBeenCalledWith("c", "s");
  expect(within(detail).getByText("Mara nods. (OOC: she is lying)")).toBeInTheDocument();
  expect(within(detail).getByText("Mara nods.")).toBeInTheDocument();
  // Named where a rule is still there to name, its id where it is not.
  expect(within(detail).getByText("Strip asides")).toBeInTheDocument();
  expect(within(detail).getByText("r-gone")).toBeInTheDocument();
  fireEvent.click(within(detail).getByRole("button", { name: "Restore original" }));
  await waitFor(() => expect(api.editMessage).toHaveBeenCalledWith(
    "c", "s", 3, "Mara nods. (OOC: she is lying)", { restore: true }));
  await waitFor(() => expect(onTranscriptEdited).toHaveBeenCalledOnce());
  expect(onSceneChanged).not.toHaveBeenCalled();
});

test("a stale restore says so and reloads the scene rather than writing anything", async () => {
  mockRewriteRecord();
  (api.editMessage as any).mockRejectedValue(Object.assign(new Error("stale"), {
    status: 409, kind: "rewrite_stale",
    detail: "This message no longer matches its recorded rewrite; reload the scene." }));
  const onSceneChanged = vi.fn();
  const onTranscriptEdited = vi.fn();
  renderRewrites({ onSceneChanged, onTranscriptEdited });
  fireEvent.click(await screen.findByRole("button", { name: /Rewritten/ }));
  const detail = await screen.findByRole("region", { name: "Rewrite of post 4" });
  fireEvent.click(within(detail).getByRole("button", { name: "Restore original" }));
  expect(await screen.findByText(/changed since it was rewritten/)).toBeInTheDocument();
  expect(onSceneChanged).toHaveBeenCalledOnce();
  expect(onTranscriptEdited).not.toHaveBeenCalled();
  expect(api.editMessage).toHaveBeenCalledOnce();
});

test("a rewritten post whose record is gone offers nothing to restore", async () => {
  (api.getSceneRewrites as any).mockResolvedValue({});
  (api.getRegex as any).mockResolvedValue({ layer: { rules: [], off: [] }, inherited: [], warnings: {} });
  renderRewrites();
  fireEvent.click(await screen.findByRole("button", { name: /Rewritten/ }));
  expect(await screen.findByText(/no longer has a recorded original/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Restore original" })).not.toBeInTheDocument();
});

test("a hand-mangled record's rules do not crash the opened rewrite", async () => {
  (api.getSceneRewrites as any).mockResolvedValue({
    "resp-1": { original: "Mara nods. (OOC: she is lying)", rules: "r-strip", at: 7 } });
  (api.getRegex as any).mockResolvedValue({ layer: { rules: [], off: [] }, inherited: [], warnings: {} });
  renderRewrites();
  fireEvent.click(await screen.findByRole("button", { name: /Rewritten/ }));
  const detail = await screen.findByRole("region", { name: "Rewrite of post 4" });
  expect(within(detail).getByText("Mara nods. (OOC: she is lying)")).toBeInTheDocument();
  expect(within(detail).getByRole("button", { name: "Restore original" })).toBeInTheDocument();
});

test("a later part of a response opens its own record, by the key the scene read names", async () => {
  (api.getSceneRewrites as any).mockResolvedValue({
    "resp-1": { original: "First part...", rules: ["r-strip"], at: "" },
    "resp-1#c-2": { original: "Second part...", rules: ["r-strip"], at: "" } });
  (api.getRegex as any).mockResolvedValue({ layer: { rules: [], off: [] }, inherited: [], warnings: {} });
  render(<MemoryRouter><SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}}
    rewritten={[{ index: 4, message: { role: "assistant", content: "Second part…",
      response_id: "resp-1", response_part: "c-2", rewritten: true,
      rewrite_key: "resp-1#c-2", speaker: "Mara" } }]} /></MemoryRouter>);
  fireEvent.click(await screen.findByRole("button", { name: /Rewritten/ }));
  const detail = await screen.findByRole("region", { name: "Rewrite of post 5" });
  expect(within(detail).getByText("Second part...")).toBeInTheDocument();
  expect(within(detail).queryByText("First part...")).not.toBeInTheDocument();
});

// ---- quick replies share the two manual tasks ----

test("a task the strip is running disables its manual button", async () => {
  const { rerender } = render(<MemoryRouter><SceneInspector cid="c" sid="s" refreshKey={0}
    onSceneChanged={() => {}} stripTasks={{ rolling_summary: true }} /></MemoryRouter>);
  expect(await screen.findByRole("button", { name: /refresh now/i })).toBeDisabled();
  expect(screen.getByRole("button", { name: /ask now/i })).toBeEnabled();
  rerender(<MemoryRouter><SceneInspector cid="c" sid="s" refreshKey={0}
    onSceneChanged={() => {}} stripTasks={{ scene_break: true }} /></MemoryRouter>);
  expect(await screen.findByRole("button", { name: /ask now/i })).toBeDisabled();
  expect(screen.getByRole("button", { name: /refresh now/i })).toBeEnabled();
});

test("Not here is held while the strip has a scene-break question out", async () => {
  // Dismissing under a strip-started question would let that question land
  // afterwards and raise the prompt the player just turned down.
  (api.getSceneBreak as any).mockResolvedValue(BREAK_YES);
  render(<MemoryRouter><SceneInspector cid="c" sid="s" refreshKey={0}
    onSceneChanged={() => {}} stripTasks={{ scene_break: true }} /></MemoryRouter>);
  expect(await screen.findByRole("button", { name: /not here/i })).toBeDisabled();
});

test("its manual buttons report themselves busy", async () => {
  const onTaskBusy = vi.fn();
  render(<MemoryRouter><SceneInspector cid="c" sid="s" refreshKey={0}
    onSceneChanged={() => {}} onTaskBusy={onTaskBusy} /></MemoryRouter>);
  fireEvent.click(await screen.findByRole("button", { name: /refresh now/i }));
  await waitFor(() => expect(onTaskBusy).toHaveBeenLastCalledWith("rolling_summary", false));
  expect(onTaskBusy.mock.calls[0]).toEqual(["rolling_summary", true]);
  onTaskBusy.mockClear();
  fireEvent.click(screen.getByRole("button", { name: /ask now/i }));
  await waitFor(() => expect(onTaskBusy).toHaveBeenLastCalledWith("scene_break", false));
  expect(onTaskBusy.mock.calls[0]).toEqual(["scene_break", true]);
});
