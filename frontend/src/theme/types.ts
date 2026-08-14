/** The two concrete looks. Everything rendered resolves to one of these. */
export type ThemeName = "light" | "dark";

/** What the user picks, and what the config file stores. `system` is not a
 *  look — it defers to `prefers-color-scheme` and re-resolves when that
 *  changes. */
export type ThemeMode = ThemeName | "system";

export type Theme = {
  name: ThemeName;
  label: string;
  tokens: Record<string, string>;
};
