import { describe, expect, it } from "vitest";

import { slugify } from "../slugify";

describe("slugify maxLength", () => {
  it("caps at maxLength without a trailing dash", () => {
    expect(slugify("a".repeat(80), { maxLength: 64 })).toBe("a".repeat(64));
    // Truncation landing on a separator must not leave "-" at the end.
    expect(slugify("ab cd", { maxLength: 3 })).toBe("ab");
  });
  it("is uncapped by default", () => {
    expect(slugify("a".repeat(80))).toHaveLength(80);
  });
  it("matches library spelling for apostrophes", () => {
    expect(slugify("Bryn's Hollow", { maxLength: 64 })).toBe("bryns-hollow");
  });
});
