import { describe, expect, it } from "vitest";

import { getDescriptor, managedKeys } from "../entitySchemas";

describe("character descriptor", () => {
  it("is registered for the character kind", () => {
    expect(getDescriptor("character")?.kind).toBe("character");
  });

  it("manages the headline character keys", () => {
    const keys = managedKeys(getDescriptor("character")!);
    for (const k of ["name", "id", "role", "voice", "image", "structural_relationships"]) {
      expect(keys).toContain(k);
    }
  });

  it("has no descriptor for a kind not yet implemented", () => {
    expect(getDescriptor("item")).toBeUndefined();
  });
});
