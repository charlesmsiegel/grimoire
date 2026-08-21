import { useCallback, useEffect, useRef } from "react";
import { registerScope, type Hotkey, type Scope } from "./registry";

export type { Hotkey, Scope };

/** Bind `keys` for as long as the calling component is mounted.
 *
 *  The array is read at dispatch rather than at registration, so the caller
 *  owes no `useMemo`: `enabled` and every closure are this render's. That is
 *  the opposite bargain from `usePaletteSource`, and on purpose — a binding
 *  table changes with `busy` on nearly every render, and a registry that
 *  re-subscribed on each one would be a render loop wearing a hook.
 *
 *  `modal` says the component is covering the screen. It is a dependency of
 *  the registration effect, which is what makes "on top" mean the overlay that
 *  OPENED last rather than the one that mounted last: the palette mounts with
 *  the shell, long before any drawer, and still has to be the thing Escape
 *  reaches while it is open. */
export function useHotkeys(keys: Hotkey[], opts: { modal?: boolean } = {}): void {
  const modal = !!opts.modal;
  // Assigned in render, not in an effect: the help overlay reads the registry
  // while rendering, and an effect would show it the previous pass's table.
  const latest = useRef(keys);
  latest.current = keys;
  const modalRef = useRef(modal);
  modalRef.current = modal;
  const source = useCallback(() => ({ keys: latest.current, modal: modalRef.current }), []);
  useEffect(() => registerScope(source), [source, modal]);
}
