// Pure rules for the composer's quick replies: when each button is offered and
// enabled (mirroring the control it stands for), and the set edits the editor
// makes. Kept out of the components so the rules can be read, and tested, in
// one place.
import type { QuickReply, QuickReplyEntry, QuickReplyHide, QuickReplyTask } from "../api/types";

export function isHide(e: QuickReplyEntry): e is QuickReplyHide {
  return "hidden" in e && e.hidden === true;
}

const TASK_TITLES: Record<QuickReplyTask, string> = {
  rolling_summary: "Refresh the scene summary now",
  scene_break: "Ask whether the scene should break now",
  next_scene: "Choose the next scene",
};

/** The whole of what a button does, for its `title`: a one-tap send is at
 *  least visible before it happens. */
export function quickReplyTitle(r: QuickReply): string {
  switch (r.kind) {
    case "send":
    case "direct":
      return r.text ?? "";
    case "roll":
      return r.roll_label ? `${r.notation ?? ""} — ${r.roll_label}` : (r.notation ?? "");
    case "task":
      return r.task ? TASK_TITLES[r.task] : "";
    case "opener":
      return "Open the opener generator";
    default:
      return "";
  }
}

/** What the composer knows that decides a button. */
export type QuickReplyContext = {
  busy: boolean;
  rolling: boolean;
  renaming: boolean;
  sceneLocked: boolean;
  /** Posts in the transcript on screen. */
  posts: number;
  /** Whether the campaign's mechanics binding has been read (`moduleBound !== null`). */
  moduleKnown: boolean;
  pcless: boolean;
  /** A connection is ready. */
  ready: boolean;
  /** The empty-scene cast panel (and its opener generator) is mounted. */
  openerOffered: boolean;
  /** The task is already running, from the strip or the inspector. */
  taskRunning: (t: "rolling_summary" | "scene_break") => boolean;
};

export type QuickReplyAvailability = { shown: boolean; disabled: boolean; title: string };

export const PCLESS_TITLE = "This scene has no player character";

/** Each button is disabled exactly when the control it stands for is. */
export function quickReplyAvailability(r: QuickReply, ctx: QuickReplyContext): QuickReplyAvailability {
  const title = quickReplyTitle(r);
  const shown = (disabled: boolean, why?: string): QuickReplyAvailability =>
    ({ shown: true, disabled, title: why ?? title });
  switch (r.kind) {
    case "send":
    case "direct":
      // Speak is disabled in a scene with no player character; so is a post.
      if (r.kind === "send" && ctx.pcless) return shown(true, PCLESS_TITLE);
      if (r.mode === "insert") return shown(false);
      return shown(ctx.busy || ctx.rolling || ctx.renaming);   // the Send button's guards
    case "roll":
      // The dice button's guards, plus the module read -- but NOT a bound
      // module: a saved roll is something the player configured, and the roll
      // route only parses dice.
      return shown(ctx.busy || ctx.sceneLocked || ctx.posts === 0 || ctx.rolling || !ctx.moduleKnown);
    case "task":
      if (r.task === "next_scene") return shown(ctx.posts === 0);
      if (r.task === "rolling_summary" || r.task === "scene_break")
        return shown(ctx.posts === 0 || ctx.sceneLocked || !ctx.ready || ctx.taskRunning(r.task));
      return { shown: false, disabled: true, title };
    case "opener":
      return ctx.openerOffered ? shown(!ctx.ready) : { shown: false, disabled: true, title };
    default:
      return { shown: false, disabled: true, title };
  }
}
