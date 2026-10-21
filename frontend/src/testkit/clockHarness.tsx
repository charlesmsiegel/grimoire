// Fixtures and helpers shared by the ClockPanel suites (`ClockPanel.test.tsx`,
// `ClockPanel.checkpoint.test.tsx`, `ClockPanel.revision.test.tsx`). Each suite
// installs `clockMocks`' factory for `../api/client`; this module imports the
// mocked `api` and so must never be reached from inside that factory.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { ClockPanel } from "../components/ClockPanel";
import { api, type AdvanceDigest, type ForkReport } from "../api/client";
import { fixture } from "./fixture";

export const CLOCK = {
  now: "2026-12-24", friendly: "24 December 2026",
  log: [{ from: "", to: "2026-12-24", reason: "the caravan sets out", at: "2026-12-01T10:00:00Z" }],
  revision: "rev-1",
};

export const DIGEST: AdvanceDigest = {
  from: "2026-12-24", to: "2026-12-27",
  from_friendly: "24 December 2026", to_friendly: "27 December 2026",
  elapsed_days: 3, backward: false, truncated: false,
  holidays: [{ name: "Christmas Day", native: "2026-12-25", friendly: "25 December 2026", in_days: 1 }],
  birthdays: [{ name: "Seraphine", age: 36, native: "2026-12-26", friendly: "26 December 2026" }],
  open_threads: [{ id: "debt", title: "The moneylender's debt", status: "open",
                   last_scene: "001--a", latest_beat: "Interest accrues.",
                   aging: { state: "stale", days_since: 53, days_over: null, due_in: null } }],
  // Scheduled events (#101) and the commitments this move ages (#103): both
  // arrive on every digest, so the fixture carries both.
  events: [{ id: "coronation", name: "The coronation", date: "2026-12-26",
             friendly: "26 December 2026", note: "", fired: null, passed: false,
             in_days: 2 }],
  commitments: [{ id: "the-debt", title: "Repay the moneylender", kind: "promise",
                  status: "open", due: "2026-12-20", last_scene: "001--a",
                  latest_beat: "Mara swore it.",
                  aging: { state: "overdue", days_since: 53, days_over: 7, due_in: null } }],
  aging: { overdue: 1, stale: 1, stale_after: 30 },
  // The checkpoint nudge (#107) rides every digest. Three days is not a large
  // skip, so the default fixture is the ungated path.
  fork: false, fork_threshold: 30,
};

/** The same move, over the threshold — what the checkpoint prompt answers to. */
export const BIG: AdvanceDigest = { ...DIGEST, elapsed_days: 90, to: "2027-03-24",
              to_friendly: "24 March 2027", fork: true };

export const FORK_REPORT: ForkReport = { id: "before-24-march-2027", from_scene: "",
                      removed_scenes: [], records: 0, refused: [], failed: [],
                      replayed: false };

/** The stable half of a checkpoint's idempotency key: the operation it names.
 *
 *  The key ends in a fingerprint of the digest the reader was shown, so that a
 *  move whose contents changed under an unmoved token is a different operation
 *  (#409). Tests assert this prefix and compare whole keys to each other rather
 *  than pinning the hash, which is the panel's business and not a contract.
 */
export const keyFor = (to: string, rev: string) => `checkpoint:${to}:${rev}:`;

/** The campaign's write token (#409), as every pricing below hands one back.
 *
 *  One value for the whole suite because almost every test is about something
 *  else and only needs the panel to have a token to carry; the tests that are
 *  about the token say so by using a second one. */
export const REV = "rev-1";

/** Every suite's `beforeEach`: fresh mocks answering with the fixtures above. */
export function resetClockMocks() {
  vi.clearAllMocks();
  vi.mocked(api.getCampaignClock).mockResolvedValue(CLOCK);
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: REV, digest: DIGEST });
  vi.mocked(api.advanceTime).mockResolvedValue(
    { ok: true, moved: true, now: "2026-12-27", friendly: "27 December 2026",
      digest: DIGEST, fired: DIGEST.events });
  vi.mocked(api.getCalendarMonths).mockResolvedValue({ months: [] });
  vi.mocked(api.forkCampaign).mockResolvedValue(FORK_REPORT);
  vi.mocked(api.getCampaign).mockResolvedValue(
    fixture({ meta: { name: "Before 24 March 2027" }, body: "" }));
  vi.mocked(api.getCalendarConfig).mockResolvedValue(fixture({
    primary: { provider: "gregorian", region: "US", custom_holidays: [], anchor: null },
    secondary: null, confirmed: true }));
}

/** Fill in a reason and ask to advance, from the default one-day state. */
export async function askToAdvance(days?: string) {
  render(<ClockPanel cid="c" />);
  await screen.findByText(/Now: 24 December 2026/);
  if (days !== undefined) {
    fireEvent.change(screen.getByLabelText("Days"), { target: { value: days } });
  }
  fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "a season passes" } });
  fireEvent.click(screen.getByText("Advance time"));
  // Confirming always prices the move first, so this is the settle every caller
  // needs: without it the helper returns through an `await` with the preview
  // still in flight, and the assertion after it runs against a half-built page
  // (CLAUDE.md — an `await` means the page has SETTLED).
  await waitFor(() => expect(api.previewAdvance).toHaveBeenCalled());
}
