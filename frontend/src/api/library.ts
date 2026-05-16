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

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  init?: RequestInit,
): Promise<T> {
  const headers = new Headers(init?.headers);
  if (body !== undefined) headers.set("Content-Type", "application/json");
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new ApiError(res.status, text);
  }
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") ?? "";
  if (!ct.includes("application/json")) return undefined as T;
  return (await res.json()) as T;
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

  variants: (kindPlural: string, assetId: string) =>
    request<LibraryEntity[]>(
      "GET",
      `/library/variants/${kindPlural}/${encodeURIComponent(assetId)}`,
    ),

  listStyleGuides: () => request<LibraryEntity[]>("GET", `/library/style-guides`),
  getStyleGuide: (id: string) =>
    request<LibraryEntity>("GET", `/library/style-guides/${encodeURIComponent(id)}`),

  listImagePresets: () => request<LibraryEntity[]>("GET", `/library/image-presets`),
  getImagePreset: (id: string) =>
    request<LibraryEntity>("GET", `/library/image-presets/${encodeURIComponent(id)}`),
};

export const mechanicsApi = {
  listInstalled: () => request<RegisteredModule[]>("GET", `/mechanics/installed`),
  rescan: () => request<RescanReport | Record<string, unknown>>("POST", `/mechanics/rescan`),
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
