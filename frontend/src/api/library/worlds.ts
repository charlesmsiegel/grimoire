import { z } from "zod";

import { LibraryEntitySchema, type LibraryEntity } from "../schemas/libraryEntity";
import { GreetingSchema, WorldMetaSchema, type Greeting, type WorldMeta } from "../schemas/world";
import { request } from "./request";

// Defined once as Zod schemas (used by the client's checkSchema drift check,
// issue #599) and re-exported here so list payload types cannot drift.
export type { Greeting, WorldMeta } from "../schemas/world";
export type { LibraryEntity } from "../schemas/libraryEntity";

export type EntityKind =
  | "character"
  | "item"
  | "location"
  | "lore"
  | "faction"
  | "greeting"
  | "monster";

export const ENTITY_KIND_PLURAL: Record<EntityKind, string> = {
  character: "characters",
  item: "items",
  location: "locations",
  lore: "lore",
  faction: "factions",
  greeting: "greetings",
  monster: "monsters",
};

export const ENTITY_KIND_SINGULAR: Record<string, EntityKind> = {
  characters: "character",
  items: "item",
  locations: "location",
  lore: "lore",
  factions: "faction",
  greetings: "greeting",
  monsters: "monster",
};

export interface WorldSummary {
  counts: Record<string, number>;
  has_description: boolean;
  has_genre: boolean;
}

export interface ReclassificationSuggestion {
  kind: EntityKind | "lore";
  confidence: number;
  reason: string;
}

export interface ReclassificationPreview {
  source_id: string;
  target_kind: EntityKind;
  frontmatter: Record<string, unknown>;
  body: string;
  kept: string[];
  dropped: string[];
  into_notes: string[];
  warnings: string[];
  required_overrides: string[];
  suggestion: ReclassificationSuggestion;
}

export interface ReclassificationResult {
  source_id: string;
  target_id: string;
  target_kind: EntityKind;
  fields_kept: string[];
  fields_dropped: string[];
  fields_into_notes: string[];
  warnings: string[];
}

export interface ReclassificationAuditRecord {
  ts: string;
  world_id: string;
  source_id: string;
  target_id: string;
  target_kind: EntityKind;
  actor: string;
  overrides: Record<string, unknown>;
}

export interface ReclassificationUndoResult {
  restored_source_id: string;
  deleted_target_id: string;
  undo_of: string;
  warnings: string[];
}

export interface CampaignRef {
  id: string;
  name: string;
}

export interface StyleGuideEditPayload {
  id: string;
  name: string;
  description: string;
  tags: string[];
  intro: string;
  pacing: string[];
  voice: string[];
  themes: string[];
  avoid: string[];
  extra_sections: [string, string][];
}

export interface ImagePresetEditPayload {
  id: string;
  name: string;
  description: string;
  tags: string[];
  style_preamble: string;
  default_negative_prompt: string;
  default_params: Record<string, unknown>;
}

export interface LibraryRescanReport {
  scope: "all" | "library" | "campaigns";
  library_files: number;
  campaign_files: number;
}

