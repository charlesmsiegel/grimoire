import { api, type EntityScope } from "./client";

/** How long a pointer (or keyboard focus) has to rest on a link into a world
 *  before its reads are started. Long enough that sweeping the cursor across
 *  the worlds shelf on the way somewhere else asks for nothing; short enough
 *  that it is still well ahead of the click it predicts -- a reader's hover
 *  typically lasts a few hundred milliseconds before they press. */
export const INTENT_DWELL_MS = 70;

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

function cancelDwell(): void {
  if (dwell !== null) {
    clearTimeout(dwell);
    dwell = null;
  }
}

type IntentProps = {
  onPointerEnter?: () => void;
  onPointerLeave?: () => void;
  onFocus?: () => void;
  onBlur?: () => void;
  onPointerDown?: () => void;
  onTouchStart?: () => void;
};

/** Handlers that read a reader's intent to open `scope`'s roster, to spread
 *  onto the link (or button) that opens it.
 *
 *  A rest of `INTENT_DWELL_MS` -- by the pointer, or by keyboard focus, since
 *  tabbing across a shelf passes through every card on the way -- or a press,
 *  which is immediate: `pointerdown` leads the click it starts by the whole
 *  press, and on a phone, where nothing hovers, `touchstart` is the earliest
 *  word of intent there is.
 *
 *  `null` spreads nothing, and is how a caller says "this link is the page I
 *  am already on" -- which is never worth prefetching. */
export function intentProps(scope: EntityScope | null): IntentProps {
  if (!scope) return {};
  const soon = () => {
    cancelDwell();
    dwell = setTimeout(() => {
      dwell = null;
      prefetchScope(scope);
    }, INTENT_DWELL_MS);
  };
  const now = () => {
    cancelDwell();
    prefetchScope(scope);
  };
  return {
    onPointerEnter: soon, onPointerLeave: cancelDwell,
    onFocus: soon, onBlur: cancelDwell,
    onPointerDown: now, onTouchStart: now,
  };
}
