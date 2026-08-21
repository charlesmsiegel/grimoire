/** The app's one keyboard.
 *
 *  Every binding in the app is registered here and dispatched from a single
 *  `keydown` listener, so that "which shortcut does this keystroke mean" has
 *  one answer computed in one place — including the answer "none of them,
 *  something is open on top". Before this, three components each added their
 *  own `window` listener and nothing could see the others (#193).
 *
 *  Module state rather than a React context, deliberately. There is one
 *  keyboard and one `window`, so a second registry would be a bug rather than
 *  a feature; and a context would need an inert default for the dozens of
 *  suites that render a component bare, which is exactly how a shortcut ships
 *  silently doing nothing. The listener is installed on the first registration
 *  and removed with the last, so importing this module costs nothing and a
 *  torn-down test leaves nothing behind. */

import { chordOf, isTypingTarget } from "./keys";

export type Hotkey = {
  /** The chord this fires on, as `keys.ts` spells one: `"n"`, `"mod+enter"`. */
  keys: string;
  run: () => void;
  /** What the help overlay calls it. A binding with no label is real but
   *  undocumented — an overlay's Escape while it is the only thing it does. */
  label?: string;
  /** Which help section it files under. */
  group?: string;
  /** `false` while the control it mirrors is disabled. Read at dispatch, so it
   *  is never a render behind the screen. */
  enabled?: boolean;
  /** Fire even while the caret is in prose. For chords a reader means from
   *  inside the composer — the send chord, Escape — and nothing else: a bare
   *  letter that ignored this would eat the word being typed. */
  whileTyping?: boolean;
  /** Fire even while an overlay is open. The palette and this help overlay
   *  promise to be reachable from anywhere; nothing else may claim that. */
  global?: boolean;
};

/** One component's bindings. `modal` means "I am covering the screen": while a
 *  modal scope is on top, only its own keys (and `global` ones) fire. */
export type Scope = { keys: Hotkey[]; modal?: boolean };

/** Scopes are held as getters, called at dispatch, so a binding table needs no
 *  memoization from its caller and always reflects the current render. */
type ScopeSource = () => Scope;

const scopes: ScopeSource[] = [];

function onKeyDown(e: KeyboardEvent) {
  // Something on the page already claimed this keystroke — the composer's own
  // Enter, a `<select>` swallowing an arrow. Handling it again would be the
  // second half of a double send.
  if (e.defaultPrevented) return;
  // An IME sends a keydown for every keystroke that is still assembling a
  // character (`keyCode` 229 is what browsers without `isComposing` send), and
  // none of them is a chord the reader typed.
  if (e.isComposing || e.keyCode === 229) return;
  const chord = chordOf(e);
  if (!chord) return;
  // Both, because they disagree: an event fired at the window while the
  // composer holds the caret has a target that is not prose but a focus that
  // is, and a keystroke inside a field is inside it either way.
  const typing = isTypingTarget(e.target) || isTypingTarget(document.activeElement);

  // Newest first. An overlay registers after whatever it covers — either
  // because it mounted later, or because opening re-registered it (see
  // `useHotkeys`) — so registration order is z-order.
  const open = scopes.map((get) => get()).reverse();
  const modalAt = open.findIndex((s) => s.modal);
  const tries: Array<[Scope, boolean]> = modalAt < 0
    ? open.map((s) => [s, false])
    // Under an overlay, only the bindings that said they outlive one are still
    // eligible — and the overlay itself goes first, so a chord it binds is
    // never answered by the view underneath.
    : [[open[modalAt], false], ...open.filter((_, i) => i !== modalAt).map((s) => [s, true] as [Scope, boolean])];

  for (const [scope, onlyGlobal] of tries) {
    for (const key of scope.keys) {
      if (key.keys !== chord) continue;
      if (key.enabled === false) continue;
      if (onlyGlobal && !key.global) continue;
      if (typing && !key.whileTyping) continue;
      e.preventDefault();
      key.run();
      return;
    }
  }
}

/** Offer `get`'s bindings until the returned function is called. */
export function registerScope(get: ScopeSource): () => void {
  if (!scopes.length) window.addEventListener("keydown", onKeyDown);
  scopes.push(get);
  return () => {
    const at = scopes.indexOf(get);
    if (at >= 0) scopes.splice(at, 1);
    if (!scopes.length) window.removeEventListener("keydown", onKeyDown);
  };
}

/** Every binding currently registered, oldest scope first — what the help
 *  overlay lists, including the ones disabled right now. */
export function activeHotkeys(): Hotkey[] {
  return scopes.flatMap((get) => get().keys);
}
