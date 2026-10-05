import { useMemo, useRef } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";

/** A thumb's travel, in CSS pixels: less than this is a tap that wandered. */
export const SWIPE_MIN_PX = 56;

// A gesture that starts on one of these belongs to it: a press, a caret, a
// drag-select, a disclosure toggle.
const INTERACTIVE = "button, a, input, textarea, select, summary, details, [contenteditable], form";

type Opts = { enabled: boolean; onNext: () => void; onPrevious: () => void };
type Start = { id: number; x: number; y: number };

/** True when the gesture began on a control, or inside something that pans
 *  sideways on its own (a wide GFM table), measured up to but not including
 *  the element carrying the handlers -- that element's own overflow is not
 *  the finger's business. */
function startsOnSomethingElse(target: EventTarget | null, root: Element): boolean {
  if (!(target instanceof Element)) return true;
  const control = target.closest(INTERACTIVE);
  if (control && control !== root && root.contains(control)) return true;
  for (let el: Element | null = target; el && el !== root; el = el.parentElement) {
    if (el.scrollWidth > el.clientWidth) return true;
  }
  return false;
}

/** Touch swipe for one element, as React pointer handlers (never a window
 *  listener: the keys rule in CLAUDE.md is about keyboards, and this stays
 *  scoped to the element it is spread on).
 *
 *  One touch pointer is tracked from `pointerdown` and decided on `pointerup`.
 *  `pointercancel` -- what the browser sends when it takes the gesture over as
 *  a vertical pan -- and a second pointer both discard it. A left swipe (the
 *  finger travelling right to left) is next; a right swipe is previous.
 *
 *  The returned object is stable across renders, so a memoized row that spreads
 *  it is not re-rendered by it; the options are read through a ref. */
export function useSwipe(opts: Opts) {
  const latest = useRef(opts);
  latest.current = opts;
  const start = useRef<Start | null>(null);

  return useMemo(() => ({
    onPointerDown(e: ReactPointerEvent<HTMLElement>) {
      if (e.pointerType !== "touch") return;
      if (start.current) {
        // A second finger: this is a pinch or a stray palm, not a swipe.
        start.current = null;
        return;
      }
      if (!latest.current.enabled) return;
      if (startsOnSomethingElse(e.target, e.currentTarget)) return;
      start.current = { id: e.pointerId, x: e.clientX, y: e.clientY };
    },
    onPointerUp(e: ReactPointerEvent<HTMLElement>) {
      const from = start.current;
      if (!from || from.id !== e.pointerId) return;
      start.current = null;
      const { enabled, onNext, onPrevious } = latest.current;
      if (!enabled) return;
      const dx = e.clientX - from.x;
      const dy = e.clientY - from.y;
      if (Math.abs(dx) < SWIPE_MIN_PX || Math.abs(dx) < 2 * Math.abs(dy)) return;
      // A drag that selected text was a selection, not a swipe.
      if (window.getSelection()?.toString()) return;
      if (dx < 0) onNext();
      else onPrevious();
    },
    onPointerCancel() {
      start.current = null;
    },
  }), []);
}
