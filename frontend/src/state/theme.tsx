import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";

import {
  ThemeContext,
  type Density,
  type FontFamily,
  type ResolvedTheme,
  type ThemeMode,
} from "./themeContext";

const STORAGE_KEY = "grimoire.theme";
const FONT_KEY = "grimoire.font";
const DENSITY_KEY = "grimoire.density";

function readStoredMode(): ThemeMode {
  if (typeof window === "undefined") return "system";
  const raw = window.localStorage.getItem(STORAGE_KEY);
  if (raw === "light" || raw === "dark" || raw === "system") return raw;
  return "system";
}

function readStoredFont(): FontFamily {
  if (typeof window === "undefined") return "system";
  const raw = window.localStorage.getItem(FONT_KEY);
  if (raw === "system" || raw === "serif" || raw === "dyslexia") return raw;
  return "system";
}

function readStoredDensity(): Density {
  if (typeof window === "undefined") return "comfortable";
  const raw = window.localStorage.getItem(DENSITY_KEY);
  if (raw === "comfortable" || raw === "compact") return raw;
  return "comfortable";
}

function systemPrefersDark(): boolean {
  if (typeof window === "undefined") return false;
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<ThemeMode>(() => readStoredMode());
  const [systemDark, setSystemDark] = useState<boolean>(() => systemPrefersDark());
  const [fontFamily, setFontFamilyState] = useState<FontFamily>(() => readStoredFont());
  const [density, setDensityState] = useState<Density>(() => readStoredDensity());

  useEffect(() => {
    if (typeof window === "undefined") return;
    const mql = window.matchMedia("(prefers-color-scheme: dark)");
    const handler = (e: MediaQueryListEvent) => setSystemDark(e.matches);
    mql.addEventListener("change", handler);
    return () => mql.removeEventListener("change", handler);
  }, []);

  const resolved: ResolvedTheme = mode === "system" ? (systemDark ? "dark" : "light") : mode;

  useEffect(() => {
    const root = document.documentElement;
    root.dataset.theme = resolved;
    root.style.colorScheme = resolved;
  }, [resolved]);

  // §17: set `data-font` and `data-density` on <html> so CSS in index.css can
  // switch font-family and spacing tokens via attribute selectors.
  useEffect(() => {
    document.documentElement.dataset.font = fontFamily;
  }, [fontFamily]);
  useEffect(() => {
    document.documentElement.dataset.density = density;
  }, [density]);

  const setMode = useCallback((next: ThemeMode) => {
    setModeState(next);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(STORAGE_KEY, next);
    }
  }, []);

  const setFontFamily = useCallback((next: FontFamily) => {
    setFontFamilyState(next);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(FONT_KEY, next);
    }
  }, []);

  const setDensity = useCallback((next: Density) => {
    setDensityState(next);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(DENSITY_KEY, next);
    }
  }, []);

  const cycle = useCallback(() => {
    setMode(mode === "light" ? "dark" : mode === "dark" ? "system" : "light");
  }, [mode, setMode]);

  const value = useMemo(
    () => ({
      mode,
      resolved,
      setMode,
      cycle,
      fontFamily,
      setFontFamily,
      density,
      setDensity,
    }),
    [mode, resolved, setMode, cycle, fontFamily, setFontFamily, density, setDensity],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}
