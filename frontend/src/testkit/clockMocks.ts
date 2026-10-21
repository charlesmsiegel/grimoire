// The `vi.mock` factory every ClockPanel suite installs.
//
// Deliberately a module of its own, importing nothing that is itself mocked --
// the same split as `campaignMocks`: a factory is hoisted above every import
// and can close over nothing, so each suite reaches this through a dynamic
// `import()` from inside its factory. The fixtures and helpers that DO need the
// mocked `api` live next door in `clockHarness`.
import { vi } from "vitest";

/** The api surface the clock panel drives. */
export async function clockApiMock() {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, api: {
    getCampaignClock: vi.fn(),
    previewAdvance: vi.fn(),
    advanceTime: vi.fn(),
    // CalendarDatePicker (the "skip to a date" control) fetches months.
    getCalendarMonths: vi.fn(),
    // Read only to know whether a calendar has been chosen for this campaign.
    getCalendarConfig: vi.fn(),
    // The checkpoint the panel offers before a large skip (#107).
    forkCampaign: vi.fn(),
    // Read only to name a fork the server REPLAYED, which carries its own name
    // rather than the one this request submitted (#409).
    getCampaign: vi.fn(),
  } };
}
