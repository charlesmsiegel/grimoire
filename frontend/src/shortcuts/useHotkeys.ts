import { useEffect, useRef } from "react";
import { registerScope, scopeSeq, type Hotkey, type Scope } from "./registry";

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
  return scope;
}
