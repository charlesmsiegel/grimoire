import { describe, expect, it } from "vitest";
import { DEFAULT_THEME, themeList, resolveTheme } from "./index";

const REQUIRED = [
  "--bg", "--surface", "--panel", "--panel2", "--ink", "--subtle", "--muted",
  "--rule", "--rule-soft", "--track", "--chrome", "--chrome-text",
  "--chrome-muted", "--chrome-rule", "--accent", "--on-accent", "--quote", "--on-quote",
  "--page", "--page-ink", "--page-muted", "--banner-bg", "--banner-ink",
  "--disabled", "--rw", "--rw2", "--rw3", "--sh2", "--sh3", "--sh4", "--sh5",
  "--fd", "--fb", "--fm", "--fx", "--glow",
];

describe("theme tokens", () => {
  it("ships exactly codex, manuscript, astral", () => {
    expect(themeList.map((t) => t.name)).toEqual(["codex", "manuscript", "astral"]);
  });

  it("defaults to codex", () => {
    expect(DEFAULT_THEME).toBe("codex");
  });

  it("falls back to codex for old saved theme names", () => {
    expect(resolveTheme("occult").name).toBe("codex");
    expect(resolveTheme("terminal").name).toBe("codex");
  });

  it("every theme defines the full variable set", () => {
    for (const t of themeList) {
      for (const v of REQUIRED) {
        expect(t.tokens[v], `${t.name} missing ${v}`).toBeTruthy();
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


  it("quote color is distinct from the accent in every theme", () => {
    for (const t of themeList) {
      expect(t.tokens["--quote"], `${t.name} quote must differ from accent`)
        .not.toBe(t.tokens["--accent"]);
    }
  });

  it("codex is the hard-edged reference", () => {
    const codex = resolveTheme("codex").tokens;
    expect(codex["--accent"]).toBe("#c0392b");
    expect(codex["--rw"]).toBe("2px");
    expect(codex["--sh4"]).toBe("4px 4px 0 rgba(26,23,18,.9)");
  });
});
