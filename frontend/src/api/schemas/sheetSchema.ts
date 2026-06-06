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
 */
const SchemaPropertySchema: z.ZodType = z.lazy(() =>
  z.looseObject({
    properties: z.record(z.string(), SchemaPropertySchema).optional(),
    items: SchemaPropertySchema.optional(),
  }),
);

export const SheetSchemaSchema = z.looseObject({
  type: z.literal("object").optional(),
  title: z.string().optional(),
  properties: z.record(z.string(), SchemaPropertySchema).default({}),
  required: z.array(z.string()).optional(),
});
