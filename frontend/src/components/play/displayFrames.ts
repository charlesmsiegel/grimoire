/** One streamed `display` frame: the server re-ran the display rules over the
 *  part so far and sends what changed since its last frame -- keep the first
 *  `keep` characters of the part, then append `tail`. */
export type DisplayFrame = { keep: number; tail: string };

/** The stream buffer with `frame` applied to the part that starts at
 *  `partStart`.
 *
 *  `keep` counts from the start of the CURRENT part, never the turn: each part
 *  (a character turn's `response_start`, or the whole of a legacy stream) runs
 *  its own display pass, so a frame cannot reach back into text an earlier
 *  part already settled. That is what lets a closing `</think>` take back
 *  words already on screen without disturbing the speaker before it.
 *
 *  `partStart` is an offset into `acc` itself, but `keep` is counted by the
 *  server, which is Python and counts code points: an emoji outside the BMP is
 *  one character there and two UTF-16 units here, so the keep is walked rather
 *  than sliced, or a frame after one would cut the next character in half. */
export function applyDisplay(acc: string, partStart: number, frame: DisplayFrame): string {
  let end = partStart;
  for (let n = 0; n < frame.keep && end < acc.length; n++) {
    const unit = acc.charCodeAt(end);
    end += unit >= 0xd800 && unit <= 0xdbff && end + 1 < acc.length ? 2 : 1;
  }
  return acc.slice(0, end) + frame.tail;
}
