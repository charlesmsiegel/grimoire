import { z } from "zod";

/**
 * Wire shape of a raw indexed library entity (`LibraryEntity` in
 * `backend/src/grimoire/types/composition.py`), served by the library
 * entity-list endpoints and `/api/library/style-guides` /
 * `/api/library/image-presets`. Only fields the frontend reads are declared.
 */
export const LibraryEntitySchema = z.object({
  id: z.string(),
  world_id: z.string().nullable(),
  kind: z.string(),
  asset_id: z.string(),
  name: z.string(),
  path: z.string(),
  frontmatter: z.record(z.string(), z.unknown()),
  body: z.string(),
  body_compressed: z.string().nullable().optional(),
  tags: z.array(z.string()),
  keywords: z.array(z.string()),
  file_mtime: z.string().nullable().optional(),
  content_hash: z.string(),
  indexed_at: z.string().nullable().optional(),
  version: z.number(),
});
export type LibraryEntity = z.infer<typeof LibraryEntitySchema>;
