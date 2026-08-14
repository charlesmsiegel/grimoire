import { describe, expect, it } from "vitest";
import { DEFAULT_MODE, MODES, normalizeMode, resolveName, resolveTheme, themes } from "./index";

const REQUIRED = [
  // the redesign's names
  "--bg", "--panel", "--surface", "--ink", "--subtle", "--muted",
  "--accent", "--accent-strong", "--alert", "--line", "--line-strong",
  // inherited aliases index.css still spells
  "--panel2", "--rule", "--rule-soft", "--track", "--chrome", "--chrome-text",
  "--chrome-muted", "--chrome-rule", "--on-accent", "--quote", "--on-quote",
  "--page", "--page-ink", "--page-muted", "--banner-bg", "--banner-ink",
  "--disabled",
  // one shadow of each kind, and the type
  "--shadow-pop", "--shadow-glow", "--fd", "--fb", "--fm", "--fx", "--glow",
];

const themeList = [themes.light, themes.dark];

describe("theme tokens", () => {
  it("ships exactly one theme in two modes", () => {
    expect(Object.keys(themes)).toEqual(["light", "dark"]);
  });

  it("offers light, dark and system, and defaults to system", () => {
    expect(MODES.map((m) => m.mode)).toEqual(["light", "dark", "system"]);
    expect(DEFAULT_MODE).toBe("system");
  });

  it("maps the three retired theme names on read", () => {
    expect(normalizeMode("codex")).toBe("light");
    expect(normalizeMode("manuscript")).toBe("dark");
    expect(normalizeMode("astral")).toBe("dark");
  });

  it("treats an unreadable stored theme as never having chosen", () => {
    // Names from even further back, and the store that has no value at all.
    expect(normalizeMode("occult")).toBe("system");
    expect(normalizeMode("terminal")).toBe("system");
    expect(normalizeMode("")).toBe("system");
    expect(normalizeMode(null)).toBe("system");
    expect(normalizeMode(undefined)).toBe("system");
  });

  it("keeps a mode that is already one of the three", () => {
    expect(normalizeMode("light")).toBe("light");
    expect(normalizeMode("dark")).toBe("dark");
    expect(normalizeMode("system")).toBe("system");
  });

  it("only lets the system preference decide under system", () => {
    expect(resolveName("system", true)).toBe("dark");
    expect(resolveName("system", false)).toBe("light");
    expect(resolveName("light", true)).toBe("light");
    expect(resolveName("dark", false)).toBe("dark");
    expect(resolveTheme("system", true).name).toBe("dark");
  });

  it("every mode defines the full variable set", () => {
    for (const t of themeList) {
      for (const v of REQUIRED) {
        expect(t.tokens[v], `${t.name} missing ${v}`).toBeTruthy();
      }
    }
  });

  it("carries no per-theme rule widths or shadow ramp", () => {
    // They existed only because codex was letterpress and astral glowed.
    // index.css spells the width literally now and has one shadow of each kind.
    for (const t of themeList) {
      for (const gone of ["--rw", "--rw2", "--rw3", "--sh2", "--sh3", "--sh4", "--sh5"]) {
        expect(t.tokens[gone], `${t.name} still defines ${gone}`).toBeUndefined();
      }
    }
  });

  it("carries no legacy token aliases", () => {
    for (const t of themeList) {
      for (const legacy of ["--fg", "--font-display", "--font-body", "--radius", "--mono"]) {
        expect(t.tokens[legacy], `${t.name} still defines ${legacy}`).toBeUndefined();
      }
    }
  });

  it("uses the same three faces in both modes", () => {
    // The five other @fontsource packages went with the other two themes; a
    // face named here that is not imported by main.tsx renders as a fallback.
    for (const t of themeList) {
      expect(t.tokens["--fd"]).toContain("Cinzel");
      expect(t.tokens["--fb"]).toContain("Spectral");
      expect(t.tokens["--fm"]).toContain("Space Mono");
    }
  });

  it("light mode has neither starfield nor glow", () => {
    // Light separates surfaces with borders. Glow on off-white is mud.
    expect(themes.light.tokens["--fx"]).toBe("none");
    expect(themes.light.tokens["--glow"]).toBe("none");
    expect(themes.light.tokens["--shadow-glow"]).toBe("none");
    expect(themes.dark.tokens["--fx"]).toContain("radial-gradient");
  });

  it("the alert color is distinct from the accent in both modes", () => {
    for (const t of themeList) {
      expect(t.tokens["--alert"], `${t.name} alert must differ from accent`)
        .not.toBe(t.tokens["--accent"]);
      expect(t.tokens["--quote"]).toBe(t.tokens["--alert"]);
    }
  });

  it("dark is the reference and derives from the retired astral", () => {
    expect(themes.dark.tokens["--bg"]).toBe("#08070f");
    expect(themes.dark.tokens["--accent"]).toBe("#6fe0da");
    expect(themes.dark.tokens["--alert"]).toBe("#e77fce");
  });
});
