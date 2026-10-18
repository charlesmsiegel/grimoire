import { useEffect, useLayoutEffect, useRef, type RefObject } from "react";
import { useHotkeys } from "../../shortcuts/useHotkeys";

/** How a popover was dismissed: Escape hands focus back to the control that
 *  opened it, a press elsewhere leaves it wherever that press put it. */
export type DismissedBy = "escape" | "outside";

/** What closes a small popover while it is open -- the scene menu, a post's
 *  actions: Escape, and a press anywhere outside it.
 *
 *  Rendered only while the popover is open, which is what makes both halves
 *  cheap and right. Escape goes through the shortcut registry as a `modal`
 *  scope, so it reaches this popover rather than the view underneath, and the
 *  view's own keys are held off while it is up -- a ← meant for the menu is
 *  not a variant swipe behind it. Mounting is what registers the scope, so
 *  "on top" is whichever popover opened last. The press is a document
 *  `pointerdown` in the capture phase: it is seen before anything under the
 *  pointer acts on it, and a control that stops its own events cannot keep
 *  the popover open behind it. */
export function Dismiss({ within, label, onDismiss }: {
  /** The popover and the control that opens it. A press inside is its own. */
  within: RefObject<HTMLElement | null>;
  /** What the shortcuts sheet calls Escape here. */
  label: string;
  onDismiss: (by: DismissedBy) => void;
}) {
  // The latest closure, read when the event arrives, so the listener below is
  // installed once per opening rather than once per render.
  const latest = useRef(onDismiss);
  useLayoutEffect(() => { latest.current = onDismiss; });
  useHotkeys(
    [{ keys: "escape", label, group: "THIS PANEL", whileTyping: true,
       run: () => latest.current("escape") }],
    { modal: true },
  );
  useEffect(() => {
    const press = (e: PointerEvent) => {
      if (within.current && !within.current.contains(e.target as Node)) latest.current("outside");
    };
    document.addEventListener("pointerdown", press, true);
    return () => document.removeEventListener("pointerdown", press, true);
  }, [within]);
  return null;
}
