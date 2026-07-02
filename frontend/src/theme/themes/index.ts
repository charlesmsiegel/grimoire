import type { Theme } from "../types";
import codex from "./codex";
import manuscript from "./manuscript";
import astral from "./astral";

export const DEFAULT_THEME = "codex";

// Register a new theme by adding its import to this array.
const all: Theme[] = [codex, manuscript, astral];

export const themes: Record<string, Theme> = Object.fromEntries(
  all.map((t) => [t.name, t]),
);

export const themeList = all;

export function resolveTheme(name: string): Theme {
  return themes[name] ?? themes[DEFAULT_THEME];
}
