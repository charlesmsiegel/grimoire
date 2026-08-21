/** The active connection's model catalog, and the labels drawn from one.
 *
 *  This module used to fetch `https://openrouter.ai/api/v1/models` from the
 *  browser, unauthenticated, whichever provider was actually configured (#149).
 *  Both halves of that were wrong once connections existed: a reader on a local
 *  endpoint was picking from OpenRouter's catalog, and no key was ever
 *  presented, so a rejected one first showed up as a failed scene.
 *
 *  The fetch now happens server-side against the connection's own provider.
 *  What is left here is the per-page-load cache and the presentation helpers.
 */
import { onConfigChanged } from "../appEvents";
import { api } from "./client";
import type { Model } from "./types";

export type { Model };

/** The catalog of the connection generation will actually run on.
 *
 *  Two requests rather than one, and deliberately: `getConfig` is itself cached
 *  and shared with the rest of the app, so the common case is one. There is no
 *  "the active connection's models" endpoint because nothing else wants one —
 *  the Connections page reads a catalog for the connection *being edited*,
 *  which is a different question with a different answer.
 *
 *  A connection with no cached catalog answers `[]` rather than fetching one:
 *  the only reader is a display label (a scene's model, sized against its
 *  context window), and going out to a provider to render one is not a trade
 *  worth making. Opening that connection on the Connections page fetches it,
 *  and the label fills in from then on.
 */
async function fetchModels(): Promise<Model[]> {
  const cfg = await api.getConfig();
  if (!cfg.active_connection) return [];
  const detail = await api.readConnection(cfg.active_connection.id);
  return detail.models;
}

// A catalog is a large download that changes rarely; every mount of the model
// pickers used to re-fetch it. One copy per page load is enough.
let modelsCache: Promise<Model[]> | null = null;

export function invalidateModelsCache() {
  modelsCache = null;
}

export function getModels(): Promise<Model[]> {
  if (!modelsCache) {
    modelsCache = fetchModels().catch((err) => {
      modelsCache = null; // never cache a failure
      throw err;
    });
  }
  return modelsCache;
}

// The cache is keyed on nothing, so it has to be dropped whenever the
// connection it describes might have moved — a different connection made
// active, a model list refreshed, an endpoint repointed. Subscribing here
// rather than calling `invalidateModelsCache()` from each of those views is the
// same rule `appEvents` already states for the status bar: the mutators are the
// one place every path goes through, so a caller cannot forget. It over-fires
// (a theme write is a config write) and that costs one refetch of a list that
// is only read on a scene inspector.
onConfigChanged(invalidateModelsCache);

function compact(n: number): string {
  if (n >= 1e6) return strip(n / 1e6) + "M";
  if (n >= 1e3) return strip(n / 1e3) + "K";
  return String(Math.round(n));
}

function strip(x: number): string {
  return String(Math.round(x * 10) / 10);
}

export function tokensPerDollar(price: string | null): string {
  if (price == null) return "";
  const n = Number(price);
  if (!isFinite(n) || n === 0) return "Free";
  return compact(1 / n);
}

export function contextLabel(context: number): string {
  if (!context) return "";
  if (context >= 1e6) return Math.round(context / 1e6) + "M ctx";
  if (context >= 1e3) return Math.round(context / 1e3) + "K ctx";
  return context + " ctx";
}

export function priceLabel(model: Model): string {
  if (model.prompt == null || model.completion == null) return "";
  if (Number(model.prompt) === 0 && Number(model.completion) === 0) return "Free";
  return `${tokensPerDollar(model.prompt)} / ${tokensPerDollar(model.completion)} tok/$`;
}
