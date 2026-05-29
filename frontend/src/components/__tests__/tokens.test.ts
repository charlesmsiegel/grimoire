import { describe, expect, it } from "vitest";

import { estimateEntityTokens, estimateTokens } from "../tokens";

describe("estimateTokens", () => {
  it("returns 0 for empty text", () => {
    expect(estimateTokens("")).toBe(0);
  });

  it("falls back to len/4 before the encoder loads", () => {
    // "abcd".length / 4 === 1
    expect(estimateTokens("abcd")).toBe(1);
    expect(estimateTokens("a".repeat(40))).toBe(10);
  });
});

describe("estimateEntityTokens", () => {
  it("grows with body length", () => {
    const small = estimateEntityTokens({ name: "X" }, "short");
    const big = estimateEntityTokens({ name: "X" }, "a".repeat(400));
    expect(big).toBeGreaterThan(small);
  });
});
