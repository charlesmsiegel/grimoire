/**
 * Typed wrappers around the generic `api` client for the per-campaign views
 * (Cast / World / Timeline / Mechanics / Composition / Images, task 34).
 *
 * Co-locating these next to `api/client.ts` keeps the views free of URL
 * construction and lets task 33's `api/campaign.ts` retain its narrower
 * play-view surface without import cycles.
 *
 * List endpoints pass a `checkSchema` so backend payload drift surfaces as a
 * dev console warning instead of a silently broken grid (issue #599).
 */

import { z } from "zod";

import { api } from "./client";
import { ImageMetadataSchema } from "./schemas/image";
import { LibraryEntitySchema } from "./schemas/libraryEntity";
import { RegisteredMechanicsModuleSchema } from "./schemas/mechanics";
import { ResolvedCharacterSchema, ResolvedEntitySchema } from "./schemas/resolved";
import { SceneSummarySchema } from "./schemas/scene";
import { SheetSchemaSchema } from "./schemas/sheetSchema";
import { GreetingSchema, WorldMetaSchema } from "./schemas/world";
import type { SheetSchema } from "../sheets/types";
import type {
  Composition,
  Greeting,
  ImageMetadata,
  RegisteredMechanicsModule,
  ResolvedCharacter,
  ResolvedEntity,
  SceneSummary,
  WorldDiff,
  WorldMeta,
  UpgradeReport,
} from "./types";

const enc = encodeURIComponent;

const ResolvedCharacterList = z.array(ResolvedCharacterSchema);
const ResolvedEntityList = z.array(ResolvedEntitySchema);

export const viewsApi = {
  listCharacters: (campaignId: string) =>
    api.get<ResolvedCharacter[]>(`/api/campaigns/${enc(campaignId)}/characters`, {
      checkSchema: ResolvedCharacterList,
    }),
  /** Dramatis personae: PCs + emergent characters + library characters that
   * have appeared in at least one scene. The full composition is
   * `listCharacters`. */
  listCast: (campaignId: string) =>
    api.get<ResolvedCharacter[]>(`/api/campaigns/${enc(campaignId)}/cast`, {
      checkSchema: ResolvedCharacterList,
    }),

  listItems: (campaignId: string) =>
    api.get<ResolvedEntity[]>(`/api/campaigns/${enc(campaignId)}/items`, {
      checkSchema: ResolvedEntityList,
    }),
  listLocations: (campaignId: string) =>
    api.get<ResolvedEntity[]>(`/api/campaigns/${enc(campaignId)}/locations`, {
      checkSchema: ResolvedEntityList,
    }),
  listLore: (campaignId: string) =>
    api.get<ResolvedEntity[]>(`/api/campaigns/${enc(campaignId)}/lore`, {
      checkSchema: ResolvedEntityList,
    }),
  listFactions: (campaignId: string) =>
    api.get<ResolvedEntity[]>(`/api/campaigns/${enc(campaignId)}/factions`, {
      checkSchema: ResolvedEntityList,
    }),
  listMonsters: (campaignId: string) =>
    api.get<ResolvedEntity[]>(`/api/campaigns/${enc(campaignId)}/monsters`, {
      checkSchema: ResolvedEntityList,
    }),

  listScenes: (campaignId: string) =>
    api.get<SceneSummary[]>(`/api/campaigns/${enc(campaignId)}/scenes`, {
      checkSchema: z.array(SceneSummarySchema),
    }),

  getComposition: (campaignId: string) =>
    api.get<Composition>(`/api/campaigns/${enc(campaignId)}/composition`),
  setComposition: (campaignId: string, composition: Composition) =>
    api.put<Composition>(`/api/campaigns/${enc(campaignId)}/composition`, composition),
  upgradeRef: (campaignId: string, worldId: string) =>
    api.post<UpgradeReport>(
      `/api/campaigns/${enc(campaignId)}/composition/refs/${enc(worldId)}/upgrade`,
    ),
  worldDiff: (worldId: string, fromVersion: number, toVersion?: number) =>
    api.get<WorldDiff>(`/api/library/worlds/${enc(worldId)}/diff`, {
      query: { from: fromVersion, to: toVersion },
    }),

  listImages: (campaignId: string, opts: { sceneId?: string; starredOnly?: boolean } = {}) =>
    api.get<ImageMetadata[]>(`/api/campaigns/${enc(campaignId)}/images`, {
      query: { scene_id: opts.sceneId, starred_only: opts.starredOnly },
      checkSchema: z.array(ImageMetadataSchema),
    }),
  generateImage: (
    campaignId: string,
    body: { scene_id?: string; post_id?: string; request?: Record<string, unknown> },
  ) => api.post<{ job_id: string }>(`/api/campaigns/${enc(campaignId)}/images/generate`, body),

  getSheet: (campaignId: string, kind: string, entityId: string) =>
    api.get<Record<string, unknown>>(
      `/api/campaigns/${enc(campaignId)}/sheets/${enc(kind)}/${enc(entityId)}`,
    ),
  putSheet: (campaignId: string, kind: string, entityId: string, sheet: Record<string, unknown>) =>
    api.put<{ ok: true }>(
      `/api/campaigns/${enc(campaignId)}/sheets/${enc(kind)}/${enc(entityId)}`,
      sheet,
    ),
  bulkCreateMissingSheets: (campaignId: string) =>
    api.post<{
      created: { kind: string; entity_id: string }[];
      skipped: { kind: string; entity_id: string }[];
    }>(`/api/campaigns/${enc(campaignId)}/sheets/bulk-create-missing`),

  patchCharacterOverride: (
    campaignId: string,
    entityId: string,
    payload: { override: Record<string, unknown>; world_id?: string; source?: string },
  ) =>
    api.patch<{ ok: true; world_id: string; ref: string }>(
      `/api/campaigns/${enc(campaignId)}/characters/${enc(entityId)}/override`,
      payload,
    ),

  promoteCharacterToLibrary: (
    campaignId: string,
    entityId: string,
    payload: { target_world_id: string; confirm?: boolean; source?: string },
  ) =>
    api.post<unknown>(
      `/api/campaigns/${enc(campaignId)}/characters/${enc(entityId)}/promote-to-library`,
      payload,
    ),

  // ---------- Library / mechanics ----------

  listWorlds: () =>
    api.get<WorldMeta[]>(`/api/library/worlds`, { checkSchema: z.array(WorldMetaSchema) }),
  listGreetingsForWorld: (worldId: string) =>
    api.get<Greeting[]>(`/api/library/worlds/${enc(worldId)}/greetings`, {
      checkSchema: z.array(GreetingSchema),
    }),

  installedMechanics: () =>
    api.get<RegisteredMechanicsModule[]>(`/api/mechanics/installed`, {
      checkSchema: z.array(RegisteredMechanicsModuleSchema),
    }),
  getSheetSchema: (moduleId: string, kind: string) =>
    api.get<SheetSchema>(`/api/mechanics/${enc(moduleId)}/sheets/${enc(kind)}`, {
      schema: SheetSchemaSchema,
    }),
  getMechanicsThemeCss: (moduleId: string) =>
    api.getText(`/api/mechanics/${enc(moduleId)}/theme.css`),

  listStyleGuides: () =>
    api.get<{ id: string; asset_id: string; name: string }[]>(`/api/library/style-guides`, {
      checkSchema: z.array(LibraryEntitySchema),
    }),
  listImagePresets: () =>
    api.get<{ id: string; asset_id: string; name: string }[]>(`/api/library/image-presets`, {
      checkSchema: z.array(LibraryEntitySchema),
    }),
};
