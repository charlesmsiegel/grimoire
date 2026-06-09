import { z } from "zod";

/**
 * Wire shape of `/api/campaigns/{id}/images` (`ImageMetadata` in
 * `backend/src/grimoire/types/imagegen.py`). Only fields the frontend reads
 * are declared; extras (e.g. `params`) are tolerated.
 */
export const ImageMetadataSchema = z.object({
  id: z.string(),
  campaign_id: z.string(),
  file_path: z.string(),
  thumbnail_path: z.string().nullable(),
  prompt: z.string(),
  negative_prompt: z.string(),
  backend: z.string(),
  model: z.string(),
  seed: z.number().nullable(),
  scene_id: z.string().nullable(),
  post_id: z.string().nullable(),
  created_at: z.string().nullable(),
  user_starred: z.boolean(),
  tags: z.array(z.string()),
});
export type ImageMetadata = z.infer<typeof ImageMetadataSchema>;
