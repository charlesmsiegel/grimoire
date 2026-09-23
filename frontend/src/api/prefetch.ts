import type { PointerEvent as ReactPointerEvent } from "react";
import { api, type EntityScope } from "./client";

/** How long a pointer (or keyboard focus) has to rest on a link into a world
 *  before its reads are started. Long enough that sweeping the cursor across
 *  the worlds shelf on the way somewhere else asks for nothing; short enough
 *  that it is still well ahead of the click it predicts -- a reader's hover
 *  typically lasts a few hundred milliseconds before they press. */
export const INTENT_DWELL_MS = 70;

/** How far, in CSS px, a finger may drift from where it landed and still be
 *  resting. No finger is perfectly still, so zero would turn every press into
 *  a scroll; much more and the start of a slow scroll reads as a rest, since
 *  the browser only calls a gesture a scroll (`pointercancel`) once it passes
 *  its own, larger slop -- which a slow start can take longer than the dwell
 *  to reach. */
const TOUCH_SLOP_PX = 10;

/** A target prefetched this recently is not asked again. The api client's
 *  in-flight sharing already folds a second ask into the first while it is on
 *  the wire, and once it lands the remembered answer makes the next ask a
 *  no-op; this window is what stops a read that FAILED from being re-sent on
 *  every hover of a shelf the reader is idly running the pointer across. */
const DEDUP_MS = 5000;

const issued = new Map<string, number>();

/** Start the reads a world's roster screen will make, so it has something to
 *  paint the moment it mounts: the world record behind the page's header, and
 *  the scope's character rows -- plus, for a campaign's copy, its roster,
 *  which that grid filters by before it paints anything.
 *
 *  The SAME non-fresh GETs the page makes, which is the whole mechanism: one
 *  still in flight when the page mounts is joined rather than repeated, and
 *  one that has landed is remembered by the api client, so the page paints it
 *  on its first render and revalidates. A read already remembered is not
 *  re-asked -- the page will revalidate it anyway.
 *
 *  Never surfaces a failure. A prefetch is a guess; the page's own read is the
 *  one whose failure means something, and it makes it regardless. */
export function prefetchScope(scope: EntityScope): void {
  const key = JSON.stringify([scope.kind, scope.id]);
  const now = Date.now();
  const last = issued.get(key);
  if (last !== undefined && now - last < DEDUP_MS) return;
  issued.set(key, now);
  // Started inside a promise so a read that throws synchronously is the same
  // swallowed nothing as one that rejects.
  const quietly = (read: () => Promise<unknown>) => {
    Promise.resolve().then(read).catch(() => {});
  };
  if (scope.kind === "world" && !api.rememberedWorld(scope.id)) {
    quietly(() => api.getWorld(scope.id));
  }
  if (!api.rememberedCharacters(scope)) quietly(() => api.listCharacters(scope));
  if (scope.kind === "campaign" && !api.rememberedAppearances(scope.id)) {
    quietly(() => api.listAppearances(scope.id));
  }
}

/** Forget what has been prefetched. For tests, which share this module. */
export function resetPrefetch(): void {
  issued.clear();
  cancelDwell();
}

// One timer for the whole app: a pointer rests on one thing at a time, and a
// reader whose pointer moved on from a card has stopped predicting it.
let dwell: ReturnType<typeof setTimeout> | null = null;

/** The finger that may be resting on a link: from its `pointerdown` until it
 *  drifts, lifts, is cancelled or has rested long enough. Module-wide for the
 *  timer's reason, and because a re-render mid-press hands the element new
 *  handlers -- state closed over by one render's would be gone by the lift. */
let touch: { id: number; x: number; y: number } | null = null;

function cancelDwell(): void {
  touch = null;
  if (dwell !== null) {
    clearTimeout(dwell);
    dwell = null;
  }
}

type IntentProps = {
  onPointerEnter?: (e: ReactPointerEvent) => void;
  onPointerLeave?: () => void;
  onFocus?: () => void;
  onBlur?: () => void;
  onPointerDown?: (e: ReactPointerEvent) => void;
  onPointerMove?: (e: ReactPointerEvent) => void;
  onPointerUp?: (e: ReactPointerEvent) => void;
  onPointerCancel?: () => void;
};

/** Handlers that read a reader's intent to open `scope`'s roster, to spread
 *  onto the link (or button) that opens it.
 *
 *  A rest of `INTENT_DWELL_MS` -- by the pointer, or by keyboard focus, since
 *  tabbing across a shelf passes through every card on the way -- or a mouse
 *  or pen press, which is immediate: `pointerdown` leads the click it starts
 *  by the whole press.
 *
 *  A finger is the exception, because on a phone a press is not yet intent: a
 *  scroll starts with a finger coming down on whatever card is under it, and
 *  prefetching on that (it used to, on `touchstart`) asked the server for a
 *  world per flick -- contention landing exactly while the reader was still
 *  choosing. So a touch waits for the same dwell, and only for a finger that
 *  stays where it landed: drifting past `TOUCH_SLOP_PX`, or the browser taking
 *  the gesture over as a scroll (`pointercancel`), ends it. A tap quicker than
 *  the dwell lifts first, and that is intent -- a click is about to follow --
 *  so the lift asks at once. A touch that has already rested asks nothing
 *  more; neither does its `pointerenter`, which a finger fires as it lands.
 *
 *  `null` spreads nothing, and is how a caller says "this link is the page I
 *  am already on" -- which is never worth prefetching. */
export function intentProps(scope: EntityScope | null): IntentProps {
  if (!scope) return {};
  const soon = () => {
    cancelDwell();
    dwell = setTimeout(() => {
      dwell = null;
      touch = null;
      prefetchScope(scope);
    }, INTENT_DWELL_MS);
  };
  const now = () => {
    cancelDwell();
    prefetchScope(scope);
  };
  const isTouch = (e: ReactPointerEvent) => e.pointerType === "touch";
  /** The resting finger this event is about, if it is one. */
  const resting = (e: ReactPointerEvent) => (touch && touch.id === e.pointerId ? touch : null);
  return {
    onPointerEnter: (e) => { if (!isTouch(e)) soon(); },
    onPointerLeave: cancelDwell,
    onFocus: soon, onBlur: cancelDwell,
    onPointerDown: (e) => {
      if (!isTouch(e)) { now(); return; }
      soon();
      touch = { id: e.pointerId, x: e.clientX, y: e.clientY };   // after: `soon` clears it
    },
    onPointerMove: (e) => {
      const t = resting(e);
      if (t && Math.hypot(e.clientX - t.x, e.clientY - t.y) > TOUCH_SLOP_PX) cancelDwell();
    },
    onPointerUp: (e) => { if (resting(e)) now(); },
    onPointerCancel: cancelDwell,
  };
}
