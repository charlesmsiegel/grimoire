import { describe, expect, it } from "vitest";

import {
  createDefaultFields,
  getDescriptor,
  managedKeys,
  primaryLabelKey,
} from "../entitySchemas";
import type { EntityKind } from "../../../api/library";
import characterProps from "./fixtures/character-schema-properties.json";
import locationProps from "./fixtures/location-schema-properties.json";
import itemProps from "./fixtures/item-schema-properties.json";
import monsterProps from "./fixtures/monster-schema-properties.json";
import factionProps from "./fixtures/faction-schema-properties.json";
import loreProps from "./fixtures/lore-schema-properties.json";

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

const FIXTURES: Record<string, string[]> = {
  character: characterProps,
  location: locationProps,
  item: itemProps,
  monster: monsterProps,
  faction: factionProps,
  lore: loreProps,
};

describe("create-mode helpers", () => {
  it("marks headline create fields per kind", () => {
    expect(createDefaultFields(getDescriptor("character")!).map((f) => f.key)).toEqual(
      expect.arrayContaining(["role", "description"]),
    );
    expect(createDefaultFields(getDescriptor("location")!).map((f) => f.key)).toContain("kind");
  });
  it("uses title as the primary label for lore, name otherwise", () => {
    expect(primaryLabelKey(getDescriptor("lore")!)).toBe("title");
    expect(primaryLabelKey(getDescriptor("character")!)).toBe("name");
  });
});

describe("descriptor drift", () => {
  for (const [kind, props] of Object.entries(FIXTURES)) {
    it(`${kind} descriptor only manages keys in its schema`, () => {
      const allowed = new Set(props);
      for (const key of managedKeys(getDescriptor(kind as EntityKind)!)) {
        expect(allowed.has(key), `${kind} key '${key}' missing from schema`).toBe(true);
      }
    });
  }
});
