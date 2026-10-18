import { useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import RerollRoutePicker, { canGenerateOnRoute, type RerollRoute } from "../RerollRoute";
import { useHotkeys } from "../../shortcuts/useHotkeys";

/** The reroll box: where this reroll goes, and what it is told once there.
 *
 *  An overlay, so it registers as one (`modal`): while it is up the scene's
 *  bindings are held off and Escape is its own, from any control in it or
 *  from nowhere at all. The model list is radios, which are not typing
 *  targets, so without the hold a bare ← or → on one answered the scene's
 *  variant swipe -- activating another variant behind the box, or reopening
 *  it and dropping the route being chosen -- and the dispatcher's
 *  `preventDefault` took the radios' own arrow travel with it. A dialog the
 *  picker opens (a model's Test…) is a modal that registers later, so it is
 *  on top, and its Escape closes only it.
 *
 *  Enter stays on the element, where it is a commit: from the guidance box,
 *  or from a model row. The picker keeps the Enter its own controls answer (a
 *  select, a button, the typed-id box) by stopping it before it gets here. */
export default function RerollPopover({ cid, pop, rolling, generationDisabled, actions }: {
  cid: string;
  pop: { prompt: string; route: RerollRoute };
  rolling: boolean;
  generationDisabled: boolean;
  actions: {
    setRerollPrompt: (text: string | null) => void;
    setRerollRoute: (route: RerollRoute) => void;
    reroll: () => void;
  };
}) {
  const anchor = useRef<HTMLSpanElement>(null);
  const root = useRef<HTMLSpanElement>(null);
  const close = useRef<HTMLButtonElement>(null);
  const guideRow = useRef<HTMLSpanElement>(null);
  const guidance = useRef<HTMLInputElement>(null);
  const [position, setPosition] = useState<{
    top: number; left: number; maxHeight: number; maxWidth: number; compact: boolean;
  } | null>(null);
  useLayoutEffect(() => {
    const gutter = anchor.current?.parentElement;
    const form = root.current;
    if (!gutter || !form) return;
    const stream = gutter.closest(".stream");
    const viewport = window.visualViewport;
    const measure = () => {
      const bounds = stream?.getBoundingClientRect();
      const viewTop = viewport?.offsetTop ?? 0, viewLeft = viewport?.offsetLeft ?? 0;
      const viewBottom = viewTop + (viewport?.height ?? innerHeight);
      const viewRight = viewLeft + (viewport?.width ?? innerWidth);
      // The transcript's own viewport ends below its head and above its
      // composer. Escaping overflow with a portal is only half the repair:
      // a fixed box can still paint under that chrome unless it fits here.
      let top = Math.max(bounds?.top ?? viewTop, viewTop) + 8;
      let left = Math.max(bounds?.left ?? viewLeft, viewLeft) + 8;
      let bottom = Math.min(bounds?.bottom ?? viewBottom, viewBottom) - 8;
      let right = Math.min(bounds?.right ?? viewRight, viewRight) - 8;
      const closeStyle = close.current && getComputedStyle(close.current);
      const closeHeight = (close.current?.getBoundingClientRect().height ?? 0)
        + (parseFloat(closeStyle?.marginTop ?? "0") || 0) + (parseFloat(closeStyle?.marginBottom ?? "0") || 0);
      const guideHeight = guideRow.current?.getBoundingClientRect().height ?? 0;
      const formStyle = getComputedStyle(form);
      const borders = (parseFloat(formStyle.borderTopWidth) || 0) + (parseFloat(formStyle.borderBottomWidth) || 0);
      // A keyboard or short landscape window can leave only a sliver of
      // transcript. Keep the fixed chrome plus one 44px touch row readable by
      // using the visual viewport then; normal windows still avoid the chrome.
      const compact = bottom - top < closeHeight + guideHeight + borders + 44;
      if (compact) {
        top = viewTop + 8; left = viewLeft + 8;
        bottom = viewBottom - 8; right = viewRight - 8;
      }
      const maxHeight = Math.max(0, bottom - top), maxWidth = Math.max(0, right - left);
      const box = form.getBoundingClientRect(), at = gutter.getBoundingClientRect();
      const height = Math.min(box.height, maxHeight), width = Math.min(box.width, maxWidth);
      const next = {
        top: Math.max(top, Math.min(at.bottom - height, bottom - height)),
        left: Math.max(left, Math.min(at.right + 10, right - width)),
        maxHeight, maxWidth, compact,
      };
      // ResizeObserver also sees the cap we set. Identical measurements must
      // retain state so observing that layout cannot create a render loop.
      setPosition((previous) => previous?.top === next.top && previous.left === next.left
        && previous.maxHeight === next.maxHeight && previous.maxWidth === next.maxWidth
        && previous.compact === next.compact ? previous : next);
    };
    measure();
    // This form opens on request, not on page load. Focus its guidance once
    // the portal is attached, without scrolling the transcript behind it.
    guidance.current?.focus({ preventScroll: true });
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(form);
    if (stream) observer?.observe(stream);
    document.addEventListener("scroll", measure, true);
    window.addEventListener("resize", measure);
    viewport?.addEventListener("resize", measure);
    viewport?.addEventListener("scroll", measure);
    return () => {
      observer?.disconnect();
      document.removeEventListener("scroll", measure, true);
      window.removeEventListener("resize", measure);
      viewport?.removeEventListener("resize", measure);
      viewport?.removeEventListener("scroll", measure);
    };
  }, []);
  useHotkeys(
    [{ keys: "escape", label: "Close the reroll box", group: "THIS PANEL", whileTyping: true,
       run: () => actions.setRerollPrompt(null) }],
    { modal: true },
  );
  // A model id typed into the picker and not yet taken: Reroll waits for it
  // (`RerollRoutePicker`'s `onPending`), and so does Enter's commit.
  const [pending, setPending] = useState(false);
  const disabled = rolling || pending || !canGenerateOnRoute(!generationDisabled, pop.route);
  return <><span ref={anchor} />{createPortal(
    // Keydown is only Enter's commit, filtered from what its controls send;
    // they are the interactive elements.
    // eslint-disable-next-line jsx-a11y/no-static-element-interactions
    <span className={`reroll-pop${position?.compact ? " reroll-pop-viewport" : ""}`} ref={root}
          style={position ? { top: position.top, left: position.left,
            maxHeight: position.maxHeight, maxWidth: position.maxWidth } : undefined}
          onKeyDown={(e) => {
            // React bubbles a portal's events through the tree that rendered
            // it, so a key in the test dialog -- portalled to the body --
            // arrives here as well. It is the dialog's, not the popover's.
            if (!(e.target instanceof Node) || !e.currentTarget.contains(e.target)) return;
            // `preventDefault` is what tells the shortcut dispatcher this
            // keystroke is spoken for: ⌘⏎ typed here means "reroll", not
            // "send", and without it both fired (PR #400 review).
            if (e.key === "Enter") { e.preventDefault(); if (!disabled) actions.reroll(); }
          }}>
      <button type="button" ref={close} className="subtle reroll-close" aria-label="Close the reroll box"
              onKeyDown={(event) => { if (event.key === "Enter") event.stopPropagation(); }}
              onClick={() => actions.setRerollPrompt(null)}>Close</button>
      {/* Above the guidance, not beside it: this is where the reroll goes,
          and the hint is what it says once it gets there. Untouched, it is
          Default: the campaign's own scene route, with no override. */}
      <RerollRoutePicker cid={cid} value={pop.route} onChange={actions.setRerollRoute}
                         onPending={setPending} />
      <span className="reroll-guide" ref={guideRow}>
        <input
          ref={guidance}
          placeholder="Guide the reroll (optional)…"
          aria-label="Reroll guidance"
          value={pop.prompt}
          onChange={(e) => actions.setRerollPrompt(e.target.value)}
        />
        <button className="btn-chrome" onClick={actions.reroll} disabled={disabled}>
          Reroll ▸
        </button>
      </span>
    </span>, document.body,
  )}</>;
}
