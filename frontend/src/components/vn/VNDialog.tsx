import { useLayoutEffect, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { useHotkeys } from '../../shortcuts/useHotkeys';
import { isTopModal } from '../../shortcuts/registry';
import { modalFocusables } from '../art/useArtModal';

/** A registry-aware overlay, rather than a native top-layer dialog: the art
 * picker and its viewer must be able to open their own portals above this. */
export function VNDialog({ title, children, onClose, busy = false }: {
  title: string; children: ReactNode; onClose: () => void; busy?: boolean;
}) {
  const root = useRef<HTMLDivElement>(null);
  function tab(backward: boolean) {
    if (!root.current) return;
    const nodes = modalFocusables(root.current);
    const index = nodes.indexOf(document.activeElement as HTMLElement);
    nodes[(index + (backward ? -1 : 1) + nodes.length) % nodes.length]?.focus();
  }
  const scope = useHotkeys([
    { keys: 'escape', whileTyping: true, enabled: !busy, label: `Close ${title}`, run: onClose },
    { keys: 'tab', whileTyping: true, run: () => tab(false) },
    { keys: 'shift+tab', whileTyping: true, run: () => tab(true) },
  ], { modal: true });
  useLayoutEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const focus = () => (root.current?.querySelector<HTMLElement>('[data-initial-focus]')
      ?? (root.current && modalFocusables(root.current)[0]) ?? root.current)?.focus({ preventScroll: true });
    focus();
    function contain(event: FocusEvent) {
      if (isTopModal(scope) && !root.current?.closest('[inert]') && !root.current?.contains(event.target as Node)) focus();
    }
    document.addEventListener('focusin', contain);
    return () => {
      document.removeEventListener('focusin', contain);
      if (opener?.isConnected && !opener.closest('[hidden],[inert]')) opener.focus({ preventScroll: true });
    };
  }, [scope]);
  return createPortal(<div className="vn-modal-backdrop">
    <div ref={root} className="vn-modal" role="dialog" aria-modal="true" aria-label={title} tabIndex={-1}>
      <header><h2>{title}</h2><button disabled={busy} onClick={onClose} aria-label={`Close ${title}`}>Close</button></header>
      {children}
    </div>
  </div>, document.body);
}
