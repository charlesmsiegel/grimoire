import type { CharacterCard } from "../../api/types";

/**
 * Reconstruct the frontmatter keys a resolved CharacterCard projects, for
 * seeding the structured override form (issue #601). Every key the override
 * form can edit must be seeded here — an unseeded key would diff as a
 * top-level replacement that hides the existing value. Custom `image:` keys
 * round-trip through `image.extra` (the backend collects unknown keys there).
 */
export function characterCardToFrontmatter(c: CharacterCard): Record<string, unknown> {
  const fm: Record<string, unknown> = {
    id: c.id,
    name: c.name,
    role: c.role,
    aliases: c.aliases,
    tags: c.tags,
    role_tags: c.role_tags,
    structural_relationships: c.structural_relationships,
    description: c.description,
    voice: { ...c.voice },
  };
  if (c.age != null) fm.age = c.age;
  if (c.household_id != null) fm.household_id = c.household_id;
  if (c.image) {
    fm.image = {
      ...c.image.extra,
      base_prompt: c.image.base_prompt,
      negative_prompt: c.image.negative_prompt,
      canonical_seed: c.image.canonical_seed,
    };
  }
  return fm;
}
