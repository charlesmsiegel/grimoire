import { useEffect, useRef, type MouseEvent, type RefObject } from 'react';

export const nativeControl = 'a,button,input,select,textarea,label,summary,[role="button"],[contenteditable="true"]';

/** Wheel is deliberately native/non-passive: React's delegated listener cannot
 * cancel browser scrolling. Scrollable prose owns its wheel even at its ends,
 * so reading the bottom line never accidentally turns the page. */
export function useVNNavigation(root: RefObject<HTMLElement>, options: {
  enabled: boolean; hidden: boolean; blocked: boolean;
  move: (direction: number) => void; show: () => void;
}) {
  const live = useRef(options); live.current = options;
  useEffect(() => {
    const node = root.current;
    if (!node) return;
    let total = 0, lastEvent = 0, lastMove = -Infinity;
    function wheel(event: WheelEvent) {
      const state = live.current;
      if (!state.enabled || state.hidden || state.blocked || event.defaultPrevented
        || event.ctrlKey || event.altKey || event.metaKey || event.shiftKey
        || Math.abs(event.deltaX) > Math.abs(event.deltaY) || !event.deltaY
        || window.getSelection()?.toString()) return;
      if (document.activeElement?.matches('input,textarea,select,[contenteditable="true"]')) return;
      let target = event.target instanceof Element ? event.target : null;
      if (target?.closest(nativeControl)) return;
      while (target && target !== node) {
        const style = getComputedStyle(target);
        if ((/(auto|scroll)/.test(style.overflowY) && target.scrollHeight > target.clientHeight)
          || (/(auto|scroll)/.test(style.overflowX) && target.scrollWidth > target.clientWidth)) return;
        target = target.parentElement;
      }
      event.preventDefault();
      const now = performance.now();
      const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? node!.clientHeight || 600 : 1);
      if (now - lastEvent > 200 || Math.sign(delta) !== Math.sign(total)) total = 0;
      lastEvent = now;
      if (now - lastMove < 200) { total = 0; return; }
      total += delta;
      if (Math.abs(total) < 40) return;
      lastMove = now;
      state.move(Math.sign(total)); total = 0;
    }
    node.addEventListener('wheel', wheel, { passive: false });
    return () => node.removeEventListener('wheel', wheel);
  }, [root]);
  return (event: MouseEvent<HTMLElement>) => {
    if (options.blocked || event.defaultPrevented || !event.currentTarget.contains(event.target as Node)
      || (event.target as Element).closest(nativeControl) || window.getSelection()?.toString()) return;
    if (options.hidden) options.show();
    else if (options.enabled) options.move(1);
  };
}
