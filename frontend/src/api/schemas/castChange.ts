import { z } from "zod";

export const PendingCastChangeSchema = z.object({
  id: z.string(),
  campaign_id: z.string(),
  scene_id: z.string(),
  character_ref: z.string(),
  change: z.enum(["enter", "leave"]),
  is_pc: z.boolean(),
  evidence: z.string(),
  confidence: z.number(),
  turn_id: z.string().nullable(),
  status: z.string(),
  created_at: z.string(),
});

export const PendingCastChangeArraySchema = z.array(PendingCastChangeSchema);

export type PendingCastChange = z.infer<typeof PendingCastChangeSchema>;
