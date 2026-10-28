import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { api } from "../api/client";
import { getModels } from "../api/models";
import type { PromptEntry, PromptSnapshot } from "../api/types";
import { SceneInspector } from "../components/SceneInspector";
import { fixture } from "./fixture";

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

export function resetSceneInspectorMocks() {
  localStorage.clear();
  vi.clearAllMocks();
  vi.mocked(api.getSceneFocus).mockResolvedValue({ text: "Let Mara choose.", status: "ok" });
  vi.mocked(api.getPromptLayout).mockResolvedValue({ enabled: false, sections: [] });
  vi.mocked(api.getAuthorsNotesNext).mockResolvedValue({ turn: 1, count: 0, notes: [] });
  vi.mocked(api.getCast).mockResolvedValue(fixture([{ kind: "characters", id: "seraphine", role: "npc" }]));
  vi.mocked(api.getSceneUsage).mockResolvedValue(fixture({
    campaign: "c", scene: "s", since: "2026-08-01", until: "2026-08-14",
    generated_at: "2026-08-14T12:00:00Z",
    totals: { calls: 0, errors: 0, prompt_tokens: 0, completion_tokens: 0, total_tokens: 0,
              cache_read_tokens: 0, cache_write_tokens: 0, cost_usd: 0, estimated_usd: 0,
              priced_calls: 0, unpriced_calls: 0, duration_ms: 0 },
    by_task: [], turns: [], listed: 0, truncated: false }));
  vi.mocked(api.getCampaignBudget).mockResolvedValue(
    { limit_usd: 0, period: "monthly", level: "off", warn_fraction: 0.8 });
  vi.mocked(api.addToCast).mockResolvedValue({ ok: true });
  vi.mocked(api.getSuggestions).mockResolvedValue([]);
  vi.mocked(api.dismissSuggestion).mockResolvedValue({ ok: true });
  vi.mocked(api.removeFromCast).mockResolvedValue({ ok: true });
  vi.mocked(api.getCampaign).mockResolvedValue(fixture({ meta: { id: "c", world: "w" }, body: "" }));
  vi.mocked(api.listCharacters).mockResolvedValue([{ id: "seraphine", name: "Seraphine", default_version: "default", versions: [] }]);
  vi.mocked(api.listPCs).mockResolvedValue([]);
  vi.mocked(api.listCampaignPCs).mockResolvedValue([]);
  vi.mocked(api.getSceneLocation).mockResolvedValue({ current: { id: "crypt", name: "The Crypt" }, visited: [] });
  vi.mocked(api.getSceneContext).mockResolvedValue({
    model: "m", total_tokens: 100, dropped_tokens: 0, budget_tokens: 0,
    sections: [{ label: "World info", text: "lore text", tokens: 100,
                 tier: "spotlight", dropped: false, trimmed: 0 }],
  });
  vi.mocked(api.listScenePrompts).mockResolvedValue({ entries: [] });
  vi.mocked(api.getScenePrompt).mockRejectedValue(new Error("not found"));
  vi.mocked(api.getScenePromptDiff).mockRejectedValue(new Error("not found"));
  vi.mocked(api.getCastDetail).mockResolvedValue({ kind: "characters", id: "seraphine", name: "Seraphine", version: "default", body: "keeper", source: "library" });
  vi.mocked(getModels).mockResolvedValue([{ id: "m", name: "M", context: 1000, prompt: "0", completion: "0" }]);
  vi.mocked(api.getChronicle).mockResolvedValue([
    { id: "s0", one_line: "They first met.", summary: "", keywords: [],
      cast: [], location: "", date: "", absorbed: "t" }]);
  vi.mocked(api.getCalendarConfig).mockResolvedValue(fixture({
    primary: { provider: "gregorian", region: "US", custom_holidays: [], anchor: null },
    secondary: null, confirmed: true }));
  vi.mocked(api.setCalendarConfig).mockResolvedValue({ ok: true });
  vi.mocked(api.getCalendarProviders).mockResolvedValue({ providers: [
    { id: "gregorian", name: "Gregorian" }, { id: "hebrew", name: "Hebrew" },
  ] });
  vi.mocked(api.getSceneDatetime).mockResolvedValue({ current: null, history: [], suggested: null });
  vi.mocked(api.dismissNotices).mockResolvedValue({ ok: true, marked: [] });
  vi.mocked(api.setSceneDatetime).mockResolvedValue({ ok: true, advanced: false, friendly: "", id: "s" });
  vi.mocked(api.getCalendarMonths).mockResolvedValue({ months: GREG_MONTHS });
  vi.mocked(api.getCampaignClock).mockResolvedValue(
    fixture({ now: "2026-05-01", friendly: "1 May 2026", log: [] }));
  vi.mocked(api.listAppearances).mockResolvedValue([]);
  vi.mocked(api.listEntityImages).mockResolvedValue([]);
  vi.mocked(api.listEntities).mockResolvedValue([]);
  vi.mocked(api.setSceneLocation).mockResolvedValue({ ok: true, moved: true, name: "" });
  // Empty by default, so the briefing section renders nothing and the suites
  // that predate it assert on the same rail they always did (#118).
  vi.mocked(api.sceneBriefing).mockResolvedValue(EMPTY_BRIEFING);
  vi.mocked(api.getRollingSummary).mockResolvedValue({
    summary: "", at: 0, total: 0, stale: false, every: 10, due: false });
  vi.mocked(api.refreshRollingSummary).mockResolvedValue({
    summary: "Refolded.", at: 4, total: 4, stale: false, every: 10, due: false,
    refreshed: true });
  // The scene-break detector saying nothing (#84), so every suite that predates
  // it renders the rail it always did.
  vi.mocked(api.getSceneBreak).mockResolvedValue(NO_SCENE_BREAK);
  vi.mocked(api.askSceneBreak).mockResolvedValue({ ...NO_SCENE_BREAK, asked: false });
  vi.mocked(api.dismissSceneBreak).mockResolvedValue(NO_SCENE_BREAK);
  // No rules by default (#129), so every suite that predates pins renders the
  // rail it always did -- an empty section with its hint.
  vi.mocked(api.getPins).mockResolvedValue({ pins: [] });
  vi.mocked(api.setPin).mockResolvedValue(fixture({ ok: true }));
  vi.mocked(api.removePin).mockResolvedValue({ ok: true });
}
export const NO_SCENE_BREAK = {
  verdict: "" as const, reason: "", title: "", stale: false,
  posts: 0, score: 0, signals: [], every: 20, due: false,
};
export const EMPTY_BRIEFING = {
  focus: [], plot: [], commitments: [], relationships: [], last_time: null };

export function renderInspector(onSceneChanged: () => void = () => {}) {
  // Routed: a refold the model could not be reached for links to
  // Connections (#210), and a `Link` outside a router throws.
  render(<MemoryRouter><SceneInspector cid="c" sid="s" refreshKey={0}
                                       onSceneChanged={onSceneChanged} /></MemoryRouter>);
}
export const TURNS: PromptEntry[] = [
  { id: "000002", scene: "s", ts: "2026-08-06T12:00:00Z", task: "regenerate",
    model: "m", total_tokens: 90, dropped_tokens: 0, budget_tokens: 0 },
  { id: "000001", scene: "s", ts: "2026-08-06T11:00:00Z", task: "chat",
    model: "m", total_tokens: 80, dropped_tokens: 0, budget_tokens: 0 },
];

export const FROZEN: PromptSnapshot = {
  id: "000001", ts: "2026-08-06T11:00:00Z", task: "chat", model: "m",
  total_tokens: 80, dropped_tokens: 0, budget_tokens: 0,
  sections: [{ label: "World info", text: "the lore as it stood then", tokens: 80,
               tier: "spotlight", dropped: false, trimmed: 0 }],
};
