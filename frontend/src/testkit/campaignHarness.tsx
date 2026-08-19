// The shared fixtures, per-test defaults and render helpers for every suite
// that drives the campaign play view.
//
// `CampaignView.test.tsx` and `components/review/SceneReview.test.tsx` render
// the same page against the same mocked API; splitting the review out (#378)
// would otherwise have meant a second copy of two hundred lines of defaults,
// which is the kind of duplicate that drifts silently. This module imports the
// mocked `api` and so must never be reached from a `vi.mock` factory --
// `campaignMocks` is the half that can be, and holds the factories.
import type { ReactNode } from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route, useLocation } from "react-router-dom";
import type { Mock } from "vitest";
import CampaignView from "../routes/CampaignView";
import CommandPalette, { usePaletteHotkey } from "../components/CommandPalette";
import { PaletteProvider } from "../components/palette";
import type { ChatEvent } from "../api/stream";
import { api } from "../api/client";
import { getModels } from "../api/models";

export const ONE_SCENE = [{ id: "s1", title: "Old", model: "", created: "", updated: "" }];
// The scene-break detector saying nothing (#84), so every suite that predates
// it renders the rail it always did and the play loop's per-post question is a
// no-op it never has to think about.
export const NO_SCENE_BREAK = {
  verdict: "" as const, reason: "", title: "", stale: false,
  posts: 0, score: 0, signals: [], every: 20, due: false,
};

// Stand-in `phases` for the absorb mocks that are about something else. What
// every one of them relies on is the single property named here: no phase was
// cut short by the time budget, so no budget notice renders.
export const PHASES_NONE_CUT = [
  { name: "extraction", status: "ok", reason: null, attempted: true, budget_exhausted: false },
  { name: "dossiers", status: "ok", reason: null, attempted: true, budget_exhausted: false },
  { name: "audit", status: "ok", reason: null, attempted: true, budget_exhausted: false },
];

// The built-ins response_presets.py ships (templates/response_presets/*.md) —
// the chip's dropdown lists whatever listResponsePresets returns.
export const RESPONSE_PRESETS = [
  { id: "standard", name: "Standard", built_in: true },
  { id: "brisk", name: "Brisk", built_in: true },
  { id: "cinematic", name: "Cinematic", built_in: true },
  { id: "terse", name: "Terse", built_in: true },
];

// What GET /api/campaigns/:cid/scenes/:sid/response returns: the scene's own
// (here: empty) fields plus the SERVER-resolved bundle and its provenance.
export const RESPONSE_BUNDLE = {
  response_preset: "", style_id: "",
  length_reply_words: "", length_blocks: "", length_paragraphs: "",
  length_speakers: "", length_blocks_per_speaker: "",
  effective: { style_id: "", reply_words: 550, blocks: 5, paragraphs: 2, speakers: 4, blocks_per_speaker: 2 },
  provenance: { reply_words: { scope: "default", source: "default" } },
};

