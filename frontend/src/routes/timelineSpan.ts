/** The date-span arithmetic behind the timeline's FROM/TO filter (#198).
 *
 *  Its own module, and its own tests, because it is the one part of that screen
 *  that is a *rule* rather than a rendering: the carry-forward below decides
 *  which scenes a reader sees, and proving it through the component means
 *  asserting on rendered rows to check a number. `sceneDraft.ts` and
 *  `pickerLayout.ts` are here for the same reason.
 */

/** Anything with an in-fiction date. Structural, so the tests need no fixtures
 *  from `api/client` and the helper cannot drift into caring about the rest of
 *  a scene. */
export type Dated = { date: string };

export type Span = {
  /** The distinct dates, in **play order** — first appearance walking the
   *  scenes as the server sorted them.
   *
   *  Play order, not sorted order, and that is the whole of it: a native date
   *  is `<year>-<month key>-<day>` where the month key is a *string* a calendar
   *  provider supplies, so sorting those strings orders months alphabetically.
   *  The scene sequence is the authority on when things happened (a flashback
   *  is out of date order on purpose), so the dates inherit its order rather
   *  than imposing one of their own. */
  moments: string[];
  /** Each scene's position in `moments`, by index, parallel to the input.
   *
   *  A scene with no date **carries the rank of the last dated scene before
   *  it**, which is what an undated scene means on a timeline: it happened
   *  after that date and before the next. Without the carry-forward every
   *  undated scene — the ordinary case for anything not yet absorbed, and for
   *  any scene whose datetime was never set — would drop out of every span the
   *  reader picked, which is the opposite of what a span is for.
   *
   *  A scene before *any* dated one ranks -1: it genuinely precedes the first
   *  known moment, so it is in no span that starts at one, and in every span
   *  that merely ends at one. */
  ranks: number[];
};

export function spanOf(scenes: Dated[]): Span {
  const moments: string[] = [];
  // The index alongside the list: `moments.indexOf` per scene is quadratic, and
  // this runs over every scene a campaign has ever played. One pass rather than
  // two, so the rank is the value just written rather than a second lookup that
  // has to be cast back from `number | undefined`.
  const at = new Map<string, number>();
  let running = -1;
  const ranks = scenes.map((s) => {
    if (s.date) {
      let rank = at.get(s.date);
      if (rank === undefined) { rank = moments.length; at.set(s.date, rank); moments.push(s.date); }
      running = rank;
    }
    return running;
  });
  return { moments, ranks };
}

/** What a held bound resolves to: its index in `moments`, or `null` for no
 *  bound at all.
 *
 *  The bounds are held as **dates**, not as indices, and this function is why.
 *  An index is a position in a list derived from the data, so a scene re-dated
 *  underneath it silently re-points the filter at a different moment — the
 *  reader's "from 3 Reaping" quietly becomes "from 4 Reaping" with nothing on
 *  screen changing. A date either still exists in the campaign, in which case
 *  it means exactly what it meant, or it does not, in which case dropping the
 *  bound is the only honest reading — a span cannot start at a moment the
 *  campaign no longer has.
 */
export function boundRank(moments: string[], date: string | null): number | null {
  if (!date) return null;
  const i = moments.indexOf(date);
  return i === -1 ? null : i;
}

/** Whether a scene at `rank` falls inside `[from, to]`, either bound `null`
 *  meaning unbounded on that side. */
export function inSpan(rank: number, from: number | null, to: number | null): boolean {
  if (from !== null && rank < from) return false;
  if (to !== null && rank > to) return false;
  return true;
}
