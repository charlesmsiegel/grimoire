import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { normalizeMode, resolveTheme } from "./themes";
import type { ThemeMode, ThemeName } from "./types";

type ThemeCtx = {
  /** What the user picked — the value the config stores. */
  mode: ThemeMode;
  /** What that resolves to right now. `mode` when it is a look; the system's
   *  answer when it is `system`. */
  name: ThemeName;
  setTheme: (mode: string) => void;
};
const Ctx = createContext<ThemeCtx | null>(null);

const DARK_QUERY = "(prefers-color-scheme: dark)";

/** jsdom has `matchMedia` only when a test shims it, and the Android WebView
 *  is old enough in the field to be worth not trusting either. No answer is
 *  "the OS is not telling us", which is light. */
function systemPrefersDark(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia(DARK_QUERY).matches;
}

function applyTheme(mode: ThemeMode, prefersDark: boolean): ThemeName {
  const theme = resolveTheme(mode, prefersDark);
  const root = document.documentElement;
  for (const [key, value] of Object.entries(theme.tokens)) {
    root.style.setProperty(key, value);
  }
  // Two attributes, because two questions get asked: `data-theme` is the look
  // (what CSS and a screenshot see), `data-theme-mode` is the choice (what the
  // segmented control highlights, which is `system` even while it renders dark).
  root.dataset.theme = theme.name;
  root.dataset.themeMode = mode;
  return theme.name;
}

export function ThemeProvider({ initial, children }: { initial: string; children: ReactNode }) {
  const [mode, setMode] = useState<ThemeMode>(() => normalizeMode(initial));
  const [prefersDark, setPrefersDark] = useState(systemPrefersDark);

  // Only meaningful under `system`, but subscribed unconditionally: the
  // listener is cheap, and re-subscribing on every mode change would mean the
  // first flip back to `system` renders against a stale reading.
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia(DARK_QUERY);
    const onChange = (e: MediaQueryListEvent) => setPrefersDark(e.matches);
    // Safari below 14 — and the WebView that shipped with it — has only the
    // deprecated form. addListener is absent in jsdom's stub, hence both.
    if (mq.addEventListener) mq.addEventListener("change", onChange);
    else mq.addListener?.(onChange);
    setPrefersDark(mq.matches);
    return () => {
      if (mq.removeEventListener) mq.removeEventListener("change", onChange);
      else mq.removeListener?.(onChange);
    };
  }, []);

  useEffect(() => setMode(normalizeMode(initial)), [initial]);

  // Applied in an effect rather than during render so a system flip and a
  // click both take the same path to the DOM.
  const [name, setName] = useState<ThemeName>(() =>
    applyTheme(normalizeMode(initial), systemPrefersDark()));
  useEffect(() => setName(applyTheme(mode, prefersDark)), [mode, prefersDark]);

  const setTheme = (next: string) => setMode(normalizeMode(next));

  return <Ctx.Provider value={{ mode, name, setTheme }}>{children}</Ctx.Provider>;
}

export function useTheme(): ThemeCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useTheme must be used within ThemeProvider");
  return ctx;
}
