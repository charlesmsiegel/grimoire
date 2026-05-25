import { request } from "./request";
import type { RescanReport } from "./mechanics";

export type PluginKind =
  | "llm_provider"
  | "embedding_provider"
  | "imagegen_backend"
  | "export_adapter";

export interface PluginConfig {
  plugin_id: string;
  values: Record<string, unknown>;
  secrets_set: Record<string, boolean>;
  configured: boolean;
}

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