export const libraryApi = {
  listWorlds: () =>
    request<WorldMeta[]>("GET", `/library/worlds`, undefined, {
      checkSchema: z.array(WorldMetaSchema),
    }),
  rescanWorlds: () => request<LibraryRescanReport>("POST", `/library/worlds/rescan`),
  getWorld: (worldId: string) =>
    request<WorldMeta>("GET", `/library/worlds/${encodeURIComponent(worldId)}`),
  worldSummary: (worldId: string) =>
    request<WorldSummary>("GET", `/library/worlds/${encodeURIComponent(worldId)}/summary`),
  createWorld: (id: string, meta: Record<string, unknown>) =>
    request<WorldMeta>("POST", `/library/worlds`, { id, meta }),
  updateWorld: (worldId: string, patch: Record<string, unknown>) =>
    request<WorldMeta>("PATCH", `/library/worlds/${encodeURIComponent(worldId)}`, {
      patch,
    }),
  deleteWorld: (worldId: string) =>
    request<void>("DELETE", `/library/worlds/${encodeURIComponent(worldId)}`),
  forkWorld: (worldId: string, targetId: string) =>
    request<WorldMeta>("POST", `/library/worlds/${encodeURIComponent(worldId)}/fork`, {
      target_id: targetId,
    }),

  listEntities: (worldId: string, kindPlural: string) =>
    request<LibraryEntity[] | Greeting[]>(
      "GET",
      `/library/worlds/${encodeURIComponent(worldId)}/${kindPlural}`,
      undefined,
      { checkSchema: z.array(z.union([LibraryEntitySchema, GreetingSchema])) },
    ),
  getEntity: (worldId: string, kindPlural: string, entityId: string) =>
    request<LibraryEntity | Greeting>(
      "GET",
      `/library/worlds/${encodeURIComponent(worldId)}/${kindPlural}/${encodeURIComponent(entityId)}`,
    ),
  createEntity: (
    worldId: string,
    kindPlural: string,
    body: { id: string; frontmatter?: Record<string, unknown>; body?: string },
  ) =>
    request<LibraryEntity>(
      "POST",
      `/library/worlds/${encodeURIComponent(worldId)}/${kindPlural}`,
      body,
    ),
  updateEntity: (
    worldId: string,
    kindPlural: string,
    entityId: string,
    body: { frontmatter_patch?: Record<string, unknown>; body?: string },
  ) =>
    request<LibraryEntity>(
      "PATCH",
      `/library/worlds/${encodeURIComponent(worldId)}/${kindPlural}/${encodeURIComponent(entityId)}`,
      body,
    ),
  deleteEntity: (worldId: string, kindPlural: string, entityId: string) =>
    request<void>(
      "DELETE",
      `/library/worlds/${encodeURIComponent(worldId)}/${kindPlural}/${encodeURIComponent(entityId)}`,
    ),
  dependents: (worldId: string, kindPlural: string, entityId: string) =>
    request<CampaignRef[]>(
      "GET",
      `/library/worlds/${encodeURIComponent(worldId)}/${kindPlural}/${encodeURIComponent(entityId)}/dependents`,
    ),

  previewReclassify: (worldId: string, sourceId: string, targetKind: EntityKind) =>
    request<ReclassificationPreview>(
      "GET",
      `/library/worlds/${encodeURIComponent(worldId)}/lore/${encodeURIComponent(sourceId)}/reclassify/preview?target_kind=${encodeURIComponent(targetKind)}`,
    ),
  commitReclassify: (
    worldId: string,
    sourceId: string,
    body: { target_kind: EntityKind; overrides?: Record<string, unknown> },
  ) =>
    request<ReclassificationResult>(
      "POST",
      `/library/worlds/${encodeURIComponent(worldId)}/lore/${encodeURIComponent(sourceId)}/reclassify`,
      body,
    ),
  listReclassifications: (worldId: string) =>
    request<ReclassificationAuditRecord[]>(
      "GET",
      `/library/worlds/${encodeURIComponent(worldId)}/reclassifications`,
    ),
  undoReclassify: (worldId: string, ts: string) =>
    request<ReclassificationUndoResult>(
      "POST",
      `/library/worlds/${encodeURIComponent(worldId)}/reclassifications/${encodeURIComponent(ts)}/undo`,
    ),

  variants: (kindPlural: string, assetId: string) =>
    request<LibraryEntity[]>(
      "GET",
      `/library/variants/${kindPlural}/${encodeURIComponent(assetId)}`,
      undefined,
      { checkSchema: z.array(LibraryEntitySchema) },
    ),

  listStyleGuides: () =>
    request<LibraryEntity[]>("GET", `/library/style-guides`, undefined, {
      checkSchema: z.array(LibraryEntitySchema),
    }),
  getStyleGuide: (id: string) =>
    request<LibraryEntity>("GET", `/library/style-guides/${encodeURIComponent(id)}`),
  getStyleGuideEdit: (id: string) =>
    request<StyleGuideEditPayload>("GET", `/library/style-guides/${encodeURIComponent(id)}/edit`),
  createStyleGuide: (payload: {
    id: string;
    name: string;
    description?: string;
    tags?: string[];
    pacing?: string[];
    voice?: string[];
    themes?: string[];
    avoid?: string[];
  }) => request<LibraryEntity>("POST", `/library/style-guides`, payload),
  updateStyleGuide: (
    id: string,
    patch: {
      name?: string;
      description?: string;
      tags?: string[];
      pacing?: string[];
      voice?: string[];
      themes?: string[];
      avoid?: string[];
    },
  ) => request<LibraryEntity>("PATCH", `/library/style-guides/${encodeURIComponent(id)}`, patch),
  deleteStyleGuide: (id: string) =>
    request<void>("DELETE", `/library/style-guides/${encodeURIComponent(id)}`),

  listImagePresets: () =>
    request<LibraryEntity[]>("GET", `/library/image-presets`, undefined, {
      checkSchema: z.array(LibraryEntitySchema),
    }),
  getImagePreset: (id: string) =>
    request<LibraryEntity>("GET", `/library/image-presets/${encodeURIComponent(id)}`),
  getImagePresetEdit: (id: string) =>
    request<ImagePresetEditPayload>("GET", `/library/image-presets/${encodeURIComponent(id)}/edit`),
  createImagePreset: (payload: {
    id: string;
    name: string;
    description?: string;
    tags?: string[];
    style_preamble?: string;
    default_negative_prompt?: string;
    default_params?: Record<string, unknown>;
  }) => request<LibraryEntity>("POST", `/library/image-presets`, payload),
  updateImagePreset: (
    id: string,
    patch: {
      name?: string;
      description?: string;
      tags?: string[];
      style_preamble?: string;
      default_negative_prompt?: string;
      default_params?: Record<string, unknown>;
    },
  ) => request<LibraryEntity>("PATCH", `/library/image-presets/${encodeURIComponent(id)}`, patch),
  deleteImagePreset: (id: string) =>
    request<void>("DELETE", `/library/image-presets/${encodeURIComponent(id)}`),
  previewImagePreset: (id: string, body: { prompt?: string | null; seed?: number | null } = {}) =>
    request<{ image_data_url: string; backend: string; model: string; seed: number }>(
      "POST",
      `/library/image-presets/${encodeURIComponent(id)}/preview`,
      body,
    ),

  patchWorldCharacterImageTemplate: (
    worldId: string,
    characterId: string,
    image: {
      base_prompt?: string;
      negative_prompt?: string;
      canonical_seed?: number | null;
    },
  ) =>
    request<LibraryEntity>(
      "PATCH",
      `/library/worlds/${encodeURIComponent(worldId)}/characters/${encodeURIComponent(characterId)}`,
      { frontmatter_patch: { image } },
    ),
};

interface _CampaignSummary {
  id: string;
  name?: string;
}
interface _WorldRef {
  world_id: string;
}
interface _Composition {
  worlds?: _WorldRef[];
}

export async function fetchWorldDependents(worldId: string): Promise<CampaignRef[]> {
  const campaigns = await request<_CampaignSummary[]>("GET", `/campaigns`, undefined, {
    cache: false,
  });
  const out: CampaignRef[] = [];
  for (const c of campaigns) {
    try {
      const comp = await request<_Composition>(
        "GET",
        `/campaigns/${encodeURIComponent(c.id)}/composition`,
        undefined,
        { cache: false },
      );
      if (comp.worlds?.some((r) => r.world_id === worldId)) {
        out.push({ id: c.id, name: c.name ?? "" });
      }
    } catch {
      // skip
    }
  }
  return out;
}
