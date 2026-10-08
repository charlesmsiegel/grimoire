import type { InferenceSettings, MigrationStatus } from "../../api/client";

/** How the model-settings surfaces -- Settings, `/models`, a campaign's
 *  Models section and the setup wizard -- put the one-time upgrade into
 *  words. One copy, so no two of them can describe the same status
 *  differently.
 *
 *  Two forms, never both at once:
 *  - the **banner** (`InferenceBanner`), while the library's own settings
 *    cannot be saved: a newer build wrote them, or the layout has not
 *    switched yet (format "1");
 *  - a quiet **line**, once the layout has switched and something is still
 *    left: a campaign the move has not reached, a stopped run's reason, or
 *    what the move skipped. Information rather than a lock, since nothing at
 *    format "2" waits on it. */

/** What the banner shows, or null where it draws nothing. */
export function migrationBanner(s: InferenceSettings | null | undefined): MigrationStatus | null {
  if (!s) return null;
  if (s.newer || s.migration.state === "newer") return { ...s.migration, state: "newer" };
  return s.format !== "2" ? s.migration : null;
}

/** Whether the move is still under way or stopped -- not done, not newer. */
export function migrationUnfinished(s: InferenceSettings | null | undefined): boolean {
  if (!s) return false;
  const { state } = s.migration;
  return state === "pending" || state === "running" || state === "failed";
}

const sentence = (text: string) => (/[.!?]$/.test(text) ? text : `${text}.`);

/** The quiet line, or null where there is nothing to say or the banner is
 *  saying it. A stopped run's reason and what was skipped are two facts, and
 *  the line carries both rather than letting one hide the other; skipped
 *  items are named even once the move is done (an unreadable campaign, a
 *  provider whose preset could not be written). */
export function migrationLine(s: InferenceSettings | null | undefined): string | null {
  if (!s || migrationBanner(s)) return null;
  const { state, reason, skipped } = s.migration;
  const unfinished = migrationUnfinished(s);
  if (!unfinished && skipped.length === 0) return null;
  const left = skipped.length > 0
    ? `left ${skipped.length} ${skipped.length === 1 ? "thing" : "things"} for later `
      + `(${skipped.join("; ")}).`
    : "";
  if (state === "failed" && reason) {
    const head = sentence(`The upgrade has not finished: ${reason}`);
    return left ? `${head} It ${left}` : head;
  }
  if (left) return `The upgrade ${left}`;
  return "Part of this library has not finished upgrading yet.";
}
