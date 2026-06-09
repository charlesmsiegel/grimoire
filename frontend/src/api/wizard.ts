/**
 * Wizard / per-campaign REST helpers. Built on top of `api` (root-relative
 * `fetch` wrappers from `./client`) plus the catalog endpoints exposed via
 * `libraryApi` / `mechanicsApi` / `pluginsApi` in `./library`.
 *
 * Types here are deliberately narrower than the library payload shapes — the
 * wizard only needs a handful of fields per entity, so we project from the
 * richer types in `./library` into the small summaries used by the UI.
 */

import { api } from "./client";
import {
  libraryApi,
  mechanicsApi,
  pluginsApi,
  type Greeting,
  type LibraryEntity,
  type PluginKind,
  type PluginManifest,
  type RegisteredModule,
  type WorldMeta,
} from "./library";

export interface WorldSummary {
  id: string;
  name?: string;
  description?: string | null;
  current_version?: number;
  pc_role_tags: string[];
}

export interface StyleGuideSummary {
  id: string;
  name?: string;
  description?: string | null;
}

export interface ImagePresetSummary {
  id: string;
  name?: string;
  description?: string | null;
}

export interface MechanicsModuleSummary {
  id: string;
  name?: string;
  version?: string;
  api_version?: string;
  load_error?: string | null;
}

export interface PluginSummary {
  id: string;
  kind: PluginKind | string;
  name?: string;
  version?: string;
  load_error?: string | null;
}

export interface GreetingSummary {
  id: string;
  name?: string;
  description?: string | null;
  starting_location?: string | null;
  starting_time?: string | null;
  role_tags: string[];
}

export interface CharacterSummary {
  id: string;
  name?: string;
  role?: string | null;
  world_id?: string | null;
  role_tags: string[];
}

export interface CampaignSummaryPayload {
  id: string;
  name: string;
  description?: string | null;
  mechanics_module?: string | null;
  style_guide_id?: string | null;
  image_preset_id?: string | null;
  forked_from_campaign_id?: string | null;
  forked_at_post_id?: string | null;
  forked_at_turn_id?: string | null;
  forked_image_handling?: string | null;
}

export interface WorldRefInput {
  world_id: string;
  priority: number;
  include: string[];
  track_latest: boolean;
}

export interface CompositionInput {
  worlds: WorldRefInput[];
  mechanics?: string | null;
  style_guide_id?: string | null;
  image_preset_id?: string | null;
  inline_style_guide?: string | null;
  content_boundaries?: string | null;
}

export interface CampaignCreateInput {
  id: string;
  name: string;
  description?: string | null;
  composition?: CompositionInput;
  greeting_id?: string | null;
  tags?: string[] | null;
}

function stringOrNull(v: unknown): string | null {
  return typeof v === "string" ? v : null;
}

function fromWorldMeta(s: WorldMeta): WorldSummary {
  return {
    id: s.id,
    name: s.name,
    description: s.description,
    current_version: s.version,
    pc_role_tags: s.pc_role_tags ?? [],
  };
}

function fromEntity(e: LibraryEntity): StyleGuideSummary {
  return {
    id: e.id,
    name: e.name,
    description: stringOrNull(e.frontmatter?.description),
  };
}

function fromGreeting(g: Greeting): GreetingSummary {
  return {
    id: g.id,
    name: g.name,
    description: g.mood || null,
    starting_location: g.starting_location ?? null,
    starting_time: g.starting_time ?? null,
    role_tags: g.role_tags ?? [],
  };
}

function fromCharacterEntity(e: LibraryEntity): CharacterSummary {
  const rawTags = e.frontmatter?.role_tags;
  return {
    id: e.id,
    name: e.name,
    role: stringOrNull(e.frontmatter?.role),
    world_id: e.world_id,
    role_tags: Array.isArray(rawTags) ? (rawTags as string[]) : [],
  };
}

function fromRegisteredModule(m: RegisteredModule): MechanicsModuleSummary {
  return {
    id: m.manifest.id,
    name: m.manifest.name,
    version: m.manifest.version,
    api_version: m.manifest.api_version,
    load_error: null,
  };
}

