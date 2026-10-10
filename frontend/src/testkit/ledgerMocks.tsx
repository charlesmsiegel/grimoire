// The `vi.mock` factory every Ledger suite installs.
//
// A module of its own, importing nothing that is itself mocked, for the reason
// `campaignMocks` gives: a factory is hoisted above every import and can close
// over nothing, so each suite reaches this through a dynamic `import()` from
// inside its factory -- and if this module imported `../api/client`, that
// import would be waiting on the very factory that is waiting on this module.
// The defaults and the render helper that DO need the mocked `api` live next
// door in `ledgerHarness`.
import { vi } from "vitest";

/** LedgerView's whole api surface: the ledger's own reads and hand edits, and
 *  the continuity review's reads, runs and writes. The real module is spread
 *  underneath, so `ApiError` and `staleCurrent` stay the real ones -- a suite
 *  that throws an `ApiError` at the view is testing what the view does with
 *  the class it will actually meet. */
export async function ledgerApiMock() {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return {
    ...actual,
    api: {
      getCampaign: vi.fn(),
      campaignLedger: vi.fn(),
      campaignChanges: vi.fn(),
      campaignRelationshipHistory: vi.fn(),
      ledgerCreateThread: vi.fn(), ledgerSaveThread: vi.fn(), ledgerDeleteThread: vi.fn(),
      ledgerCreateCommitment: vi.fn(), ledgerSaveCommitment: vi.fn(),
      ledgerDeleteCommitment: vi.fn(),
      ledgerRecordFact: vi.fn(), ledgerSaveFact: vi.fn(), ledgerRetireFact: vi.fn(),
      ledgerDeleteFact: vi.fn(),
      ledgerSaveRelationship: vi.fn(), ledgerDeleteRelationship: vi.fn(),
      ledgerSaveChronicleLine: vi.fn(),
      // The continuity review (Slice D).
      getContinuity: vi.fn(), continuityCandidates: vi.fn(),
      reconcileContinuity: vi.fn(), awaitCampaignRun: vi.fn(),
      applyCandidate: vi.fn(), dismissCandidate: vi.fn(), restoreSuppression: vi.fn(),
      removeAlias: vi.fn(), removeLink: vi.fn(),
      // What the sweep asked (roadmap 01b).
      listCampaignPrompts: vi.fn(), getCampaignPrompt: vi.fn(),
      staleCurrent: actual.api.staleCurrent,
    },
  };
}
