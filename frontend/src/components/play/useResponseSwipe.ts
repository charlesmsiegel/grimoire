import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../../api/client";
import type { ResponseSwipe } from "../../api/types";

type Args = {
  cid: string;
  sid: string | null;
  /** The swipe target's response id; null when the last post is not a response. */
  rid: string | null;
  /** The target's text. It changes on every activate and every landed reroll
   *  while `rid` stays the same, which is what makes the count stale. */
  content: string | null;
  /** The transcript request the target came from, so a set fetched for one
   *  window is never shown against another. */
  windowToken: unknown;
};

// The swipe plus the key its read was made under (see `pending`).
type Held = { target: string; swipe: ResponseSwipe | null; content: string | null; windowToken: unknown; tick: number };

export type ResponseSwipeState = {
  swipe: ResponseSwipe | null;
  /** Ids of the complete variants, in order — the ones the arrows step through. */
  complete: string[];
  /** Where the active variant sits in `complete`, or null if it is not there. */
  position: number | null;
  /** Ask again: after an activate, a reroll or a settled turn. */
  refresh: () => void;
  /** A read is outstanding: `swipe` (kept, so nothing flickers) was fetched
   *  before the latest content, window or refresh, so its count and position
   *  may be stale. A consumer must not step or generate from it meanwhile. */
  pending: boolean;
};

/** The swipe read for the transcript's last response, kept current.
 *
 *  Every dependency change retires the request in flight (the cleanup flag), so
 *  only the latest request may write — a stale success and a stale rejection
 *  are both able to clobber the current target's state, the same hazard
 *  `fetchAlternates` guards. A failed read, or one that resolves to nothing,
 *  hides the arrows (`swipe` null) rather than leaving a stale count up.
 *
 *  Everything returned is memoized, and a refetch that reads back the same
 *  swipe keeps the old object: the transcript rows are memoized on it. The key
 *  a read was made under is held beside the swipe, apart from it, so that
 *  landing an identical read clears `pending` without changing the identity. */
export function useResponseSwipe({ cid, sid, rid, content, windowToken }: Args): ResponseSwipeState {
  const [tick, setTick] = useState(0);
  // Tagged with the target it describes: a read for the previous response must
  // not sit in front of the new one while that one's request is out. A content
  // or window change keeps the tag, so the count holds (as `pending`) until the
  // refetch lands.
  const target = `${cid}\n${sid}\n${rid}`;
  const [held, setHeld] = useState<Held>({ target, swipe: null, content, windowToken, tick });
  const swipe = held.target === target ? held.swipe : null;
  const pending = !!sid && !!rid && !(held.target === target && held.content === content
    && Object.is(held.windowToken, windowToken) && held.tick === tick);

  useEffect(() => {
    if (!sid || !rid) {
      setHeld((prev) => (prev.target === target && prev.swipe === null
        ? prev : { target, swipe: null, content, windowToken, tick }));
      return;
    }
    let live = true;
    const landed = (next: ResponseSwipe | null) => setHeld((prev) => ({
      // an identical read keeps the old object: only the key it answers moves
      target, content, windowToken, tick,
      swipe: prev.target === target && prev.swipe && next
        && JSON.stringify(prev.swipe) === JSON.stringify(next) ? prev.swipe : next,
    }));
    api.getResponseSwipe(cid, sid, rid)
      // `undefined`: a harness with every mock reset resolves nothing, and
      // that is no more a swipe than a rejection is.
      .then((s) => { if (live) landed(s ?? null); })
      .catch(() => { if (live) landed(null); });
    return () => { live = false; };
  }, [target, cid, sid, rid, content, windowToken, tick]);

  const refresh = useCallback(() => setTick((t) => t + 1), []);

  const complete = useMemo(
    () => (swipe ? swipe.variants.filter((v) => v.status === "complete").map((v) => v.id) : []),
    [swipe],
  );
  const position = useMemo(() => {
    if (!swipe || swipe.active === null) return null;
    const id = swipe.variants[swipe.active]?.id;
    const at = id === undefined ? -1 : complete.indexOf(id);
    return at < 0 ? null : at;
  }, [swipe, complete]);

  return useMemo(
    () => ({ swipe, complete, position, refresh, pending }),
    [swipe, complete, position, refresh, pending],
  );
}
