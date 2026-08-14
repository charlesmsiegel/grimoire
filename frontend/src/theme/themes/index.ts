import type { Theme, ThemeMode, ThemeName } from "../types";
import light from "./light";
import dark from "./dark";

/** What a fresh install gets, and what an unreadable config falls back to.
 *  `system` rather than a look: the OS already knows whether it is night. */
export const DEFAULT_MODE: ThemeMode = "system";

export const themes: Record<ThemeName, Theme> = { light, dark };

/** The Light / Dark / System control's options, in the order it shows them. */
export const MODES: { mode: ThemeMode; label: string }[] = [
  { mode: "light", label: "LIGHT" },
  { mode: "dark", label: "DARK" },
  { mode: "system", label: "SYSTEM" },
];

/** Stored theme names from the three-theme era. The config field survived the
 *  collapse unchanged, so every install that ever picked a theme still has one
 *  of these on disk; they are mapped on read rather than migrated on disk, so
 *  a store shared with an older build keeps working in both directions. */
const LEGACY: Record<string, ThemeMode> = {
  codex: "light",
  manuscript: "dark",
  astral: "dark",
};

/** Coerce whatever the config holds into a mode. Anything unrecognised —
 *  a theme from even further back, a truncated file, `undefined` — is the
 *  same case as never having chosen: follow the system. */
export function normalizeMode(stored: string | null | undefined): ThemeMode {
  if (stored === "light" || stored === "dark" || stored === "system") return stored;
  return LEGACY[stored ?? ""] ?? DEFAULT_MODE;
}

/** Which look `mode` means right now. `system` is the only one that needs the
 *  second argument, and it is passed in rather than read here so the caller
 *  owns the `matchMedia` subscription. */
export function resolveName(mode: ThemeMode, prefersDark: boolean): ThemeName {
  if (mode === "system") return prefersDark ? "dark" : "light";
  return mode;
}

export function resolveTheme(mode: ThemeMode, prefersDark: boolean): Theme {
  return themes[resolveName(mode, prefersDark)];
}
