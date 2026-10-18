import { useRef, useState } from "react";
import { Dismiss, type DismissedBy } from "./Dismiss";

/** One panel the menu opens above the transcript, and whether it is open. */
export type ScenePanel = { label: string; open: boolean; toggle: () => void };

/** `Scene ⋯`: the panels about the scene, behind one control.
 *
 *  Eight panels behind one control is the design's overflow and the
 *  mobile-clutter half of the brief: the bar's own focus-mode comment conceded
 *  the cost of not having one ("eleven controls at the 44px touch target this
 *  width mandates wrap into four rows"). Everything in it opens a panel above
 *  the transcript, and none of it is something you press mid-turn.
 *
 *  A `<details>` rather than a hand-built popover: it is a disclosure and the
 *  browser already knows how to open one from the keyboard. Driven through a
 *  ref, not a controlled `open`: the element owns that state, and driving it
 *  from React means fighting the browser for it on every keyboard toggle.
 *  `open` here only follows it, for the one thing that has to know -- while
 *  the menu is up, Escape and a press anywhere outside it shut it, as they
 *  shut every other overlay in the app (`Dismiss`). A disclosure alone does
 *  neither, so the menu stood over the panel it was hiding until its own
 *  summary was found and pressed again.
 *
 *  It closes on a pick too, because a menu still standing over the panel it
 *  just opened is in the way of the thing you asked for. */
export function SceneMenu({ panels, onNewScene }: {
  /** One list, so a panel added tomorrow is one row rather than a button, a
   *  label and a place in the wrap order. `open` is the panel's own flag,
   *  which is what lets the row say which one is showing without a second
   *  copy of that state. */
  panels: ScenePanel[];
  onNewScene: () => void;
}) {
  const menu = useRef<HTMLDetailsElement>(null);
  const summary = useRef<HTMLElement>(null);
  const [open, setOpen] = useState(false);

  function shut() {
    if (menu.current) menu.current.open = false;
    setOpen(false);
  }

  /** Run a menu item, then shut the menu it was picked from.
   *
   *  On each item rather than one handler on the container: a click handler
   *  on a `<div>` is a control a keyboard cannot reach, and every item here
   *  is already a button that can carry it. */
  function pick(run: () => void) {
    shut();
    run();
  }

  function dismiss(by: DismissedBy) {
    shut();
    if (by === "escape") summary.current?.focus();
  }

  return (
    <details className="scene-menu" ref={menu} onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary className="scene-action" ref={summary}>Scene ⋯</summary>
      <div className="scene-menu-options">
        {panels.map(({ label, open: shown, toggle }) => (
          <button key={label} className="scene-menu-item" aria-pressed={shown}
                  onClick={() => pick(toggle)}>
            {label}{shown ? " ✓" : ""}
          </button>
        ))}
        <button className="scene-menu-item" onClick={() => pick(onNewScene)}>+ New scene</button>
      </div>
      {open && <Dismiss within={menu} label="Close the scene menu" onDismiss={dismiss} />}
    </details>
  );
}
