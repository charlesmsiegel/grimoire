/**
 * REST client for the Library, Mechanics, and Plugins endpoints exposed by
 * the backend (spec 14 §Backend contract / spec 18 §Interface).
 *
 * The functions here are thin wrappers around `fetch` that throw `ApiError`
 * on non-2xx responses. Components consume them directly via small custom
 * hooks (see `useLibraryResource`).
 */

const API_BASE = "/api";

export class ApiError extends Error {
  constructor(
    public status: number,
    public body: string,
    message?: string,
  ) {
    super(message ?? `HTTP ${status}: ${body || "request failed"}`);
    this.name = "ApiError";
  }
}

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

export interface SettingMeta {
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
  setting_id: string | null;
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
  setting_id: string;
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
// Library: settings & entities
// --------------------------------------------------------------------------

export const libraryApi = {
  listSettings: () => request<SettingMeta[]>("GET", `/library/settings`),
  getSetting: (settingId: string) =>
    request<SettingMeta>("GET", `/library/settings/${encodeURIComponent(settingId)}`),
  createSetting: (id: string, meta: Record<string, unknown>) =>
    request<SettingMeta>("POST", `/library/settings`, { id, meta }),
  updateSetting: (settingId: string, patch: Record<string, unknown>) =>
    request<SettingMeta>("PATCH", `/library/settings/${encodeURIComponent(settingId)}`, {
      patch,
    }),
  deleteSetting: (settingId: string) =>
    request<void>("DELETE", `/library/settings/${encodeURIComponent(settingId)}`),
  forkSetting: (settingId: string, targetId: string) =>
    request<SettingMeta>("POST", `/library/settings/${encodeURIComponent(settingId)}/fork`, {
      target_id: targetId,
    }),

  listEntities: (settingId: string, kindPlural: string) =>
    request<LibraryEntity[] | Greeting[]>(
      "GET",
      `/library/settings/${encodeURIComponent(settingId)}/${kindPlural}`,
    ),
  getEntity: (settingId: string, kindPlural: string, entityId: string) =>
    request<LibraryEntity | Greeting>(
      "GET",
      `/library/settings/${encodeURIComponent(settingId)}/${kindPlural}/${encodeURIComponent(entityId)}`,
    ),
  createEntity: (
    settingId: string,
    kindPlural: string,
    body: { id: string; frontmatter?: Record<string, unknown>; body?: string },
  ) =>
    request<LibraryEntity>(
      "POST",
      `/library/settings/${encodeURIComponent(settingId)}/${kindPlural}`,
      body,
    ),
  updateEntity: (
    settingId: string,
    kindPlural: string,
    entityId: string,
    body: { frontmatter_patch?: Record<string, unknown>; body?: string },
  ) =>
    request<LibraryEntity>(
      "PATCH",
      `/library/settings/${encodeURIComponent(settingId)}/${kindPlural}/${encodeURIComponent(entityId)}`,
      body,
    ),
  deleteEntity: (settingId: string, kindPlural: string, entityId: string) =>
    request<void>(
      "DELETE",
      `/library/settings/${encodeURIComponent(settingId)}/${kindPlural}/${encodeURIComponent(entityId)}`,
    ),
  dependents: (settingId: string, kindPlural: string, entityId: string) =>
    request<CampaignRef[]>(
      "GET",
      `/library/settings/${encodeURIComponent(settingId)}/${kindPlural}/${encodeURIComponent(entityId)}/dependents`,
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

export const pluginsApi = {
  listInstalled: () => request<PluginManifest[]>("GET", `/plugins/installed`),
  rescan: () => request<RescanReport>("POST", `/plugins/rescan`),
  configure: (id: string, config: Record<string, unknown>) =>
    request<{ ok: boolean }>("POST", `/plugins/${encodeURIComponent(id)}/config`, config),
  health: (id: string) => request<unknown>("GET", `/plugins/${encodeURIComponent(id)}/health`),
};
