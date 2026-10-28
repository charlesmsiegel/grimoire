import type { ComponentProps } from "react";
import { vi } from "vitest";

// Mock factories must never runtime-import the client they replace.
export async function sceneInspectorApiMock() {
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
      getSceneFocus: vi.fn(), setSceneFocus: vi.fn(), getPromptLayout: vi.fn(),
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
}

export function sceneInspectorModelsMock() { return { getModels: vi.fn() }; }

export function responseTargetsMock() { return {
  ResponseTargetsPicker: ({ scope, cid, sid }: ComponentProps<typeof import("../components/ResponseTargetsPicker").ResponseTargetsPicker>) => (
    <div data-testid="response-preset-picker" data-scope={scope} data-cid={cid} data-sid={sid} />
  ),
}; }

export function authorsNotesMock() { return {
  AuthorsNotesPanel: ({ cid, sid }: ComponentProps<typeof import("../components/AuthorsNotesPanel").AuthorsNotesPanel>) => (
    <div data-testid="authors-notes-panel" data-cid={cid} data-sid={sid} />
  ),
}; }
