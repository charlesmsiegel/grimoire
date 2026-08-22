import { useEffect, useRef, useState } from "react";
import {
  otherModalOpen, registerScope, scopeChanged, scopeSeq, watchHotkeys,
  type Hotkey, type Scope,
} from "./registry";

export type { Hotkey, Scope };

/** Bind `keys` for as long as the calling component is mounted. Returns the
 *  scope, which only a caller that has to reason about the others needs (the
 *  help sheet, to leave itself out of "what is on top").
 *
 *  The table is read at dispatch rather than copied at registration, so the
 *  caller owes no `useMemo`: `enabled` and every closure are this render's.
 *  That is the opposite bargain from `usePaletteSource`, and on purpose — a
 *  binding table changes with `busy` on nearly every render, and a registry
 *  that re-subscribed on each one would be a render loop wearing a hook.
 *
 *  `modal` says the component is covering the screen. It is a dependency of
 *  the registration effect, which is what makes "on top" mean the overlay that
 *  OPENED last rather than the one that mounted last: the palette mounts with
 *  the shell, long before any drawer, and still has to be what Escape reaches
 *  while it is open. */
export function useHotkeys(keys: Hotkey[], opts: { modal?: boolean } = {}): Scope {
  const modal = !!opts.modal;
  // Rewritten in render, not in an effect: the help sheet reads the registry
  // while rendering, and an effect would show it the previous pass's table.
  const held = useRef<Scope | null>(null);
  if (held.current === null) held.current = { keys, modal, seq: scopeSeq() };
  else { held.current.keys = keys; held.current.modal = modal; }
  const scope = held.current;
  useEffect(() => registerScope(scope), [scope, modal]);
  // Every render, with no dependency list on purpose: `enabled` moves with the
  // owner's render and nothing else can see that happen. Free while nobody is
  // watching, which is every moment the shortcuts sheet is closed.
  useEffect(() => scopeChanged(scope));
  return scope;
}

/** Whether an overlay other than `self` is up, kept current.
 *
 *  For a component that must wait for the screen to be clear rather than
 *  arrive on top of it -- see `otherModalOpen`. `self` is the scope its caller
 *  got from `useHotkeys`, so its own modality never counts against it. */
export function useOtherModalOpen(self?: Scope): boolean {
  const [open, setOpen] = useState(() => otherModalOpen(self));
  useEffect(() => {
    setOpen(otherModalOpen(self));
    return watchHotkeys(() => setOpen(otherModalOpen(self)));
  }, [self]);
  return open;
}