/** Everything a campaign-view suite needs reset and re-defaulted per test. */
export function installCampaignMocks() {
  localStorage.clear();
  vi.clearAllMocks();
  // Every api mock also gets its IMPLEMENTATION reset, which `clearAllMocks`
  // above does not do — it clears call history only. That gap was a real flake.
  //
  // ~20 tests below deliberately mock an api with a promise that never settles
  // ("never lands", "never settles") to hold a request in flight. Those
  // implementations outlived their test, so any later test in this file that
  // touched the same api awaited a promise nobody would ever resolve, and hung
  // until testing-library's async ceiling.
  //
  // It presented as load — a different test failing each run, always a `findBy*`
  // timing out — so it read as a slow machine and cost CI reds on branches that
  // had not touched any of it. It is not load, and raising the ceiling only
  // moves when the hang is reported: measured, it still failed at 8000ms (at
  // 8074ms) on an idle machine.
  //
  // Scoped to `api` deliberately, having tried both neighbours: naming the
  // offending apis individually does not hold, because the list is whatever the
  // file mocks today and the next test to hold a request in flight rejoins the
  // class unnoticed; and `vi.resetAllMocks()` reaches too far, wiping the
  // module-scope mocks (`getModels`, the child-component stubs) that three tests
  // here depend on. `api` is exactly the surface with the problem, and exactly
  // the surface the defaults below rebuild.
  //
  // Reset to a RESOLVED promise rather than bare: a reset mock returns
  // `undefined`, and a component that does `api.thing().then(…)` — WeatherWidget
  // does — throws on it during commit instead of waiting. An already-settled
  // promise is the smallest default that lets no api leave a caller waiting.
  for (const fn of Object.values(api)) {
    if (typeof fn === "function" && "mockReset" in fn) {
      (fn as unknown as Mock).mockReset().mockResolvedValue(undefined);
    }
  }
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
  (api.getAlternates as any).mockResolvedValue({ active: null, alternates: [] });
  (api.pickAlternate as any).mockResolvedValue({ ok: true });
  (api.getRollProposal as any).mockResolvedValue({ record: null });
  (api.resolveProposal as any).mockImplementation(streamsDone);
  (api.getSceneChecks as any).mockResolvedValue({ actors: [] });
  (api.rollCheck as any).mockResolvedValue({ ok: true, resolution: {}, message: "" });
  (api.getConfig as any).mockResolvedValue({ theme: "codex", system_prompt: "", quote_color: "off", user_label: "You", assistant_label: "Grimoire", active_connection_id: "openrouter", active_connection: { id: "openrouter", kind: "openrouter", name: "OpenRouter" }, ready: true });
  (api.editMessage as any).mockResolvedValue({ ok: true });
  (api.getReplay as any).mockResolvedValue(null);
  (api.replayPreview as any).mockResolvedValue(
    { posts: 1, turns: 1, threshold: 10, fork: false, blocked: "" });
  (api.getCast as any).mockResolvedValue([]);
  (api.addToCast as any).mockResolvedValue({ ok: true });
  // "The turn changed nobody", so no cast-change chips render: these suites are
  // about the transcript and the panels around it, and the suggestion strip has
  // its own tests in components/play/CastChanges.test.tsx.
  (api.castChanges as any).mockResolvedValue({ enter: [], leave: [], unknown: [] });
  // The embedded SceneInspector renders the suggestion strip for real. Without
  // this the strip calls `undefined`, and every inspector mount in this file
  // takes an exception path — extra async work in the suite least able to
  // afford it (#351).
  (api.getSuggestions as any).mockResolvedValue([]);
  (api.removeFromCast as any).mockResolvedValue({ ok: true });
  (api.getSceneLocation as any).mockResolvedValue({ current: null, visited: [] });
  (api.getSceneContext as any).mockResolvedValue({ model: "m", total_tokens: 0,
    dropped_tokens: 0, budget_tokens: 0, sections: [] });
  // No pins (#129), so the inspector's rail here is the one these suites were
  // written against.
  (api.getPins as any).mockResolvedValue({ pins: [] });
  // Empty, so the inspector's Briefing section (#118) renders nothing here and
  // these suites keep asserting on the rail they were written against.
  (api.sceneBriefing as any).mockResolvedValue({
    focus: [], plot: [], commitments: [], relationships: [], last_time: null });
  (api.listScenePrompts as any).mockResolvedValue({ entries: [] });
  (api.getScenePrompt as any).mockResolvedValue(null);
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
  // No budget, so the banner is absent and these suites keep asserting on the
  // page they were written against. The tests that want one set it themselves.
  (api.getCampaignBudget as any).mockResolvedValue(
    { limit_usd: 0, period: "monthly", level: "off", warn_fraction: 0.8 });
  (api.getSceneUsage as any).mockResolvedValue({
    campaign: "run", scene: "s1", since: "", until: "", generated_at: "",
    totals: { calls: 0, errors: 0, prompt_tokens: 0, completion_tokens: 0,
              total_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0,
              cost_usd: 0, estimated_usd: 0, priced_calls: 0, unpriced_calls: 0,
              duration_ms: 0 },
    by_task: [], turns: [], listed: 0, truncated: false });
  (getModels as any).mockResolvedValue([]);
  (api.absorbScene as any).mockResolvedValue({
    one_line: "They met.", summary: "A met B.", keywords: ["salt"],
    timeline_events: [], cast: [], location: "", date: "",
    mechanics: { status: "ok", reason: null, warnings: [], dropped: [] },
    commit_token: "tok",
    dossiers: { status: "skipped", reason: null, proposed: [], failed: [], skipped: [] },
    voice: { status: "skipped", reason: null, checked: [], flagged: [], unjudged: [], failed: [], skipped: [] },
    phases: PHASES_NONE_CUT,
    edits: [{ id: "character_state:seraphine", kind: "character_state",
      target: { kind: "characters", id: "seraphine" }, label: "Seraphine — current state",
      field: "current_state", before: "Wary.", after: "Loyal now.", authored: false }] });
  (api.saveChronicle as any).mockResolvedValue({ id: "s1", one_line: "They met.",
    summary: "A met B.", keywords: ["salt"], cast: [], location: "", date: "", absorbed: "t",
    applied: [], failures: [] });
  (api.getChronicle as any).mockResolvedValue([]);
  (api.campaignChanges as any).mockResolvedValue([]);
  (api.getIncoming as any).mockResolvedValue([]);
  (api.campaignProvenance as any).mockResolvedValue({});
  (api.campaignLedger as any).mockResolvedValue({ plot: [], commitments: [], facts: [], chronicle: [] });
  (api.listResponsePresets as any).mockResolvedValue([]);
  // A pack is bound by default, so the dice button is present for the tests
  // that predate it being conditional.
  (api.getCampaignModule as any).mockResolvedValue(
    { setting: "pool-basic", resolved: "pool-basic", source: "campaign" });
  (api.setCampaignModule as any).mockResolvedValue({ ok: true });
  (api.listModules as any).mockResolvedValue([{ id: "pool-basic", name: "Pool Basic" }]);
  (api.getCampaignSheets as any).mockResolvedValue({ coverage: {} });
  (api.getRollingSummary as any).mockResolvedValue({
    summary: "", at: 0, total: 0, stale: false, every: 10, due: false });
  (api.refreshRollingSummary as any).mockResolvedValue({
    summary: "", at: 0, total: 0, stale: false, every: 10, due: false,
    refreshed: false });
  (api.getSceneBreak as any).mockResolvedValue(NO_SCENE_BREAK);
  (api.askSceneBreak as any).mockResolvedValue({ ...NO_SCENE_BREAK, asked: false });
  (api.dismissSceneBreak as any).mockResolvedValue(NO_SCENE_BREAK);
}

