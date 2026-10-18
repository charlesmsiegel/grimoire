// The shared fixtures, per-test defaults and render helpers for every suite
// that drives the campaign play view.
//
// `CampaignView.test.tsx` and `components/review/SceneReview.test.tsx` render
// the same page against the same mocked API; splitting the review out (#378)
// would otherwise have meant a second copy of two hundred lines of defaults,
// which is the kind of duplicate that drifts silently. This module imports the
// mocked `api` and so must never be reached from a `vi.mock` factory --
// `campaignMocks` is the half that can be, and holds the factories.
import { useRef, type ReactNode } from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { RunRegistryProvider } from "../runs/RunRegistryProvider";
import { Link, MemoryRouter, Routes, Route, useLocation } from "react-router-dom";
import CampaignView from "../routes/CampaignView";
import CommandPalette, { usePaletteHotkey } from "../components/CommandPalette";
import { PaletteProvider } from "../components/palette";
import { ShellStatusProvider, useShellStatus } from "../components/ShellStatus";
import ShortcutsHelp from "../shortcuts/ShortcutsHelp";
import type { ChatEvent } from "../api/stream";
import { api } from "../api/client";
import { getModels } from "../api/models";
import type { AbsorbPhase, InferenceSettings, RouteRow, SceneAbsorb, SceneMeta } from "../api/types";
import { fixture, type DeepPartial } from "./fixture";

export const ONE_SCENE: SceneMeta[] = [{ id: "s1", title: "Old", model: "", created: "", updated: "", date: "" }];
// The scene-break detector saying nothing (#84), so every suite that predates
// it renders the rail it always did and the play loop's per-post question is a
// no-op it never has to think about.
export const NO_SCENE_BREAK = {
  verdict: "" as const, reason: "", title: "", stale: false,
  posts: 0, score: 0, signals: [], every: 20, due: false,
};

/** `GET /campaigns/{cid}/inference` as the play view reads it: one provider,
 *  and the scene route resolving to it with nothing wrong. `scene` replaces
 *  fields of the scene route (its `resolves` and `problem`, say). */
export function campaignInference(scene: DeepPartial<RouteRow> = {}): InferenceSettings {
  return fixture({
    format: "2", newer: false, migration: { state: "done", reason: "", skipped: [] },
    roles: {}, preset_clear: "⁣none",
    routes: [{ key: "scene", label: "Scene turns", hint: "", tasks: ["chat", "retry", "regenerate", "extend"],
               operation: "generate", default_role: "primary", requires: [], campaign_scoped: true,
               use: "", pin: { provider: "", model: "", preset: "" }, preset: "", problem: null,
               role: "primary", inherits: null,
               resolves: { provider: "openrouter", provider_name: "OpenRouter", model: "campaign/model",
                           preset: "", preset_name: "", via: "role", scope: "global" },
               ...scene }],
    providers: [{ id: "openrouter", name: "OpenRouter", kind: "openrouter", preset: "openrouter",
                  usable: true, problem: null }],
    presets: [],
  });
}

// Stand-in `phases` for the absorb mocks that are about something else. What
// every one of them relies on is the single property named here: no phase was
// cut short by the time budget, so no budget notice renders.
export const PHASES_NONE_CUT: AbsorbPhase[] = [
  { name: "extraction", status: "ok", reason: null, attempted: true, budget_exhausted: false },
  { name: "identity", status: "skipped", reason: null, attempted: false, budget_exhausted: false },
  { name: "dossiers", status: "ok", reason: null, attempted: true, budget_exhausted: false },
  { name: "audit", status: "ok", reason: null, attempted: true, budget_exhausted: false },
];

// What GET /api/campaigns/:cid/scenes/:sid/response returns: the scene's own
// (here: empty) fields plus the SERVER-resolved bundle and its provenance.
export const RESPONSE_BUNDLE = {
  response_preset: "", style_id: "",
  length_reply_words: "", length_blocks: "", length_paragraphs: "",
  length_speakers: "", length_blocks_per_speaker: "",
  response_opening_words: "", response_opening_paragraphs: "",
  response_continuation_words: "", response_continuation_paragraphs: "",
  effective: { style_id: "", reply_words: 550, blocks: 5, paragraphs: 2, speakers: 4, blocks_per_speaker: 2,
    opening: { words: 400, paragraphs: 3 }, continuation: { words: 150, paragraphs: 2 } },
  provenance: { reply_words: { scope: "default", source: "default" },
    "continuation.words": { scope: "default", source: "default" } },
};

