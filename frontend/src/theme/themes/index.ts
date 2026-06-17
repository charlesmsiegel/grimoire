import type { Theme } from "../types";
import occult from "./occult";
import terminal from "./terminal";
import ink from "./ink";

export const DEFAULT_THEME = "occult";

// Register a new theme by adding its import to this array.
const all: Theme[] = [occult, terminal, ink];

export const themes: Record<string, Theme> = Object.fromEntries(
  all.map((t) => [t.name, t]),
);

export const themeList = all;

export function resolveTheme(name: string): Theme {
  return themes[name] ?? themes[DEFAULT_THEME];
}
