import type { PromptFieldStatuses } from "../api/types";

const FIELDS = [
  ["world_default_prompt", "World default prompt"],
  ["campaign_default_prompt", "Campaign default prompt"],
  ["scene_focus", "Scene focus"],
] as const;

/** Captured status only: no prose, paths, live re-read or inferred warning.
 *  Creation and frozen/live context views share this same distinction;
 *  unavailable inheritance, unset fields and legacy captures are normal. */
export function promptFieldWarning(statuses?: PromptFieldStatuses): string {
  return FIELDS.filter(([id]) => statuses?.[id] === "unreadable")
    .map(([, label]) => `${label} could not be read and was omitted from this composition.`)
    .join(" ");
}