// What GET .../scenes/:sid/group returns for a scene nobody has configured.
export const DEFAULT_GROUP = {
  order: "directed" as const, order_list: [] as string[],
  talkativeness: {} as Record<string, number>, sitting_out: [] as string[], auto_rounds: 0,
};

/** What `api.absorbScene` resolves to now (#396).
 *
 *  The absorb is a detached run: the client starts it, polls it, and reads the
 *  review back off the store, so what reaches the panel is the stored review
 *  plus the GENERATION Cancel is addressed to. Wrapped here rather than at
 *  sixty call sites, so a suite says what its review IS and this says what the
 *  transport looks like.
 */
export function reviewResult(review: DeepPartial<SceneAbsorb>, generation = "gen1") {
  return { review: fixture<SceneAbsorb>(review), generation };
}

/** Make the next End scene answer with this review. */
export function absorbs(review: DeepPartial<SceneAbsorb>, generation = "gen1") {
  vi.mocked(api.absorbScene).mockResolvedValue(reviewResult(review, generation));
}

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
    if (vi.isMockFunction(fn)) fn.mockReset().mockResolvedValue(undefined);
  }
  vi.mocked(api.getCampaign).mockResolvedValue(fixture({ meta: { id: "run", name: "Run One", world: "w", world_name: "Saltmarch" }, body: "" }));
  vi.mocked(api.getWorld).mockResolvedValue(fixture({ meta: { id: "w", name: "Saltmarch" }, body: "", counts: {} }));
  vi.mocked(api.getTracker).mockResolvedValue({
    enabled: true, names: {}, keys: [], entries: {}, moods: {}, labels: {},
  });
  // One scene, not none: the play view mounts on a scene now, so an empty list
  // is a campaign whose play view does not exist rather than a useful default.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [] }));
  vi.mocked(api.createScene).mockResolvedValue({ id: "s1" });
  vi.mocked(api.renameScene).mockResolvedValue({ id: "s1", title: "New" });
  vi.mocked(api.deleteScene).mockResolvedValue({ ok: true });
  vi.mocked(api.branchScene).mockResolvedValue(fixture({ id: "s1-b", scene: { meta: {}, messages: [] } }));
  // Every streaming route ends a successful turn with a `done` frame — that is
  // how the client knows the backend finalized and persisted, rather than the
  // body merely reaching EOF. A default that resolved silently modelled a
  // truncated stream, so these mocks now send it.
  const streamsDone = async (...args: unknown[]) => {
    (args.find((a) => typeof a === "function") as ((e: ChatEvent) => void) | undefined)?.(
      { done: true });
  };
  vi.mocked(api.chat).mockImplementation(streamsDone);
  // The recovery surface, rebuilt for the same reason as everything above: a
  // reset mock resolves `undefined`, and recovery reading `.retained` off that
  // throws -- which its own `catch` then reads as "cannot tell", restoring a
  // prompt the transcript already has. A silent double-paste in the composer,
  // in every test that never mentions recovery.
  vi.mocked(api.findRun).mockResolvedValue({ run: null });
  vi.mocked(api.attachRun).mockResolvedValue(undefined);
  vi.mocked(api.attemptState).mockResolvedValue({ attempt: "", retained: true, run: null });
  vi.mocked(api.cancelAttempt).mockResolvedValue(
    { run: { id: "r", attempt_id: "a", state: "cancelled", next_index: 0 } });
  vi.mocked(api.cancelRun).mockResolvedValue(
    { run: { id: "r", attempt_id: "a", state: "cancelled", next_index: 0 } });
  vi.mocked(api.retry).mockImplementation(streamsDone);
  vi.mocked(api.regenerate).mockImplementation(streamsDone);
  vi.mocked(api.getAlternates).mockResolvedValue({ active: null, alternates: [] });
  vi.mocked(api.pickAlternate).mockResolvedValue({ ok: true });
  // "No arrows": a failed swipe read hides them, so the suites that predate
  // swipes render the gutter they were written against. The tests that want
  // arrows say what the read answers.
  vi.mocked(api.getResponseSwipe).mockRejectedValue(new Error("no swipe read"));
  vi.mocked(api.extendResponse).mockImplementation(streamsDone);
  vi.mocked(api.getRollProposal).mockResolvedValue({ record: null });
  vi.mocked(api.resolveProposal).mockImplementation(streamsDone);
  vi.mocked(api.getSceneChecks).mockResolvedValue({ actors: [] });
  vi.mocked(api.rollCheck).mockResolvedValue(fixture({ ok: true, resolution: {}, message: "" }));
  vi.mocked(api.getConfig).mockResolvedValue(fixture({ theme: "codex", system_prompt: "", quote_color: "off", user_label: "You", assistant_label: "Grimoire", active_connection_id: "openrouter", active_connection: { id: "openrouter", kind: "openrouter", name: "OpenRouter", model: "campaign/model" }, ready: true }));
  vi.mocked(api.getCampaignSettings).mockResolvedValue({ overrides: {}, inherited: {
    context_scan_depth: "8", context_budget: "12000", recap_depth: "5", archive_depth: "3",
    rolling_summary_every: "10", scene_break_every: "6", system_prompt: "", user_label: "You", assistant_label: "Grimoire",
  }, effective: {
    context_scan_depth: "8", context_budget: "12000", recap_depth: "5", archive_depth: "3",
    rolling_summary_every: "10", scene_break_every: "6", system_prompt: "", user_label: "You", assistant_label: "Grimoire",
  } });
  // The reroll popover's route picker (#77) and the header's model: the
  // campaign's inference view, with one provider and the scene route resolving
  // to it, and a provider that lists no model -- so an untouched popover is
  // Default and the suites that never open it are unaffected.
  vi.mocked(api.getCampaignInference).mockResolvedValue(campaignInference());
  vi.mocked(api.readConnectionCapabilities).mockImplementation(
    (_provider, need) => Promise.resolve({
      provider_preset: { id: "openrouter", label: "OpenRouter", kind: "openrouter", base_url: "",
                         url_locked: true, billing: "metered", reports_price: true,
                         always: [], possible: [], never: [] },
      need, reason: null, groups: { fits: [], unverified: [] }, hidden: [] }));
  // The regex test dialog's connections: one, the active one.
  vi.mocked(api.listConnections).mockResolvedValue(fixture([
    { id: "openrouter", kind: "openrouter", name: "OpenRouter", base_url: "",
      model: "campaign/model", effective_model: "campaign/model", post_process: "none", key_set: true, rev: "r1" }]));
  vi.mocked(api.readConnection).mockResolvedValue(
    fixture({ id: "openrouter", kind: "openrouter", name: "OpenRouter", base_url: "",
      model: "campaign/model", effective_model: "campaign/model", post_process: "none", key_set: true, rev: "r1",
      models: [], fetched_at: "" }));
  vi.mocked(api.editMessage).mockResolvedValue({ ok: true });
  vi.mocked(api.setExcluded).mockResolvedValue({ ok: true });
  vi.mocked(api.getReplay).mockResolvedValue(null);
  vi.mocked(api.replayPreview).mockResolvedValue(
    { posts: 1, turns: 1, threshold: 10, fork: false, blocked: "" });
  vi.mocked(api.getCast).mockResolvedValue([]);
  vi.mocked(api.addToCast).mockResolvedValue({ ok: true });
  // "The turn changed nobody", so no cast-change chips render: these suites are
  // about the transcript and the panels around it, and the suggestion strip has
  // its own tests in components/play/CastChanges.test.tsx.
  vi.mocked(api.castChanges).mockResolvedValue({ enter: [], leave: [], unknown: [] });
  // The embedded SceneInspector renders the suggestion strip for real. Without
  // this the strip calls `undefined`, and every inspector mount in this file
  // takes an exception path — extra async work in the suite least able to
  // afford it (#351).
  vi.mocked(api.getSuggestions).mockResolvedValue([]);
  vi.mocked(api.removeFromCast).mockResolvedValue({ ok: true });
  vi.mocked(api.getSceneLocation).mockResolvedValue({ current: null, visited: [] });
  vi.mocked(api.getSceneContext).mockResolvedValue({ model: "m", total_tokens: 0,
    dropped_tokens: 0, budget_tokens: 0, sections: [] });
  vi.mocked(api.getSceneFocus).mockResolvedValue({ text: "Let Mara choose.", status: "ok" });
  vi.mocked(api.setSceneFocus).mockResolvedValue({ text: "Let Seraphine choose.", status: "ok" });
  vi.mocked(api.getPromptLayout).mockResolvedValue({ enabled: false, sections: [] });
  // No pins (#129), so the inspector's rail here is the one these suites were
  // written against.
  vi.mocked(api.getPins).mockResolvedValue({ pins: [] });
  // Empty, so the inspector's Briefing section (#118) renders nothing here and
  // these suites keep asserting on the rail they were written against.
  vi.mocked(api.sceneBriefing).mockResolvedValue({
    focus: [], plot: [], commitments: [], relationships: [], last_time: null });
  vi.mocked(api.listScenePrompts).mockResolvedValue({ entries: [] });
  vi.mocked(api.getCalendarConfig).mockResolvedValue(fixture({
    primary: { provider: "gregorian", region: "US", custom_holidays: [], anchor: null },
    secondary: null, confirmed: true }));
  vi.mocked(api.getCalendarProviders).mockResolvedValue({ providers: [
    { id: "gregorian", name: "Gregorian" }, { id: "hebrew", name: "Hebrew" },
  ] });
  vi.mocked(api.getSceneDatetime).mockResolvedValue(fixture({ current: null, history: [] }));
  vi.mocked(api.listStyles).mockResolvedValue([]);
  vi.mocked(api.getSceneResponse).mockResolvedValue(RESPONSE_BUNDLE);
  vi.mocked(api.getSceneGroup).mockResolvedValue(DEFAULT_GROUP);
  vi.mocked(api.listCharacters).mockResolvedValue([]);
  vi.mocked(api.listPCs).mockResolvedValue([]);
  vi.mocked(api.listCampaignPCs).mockResolvedValue([]);
  vi.mocked(api.listAppearances).mockResolvedValue([]);
  vi.mocked(api.listEntityImages).mockResolvedValue([]);
  vi.mocked(api.listEntities).mockResolvedValue([]);
  // No budget, so the banner is absent and these suites keep asserting on the
  // page they were written against. The tests that want one set it themselves.
  vi.mocked(api.getCampaignBudget).mockResolvedValue(
    { limit_usd: 0, period: "monthly", level: "off", warn_fraction: 0.8 });
  vi.mocked(api.getSceneUsage).mockResolvedValue({
    campaign: "run", scene: "s1", since: "", until: "", clamped: false,
    generated_at: "",
    totals: { calls: 0, errors: 0, prompt_tokens: 0, completion_tokens: 0,
              total_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0,
              cost_usd: 0, estimated_usd: 0, modelled_usd: 0, priced_calls: 0,
              unpriced_calls: 0, subscription_calls: 0, modelled_calls: 0,
              unmetered_calls: 0, duration_ms: 0 },
    // No per-post spend either, for the reason the budget above is off: these
    // suites assert on the transcript they were written against, and a cost
    // chip on every player post is a string in it. The tests that want one set
    // it themselves.
    by_task: [], by_post: [], turns: [], listed: 0, truncated: false });
  // No author's notes: the inspector section draws no count.
  vi.mocked(api.getAuthorsNotesNext).mockResolvedValue({ turn: 1, count: 0, notes: [] });
  vi.mocked(api.getAuthorsNotes).mockResolvedValue({ campaign: null, scenes: {}, characters: {} });
  vi.mocked(getModels).mockResolvedValue([]);
  // `{review, generation}`, not the review alone (#396): the absorb is a
  // detached run now, and the client's `absorbScene` starts it, polls it, and
  // reads the result back off the store -- so what it hands the panel is the
  // stored review plus the generation Cancel is addressed to.
  vi.mocked(api.absorbScene).mockResolvedValue(fixture({
    generation: "gen1",
    review: {
    one_line: "They met.", summary: "A met B.", keywords: ["salt"],
    timeline_events: [], cast: [], location: "", date: "",
    mechanics: { status: "ok", reason: null, warnings: [], dropped: [] },
    commit_token: "tok",
    dossiers: { status: "skipped", reason: null, proposed: [], failed: [], skipped: [] },
    voice: { status: "skipped", reason: null, checked: [], flagged: [], unjudged: [], failed: [], skipped: [] },
    phases: PHASES_NONE_CUT,
    edits: [{ id: "character_state:seraphine", kind: "character_state",
      target: { kind: "characters", id: "seraphine" }, label: "Seraphine — current state",
      field: "current_state", before: "Wary.", after: "Loyal now.", authored: false }] } }));
  // No stored review by default: every mount asks, and a mock resolving
  // `undefined` would throw inside the adoption pass -- whose own catch then
  // reads as "ask again later", quietly costing every suite its recovery path.
  vi.mocked(api.pendingReview).mockResolvedValue(
    { review: null, generation: null, stale: null });
  vi.mocked(api.liveReview).mockResolvedValue(null);
  vi.mocked(api.discardReview).mockResolvedValue({ removed: true, stopped: 0 });
  vi.mocked(api.awaitRun).mockResolvedValue(
    { id: "r", attempt_id: "a", state: "landed", next_index: 0 });
  vi.mocked(api.saveChronicle).mockResolvedValue({ id: "s1", one_line: "They met.",
    summary: "A met B.", keywords: ["salt"], cast: [], location: "", date: "", absorbed: "t",
    applied: [], failures: [] });
  vi.mocked(api.getChronicle).mockResolvedValue([]);
  vi.mocked(api.campaignChanges).mockResolvedValue([]);
  vi.mocked(api.getIncoming).mockResolvedValue([]);
  vi.mocked(api.listDiverged).mockResolvedValue([]);
  vi.mocked(api.getComposition).mockResolvedValue({ rows: [] });
  vi.mocked(api.setSyncPin).mockResolvedValue({ pinned: [] });
  vi.mocked(api.acceptIncoming).mockResolvedValue({ ok: true });
  vi.mocked(api.rejectIncoming).mockResolvedValue({ ok: true });
  vi.mocked(api.campaignProvenance).mockResolvedValue({});
  vi.mocked(api.campaignLedger).mockResolvedValue(fixture({ plot: [], commitments: [], facts: [], chronicle: [] }));
  // A pack is bound by default, so the dice button is present for the tests
  // that predate it being conditional.
  vi.mocked(api.getCampaignModule).mockResolvedValue(
    { setting: "pool-basic", resolved: "pool-basic", source: "campaign" });
  vi.mocked(api.setCampaignModule).mockResolvedValue({ ok: true });
  vi.mocked(api.listModules).mockResolvedValue(fixture([{ id: "pool-basic", name: "Pool Basic" }]));
  vi.mocked(api.getCampaignSheets).mockResolvedValue(fixture({ coverage: {} }));
  vi.mocked(api.getRollingSummary).mockResolvedValue({
    summary: "", at: 0, total: 0, stale: false, every: 10, due: false });
  vi.mocked(api.refreshRollingSummary).mockResolvedValue({
    summary: "", at: 0, total: 0, stale: false, every: 10, due: false,
    refreshed: false });
  vi.mocked(api.getSceneBreak).mockResolvedValue(NO_SCENE_BREAK);
  vi.mocked(api.askSceneBreak).mockResolvedValue({ ...NO_SCENE_BREAK, asked: false });
  vi.mocked(api.dismissSceneBreak).mockResolvedValue(NO_SCENE_BREAK);
  vi.mocked(api.getEffectiveQuickReplies).mockResolvedValue({ replies: [] });
}

