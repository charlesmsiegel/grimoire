import { describe, expect, it } from "vitest";

import { slugify } from "../slugify";

describe("slugify", () => {
  it("lowercases and hyphenates", () => {
    expect(slugify("Ravenmark")).toBe("ravenmark");
    expect(slugify("The Old Gods")).toBe("the-old-gods");
  });
  it("strips punctuation and collapses separators", () => {
    expect(slugify("Drizzt Do'Urden!!")).toBe("drizzt-dourden");
    expect(slugify("  a__b  c ")).toBe("a-b-c");
  });
  it("trims leading/trailing hyphens and handles empty", () => {
    expect(slugify("--Hi--")).toBe("hi");
    expect(slugify("")).toBe("");
    expect(slugify("***")).toBe("");
  });
});
