import { describe, expect, it } from "vitest";
import { normalizeMode, resolveName, resolveTheme } from "./index";

describe("theme tokens", () => {

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
});
