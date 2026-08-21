import { type PromptEntry } from "../api/client";

/** How a captured turn names itself, shared by everything that shows one: the
 *  Turn history rail, the frozen-context banner, and the two ends of a
 *  comparison (#130).
 *
 *  Extracted rather than copied because the third caller would have been the
 *  copy: `ContextDiff` cannot import `SceneInspector` (which imports it), so
 *  the alternative was a second table that drifts the first time a task is
 *  added — and the label is what tells a Retry from a Regenerate on a panel
 *  whose whole job is telling two turns apart.
 */
const LABELS: Record<PromptEntry["task"], string> = {
  chat: "Send", director: "Director", retry: "Retry",
  regenerate: "Regenerate", continuation: "Roll result", opener: "Opener",
  replay: "Replay",
};

/** Typed against the union above so a new task cannot be forgotten here, read
 *  as an open map so an OLDER snapshot naming a task this build has dropped
 *  falls back to its raw name rather than rendering `undefined`. */
const OPEN: Record<string, string> = LABELS;

export function taskLabel(task: string): string {
  return OPEN[task] ?? task;
}

/** The captured timestamp is UTC (`…Z`, stamped by the store); show it local.
 *  An unparseable one is shown as it was stored — a debug view that blanks the
 *  only thing distinguishing two rows is worse than one showing a raw string. */
export function whenLabel(ts: string): string {
  const d = new Date(ts);
  return isNaN(d.getTime()) ? ts : d.toLocaleString();
}
