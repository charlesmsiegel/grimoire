import type { InferenceSettings } from "../../api/client";
import { ROLE_LABEL } from "../inference/selection";
import { taskHash } from "../models/taskHash";
import { GENERATIVE } from "../models/selections";

/** Every library-wide place that names preset `id` (spec 4): a role's own
 *  preset, a role's fallback's, a task's preset override, and a task's pinned
 *  model's preset -- each a link to where it is changed. A campaign's own
 *  choices are its Inspector's, and are not listed here. */
export function presetUses(settings: InferenceSettings, id: string): { label: string; to: string }[] {
  const out: { label: string; to: string }[] = [];
  for (const role of GENERATIVE) {
    const card = settings.roles[role];
    if (card.stored.preset === id) out.push({ label: ROLE_LABEL[role], to: "/models" });
    if (card.fallback.preset === id) out.push({ label: `${ROLE_LABEL[role]} fallback`, to: "/models" });
  }
  for (const r of settings.routes) {
    const to = `/models/edit${taskHash(r.key)}`;
    if (r.preset === id) out.push({ label: r.label, to });
    if (r.pin.preset === id) out.push({ label: `${r.label} (pinned model)`, to });
  }
  return out;
}
