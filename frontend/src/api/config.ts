/**
 * Application configuration REST client.
 *
 * Wraps endpoints for managing app-level defaults and settings,
 * including LLM tier configuration, embedding, and imagegen defaults.
 */

import { api } from "./client";

export interface LLMDefaults {
  heavy: string;
  light: string;
}

export interface EmbeddingDefaults {
  route: string | null;
}

export interface ImagegenDefaults {
  backend: string | null;
}

export interface GGUFInfo {
  architecture: string | null;
  name: string | null;
  context_length: number | null;
  embedding_length: number | null;
  has_chat_template: boolean;
  file_type: number | null;
  quantization_version: number | null;
}

export interface BrowseEntry {
  name: string;
  path: string;
  is_dir: boolean;
}

export interface BrowseFilesResponse {
  directory: string;
  parent: string;
  entries: BrowseEntry[];
}

export const configApi = {
  getLLMDefaults: () => api.get<LLMDefaults>("/api/config/llm-defaults"),

  setLLMDefaults: (body: LLMDefaults) =>
    api.put<LLMDefaults>("/api/config/llm-defaults", body),

  getEmbeddingDefaults: () =>
    api.get<EmbeddingDefaults>("/api/config/embedding-defaults"),

  patchEmbeddingDefaults: (body: Partial<EmbeddingDefaults>) =>
    api.patch<EmbeddingDefaults>("/api/config/embedding-defaults", body),

  getImagegenDefaults: () =>
    api.get<ImagegenDefaults>("/api/config/imagegen-defaults"),

  patchImagegenDefaults: (body: Partial<ImagegenDefaults>) =>
    api.patch<ImagegenDefaults>("/api/config/imagegen-defaults", body),

  browseFiles: (directory?: string, glob?: string) =>
    api.get<BrowseFilesResponse>("/api/config/browse-files", {
      query: { directory: directory || undefined, glob: glob || undefined },
    }),

  ggufIntrospect: (path: string) =>
    api.get<GGUFInfo>("/api/config/gguf-introspect", { query: { path } }),
};