// The two paths the play view answers to, nested exactly as App.tsx nests them
// (#87): one CampaignView instance serves both, so switching campaigns never
// remounts it whether or not the URL carries a scene.
export function playRoutes(ready = true) {
  return (
    <Routes>
      {/* Mirrors `App` exactly, and that is the point: `/scenes` is the LIST
          and `/scenes/:sid` is play. A harness that kept the old nesting would
          go on passing against a routing shape the app no longer has, which is
          worse than failing. `ScenesStub` stands in for the list so a test that
          lands there says so instead of rendering nothing. */}
      <Route path="/campaigns/:cid/scenes" element={<ScenesStub />} />
      <Route path="/campaigns/:cid/scenes/:sid" element={<CampaignView ready={ready} />} />
      {/* The review's own address, same element, exactly as `App` mounts it. */}
      <Route path="/campaigns/:cid/scenes/:sid/wrap-up"
             element={<CampaignView ready={ready} />} />
    </Routes>
  );
}

/** Stands in for the scenes list: the play harness is about the transcript,
 *  and a test that ends up on the list should be able to see that it did. */
function ScenesStub() {
  return <div data-testid="scenes-list" />;
}

// Reads back the URL the view has navigated itself to -- and every one it
// passed through, since the view replaces its own address and a test may need
// to know a hop happened -- and offers the two navigations the shell makes
// that this harness has no rail or link to make: the rail's Wrap-up row, which
// goes to the review's own address, and a link to the scene's.
export function Here() {
  const { pathname } = useLocation();
  const visited = useRef<string[]>([]);
  if (visited.current[visited.current.length - 1] !== pathname) visited.current.push(pathname);
  const scene = pathname.replace(/\/wrap-up$/, "");
  return (
    <>
      <span data-testid="here">{pathname}</span>
      <span data-testid="visited">{visited.current.join(" ")}</span>
      <Link data-testid="go-wrap-up" to={scene + "/wrap-up"}>wrap up</Link>
      <Link data-testid="go-scene" to={scene}>the scene</Link>
    </>
  );
}
export const here = () => screen.getByTestId("here").textContent;
export const visited = () => (screen.getByTestId("visited").textContent ?? "").split(" ");

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
      {/* The other half of the shell's keyboard, mounted where `App` mounts
       *  it (#193). Renders nothing until `?`, and registers the bindings the
       *  page's own are listed beside. */}
      <ShortcutsHelp />
      {children}
    </PaletteProvider>
  );
}

