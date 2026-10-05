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

export type ResponseSwipeState = {
  swipe: ResponseSwipe | null;
  /** Ids of the complete variants, in order — the ones the arrows step through. */
  complete: string[];
  /** Where the active variant sits in `complete`, or null if it is not there. */
  position: number | null;
  /** Ask again: after an activate, a reroll or a settled turn. */
  refresh(): void;
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
 *  swipe keeps the old object: the transcript rows are memoized on it. */
export function useResponseSwipe({ cid, sid, rid, content, windowToken }: Args): ResponseSwipeState {
  // Tagged with the target it describes: a read for the previous response must
  // not sit in front of the new one while that one's request is out. A content
  // or window change keeps the tag, so the count holds until the refetch lands.
  const target = `${cid}\n${sid}\n${rid}`;
  const [held, setHeld] = useState<{ target: string; swipe: ResponseSwipe | null }>({ target, swipe: null });
  const swipe = held.target === target ? held.swipe : null;
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!sid || !rid) {
      setHeld({ target, swipe: null });
      return;
    }
    let live = true;
    api.getResponseSwipe(cid, sid, rid)
      .then((s) => {
        if (!live) return;
        // `undefined`: a harness with every mock reset resolves nothing, and
        // that is no more a swipe than a rejection is.
        const next = s ?? null;
        setHeld((prev) => (prev.target === target && prev.swipe && next
          && JSON.stringify(prev.swipe) === JSON.stringify(next) ? prev : { target, swipe: next }));
      })
      .catch(() => { if (live) setHeld({ target, swipe: null }); });
    return () => { live = false; };
    // `content` and `windowToken` are not read here: they are the signal that
    // what was fetched is out of date.
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

  return useMemo(() => ({ swipe, complete, position, refresh }), [swipe, complete, position, refresh]);
}
