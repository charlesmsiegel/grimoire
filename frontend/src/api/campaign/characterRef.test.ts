import { describe, expect, it } from "vitest";

import { canonicalizeCharacterRef } from "./characterRef";

describe("canonicalizeCharacterRef", () => {
  it("leaves already-canonical library refs unchanged", () => {
    expect(canonicalizeCharacterRef("library:worlds/eberron/characters/q")).toBe(
      "library:worlds/eberron/characters/q",
    );
  });

  it("leaves already-canonical emergent refs unchanged", () => {
    expect(canonicalizeCharacterRef("campaign:emergent/character/ghost")).toBe(
      "campaign:emergent/character/ghost",
    );
  });

  it("canonicalizes the bare <world>/<id> shorthand the wizard registers", () => {
    expect(canonicalizeCharacterRef("eberron/q")).toBe("library:worlds/eberron/characters/q");
  });

  it("canonicalizes the emergent/<id> shorthand", () => {
    expect(canonicalizeCharacterRef("emergent/ghost")).toBe("campaign:emergent/character/ghost");
  });

  it("canonicalizes campaign:emergent/<id> without the character segment", () => {
    expect(canonicalizeCharacterRef("campaign:emergent/ghost")).toBe(
      "campaign:emergent/character/ghost",
    );
  });

  it("canonicalizes the singular `character` segment", () => {
    expect(canonicalizeCharacterRef("library:worlds/eberron/character/q")).toBe(
      "library:worlds/eberron/characters/q",
    );
    expect(canonicalizeCharacterRef("worlds/eberron/characters/q")).toBe(
      "library:worlds/eberron/characters/q",
    );
  });

  it("collapses an over-qualified double-prefixed world ref", () => {
    expect(canonicalizeCharacterRef("eberron/worlds/eberron/characters/q")).toBe(
      "library:worlds/eberron/characters/q",
    );
  });

  it("maps equivalent spellings to the same canonical string", () => {
    const canonical = "library:worlds/eberron/characters/q";
    for (const spelling of [
      "eberron/q",
      "worlds/eberron/characters/q",
      "library:worlds/eberron/character/q",
      "library:worlds/eberron/characters/q",
    ]) {
      expect(canonicalizeCharacterRef(spelling)).toBe(canonical);
    }
  });

  it("returns unrecognized refs unchanged", () => {
    expect(canonicalizeCharacterRef("")).toBe("");
    expect(canonicalizeCharacterRef("just-an-id")).toBe("just-an-id");
  });
});
