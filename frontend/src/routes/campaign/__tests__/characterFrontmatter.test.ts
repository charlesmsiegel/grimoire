import { describe, expect, it } from "vitest";

import { characterCardToFrontmatter } from "../characterFrontmatter";
import type { CharacterCard } from "../../../api/types";

const card: CharacterCard = {
  id: "alistair",
  name: "Alistair",
  role: "major_npc",
  world_id: "wod-london",
  aliases: ["Al"],
  age: "34",
  tags: ["vampire"],
  role_tags: ["kindred"],
  voice: {
    summary: "gruff",
    voice_register: "low",
    samples: ["hm"],
    speech_patterns: [],
    address_terms: {},
    dos: [],
    donts: [],
  },
  image: {
    base_prompt: "a pale man",
    negative_prompt: "sunlight",
    canonical_seed: 7,
    extra: { style: "noir", loras: ["gloom-v2"] },
  },
  structural_relationships: [{ to_ref: "dorian", kind: "sire", note: "" }],
  household_id: "hyde-smythe",
  description: "A tired fixer.",
  body: "",
  file_path: "",
  version: 3,
};

describe("characterCardToFrontmatter", () => {
  it("seeds every override-editable key, including relationships and role tags", () => {
    const fm = characterCardToFrontmatter(card);
    expect(fm.role_tags).toEqual(["kindred"]);
    expect(fm.household_id).toBe("hyde-smythe");
    expect(fm.structural_relationships).toEqual([{ to_ref: "dorian", kind: "sire", note: "" }]);
  });

  it("round-trips custom image keys through image.extra", () => {
    const fm = characterCardToFrontmatter(card);
    expect(fm.image).toEqual({
      style: "noir",
      loras: ["gloom-v2"],
      base_prompt: "a pale man",
      negative_prompt: "sunlight",
      canonical_seed: 7,
    });
  });
});
