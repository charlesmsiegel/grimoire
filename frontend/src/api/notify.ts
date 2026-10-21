/** The announcements a landed mutation makes on the app's event channels,
 *  hung off the mutators (`.then(notifyX)`) rather than their callers so no
 *  view can forget one. Split out of `client.ts`. */
import { invalidateConfigCache } from "./freshness";
import { ApiError } from "./transport";
import { artChanged, campaignsChanged, configChanged, shellChanged } from "../appEvents";

/** Announce a campaign mutation once it has actually landed, passing the
 *  response through untouched. Sits on the three campaign mutators rather than
 *  in their callers so no view can forget: the persistent sidebar has no other
 *  way to learn that a rename on `/` changed a row it is showing. A rejected
 *  request never reaches here, so a failed delete cannot blank the rail. */
export function notifyCampaigns<T>(result: T): T {
  campaignsChanged();
  return result;
}

export function notifyArt<T>(result: T): T {
  artChanged();
  return result;
}

export function notifyArtOnLanded(err: unknown): never {
  if (err instanceof ApiError && err.kind === "description_cleanup_incomplete" && err.body?.committed === true) {
    artChanged();
  }
  throw err;
}

/** Same shape for the config channel. Hung off the calls that already
 *  invalidate the config cache: invalidating only guarantees the *next* read
 *  is fresh, and the status bar has no reason to read again until something
 *  tells it to -- which is exactly the stale window this closes. */
export function notifyConfig<T>(result: T): T {
  configChanged();
  return result;
}

/** `notifyConfig` with the cached config dropped first: for every mutator
 *  of what a model view reads -- providers, their facts and catalogs, a
 *  landed model test, sampler presets, roles and routes. `GET /config` names
 *  each role's provider, model and preset and says whether Primary is
 *  `ready`, and the Models views re-read on the signal, so a write that moves
 *  any of it must do both or a view keeps showing what it replaced. */
export function announceModels<T>(result: T): T {
  invalidateConfigCache();
  return notifyConfig(result);
}

/** Same shape again, for the nav rail's counts.
 *
 *  Hung off the mutators that change what a badge says WITHOUT moving the
 *  pathname -- which is most of them: ending a scene, writing the ledger,
 *  creating or deleting a sheet, renaming a world the rail names. Navigation
 *  refetches the payload on its own; these are the changes navigation would
 *  never hear about, and the rail is persistent chrome, so a stale count sits
 *  on screen indefinitely rather than being corrected by the next click.
 *
 *  On the mutators rather than in their callers for the reason the other two
 *  give: the mutators are the one place every path goes through, so no view
 *  can forget. A rejected request never reaches here. */
export function notifyShell<T>(result: T): T {
  shellChanged();
  return result;
}

/** The exception to "a rejected request never reaches here": a 500 whose
 *  kind is one of `kinds` names parts that landed (§22 partial writes), so
 *  the badges move as on a success before the refusal is passed on. */
export function notifyShellOnLanded(kinds: readonly string[]) {
  return (err: unknown): never => {
    if (err instanceof ApiError && err.kind !== undefined && kinds.includes(err.kind)) {
      shellChanged();
    }
    throw err;
  };
}
