import { ApiError, api } from "../client";
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
  sheet_schemas?: Record<string, Record<string, unknown>>;
  content_schemas?: Record<string, Record<string, unknown>>;
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

export interface ManifestSpec {
  id: string;
  name: string;
  version: string;
  api_version: string;
  author?: string;
  homepage?: string;
  description?: string;
  sheet_kinds?: string[];
  content_kinds?: string[];
  capabilities?: string[];
  ui?: Record<string, unknown>;
}

export interface CreateModuleResponse {
  id: string;
  report: RescanReport;
}

export const mechanicsApi = {
  listInstalled: () => request<RegisteredModule[]>("GET", `/mechanics/installed`),
  rescan: () => request<RescanReport | Record<string, unknown>>("POST", `/mechanics/rescan`),
  sheetSchema: (moduleId: string, kind: string) =>
    request<Record<string, unknown>>(
      "GET",
      `/mechanics/${encodeURIComponent(moduleId)}/sheets/${encodeURIComponent(kind)}`,
    ),
  createModule: (spec: ManifestSpec) =>
    request<CreateModuleResponse>("POST", `/library/mechanics`, spec),
  updateManifest: (moduleId: string, spec: ManifestSpec) =>
    request<RescanReport>(
      "PUT",
      `/library/mechanics/${encodeURIComponent(moduleId)}/manifest`,
      spec,
    ),
  putSheetSchema: (moduleId: string, kind: string, schema: Record<string, unknown>) =>
    request<RescanReport>(
      "PUT",
      `/library/mechanics/${encodeURIComponent(moduleId)}/sheets/${encodeURIComponent(kind)}`,
      schema,
    ),
  putContentSchema: (moduleId: string, kind: string, schema: Record<string, unknown>) =>
    request<RescanReport>(
      "PUT",
      `/library/mechanics/${encodeURIComponent(moduleId)}/content/${encodeURIComponent(kind)}`,
      schema,
    ),
  putThemeCss: (moduleId: string, css: string): Promise<RescanReport> =>
    api.putText<RescanReport>(
      `${API_BASE}/library/mechanics/${encodeURIComponent(moduleId)}/theme.css`,
      css,
    ),
  themeCss: async (moduleId: string): Promise<string | null> => {
    try {
      return await api.getText(
        `${API_BASE}/library/mechanics/${encodeURIComponent(moduleId)}/theme.css`,
      );
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) return null;
      throw err;
    }
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
