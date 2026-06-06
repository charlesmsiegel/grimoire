/**
 * Typed wrappers around the generic `api` client for the per-campaign views
 * (Cast / World / Timeline / Mechanics / Composition / Images, task 34).
 *
 * Co-locating these next to `api/client.ts` keeps the views free of URL
 * construction and lets task 33's `api/campaign.ts` retain its narrower
 * play-view surface without import cycles.
 */

import { api } from "./client";
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

export const viewsApi = {
  listCharacters: (campaignId: string) =>
    api.get<ResolvedCharacter[]>(`/api/campaigns/${enc(campaignId)}/characters`),

  listItems: (campaignId: string) =>
    api.get<ResolvedEntity[]>(`/api/campaigns/${enc(campaignId)}/items`),
  listLocations: (campaignId: string) =>
    api.get<ResolvedEntity[]>(`/api/campaigns/${enc(campaignId)}/locations`),
  listLore: (campaignId: string) =>
    api.get<ResolvedEntity[]>(`/api/campaigns/${enc(campaignId)}/lore`),
  listFactions: (campaignId: string) =>
    api.get<ResolvedEntity[]>(`/api/campaigns/${enc(campaignId)}/factions`),

  listScenes: (campaignId: string) =>
    api.get<SceneSummary[]>(`/api/campaigns/${enc(campaignId)}/scenes`),

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

  listWorlds: () => api.get<WorldMeta[]>(`/api/library/worlds`),
  listGreetingsForWorld: (worldId: string) =>
    api.get<Greeting[]>(`/api/library/worlds/${enc(worldId)}/greetings`),

  installedMechanics: () => api.get<RegisteredMechanicsModule[]>(`/api/mechanics/installed`),
  getSheetSchema: (moduleId: string, kind: string) =>
    api.get<Record<string, unknown>>(`/api/mechanics/${enc(moduleId)}/sheets/${enc(kind)}`),
  getMechanicsThemeCss: (moduleId: string) =>
    api.getText(`/api/mechanics/${enc(moduleId)}/theme.css`),

  listStyleGuides: () =>
    api.get<{ id: string; asset_id: string; name: string }[]>(`/api/library/style-guides`),
  listImagePresets: () =>
    api.get<{ id: string; asset_id: string; name: string }[]>(`/api/library/image-presets`),
};
