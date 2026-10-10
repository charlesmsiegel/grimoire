// The shared fixtures, per-test defaults and render helper for every suite
// that drives the Ledger page.
//
// `routes/LedgerView.test.tsx` (the ledger's own sections) and
// `routes/LedgerContinuity.test.tsx` (the continuity review inside it) render
// the same page against the same mocked API. This module imports the mocked
// `api` and so must never be reached from a `vi.mock` factory --
// `ledgerMocks` is the half that can be, and holds the factory.
import { render } from "@testing-library/react";
import { vi, type Mock } from "vitest";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import LedgerView from "../routes/LedgerView";
import CommandPalette, { usePaletteHotkey } from "../components/CommandPalette";
import { PaletteProvider } from "../components/palette";
import {
  api, type ContinuityCandidates, type ContinuityState, type Ledger,
} from "../api/client";

/** A ledger with nothing in it. */
export const EMPTY_LEDGER: Ledger = {
  plot: [], commitments: [], facts: [], retired: [], relationships: [], chronicle: [],
  stale_after_days: 30,
};

/** The candidates read with no findings and no sweep running. */
export const EMPTY_CANDIDATES: ContinuityCandidates = {
  generated: "", matching: "basic",
  diagnostics: {
    dangling_aliases: [], broken_links: [], hidden_links: [], unreadable: [],
    malformed: [], cache_malformed: false,
  },
  run: null, names: {}, scenes: [], candidates: [],
};

/** `GET /continuity` for a campaign with no aliases, links or dismissals. */
export const EMPTY_CONTINUITY: ContinuityState = {
  aliases: [], links: [], raw_links: [], suppressions: [],
  diagnostics: { dangling_aliases: [], broken_links: [], hidden_links: [], unreadable: [] },
  malformed: [], unreadable: [], matching: "basic",
};

const mocked = (fn: unknown) => fn as Mock;

/** Everything a Ledger suite needs reset and re-defaulted per test. */
export function installLedgerMocks() {
  vi.clearAllMocks();
  mocked(api.getCampaign).mockResolvedValue({ meta: { id: "run", name: "Saltmarch" }, body: "" });
  mocked(api.campaignLedger).mockResolvedValue(EMPTY_LEDGER);
  mocked(api.campaignChanges).mockResolvedValue([]);
  mocked(api.campaignRelationshipHistory).mockResolvedValue([]);
  for (const k of ["ledgerCreateThread", "ledgerSaveThread", "ledgerDeleteThread",
                   "ledgerCreateCommitment", "ledgerSaveCommitment", "ledgerDeleteCommitment",
                   "ledgerRecordFact", "ledgerSaveFact", "ledgerRetireFact", "ledgerDeleteFact",
                   "ledgerSaveRelationship", "ledgerDeleteRelationship",
                   "ledgerSaveChronicleLine"] as const) {
    mocked(api[k]).mockResolvedValue({ ok: true, id: "new" });
  }
  mocked(api.continuityCandidates).mockResolvedValue(EMPTY_CANDIDATES);
  mocked(api.getContinuity).mockResolvedValue(EMPTY_CONTINUITY);
  mocked(api.reconcileContinuity).mockResolvedValue({
    sweep: "full", matching: "basic", embedding: "off", llm: "off", reason: "",
    candidates: 0, adjudicated: 0, pairs_capped: false, superseded: false,
    continuity: "ok", follow_on: false,
  });
  mocked(api.awaitCampaignRun).mockResolvedValue(
    { id: "r", attempt_id: null, state: "landed", next_index: 0 });
  mocked(api.listCampaignPrompts).mockResolvedValue({ entries: [] });
  mocked(api.getCampaignPrompt).mockResolvedValue(null);
  for (const k of ["applyCandidate", "dismissCandidate", "restoreSuppression",
                   "removeAlias", "removeLink"] as const) {
    mocked(api[k]).mockResolvedValue({ ok: true });
  }
}

/** Where the router is now, for a test that asserts an address. */
export function Here() {
  const location = useLocation();
  return <div data-testid="here">{location.pathname}</div>;
}

function Hotkey() { usePaletteHotkey(); return null; }

/** Render the Ledger at `path`, under the route App.tsx mounts it on. */
export function renderLedger(path = "/campaigns/run/ledger") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <PaletteProvider>
        <Hotkey />
        <CommandPalette />
        <Here />
        <Routes>
          <Route path="/campaigns/:cid/ledger/*" element={<LedgerView />} />
          <Route path="/campaigns/:cid" element={<div>the play view</div>} />
        </Routes>
      </PaletteProvider>
    </MemoryRouter>,
  );
}
