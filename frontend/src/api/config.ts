/**
 * Application configuration REST client.
 *
 * Wraps endpoints for managing app-level defaults and settings,
 * including LLM tier configuration.
 */

import { api } from "./client";

export const configApi = {
  getLLMDefaults: () =>
    api.get<{ heavy: string; light: string }>("/api/config/llm-defaults"),

  setLLMDefaults: (body: { heavy: string; light: string }) =>
    api.put<{ heavy: string; light: string }>("/api/config/llm-defaults", body),
};
