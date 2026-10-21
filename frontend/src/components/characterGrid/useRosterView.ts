import { startTransition, useEffect, useMemo, useState } from "react";
import type { CharacterSummary } from "../../api/client";
import { useResetOnKey, type Visit } from "./useCharacterRoster";

/** How many cards a roster's first commit draws; the rest follow once that
 *  has painted.
 *
 *  Drawing a card is a DOM subtree, a portrait whose `srcset` the browser has
 *  to resolve, and its share of the grid's layout -- small once, but a roster
 *  is paid for all at once, and on a phone-class CPU the whole of a large one
 *  kept the first card off the screen for as long as the last one took. The
 *  grid is three columns wide on desktop and two on a phone, so this is at
 *  least eight rows: more than
 *  the tallest viewport shows of a desktop's cards, and more than a phone's
 *  much smaller ones fill. A constant rather than a measurement of the
 *  viewport, because a grid that measured first would be a frame later for
 *  it; worth re-tuning if the card or the column count changes. */
const FIRST_PAINT_CARDS = 24;

/** Which of the roster's cards the grid shows: the campaign's Appeared / All
 *  filter, and the first-paint slice of whatever that leaves. */
export function useRosterView(
  { chars, appeared, worldScope, reveal, visit, scopeKey }: {
    chars: CharacterSummary[];
    appeared: Set<string> | null;
    worldScope: boolean;
    reveal: string | null | undefined;
    visit: Visit;
    scopeKey: string;
  },
) {
  const [showAll, setShowAll] = useState(false);
  /** Whether the whole roster is drawn, or only its first `FIRST_PAINT_CARDS`. */
  const [drawAll, setDrawAll] = useState(false);
  useResetOnKey(scopeKey, () => setDrawAll(false));

  // `showAll` is a statement about one campaign's inherited roster; left
  // standing it opens the next campaign on its whole world instead of on its
  // own cast, which is the state this filter exists to avoid. (Before the
  // reveal below, which may need to set it again for the same visit.)
  useEffect(() => { setShowAll(false); }, [visit]);

  // A character handed back by their own page has to survive the filter, or
  // landing on a grid that hides them reads as the record having been deleted.
  useEffect(() => {
    if (!reveal || appeared === null) return;
    if (!appeared.has(reveal)) setShowAll(true);
  }, [reveal, appeared]);

  // The filter is offered only where "appeared" means something and the roster
  // actually loaded; everywhere else `shown` is simply every card.
  const filterable = !worldScope && appeared !== null;
  const appearedChars = useMemo(
    () => (filterable ? chars.filter((c) => appeared.has(c.id)) : chars),
    [filterable, chars, appeared],
  );
  const shown = filterable && !showAll ? appearedChars : chars;
  // The rest of the roster, once the first slice has been PAINTED: a frame's
  // callback runs just before the paint, and a task queued from it runs just
  // after. As a transition, so drawing a long roster yields to a click or a
  // keystroke rather than holding the page until the last card is in.
  const needsMore = !drawAll && shown.length > FIRST_PAINT_CARDS;
  useEffect(() => {
    if (!needsMore) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const frame = requestAnimationFrame(() => {
      timer = setTimeout(() => startTransition(() => setDrawAll(true)), 0);
    });
    return () => { cancelAnimationFrame(frame); clearTimeout(timer); };
  }, [needsMore]);
  const drawn = needsMore ? shown.slice(0, FIRST_PAINT_CARDS) : shown;

  return { filterable, showAll, setShowAll, appearedCount: appearedChars.length, shown, drawn };
}

export type RosterView = ReturnType<typeof useRosterView>;
