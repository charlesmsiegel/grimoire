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
};