function fromPluginManifest(m: PluginManifest): PluginSummary[] {
  return m.implements.map((kind) => ({
    id: m.id,
    kind,
    name: m.name,
    version: m.version,
    load_error: null,
  }));
}

export async function fetchWorlds(): Promise<WorldSummary[]> {
  return (await libraryApi.listWorlds()).map(fromWorldMeta);
}

export async function fetchStyleGuides(): Promise<StyleGuideSummary[]> {
  return (await libraryApi.listStyleGuides()).map(fromEntity);
}

export async function fetchImagePresets(): Promise<ImagePresetSummary[]> {
  return (await libraryApi.listImagePresets()).map(fromEntity);
}

export async function fetchInstalledMechanics(): Promise<MechanicsModuleSummary[]> {
  return (await mechanicsApi.listInstalled()).map(fromRegisteredModule);
}

export async function fetchInstalledPlugins(): Promise<PluginSummary[]> {
  const manifests = await pluginsApi.listInstalled();
  return manifests.flatMap(fromPluginManifest);
}

export async function rescanMechanics(): Promise<unknown> {
  return mechanicsApi.rescan();
}

export async function rescanPlugins(): Promise<unknown> {
  return pluginsApi.rescan();
}

export async function fetchGreetings(worldId: string): Promise<GreetingSummary[]> {
  const rows = (await libraryApi.listEntities(worldId, "greetings")) as Greeting[];
  return rows.map(fromGreeting);
}

export async function fetchWorldCharacters(worldId: string): Promise<CharacterSummary[]> {
  const rows = (await libraryApi.listEntities(worldId, "characters")) as LibraryEntity[];
  return rows.map(fromCharacterEntity);
}

export async function fetchCampaigns(): Promise<CampaignSummaryPayload[]> {
  const result = await api.get<unknown>("/api/campaigns");
  return Array.isArray(result) ? (result as CampaignSummaryPayload[]) : [];
}

export interface CampaignsRescanReport {
  scope: "all" | "library" | "campaigns";
  library_files: number;
  campaign_files: number;
  /** Files whose change was dropped during the scan (parse/index errors). */
  failures: number;
}

/** Force the file watcher to re-walk ``data/campaigns`` and pick up edits
 * made outside the UI. */
export async function rescanCampaigns(): Promise<CampaignsRescanReport> {
  return api.post<CampaignsRescanReport>("/api/campaigns/rescan");
}

export interface DiscoverResult {
  discovered: number;
  campaigns: string[];
}

export async function discoverCampaigns(): Promise<DiscoverResult> {
  return api.post<DiscoverResult>("/api/campaigns/discover");
}

export async function createCampaign(input: CampaignCreateInput): Promise<CampaignSummaryPayload> {
  return api.post<CampaignSummaryPayload>("/api/campaigns", input);
}

export async function deleteCampaign(campaignId: string): Promise<void> {
  await api.delete<void>(`/api/campaigns/${encodeURIComponent(campaignId)}`);
}

export async function addCampaignPC(
  campaignId: string,
  pc: { character_ref: string; name: string; owner?: string; role_tags?: string[] },
): Promise<unknown> {
  return api.post<unknown>(`/api/campaigns/${encodeURIComponent(campaignId)}/pcs`, pc);
}

/** Materialize the opening scene from the campaign's greeting. Idempotent. */
export async function seedFirstScene(campaignId: string): Promise<unknown> {
  return api.post<unknown>(`/api/campaigns/${encodeURIComponent(campaignId)}/scenes/seed`);
}

export async function patchCampaign(
  campaignId: string,
  patch: Partial<{
    name: string;
    description: string | null;
    style_guide_id: string | null;
    image_preset_id: string | null;
    inline_style_guide: string | null;
    content_boundaries: string | null;
    mechanics: string | null;
    greeting_id: string | null;
  }>,
): Promise<unknown> {
  return api.patch<unknown>(`/api/campaigns/${encodeURIComponent(campaignId)}`, patch);
}
