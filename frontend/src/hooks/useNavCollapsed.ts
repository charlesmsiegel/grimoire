import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "grimoire.nav.collapsed";

function readInitial(): boolean {
  if (typeof window === "undefined") return false;
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

export function useNavCollapsed(): {
  collapsed: boolean;
  toggle: () => void;
  setCollapsed: (next: boolean) => void;
} {
  const [collapsed, setCollapsedState] = useState<boolean>(readInitial);

  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_KEY, collapsed ? "1" : "0");
    } catch {
      // ignore (private mode / quota)
    }
  }, [collapsed]);

  const toggle = useCallback(() => setCollapsedState((v) => !v), []);
  const setCollapsed = useCallback((next: boolean) => setCollapsedState(next), []);

  return { collapsed, toggle, setCollapsed };
}
