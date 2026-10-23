/** The API client's one public face: `api`, the error classes, the run-poll
 *  constants, and every payload type (`./types`).
 *
 *  It used to hold all of it, nearly four thousand lines. The calls now live
 *  in `./endpoints/`, one module per domain, over a shared core: `./transport`
 *  (how a request is made), `./freshness` (in-flight sharing, remembered
 *  reads, the config cache, and when each is forgotten), `./notify` (the
 *  event-channel announcements), `./paths` and `./runs` (detached runs and
 *  the draft helpers). None of them imports this module; this module only
 *  composes and re-exports them.
 *
 *  Import from here, never from the modules behind it. Suites mock this
 *  module wholesale (`vi.mock("../api/client")`), and a component that reached
 *  past it would talk to the real network under a test that thinks it has
 *  replaced it. */
// The two a page asks before it paints an answer (`useRevalidated`, which
// imports them from the leaf so a mock of this module cannot take them away).
export { issuedEpoch, memoEpoch } from "./memoEpoch";
// `errorText` and `isOffline` used to live here, next to `ApiError`. They are
// in `./errors` now — a leaf with no imports of its own, for the reason its
// docstring gives: the components that render an error are tested against a
// mock that replaces THIS module wholesale.

// Re-exported so every existing `from "../api/client"` import keeps working.
export * from "./types";
export { ApiError, RefreshRefused, RunEndedError } from "./transport";
export { invalidateConfigCache, noteRunSettled } from "./freshness";
export { READ_RETRIES, RUN_POLL_MISSES, RUN_POLL_MS, RUN_POLL_QUICK, RUN_POLL_SLOW_MS,
  STREAM_REATTACH_TRIES, type DraftScope } from "./runs";
export type { ImageOpts } from "./paths";

import { sceneRunApi } from "./runs";
import { appApi } from "./endpoints/app";
import { campaignsApi } from "./endpoints/campaigns";
import { charactersApi } from "./endpoints/characters";
import { ledgerApi } from "./endpoints/ledger";
import { mechanicsApi } from "./endpoints/mechanics";
import { reviewApi } from "./endpoints/review";
import { scenesApi } from "./endpoints/scenes";
import { settingsApi } from "./endpoints/settings";
import { worldsApi } from "./endpoints/worlds";
import { expressionsApi } from "./endpoints/expressions";

/** Every call the backend answers, as one object.
 *
 *  Composed from the domain modules rather than written out here, but still
 *  one object on purpose: callers write `api.x(...)`, suites mock it as a unit,
 *  and `vi.spyOn(api, "x")` replaces a property of this object. The domains
 *  never share a key, so the spread order decides nothing. */
export const api = {
  ...expressionsApi,
  ...appApi,
  ...worldsApi,
  ...charactersApi,
  ...campaignsApi,
  ...ledgerApi,
  ...scenesApi,
  ...sceneRunApi,
  ...reviewApi,
  ...settingsApi,
  ...mechanicsApi,
};
