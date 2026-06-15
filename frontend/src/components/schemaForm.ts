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
 * Drop blank fields from a config draft before saving, recursively. A cleared
 * number input lands in the draft as `null` (see SchemaField), an empty text
 * input as `""`, and an untouched structured field as an empty object/array;
 * all of these mean "unset" and would either fail JSON-Schema validation or
 * force opinionated empties onto a provider that should defer. We omit them so
 * the schema default applies on reload and the backend receives only what the
 * user actually set. `0` and `false` are real values and are preserved.
 */
export function cleanDraftForSave(draft: Record<string, unknown>): Record<string, unknown> {
  return (compact(draft) as Record<string, unknown>) ?? {};
}

/**
 * Recursively drop "unset" values: empty string, null, undefined, and empty
 * arrays/objects (after compacting their contents). `0` and `false` are real
 * values and are preserved. Returns `undefined` when nothing survives, so a
 * parent can in turn drop the now-empty container.
 */
function compact(value: unknown): unknown {
  if (Array.isArray(value)) {
    const arr = value.map(compact).filter((v) => v !== undefined);
    return arr.length ? arr : undefined;
  }
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      const cv = compact(v);
      if (cv !== undefined) out[k] = cv;
    }
    return Object.keys(out).length ? out : undefined;
  }
  if (value === "" || value === null) return undefined;
  return value;
}
