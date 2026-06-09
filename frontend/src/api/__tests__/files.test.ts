import { describe, expect, it } from "vitest";

import { fileUrl } from "../files";

describe("fileUrl", () => {
  it("prefixes /api/files/ and preserves path separators", () => {
    expect(fileUrl("campaigns/c1/images/img-1.png")).toBe(
      "/api/files/campaigns/c1/images/img-1.png",
    );
  });

  it("percent-encodes '+' and spaces inside segments", () => {
    // encodeURI would leave "+" alone, which the server decodes as a space.
    expect(fileUrl("campaigns/c1/images/a+b c.png")).toBe(
      "/api/files/campaigns/c1/images/a%2Bb%20c.png",
    );
  });
});
