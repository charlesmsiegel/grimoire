/**
 * Pure helpers and types for rendering JSON-schema-driven plugin config
 * forms. Kept separate from {@link SchemaField} so the JSX module
 * exports only components (React Fast Refresh requirement).
 */

export interface JsonSchema {
  type?: string | string[];
  title?: string;
  description?: string;
  enum?: unknown[];
  default?: unknown;
  format?: string;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  items?: JsonSchema;
  [key: string]: unknown;
}

export function initialDraftFromSchema(
  properties: Record<string, JsonSchema>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, schema] of Object.entries(properties)) {
    if (schema?.default !== undefined) out[key] = schema.default;
  }
  return out;
}
