/** The app's one keyboard.
 *
 *  Every binding in the app is registered here and dispatched from a single
 *  `keydown` listener, so that "which shortcut does this keystroke mean" has
 *  one answer computed in one place — including the answer "none of them,
 *  something is open on top". Before this, three components each added their
 *  own `window` listener and none could see the others (#193).
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
  /** The chord this fires on, as `keys.ts` spells one: `"n"`, `"mod+enter"`.
   *  Lowercase, modifiers first — matched literally against what `chordOf`
   *  produces. */
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
  /** Fire even while an overlay is open. The palette and the help sheet
   *  promise to be reachable from anywhere; nothing else may claim that. */
  global?: boolean;
};

/** One component's bindings. `modal` means "I am covering the screen": while a
 *  modal scope is on top, only its own keys (and `global` ones) fire.
 *
 *  Held as a live object whose fields the owning component rewrites each
 *  render, so a binding table needs no memoization from its caller, always
 *  reflects the current render — and costs no allocation on the dispatch path,
 *  which every keystroke in a writing app runs down.
 *
 *  `seq` is birth order, which is NOT the order the list below is in: opening
 *  an overlay moves its scope to the end (that is how z-order is kept), and
 *  the help sheet does it to itself. Dispatch wants the moving order; the
 *  sheet's own list wants the fixed one, or its rows shuffle between one
 *  opening and the next. */
export type Scope = { keys: Hotkey[]; modal?: boolean; seq: number };

const scopes: Scope[] = [];
let born = 0;

/** Told whenever a scope registers, unregisters, or rewrites its table, with
 *  the scope that moved. The help sheet is the only subscriber, and only while
 *  it is open: what it lists is "what you can press right now", and a turn
 *  finishing or a panel opening underneath moves that while the sheet is up.
 *  Closed, this set is empty and announcing costs a loop over nothing. */
type Watcher = (changed: Scope) => void;
const watchers = new Set<Watcher>();

export function watchHotkeys(fn: Watcher): () => void {
  watchers.add(fn);
  return () => { watchers.delete(fn); };
}

/** Announce `scope`. Carries which scope moved so a subscriber can ignore its
 *  own -- the sheet announces on its own render like everyone else, and acting
 *  on that would be a render loop. */
export function scopeChanged(scope: Scope): void {
  for (const fn of [...watchers]) fn(scope);
}

/** The next birth number. Taken once, when a component first builds its scope. */
export function scopeSeq(): number {
  return ++born;
}

/** The first binding in `scope` that answers this keystroke, run. */
function fire(
  scope: Scope, e: KeyboardEvent, chord: string, typing: boolean, onlyGlobal: boolean,
): boolean {
  for (const key of scope.keys) {
    if (key.keys !== chord) continue;
    if (key.enabled === false) continue;
    if (onlyGlobal && !key.global) continue;
    if (typing && !key.whileTyping) continue;
    e.preventDefault();
    key.run();
    return true;
  }
  return false;
}

function onKeyDown(e: KeyboardEvent) {
  // Something on the page already claimed this keystroke — the composer's own
  // Enter, a `<select>` swallowing an arrow. Handling it again would be the
  // second half of a double send.
  if (e.defaultPrevented) return;
  // Auto-repeat is the key still being held, not pressed again. Nothing bound
  // here wants a second firing from that -- ⌘⏎ held for half a second would
  // send the turn several times over, since `busy` cannot come back through
  // the closure until React has re-rendered -- and a binding that ever does
  // want it (arrow-key travel) can ask for it then.
  if (e.repeat) return;
  // An IME sends a keydown for every keystroke that is still assembling a
  // character (`keyCode` 229 is what a browser without `isComposing` sends),
  // and none of them is a chord the reader typed.
  if (e.isComposing || e.keyCode === 229) return;
  const chord = chordOf(e);
  if (!chord) return;
  // Both, because they disagree: an event fired at the window while the
  // composer holds the caret has a target that is not prose but a focus that
  // is, and a keystroke inside a field is inside it either way.
  const typing = isTypingTarget(e.target) || isTypingTarget(document.activeElement);

  // Newest first — an overlay registers after whatever it covers, either by
  // mounting later or by re-registering when it opened (see `useHotkeys`), so
  // registration order is z-order.
  const modalAt = topModal(scopes);
  // The overlay on top answers first, and everything under it is left with
  // only the bindings that said they outlive one.
  if (modalAt >= 0 && fire(scopes[modalAt], e, chord, typing, false)) return;
  for (let i = scopes.length - 1; i >= 0; i--) {
    if (i === modalAt) continue;
    if (fire(scopes[i], e, chord, typing, modalAt >= 0)) return;
  }
}

function topModal(list: Scope[]): number {
  for (let i = list.length - 1; i >= 0; i--) if (list[i].modal) return i;
  return -1;
}

/** Offer `scope`'s bindings until the returned function is called. */
export function registerScope(scope: Scope): () => void {
  if (!scopes.length) window.addEventListener("keydown", onKeyDown);
  scopes.push(scope);
  scopeChanged(scope);
  return () => {
    const at = scopes.indexOf(scope);
    if (at >= 0) scopes.splice(at, 1);
    if (!scopes.length) window.removeEventListener("keydown", onKeyDown);
    scopeChanged(scope);
  };
}

/** A binding, and whether pressing it right now would actually do anything. */
export type HotkeyRow = { key: Hotkey; reachable: boolean };

/** Every binding registered, oldest scope first — what the help sheet lists.
 *
 *  `reachable` asks the same question dispatch does, on the reader's behalf: a
 *  binding is unreachable while its own `enabled` is false, and also while an
 *  overlay is holding its whole scope off. The sheet is opened from under an
 *  overlay (it is `global`), so without the second half it would list half the
 *  page's shortcuts as live at exactly the moment none of them work.
 *
 *  `ignoring` is the caller's own scope, left out of "what is on top" — the
 *  sheet is a modal too, and the overlay that matters to the reader is the one
 *  it opened in front of. Its keys are still listed, and still live: it is the
 *  thing on top.
 */
export function activeHotkeys(ignoring?: Scope): HotkeyRow[] {
  let modal: Scope | null = null;
  for (let i = scopes.length - 1; i >= 0; i--) {
    if (scopes[i] !== ignoring && scopes[i].modal) { modal = scopes[i]; break; }
  }
  // Birth order, not the dispatch order: see `Scope.seq`.
  return [...scopes].sort((a, b) => a.seq - b.seq).flatMap((scope) => scope.keys.map((key) => ({
    key,
    reachable: key.enabled !== false
      && (!modal || scope === modal || scope === ignoring || !!key.global),
  })));
}
