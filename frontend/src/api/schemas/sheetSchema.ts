import { z } from "zod";

/**
 * Boundary validator for the JSON-Schema subset returned by
 * `GET /api/mechanics/{module}/sheets/{kind}`. Mirrors `SheetSchema` /
 * `SchemaProperty` in `sheets/types.ts`, which stay the source-of-truth
 * compile-time types.
 *
 * Widget annotations (min/max, rows, cols, severity_levels, …) are open-ended,
 * so each property object passes unknown keys through and only the recursive
 * structure (`properties`, `items`) is validated. Parsing here replaces the
 * former `as unknown as SheetSchema` cast at the use sites (issue #545).
 *
 * Draft 2020-12 boolean subschemas (`true`/`false`) are accepted by the
 * backend's metaschema check (`mechanics/loader.py::_read_schema_file`), so
 * they must parse here too. They carry no widget annotations, so they coerce
 * to `{}` — the renderer shows its generic fallback, exactly as it did before
 * this boundary existed.
 */
const SchemaPropertySchema: z.ZodType = z.lazy(() =>
  z.union([
    z.boolean().transform(() => ({})),
    z.looseObject({
      properties: z.record(z.string(), SchemaPropertySchema).optional(),
      items: SchemaPropertySchema.optional(),
    }),
  ]),
);

export const SheetSchemaSchema = z.looseObject({
  // Draft 2020-12 `type` may be a simple-type string or an array of them
  // (e.g. `["object", "null"]`), and the backend serves either. The renderer
  // ignores `type`, so accept any object-admitting declaration and normalize
  // it to the compile-time `type?: "object"`.
  type: z
    .union([
      z.literal("object"),
      z
        .array(z.string())
        .refine((types) => types.includes("object"), {
          message: 'sheet schema "type" array must include "object"',
        })
        .transform(() => "object" as const),
    ])
    .optional(),
  title: z.string().optional(),
  properties: z.record(z.string(), SchemaPropertySchema).default({}),
  required: z.array(z.string()).optional(),
});
