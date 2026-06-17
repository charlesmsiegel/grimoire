import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { resolveTheme } from "./themes";

type ThemeCtx = { name: string; setTheme: (name: string) => void };
const Ctx = createContext<ThemeCtx | null>(null);

function applyTheme(name: string): string {
  const theme = resolveTheme(name);
  const root = document.documentElement;
  for (const [key, value] of Object.entries(theme.tokens)) {
    root.style.setProperty(key, value);
  }
  root.dataset.theme = theme.name;
  return theme.name;
}

export function ThemeProvider({ initial, children }: { initial: string; children: ReactNode }) {
  const [name, setName] = useState(() => applyTheme(initial));

  useEffect(() => {
    setName(applyTheme(initial));
  }, [initial]);

  const setTheme = (next: string) => setName(applyTheme(next));

  return <Ctx.Provider value={{ name, setTheme }}>{children}</Ctx.Provider>;
}

export function useTheme(): ThemeCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useTheme must be used within ThemeProvider");
  return ctx;
}
