/**
 * REST client for the Library, Mechanics, and Plugins endpoints exposed by
 * the backend (spec 14 §Backend contract / spec 18 §Interface).
 *
 * The functions here are thin wrappers around `fetch` that throw `ApiError`
 * on non-2xx responses. Components consume them directly via small custom
 * hooks (see `useLibraryResource`).
 */

import { ApiError } from "./client";

const API_BASE = "/api";

// Re-export so existing imports of `ApiError` from `./library` keep working;
// the class identity is the canonical one from `./client` so `instanceof`
// across module boundaries is consistent.
export { ApiError };

// --------------------------------------------------------------------------
// Tiny in-memory GET cache (spec 14 §Performance budgets: "library cached"
// across campaign switches).
//
// Keyed by URL; entries expire after `CACHE_TTL_MS`. Any non-GET request
// invalidates the entire cache — this is coarse but library writes are rare
// compared to reads, and the TTL bounds staleness either way. Resources that
// must always be fresh (e.g. health probes) can pass `cache: false`.
// --------------------------------------------------------------------------

const CACHE_TTL_MS = 30_000;

interface CacheEntry {
  expires: number;
  value: unknown;
}

const cache = new Map<string, CacheEntry>();

function cacheGet(url: string): unknown | undefined {
  const entry = cache.get(url);
  if (!entry) return undefined;
  if (entry.expires < Date.now()) {
    cache.delete(url);
    return undefined;
  }
  return entry.value;
}

function cacheSet(url: string, value: unknown): void {
  cache.set(url, { expires: Date.now() + CACHE_TTL_MS, value });
}

/** Drop all cached responses. Called automatically on any non-GET request. */
export function clearLibraryCache(): void {
  cache.clear();
}

