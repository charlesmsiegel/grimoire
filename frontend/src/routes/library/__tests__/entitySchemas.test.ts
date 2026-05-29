import { describe, expect, it } from "vitest";

import { getDescriptor, managedKeys } from "../entitySchemas";
import properties from "./fixtures/character-schema-properties.json";

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

describe("character descriptor drift", () => {
  it("only manages keys that exist in the Character schema", () => {
    const allowed = new Set(properties as string[]);
    for (const key of managedKeys(getDescriptor("character")!)) {
      expect(allowed.has(key), `descriptor key '${key}' missing from Character schema`).toBe(true);
    }
  });
});
