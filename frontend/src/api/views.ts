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
  SettingMeta,
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
  upgradeRef: (campaignId: string, settingId: string) =>
    api.post<UpgradeReport>(
      `/api/campaigns/${enc(campaignId)}/composition/refs/${enc(settingId)}/upgrade`,
    ),

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

  // ---------- Library / mechanics ----------

  listSettings: () => api.get<SettingMeta[]>(`/api/library/settings`),
  listGreetingsForSetting: (settingId: string) =>
    api.get<Greeting[]>(`/api/library/settings/${enc(settingId)}/greetings`),

  installedMechanics: () => api.get<RegisteredMechanicsModule[]>(`/api/mechanics/installed`),
};