interface RequestOptions {
  /** Skip the GET cache for this call. Default true for GETs. */
  cache?: boolean;
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  init?: RequestInit,
  opts?: RequestOptions,
): Promise<T> {
  const useCache = method === "GET" && opts?.cache !== false;
  const url = `${API_BASE}${path}`;
  if (useCache) {
    const hit = cacheGet(url);
    if (hit !== undefined) return hit as T;
  }
  const headers = new Headers(init?.headers);
  if (body !== undefined) headers.set("Content-Type", "application/json");
  const res = await fetch(url, {
    ...init,
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new ApiError(res.status, text);
  }
  // Any write invalidates the cache so the next read sees fresh data.
  if (method !== "GET") clearLibraryCache();
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") ?? "";
  if (!ct.includes("application/json")) return undefined as T;
  const parsed = (await res.json()) as T;
  if (useCache) cacheSet(url, parsed);
  return parsed;
}

// --------------------------------------------------------------------------
// Shapes (mirroring backend pydantic models — see types/composition.py and
// types/plugins.py / types/mechanics.py)
// --------------------------------------------------------------------------

export type EntityKind = "character" | "item" | "location" | "lore" | "faction" | "greeting";

export const ENTITY_KIND_PLURAL: Record<EntityKind, string> = {
  character: "characters",
  item: "items",
  location: "locations",
  lore: "lore",
  faction: "factions",
  greeting: "greetings",
};

export const ENTITY_KIND_SINGULAR: Record<string, EntityKind> = {
  characters: "character",
  items: "item",
  locations: "location",
  lore: "lore",
  factions: "faction",
  greetings: "greeting",
};

export interface WorldMeta {
  id: string;
  name: string;
  description: string;
  tags: string[];
  genre: string;
  calendar: Record<string, unknown>;
  atmosphere: Record<string, unknown>;
  defaults: Record<string, unknown>;
  version: number;
}

export interface LibraryEntity {
  id: string;
  world_id: string | null;
  kind: EntityKind | string;
  asset_id: string;
  name: string;
  path: string;
  frontmatter: Record<string, unknown>;
  body: string;
  body_compressed: string | null;
  tags: string[];
  keywords: string[];
  file_mtime: string | null;
  content_hash: string;
  indexed_at: string | null;
  version: number;
}

export interface Greeting {
  id: string;
  world_id: string;
  name: string;
  starting_location: string | null;
  starting_time: string | null;
  present_characters: string[];
  pov_character: string | null;
  mood: string;
  body: string;
  tags: string[];
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

export interface ModuleManifest {
  id: string;
  name: string;
  version: string;
  api_version: string;
  author: string;
  homepage: string;
  description: string;
  sheet_kinds: string[];
  content_kinds: string[];
  capabilities: string[];
  ui: Record<string, unknown>;
}

export interface RegisteredModule {
  manifest: ModuleManifest;
  // The instance is opaque on the wire; backend serializes the dataclass and
  // the instance comes back as a stringy summary (often the class name).
  instance?: unknown;
  /**
   * Inline theme CSS for the module, if it ships one. Backend reads
   * `manifest.ui.theme_css` at load time and inlines the file contents here
   * so the Frontend can scope it via `SheetRenderer` without a second request.
   * `null` when the module declares no theme. The legacy
   * `GET /api/library/mechanics/{id}/theme.css` route is still available as a
   * fallback when this field is absent.
   */
  theme_css?: string | null;
}

/**
 * One step in a mechanics module's character-creation wizard
 * (spec 06 §Character creation).
 */
export interface CreationStep {
  id: string;
  title: string;
  /** JSON Schema 2020-12 object describing the inputs collected by this step. */
  step_schema: Record<string, unknown>;
  description: string;
  optional: boolean;
}

export type PluginKind =
  | "llm_provider"
  | "embedding_provider"
  | "imagegen_backend"
  | "export_adapter";

export interface PluginManifest {
  id: string;
  name: string;
  version: string;
  api_version: string;
  implements: PluginKind[];
  classes: Record<string, string>;
  config_schema: Record<string, unknown>;
  requirements: string[];
  author: string;
  homepage: string;
  description: string;
  isolated_venv: boolean;
  raw: Record<string, unknown>;
}

export interface RescanReport {
  discovered: string[];
  loaded: string[];
  failed: [string, string][];
  removed: string[];
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

// --------------------------------------------------------------------------
// Library: worlds & entities
// --------------------------------------------------------------------------

export const libraryApi = {
  listWorlds: () => request<WorldMeta[]>("GET", `/library/worlds`),
  getWorld: (worldId: string) =>
    request<WorldMeta>("GET", `/library/worlds/${encodeURIComponent(worldId)}`),
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
    ),

  listStyleGuides: () => request<LibraryEntity[]>("GET", `/library/style-guides`),
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

  listImagePresets: () => request<LibraryEntity[]>("GET", `/library/image-presets`),
  getImagePreset: (id: string) =>
    request<LibraryEntity>("GET", `/library/image-presets/${encodeURIComponent(id)}`),
  getImagePresetEdit: (id: string) =>
    request<ImagePresetEditPayload>(
      "GET",
      `/library/image-presets/${encodeURIComponent(id)}/edit`,
    ),
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
  ) =>
    request<LibraryEntity>(
      "PATCH",
      `/library/image-presets/${encodeURIComponent(id)}`,
      patch,
    ),
  deleteImagePreset: (id: string) =>
    request<void>("DELETE", `/library/image-presets/${encodeURIComponent(id)}`),
  previewImagePreset: (
    id: string,
    body: { prompt?: string | null; seed?: number | null } = {},
  ) =>
    request<{ image_data_url: string; backend: string; model: string; seed: number }>(
      "POST",
      `/library/image-presets/${encodeURIComponent(id)}/preview`,
      body,
    ),

  // §5 — save-prompt-template-to-card. The image template lives under the
  // `image:` key in the character's frontmatter; the backend deep-merges
  // nested dicts so we only need to send the changed sub-fields.
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

export interface ImagePresetEditPayload {
  id: string;
  name: string;
  description: string;
  tags: string[];
  style_preamble: string;
  default_negative_prompt: string;
  default_params: Record<string, unknown>;
}

export const mechanicsApi = {
  listInstalled: () => request<RegisteredModule[]>("GET", `/mechanics/installed`),
  rescan: () => request<RescanReport | Record<string, unknown>>("POST", `/mechanics/rescan`),
  /**
   * Fetch raw `theme.css` for a module. Prefer `RegisteredModule.theme_css`
   * when available (avoids the extra round-trip). Returns `null` on 404.
   */
  themeCss: async (moduleId: string): Promise<string | null> => {
    const res = await fetch(
      `${API_BASE}/library/mechanics/${encodeURIComponent(moduleId)}/theme.css`,
    );
    if (res.status === 404) return null;
    if (!res.ok) throw new ApiError(res.status, await res.text().catch(() => ""));
    return res.text();
  },
  /** JSON Schema for the content entries of `kind` declared by `moduleId`. */
  contentSchema: (moduleId: string, kind: string) =>
    request<Record<string, unknown>>(
      "GET",
      `/library/mechanics/${encodeURIComponent(moduleId)}/content/${encodeURIComponent(kind)}/schema`,
    ),
  /** Character-creation steps for the library-baseline preview (no campaign). */
  characterCreation: (moduleId: string) =>
    request<CreationStep[]>(
      "GET",
      `/library/mechanics/${encodeURIComponent(moduleId)}/character-creation`,
    ),
};

export interface PluginConfig {
  plugin_id: string;
  values: Record<string, unknown>;
  secrets_set: Record<string, boolean>;
  configured: boolean;
}

export interface PluginModelInfo {
  id: string;
  name: string;
  context_window: number;
  input_cost_per_1k: number | null;
  output_cost_per_1k: number | null;
  dimensions: number | null;
}

export const pluginsApi = {
  listInstalled: () => request<PluginManifest[]>("GET", `/plugins/installed`),
  rescan: () => request<RescanReport>("POST", `/plugins/rescan`),
  getConfig: (id: string) =>
    request<PluginConfig>("GET", `/plugins/${encodeURIComponent(id)}/config`),
  configure: (id: string, config: Record<string, unknown>) =>
    request<{ ok: boolean }>("POST", `/plugins/${encodeURIComponent(id)}/config`, config),
  patchConfig: (id: string, patch: Record<string, unknown>) =>
    request<{ ok: boolean }>("PATCH", `/plugins/${encodeURIComponent(id)}/config`, patch),
  health: (id: string) => request<unknown>("GET", `/plugins/${encodeURIComponent(id)}/health`),
  listModels: (id: string) =>
    request<PluginModelInfo[]>("GET", `/plugins/${encodeURIComponent(id)}/models`),
};

export interface TemplateSummary {
  name: string;
  variants: string[];
  active: string;
  editable: string[];
}

export interface TemplateListResponse {
  templates: TemplateSummary[];
  user_dir: string;
  default_variant: string;
}

export interface TemplateBody {
  name: string;
  variant: string;
  body: string;
  editable: boolean;
  path: string;
}

export const templatesApi = {
  list: () => request<TemplateListResponse>("GET", `/templates`),
  read: (name: string, variant: string) =>
    request<TemplateBody>(
      "GET",
      `/templates/${encodeURIComponent(name)}/${encodeURIComponent(variant)}`,
    ),
  write: (name: string, variant: string, body: string) =>
    request<{ ok: boolean; path: string }>(
      "PUT",
      `/templates/${encodeURIComponent(name)}/${encodeURIComponent(variant)}`,
      { body },
    ),
  remove: (name: string, variant: string) =>
    request<{ ok: boolean }>(
      "DELETE",
      `/templates/${encodeURIComponent(name)}/${encodeURIComponent(variant)}`,
    ),
  setActive: (name: string, variant: string | null) =>
    request<{ ok: boolean; active: string }>(
      "POST",
      `/templates/${encodeURIComponent(name)}/active`,
      { variant },
    ),
};
