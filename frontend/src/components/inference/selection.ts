import type {
  CapabilityName, CapabilityNeed, InferenceRole, InferenceSettings, ResolvedSelection, RouteRow,
} from "../../api/client";

/** How the two model-settings screens -- the library's `/models` and a
 *  campaign Inspector's Models section -- put a selection into words. One
 *  copy, so the two cannot come to disagree about what the same view says. */

export const ROLE_LABEL: Record<InferenceRole, string> = {
  primary: "Primary", fast: "Fast", decision: "Decision", embedding: "Embedding",
};

/** What each role's model must be able to do (spec 6.3), as the picker asks
 *  the capabilities API. */
export const ROLE_NEEDS: Record<InferenceRole, CapabilityNeed[]> = {
  primary: ["generate"], fast: ["generate"], decision: ["decide"], embedding: ["embed"],
};

/** provider ▸ model ▸ preset, as the server resolved it. */
export function describe(sel: ResolvedSelection | null, withPreset = true): string {
  if (!sel) return "nothing";
  const parts = [sel.provider_name || sel.provider, sel.model || "its default model"];
  if (withPreset) parts.push(sel.preset_name || sel.preset || "no preset");
  return parts.join(" ▸ ");
}

/** What inheriting would give: a label for the choice to inherit, never the
 *  headline for what something runs on (that is its `resolves`). */
export function inheritWords(sel: ResolvedSelection | null, withPreset = true): string {
  return `Inherit (resolves to ${describe(sel, withPreset)})`;
}

/** What an inherited preset is called: its name, its id when it has none. */
export function inheritedPreset(
  sel: { preset_name?: string; preset?: string } | null | undefined,
): string {
  return sel?.preset_name || sel?.preset || "no preset";
}

/** A route's own preset choice, in words: none (stopping the inheritance),
 *  a named preset, or what inheriting one resolves to. */
export function routePresetWords(row: RouteRow, settings: InferenceSettings): string {
  if (row.preset === settings.preset_clear) return "No preset (stop inheriting)";
  if (row.preset) return settings.presets.find((p) => p.id === row.preset)?.name ?? row.preset;
  return `Inherit (resolves to ${inheritedPreset(row.inherits)})`;
}

/** What a model pinned to a route must be able to do: its operation, and
 *  whatever else the route declares it needs. */
export function routePinNeeds(row: RouteRow): CapabilityNeed[] {
  return [row.operation === "decide" ? "decide" : "generate", ...row.requires];
}

/** What a model lacking each capability cannot do: "... cannot <phrase>"
 *  (`capabilities.CANNOT`, the server's one table). */
const CANNOT: Record<CapabilityName, string> = {
  generate: "generate text", vision: "read images", embed: "make embeddings",
  structured_output: "return structured output", prefill: "continue a prefilled reply",
  decide_native: "make native decisions", stream: "stream", tools: "call tools",
};

/** The server's `fallback_problem` for a task whose code policy sends no
 *  fallback (`resolve.NO_FALLBACK_POLICY`; `test_task_policy.py` holds the
 *  two equal). Worded apart: that fallback could be sent, and is not. */
export const NO_FALLBACK_POLICY = "this task's policy sends no fallback";

/** Why a fallback is never sent, worded as the library's Models page words a
 *  dropped fallback: `missing` is the server's `fallback_missing`, `fits` the
 *  role or route it is the fallback for, `fallback` its provider ▸ model
 *  when this scope names it, and `problem` the server's `fallback_problem`.
 *  Null when the fallback is sent. */
export function droppedFallbackWords(missing: CapabilityName[], fits: string,
                                     fallback = "", problem: string | null = null): string | null {
  const which = fallback ? `The fallback, ${fallback},` : "The fallback";
  // Only a route row can carry it: a role card resolves no task.
  if (problem === NO_FALLBACK_POLICY) return `${which} is never sent: ${fits} runs without a fallback.`;
  // A fallback that cannot send at all (the server's `fallback_problem`) is
  // left out before anything is asked of what it can do.
  if (problem) return `${which} cannot be sent (${problem}), so it is never tried.`;
  if (missing.length === 0) return null;
  const reason = `it cannot ${missing.map((cap) => CANNOT[cap]).join(" or ")}`;
  return `${which} is known not to fit ${fits} (${reason}), so it is never sent.`;
}

/** Whether a generative selection names a provider but no model -- the one a
 *  provider change leaves behind (the picker clears the model), and one the
 *  server refuses: saved, it would send a blank model id. The Embedding role
 *  is not one: a provider alone there is embedding off. */
export function wantsModel(sel: { provider: string; model: string }): boolean {
  return !!sel.provider && !sel.model.trim();
}

/** What a selection that `wantsModel` says, under its fields. */
export const CHOOSE_A_MODEL = "Choose a model for this provider to save.";
