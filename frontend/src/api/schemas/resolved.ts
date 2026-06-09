import { z } from "zod";

/**
 * Wire shapes for cascade-resolved entities (`ResolvedCharacter` /
 * `ResolvedEntity` in `backend/src/grimoire/types/`). Like the rest of
 * `api/schemas/`, only the fields the frontend actually reads are declared —
 * unknown extras are tolerated — and these schemas are the source of truth
 * for the matching compile-time types (re-exported from `api/types.ts`).
 */

export const ResolutionSourceSchema = z.object({
  layer: z.enum(["emergent", "override", "library_snapshot", "library_live"]),
  scope: z.string(),
  library_id: z.string().nullable().optional(),
  world_id: z.string().nullable().optional(),
  version: z.number().nullable().optional(),
  override_applied: z.boolean(),
});
export type ResolutionSource = z.infer<typeof ResolutionSourceSchema>;

export const VoiceAnchorSchema = z.object({
  summary: z.string(),
  voice_register: z.string(),
  samples: z.array(z.string()),
  speech_patterns: z.array(z.string()),
  address_terms: z.record(z.string(), z.string()),
  dos: z.array(z.string()),
  donts: z.array(z.string()),
});
export type VoiceAnchor = z.infer<typeof VoiceAnchorSchema>;

export const ImagePromptTemplateSchema = z.object({
  base_prompt: z.string(),
  negative_prompt: z.string(),
  canonical_seed: z.number().nullable(),
  extra: z.record(z.string(), z.unknown()),
});
export type ImagePromptTemplate = z.infer<typeof ImagePromptTemplateSchema>;

export const CharacterCardSchema = z.object({
  id: z.string(),
  name: z.string(),
  role: z.string(),
  world_id: z.string().nullable(),
  aliases: z.array(z.string()),
  age: z.string().nullable(),
  tags: z.array(z.string()),
  voice: VoiceAnchorSchema,
  image: ImagePromptTemplateSchema.nullable(),
  description: z.string(),
  body: z.string(),
  file_path: z.string(),
  version: z.number(),
});
export type CharacterCard = z.infer<typeof CharacterCardSchema>;

export const ResolvedCharacterSchema = z.object({
  character: CharacterCardSchema,
  current_state: z.record(z.string(), z.unknown()),
  capabilities: z.array(z.record(z.string(), z.unknown())),
  source_chain: z.array(ResolutionSourceSchema),
  overrides_applied: z.array(z.string()),
});
export type ResolvedCharacter = z.infer<typeof ResolvedCharacterSchema>;

export const ResolvedEntitySchema = z.object({
  kind: z.string(),
  asset_id: z.string(),
  world_id: z.string().nullable(),
  name: z.string(),
  frontmatter: z.record(z.string(), z.unknown()),
  body: z.string(),
  source_chain: z.array(ResolutionSourceSchema),
  overrides_applied: z.array(z.string()),
  extras: z.record(z.string(), z.unknown()),
});
export type ResolvedEntity = z.infer<typeof ResolvedEntitySchema>;
