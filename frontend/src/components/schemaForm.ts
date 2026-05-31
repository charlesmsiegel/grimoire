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
  // Intentional escape-hatch: JSON Schema defines many keywords beyond
  // the named ones above (allOf/oneOf/anyOf, $ref, additionalProperties,
  // pattern, minimum, etc.) and plugin authors may also stash custom
  // x-* extensions. Typed as `unknown` (not `any`) so reads through this
  // index must be narrowed at use sites; reads through the named keys
  // are still type-checked discriminately.
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

/**
 * Drop blank fields from a config draft before saving. A cleared number
 * input lands in the draft as `null` (see SchemaField), and an empty text
 * input as `""`; both fail JSON-Schema validation for typed/optional fields
 * (`null is not of type 'integer'`). An empty optional field means "unset",
 * so we omit it — letting the schema default apply on reload. `0` and
 * `false` are real values and are preserved.
 */
export function cleanDraftForSave(
  draft: Record<string, unknown>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(draft)) {
    if (value === null || value === undefined || value === "") continue;
    out[key] = value;
  }
  return out;
}