/** Renders the play view on a scene.
 *
 *  The default entry names one, because that is now the only way the play view
 *  is reached: `/campaigns/:cid/scenes` is the LIST. A test that wants the
 *  empty-campaign case renders the list instead, and one that wants a specific
 *  scene passes its url.
 */
export function renderCampaign(initialEntry = "/campaigns/run/scenes/s1",
                               { shell = false }: { shell?: boolean } = {}) {
  const page = (
    // The provider ABOVE the router, mirroring `main.tsx`. Without it
    // `useRunRegistry` falls back to its no-op stand-in and every recovery
    // path silently does nothing -- which is the exact shape of the bug this
    // harness is supposed to be able to catch.
    <RunRegistryProvider>
      {/* Same synchronous navigation policy as main.tsx on React 18. */}
      <MemoryRouter initialEntries={[initialEntry]} useTransitions={false}>
        {withPalette(<><Here />{playRoutes()}</>)}
      </MemoryRouter>
    </RunRegistryProvider>
  );
  // `shell`: the chrome's status, mounted as `App` mounts it, with what the
  // page published to it readable (`shellModel`, `shellReady`). Opt-in, so
  // the suites that never look at the header have no extra text to trip on.
  return render(shell
    ? <ShellStatusProvider><ShellReading />{page}</ShellStatusProvider>
    : page);
}

/** What the page has published to the header: the model its scene turns run
 *  on and whether that can send ("null" when it has no opinion). */
function ShellReading() {
  const { sceneModel, sceneReady } = useShellStatus();
  return (
    <>
      <span data-testid="shell-model">{sceneModel ?? "null"}</span>
      <span data-testid="shell-ready">{String(sceneReady)}</span>
    </>
  );
}
export const shellModel = () => screen.getByTestId("shell-model").textContent;
export const shellReady = () => screen.getByTestId("shell-ready").textContent;

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