// The two paths the play view answers to, nested exactly as App.tsx nests them
// (#87): one CampaignView instance serves both, so switching campaigns never
// remounts it whether or not the URL carries a scene.
export function playRoutes(ready = true) {
  return (
    <Routes>
      <Route path="/campaigns/:cid" element={<CampaignView ready={ready} />}>
        <Route path="scenes/:sid" element={null} />
      </Route>
    </Routes>
  );
}

// Reads back the URL the view has navigated itself to.
export function Here() {
  return <span data-testid="here">{useLocation().pathname}</span>;
}
export const here = () => screen.getByTestId("here").textContent;

// `listScenes` as the server actually answers it around a rename: the mount
// read still sees the old id, and every read AFTER the rename landed sees the
// new one. The relists that follow a mutation are `fresh` reads issued once the
// write returned, so they cannot come back pre-rename — and mocking them that
// way models a server that lost the rename it just confirmed.
export function relistsAs(before: any[], after: any[]) {
  (api.listScenes as any).mockResolvedValueOnce(before).mockResolvedValue(after);
}

// `listScenes` no longer takes a `fresh` flag — the endpoint never coalesces
// now (#87), so nothing distinguishes a mount read from a relist at the API
// level. These fixtures tell them apart by order instead: the first read of a
// campaign is its mount read, every later one is a relist.
export function readCounter() {
  const seen = new Map<string, number>();
  return (cid: string) => {
    const n = (seen.get(cid) ?? 0) + 1;
    seen.set(cid, n);
    return n;
  };
}

/** The shell's ⌘K, mounted around the page exactly as `App` mounts it.
 *
 *  The scene rail is gone: scene navigation is the palette now, and the page
 *  contributes its scenes to it through `usePaletteSource`. So the harness has
 *  to carry the palette, or a test cannot reach a second scene at all. */
export function PaletteHotkey() {
  usePaletteHotkey();
  return null;
}
export function withPalette(children: ReactNode) {
  return (
    <PaletteProvider>
      <PaletteHotkey />
      <CommandPalette />
      {children}
    </PaletteProvider>
  );
}

export function renderCampaign(initialEntry = "/campaigns/run") {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      {withPalette(<><Here />{playRoutes()}</>)}
    </MemoryRouter>,
  );
}

/** Open a scene the way the app does: ⌘K, type, pick.
 *
 *  `query` narrows the palette; `name` picks the row. They are separate because
 *  a scene title and a character name can both match a substring, and a test
 *  switching scenes must not silently open a dossier instead. */
export async function openScene(name: RegExp, query = "") {
  fireEvent.keyDown(window, { key: "k", metaKey: true });
  const input = await screen.findByRole("combobox", { name: /search/i });
  if (query) fireEvent.change(input, { target: { value: query } });
  // Matched on the row's LABEL, not on its accessible name: the name folds in
  // the meta line ("scene 2 · absorbed"), so an anchored title regex would
  // never match. The label is the scene title and nothing else.
  const row = await waitFor(() => {
    const rows = screen.getAllByRole("option").filter((r) => {
      const label = r.querySelector(".palette-label")?.textContent ?? "";
      return name.test(label) && /scene/i.test(r.textContent ?? "");
    });
    if (!rows.length) throw new Error(`no scene row matching ${name}`);
    return rows[0];
  });
  fireEvent.click(row);
}
