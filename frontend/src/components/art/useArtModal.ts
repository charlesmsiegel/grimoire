import { useLayoutEffect, useRef, useState } from "react";

export function modalFocusables(root: HTMLElement) {
  return Array.from(root.querySelectorAll<HTMLElement>(
    'button:not(:disabled),a[href],summary,input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex="0"]'))
    .filter((node) => !node.closest('[hidden],details:not([open]) :not(summary)'));
}

function canReturnFocus(node: HTMLElement | null | undefined): node is HTMLElement {
  if (!node?.isConnected || node.closest("[hidden],[inert]") || node.matches(":disabled")) return false;
  const visibility = getComputedStyle(node).visibility;
  if (visibility === "hidden" || visibility === "collapse") return false;
  // A connected context-sheet button can be hidden by a phone media query.
  // Inspect ancestors too: its own computed display does not reveal that.
  for (let ancestor: HTMLElement | null = node; ancestor; ancestor = ancestor.parentElement) {
    if (getComputedStyle(ancestor).display === "none") return false;
  }
  return true;
}

// A nested picker/viewer can unmount parent-first during route navigation.
// Independent snapshots would then reinstate a child's stale inert/overflow
// values. Own the background once and derive active portals from the stack.
const portals: HTMLElement[] = [];
const background = new Map<Element, string | null>();
let bodyOverflow = "";
function syncBackground() {
  if (!portals.length) {
    background.forEach((inert, node) => {
      if (inert === null) node.removeAttribute("inert"); else node.setAttribute("inert", inert);
    });
    background.clear();
    document.body.style.overflow = bodyOverflow;
    return;
  }
  const top = portals[portals.length - 1];
  Array.from(document.body.children).forEach((node) => {
    if (portals.includes(node as HTMLElement)) {
      if (node === top) node.removeAttribute("inert"); else node.setAttribute("inert", "");
    } else {
      if (!background.has(node)) background.set(node, node.getAttribute("inert"));
      node.setAttribute("inert", "");
    }
  });
  document.body.style.overflow = "hidden";
}

/** The viewer, reuse picker, and crop picker share one portal/focus contract.
 * Preserve existing background attributes, including a drawer's inert state,
 * and release global ownership in any React cleanup order. */
export function useArtModal(opener?: HTMLElement | null, fallbackFocus?: HTMLElement | null, initialFocus?: string) {
  const [portal] = useState(() => document.createElement("div"));
  const root = useRef<HTMLDivElement>(null);
  const restore = useRef({ opener: opener ?? (document.activeElement instanceof HTMLElement ? document.activeElement : null),
    fallbackFocus, initialFocus });
  // Source reparenting can replace the fallback control while a viewer stays
  // open. The original opener is captured; its surviving replacement is live.
  restore.current.fallbackFocus = fallbackFocus;
  useLayoutEffect(() => {
    const focusReturn = restore.current;
    if (!portals.length) bodyOverflow = document.body.style.overflow;
    document.body.append(portal);
    portals.push(portal); syncBackground();
    const first = focusReturn.initialFocus ? root.current?.querySelector<HTMLElement>(focusReturn.initialFocus)
      : root.current && modalFocusables(root.current)[0];
    first?.focus();
    const contain = (event: FocusEvent) => {
      if (root.current && !root.current.closest("[inert]") && event.target instanceof Node && !root.current.contains(event.target))
        modalFocusables(root.current)[0]?.focus();
    };
    document.addEventListener("focusin", contain);
    return () => {
      document.removeEventListener("focusin", contain);
      portal.remove();
      portals.splice(portals.indexOf(portal), 1); syncBackground();
      const target = canReturnFocus(focusReturn.opener) ? focusReturn.opener : focusReturn.fallbackFocus;
      // A surviving heading may be offscreen. Preserve every scroll owner,
      // including the page's nested scroller, while returning keyboard focus.
      if (canReturnFocus(target)) target.focus({ preventScroll: true });
    };
  }, [portal]);
  function trap(backward: boolean) {
    if (!root.current) return;
    const nodes = modalFocusables(root.current), at = nodes.indexOf(document.activeElement as HTMLElement);
    nodes[(at + (backward ? -1 : 1) + nodes.length) % nodes.length]?.focus();
  }
  return { root, portal, trap };
}
