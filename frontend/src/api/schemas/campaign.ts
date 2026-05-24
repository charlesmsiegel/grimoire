import { z } from "zod";

export const CampaignSummarySchema = z.object({
  id: z.string(),
  name: z.string(),
  description: z.string().nullable().optional(),
  mechanics_module: z.string().nullable().optional(),
});

export type CampaignSummary = z.infer<typeof CampaignSummarySchema>;
