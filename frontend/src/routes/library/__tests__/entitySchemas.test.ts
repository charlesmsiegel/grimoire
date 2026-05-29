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

  it("has no descriptor for greetings (they keep their bespoke form)", () => {
    expect(getDescriptor("greeting")).toBeUndefined();
  });
});

describe("location/item/monster descriptors", () => {
  it("registers location with kind + connections", () => {
    const keys = managedKeys(getDescriptor("location")!);
    expect(keys).toEqual(
      expect.arrayContaining(["kind", "parent_id", "connections", "coordinates"]),
    );
  });
  it("registers item with provenance + current_holder", () => {
    const keys = managedKeys(getDescriptor("item")!);
    expect(keys).toEqual(expect.arrayContaining(["provenance", "current_holder"]));
  });
  it("registers monster with category + abilities", () => {
    const keys = managedKeys(getDescriptor("monster")!);
    expect(keys).toEqual(expect.arrayContaining(["category", "abilities", "weaknesses"]));
  });
});

describe("faction/lore descriptors", () => {
  it("registers faction with membership refLists", () => {
    const keys = managedKeys(getDescriptor("faction")!);
    expect(keys).toEqual(
      expect.arrayContaining(["leaders", "members", "allies", "rivals", "base_location"]),
    );
  });
  it("registers lore using title as the label key (not name)", () => {
    const keys = managedKeys(getDescriptor("lore")!);
    expect(keys).toContain("title");
    expect(keys).not.toContain("name");
    expect(keys).toEqual(
      expect.arrayContaining(["secrecy", "keywords", "position", "selective_logic"]),
    );
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
