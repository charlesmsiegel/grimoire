import { createContext } from "react";

export type ThemeMode = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";
export type FontFamily = "system" | "serif" | "dyslexia";
export type Density = "comfortable" | "compact";

export interface ThemeContextValue {
  mode: ThemeMode;
  resolved: ResolvedTheme;
  setMode: (mode: ThemeMode) => void;
  cycle: () => void;
  fontFamily: FontFamily;
  setFontFamily: (font: FontFamily) => void;
  density: Density;
  setDensity: (density: Density) => void;
}

export const ThemeContext = createContext<ThemeContextValue | null>(null);
