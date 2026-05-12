import { useEffect } from "react";

export interface ShortcutBinding {
  /** Key value matched against KeyboardEvent.key (e.g. "k", "ArrowDown", "?"). */
  key: string;
  ctrlOrMeta?: boolean;
  shift?: boolean;
  alt?: boolean;
  handler: (event: KeyboardEvent) => void;
  /** When true, fires even if focus is in a text input. Default false. */
  fireInInputs?: boolean;
  description?: string;
}

function isEditable(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  return target.isContentEditable;
}

export function useKeyboardShortcuts(bindings: ShortcutBinding[]): void {
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      for (const b of bindings) {
        if (event.key !== b.key) continue;
        const ctrlMeta = event.ctrlKey || event.metaKey;
        if ((b.ctrlOrMeta ?? false) !== ctrlMeta) continue;
        if ((b.shift ?? false) !== event.shiftKey) continue;
        if ((b.alt ?? false) !== event.altKey) continue;
        if (!b.fireInInputs && isEditable(event.target)) continue;
        b.handler(event);
        return;
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [bindings]);
}
