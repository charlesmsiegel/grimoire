import { ApiError } from "../client";
import { request } from "./request";

const API_BASE = "/api";

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
  instance?: unknown;
  theme_css?: string | null;
}

export interface CreationStep {
  id: string;
  title: string;
  step_schema: Record<string, unknown>;
  description: string;
  optional: boolean;
}

export interface RescanReport {
  discovered: string[];
  loaded: string[];
  failed: [string, string][];
  removed: string[];
}

export const mechanicsApi = {
  listInstalled: () => request<RegisteredModule[]>("GET", `/mechanics/installed`),
  rescan: () => request<RescanReport | Record<string, unknown>>("POST", `/mechanics/rescan`),
  themeCss: async (moduleId: string): Promise<string | null> => {
    const res = await fetch(
      `${API_BASE}/library/mechanics/${encodeURIComponent(moduleId)}/theme.css`,
    );
    if (res.status === 404) return null;
    if (!res.ok) throw new ApiError(res.status, await res.text().catch(() => ""));
    return res.text();
  },
  contentSchema: (moduleId: string, kind: string) =>
    request<Record<string, unknown>>(
      "GET",
      `/library/mechanics/${encodeURIComponent(moduleId)}/content/${encodeURIComponent(kind)}/schema`,
    ),
  characterCreation: (moduleId: string) =>
    request<CreationStep[]>(
      "GET",
      `/library/mechanics/${encodeURIComponent(moduleId)}/character-creation`,
    ),
};
