// The `vi.mock` factories every campaign-view suite installs.
//
// Deliberately a module of its own, importing nothing that is itself mocked. A
// factory is hoisted above every import and can close over nothing, so each
// suite reaches these through a dynamic `import()` from inside its factory --
// and if this module imported `../api/client`, that import would be waiting on
// the very factory that is waiting on this module. The defaults and helpers
// that DO need the mocked `api` live next door in `campaignHarness`.
import { vi } from "vitest";

/** The api surface every campaign-view suite drives. `vi.mock` factories are
 *  hoisted and cannot close over anything, so each suite delegates to this
 *  through a dynamic import instead of restating a hundred lines of `vi.fn()`.
 */
export async function campaignApiMock() {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    api: {
      getCampaign: vi.fn(),
      getWorld: vi.fn(),
      listScenes: vi.fn(),
      getScene: vi.fn(),
      createScene: vi.fn(),
      renameScene: vi.fn(),
      deleteScene: vi.fn(),
      forkCampaign: vi.fn(),
      chat: vi.fn(),
      retry: vi.fn(),
      regenerate: vi.fn(),
      getAlternates: vi.fn(), pickAlternate: vi.fn(),
      roll: vi.fn(),
      getRollProposal: vi.fn(), resolveProposal: vi.fn(),
      getSceneChecks: vi.fn(), rollCheck: vi.fn(),
      getConfig: vi.fn(),
      editMessage: vi.fn(), deleteMessagesFrom: vi.fn(),
      // Retcon and its replay (#78/#79/#80). `getReplay` answers null for every
      // suite here, which is what makes the embedded ReplayPanel render nothing
      // -- the tests that want it say so.
      retconMessage: vi.fn(), getReplay: vi.fn(), replayPreview: vi.fn(),
      startReplay: vi.fn(), replayTurn: vi.fn(), acceptReplay: vi.fn(),
      cancelReplay: vi.fn(),
      absorbScene: vi.fn(), saveChronicle: vi.fn(), getChronicle: vi.fn(), retryAudit: vi.fn(),
      retryDossiers: vi.fn(),
      // consumed by the embedded SceneInspector
      getCast: vi.fn(), getSceneLocation: vi.fn(), getSceneContext: vi.fn(),
      getPins: vi.fn(), setPin: vi.fn(), removePin: vi.fn(),
      sceneBriefing: vi.fn(),
      listScenePrompts: vi.fn(), getScenePrompt: vi.fn(),
      // Resolves to "no weather" so the widget renders nothing: these suites
      // assert on the rest of the inspector, not the sky.
      getSceneWeather: vi.fn(() => Promise.resolve({ weather: null, location: null, native: null })),
      getCastDetail: vi.fn(), readEntity: vi.fn(),
      addToCast: vi.fn(), removeFromCast: vi.fn(),
      // the cast column's in-turn cast-change scan (#97) and the inspector's
      // card-text suggestion strip (#96) — separate scans, one dismissal list
      castChanges: vi.fn(), createEmergentCast: vi.fn(), dismissSuggestion: vi.fn(),
      getSuggestions: vi.fn(),
      getCalendarConfig: vi.fn(), setCalendarConfig: vi.fn(), getCalendarProviders: vi.fn(),
      getSceneDatetime: vi.fn(), setSceneDatetime: vi.fn(), getCalendarMonths: vi.fn(),
      listStyles: vi.fn(),
      listResponsePresets: vi.fn(), getSceneResponse: vi.fn(),
      // the Mechanics panel's own reads/writes: CampaignView hosts
      // MechanicsConfig, and gates the dice button on the same binding
      getCampaignModule: vi.fn(), setCampaignModule: vi.fn(),
      listModules: vi.fn(), getCampaignSheets: vi.fn(),
      listCharacters: vi.fn(), listPCs: vi.fn(), listCampaignPCs: vi.fn(),
      campaignChanges: vi.fn(),
      getIncoming: vi.fn(),
      campaignLedger: vi.fn(),
      campaignProvenance: vi.fn(),
      getCasefile: vi.fn(),
      listAppearances: vi.fn(), listEntityImages: vi.fn(), listEntities: vi.fn(),
      getRollingSummary: vi.fn(), refreshRollingSummary: vi.fn(),
      getSceneBreak: vi.fn(), askSceneBreak: vi.fn(), dismissSceneBreak: vi.fn(),
      // Cost (#153): the page's budget banner, and the inspector's Cost section.
      getCampaignBudget: vi.fn(), setCampaignBudget: vi.fn(), getSceneUsage: vi.fn(),
      actorImageUrl: (_sc: { id: string }, k: string, a: string, v: string, n: string) =>
        `/img/${k}/${a}/${v}/${n}`,
      entityImageUrl: () => "/loc-img",
    },
  };
}

/** The child components with tests and API calls of their own. Stubbed so a
 *  campaign-view suite is measuring the page rather than five other pages. */
export const componentStubs = {
  CastPanel: () => ({
    CastPanel: ({ initialPrompt, onSceneRenamed, onSeeded }: any) => (
      <div data-testid="cast-panel">
        {initialPrompt ?? ""}
        <button onClick={() => onSceneRenamed?.("s10")}>stub-datestamp</button>
        <button onClick={() => onSeeded?.()}>stub-seeded</button>
      </div>
    ),
  }),
  NewSceneChooser: () => ({
    NewSceneChooser: ({ onCreated, onClose }: any) => (
      <div data-testid="scene-chooser">
        <button onClick={() => onCreated("s9", "A premise")}>stub-pick</button>
        <button onClick={() => onClose()}>stub-close</button>
        {/* Stands in for Escape/backdrop dismissing the chooser AFTER a soft
            failure salvaged a real scene -- NewSceneChooser reports that
            scene's id to onClose in exactly this situation (see its own
            tests); every other dismissal above calls onClose with no id. */}
        <button onClick={() => onClose("s9")}>stub-close-salvaged</button>
      </div>
    ),
  }),
  CalendarConfig: () => ({ CalendarConfig: () => <div data-testid="calendar-config" /> }),
  // Same reason as the three above, and it is the one that turned up in this
  // file's flake budget rather than in review: ReplayPanel fetches its session
  // on mount, in EVERY test that renders the transcript, and it has a test file
  // of its own that drives the real thing. What CampaignView owns is which post
  // the gutter hands it, so that is all the stub reports.
  ReplayPanel: () => ({
    ReplayPanel: ({ startAt, onStartHandled, onForked, onChanged, latch }: any) => (
      <div data-testid="replay-panel" data-start-at={startAt ?? ""}>
        <button onClick={() => onStartHandled()}>stub-replay-close</button>
        <button onClick={() => onForked("forked")}>stub-replay-forked</button>
        <button onClick={() => onChanged()}>stub-replay-changed</button>
        <button onClick={() => latch()}>stub-replay-latch</button>
      </div>
    ),
  }),
  ResponsePresetPicker: () =>
    ({ ResponsePresetPicker: () => <div data-testid="response-preset-picker" /> }),
};
