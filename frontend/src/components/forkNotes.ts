import type { ForkReport } from "../api/client";

/** What a fork left behind, as one line — or "" when it left nothing behind.
 *
 *  A fork cut back to an earlier scene is an approximation of that turn, and
 *  the report is where the store says by how much. Two outcomes, kept apart
 *  because conflating them would mislead in opposite directions:
 *
 *  - `refused` — records a removed scene wrote that could not be put back, so
 *    they still hold what that scene gave them. Both "something wrote to this
 *    after the scene did" and "this kind carries no reversal at all" (a
 *    character or lore entry a removed scene CREATED) land here, and the note
 *    deliberately does not claim which: naming the records is what lets the
 *    player go and look, and asserting the wrong reason is worse than none.
 *  - `failed` — cleanup that could not run, typically a store file hand-edited
 *    into something that will not parse. The fork exists by then, so this is a
 *    thing to check rather than a thing that went wrong with the fork.
 *
 *  Shared by the shelf and the campaign page so the same fork reads the same
 *  way whichever one you started it from — this wording was drifting between
 *  the two before it had one home.
 */
export function forkNotes(report: Pick<ForkReport, "refused" | "failed">): string {
  const notes: string[] = [];
  const { refused, failed } = report;
  if (refused.length) {
    notes.push(
      `${refused.length} record${refused.length === 1 ? "" : "s"} on the fork ` +
      `still hold${refused.length === 1 ? "s" : ""} what a removed scene wrote: ` +
      refused.map((r) => r.label).join(", "));
  }
  if (failed.length) {
    notes.push("some continuity records on the fork could not be cleaned up (" +
               failed.join(", ") + ") — check them by hand");
  }
  return notes.join(". ");
}
