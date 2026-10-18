import { useEffect, useId, useRef, useState } from "react";
import { Dismiss, type DismissedBy } from "./Dismiss";

/** One thing a post's actions can do. */
export type PostAction = {
  /** What it is called: the toolbar's tooltip, and the visible label where
   *  the actions are a menu. */
  label: string;
  /** Its accessible name, which carries the post it acts on ("Edit message
   *  3"): a screen reader hears every post's actions, and the number is what
   *  tells one post's Edit from the next. */
  name: string;
  /** A single monochrome glyph. Text presentation, never an emoji: a
   *  full-colour picture among hairline glyphs reads as a different app. */
  glyph: string;
  run: () => void;
  disabled?: boolean;
  /** A toggle's state, as `aria-pressed`; absent on a plain action. */
  pressed?: boolean;
  /** Takes something away for good -- drawn in the alert colour. */
  danger?: boolean;
};

/** A post's actions: one list, drawn the way the device can reach it.
 *
 *  Where the pointer can hover, the list is a compact toolbar at the post's
 *  top-right that shows while the post is hovered or holds focus. Where it
 *  cannot (a phone, a tablet), nothing ever hovers, so the toolbar would
 *  either never show or always show -- eight tiny glyphs down every post's
 *  gutter, one of them "delete this and everything after". There the list
 *  sits behind a single ⋯ in the gutter and opens as a sheet of labelled,
 *  full-size rows at the foot of the screen. Which of the two is up is the stylesheet's call, keyed on
 *  `(hover: none)` -- the input rather than the width, as every other
 *  hover-only control in the app is -- so this component draws the one list
 *  either way and only owns whether the menu is open.
 *
 *  Both are out of the post's flow: a post is as tall as its text. The
 *  gutter's icons used to be a column in the flow, invisible until hovered,
 *  which set every post's height to theirs.
 *
 *  The menu closes on a pick, on Escape (through the shortcut registry, as
 *  any overlay's does) and on a press anywhere outside it. Escape hands focus
 *  back to the ⋯, so a keyboard is never left on a control that just went. */
export function PostActions({ post, actions }: {
  /** The post's 1-based number, as its actions' names carry it. */
  post: number;
  actions: PostAction[];
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const more = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const listId = useId();

  // Opening moves focus into the menu, onto the first thing it can do.
  useEffect(() => {
    if (open) list.current?.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus();
  }, [open]);

  function dismiss(by: DismissedBy) {
    setOpen(false);
    if (by === "escape") more.current?.focus();
  }

  if (actions.length === 0) return null;
  return (
    <div className={"post-actions" + (open ? " open" : "")} ref={root}>
      <button type="button" className="post-more" ref={more}
              aria-label={`Actions for message ${post}`}
              aria-expanded={open} aria-controls={listId}
              onClick={() => setOpen((v) => !v)}>⋯</button>
      <div className="post-actions-list" id={listId} ref={list}
           role="group" aria-label={`Message ${post} actions`}>
        {actions.map((action) => (
          <button key={action.label} type="button"
                  className={"post-action" + (action.danger ? " danger" : "")}
                  aria-label={action.name} title={action.label}
                  aria-pressed={action.pressed} disabled={action.disabled}
                  onClick={() => { setOpen(false); action.run(); }}>
            <span className="post-action-glyph" aria-hidden>{action.glyph}</span>
            <span className="post-action-label">{action.label}</span>
          </button>
        ))}
      </div>
      {open && <Dismiss within={root} label="Close this post's actions" onDismiss={dismiss} />}
    </div>
  );
}
