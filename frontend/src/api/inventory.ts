import { z } from "zod";

import { api } from "./client";

export const InventoryHoldingSchema = z.object({
  item_ref: z.string(),
  item_name: z.string(),
  quantity: z.number(),
  fungible: z.boolean().default(false),
  equipped: z.boolean().default(false),
  provenance: z.string().nullable().optional(),
  notes: z.string().nullable().optional(),
  holder_kind: z.string().optional(),
  holder_id: z.string().optional(),
});
export type InventoryHolding = z.infer<typeof InventoryHoldingSchema>;

export const InventoryFlagSchema = z.object({
  id: z.string(),
  turn_id: z.string().nullable(),
  op_json: z.string(),
  flag_reason: z.string(),
  resolved: z.number(),
  created_at: z.string(),
});
export type InventoryFlag = z.infer<typeof InventoryFlagSchema>;

const enc = encodeURIComponent;

export const inventoryApi = {
  list: (campaignId: string) =>
    api.get<{ holders: { holder: string; entries: InventoryHolding[] }[] }>(
      `/api/campaigns/${enc(campaignId)}/inventory`,
    ),
  flags: (campaignId: string, resolved = false) =>
    api.get<{ flags: InventoryFlag[] }>(
      `/api/campaigns/${enc(campaignId)}/inventory/flags?resolved=${resolved}`,
      { schema: z.object({ flags: z.array(InventoryFlagSchema) }) },
    ),
  resolveFlag: (campaignId: string, flagId: string) =>
    api.post<{ ok: boolean }>(
      `/api/campaigns/${enc(campaignId)}/inventory/flags/${enc(flagId)}/resolve`,
    ),
  submitOperation: (campaignId: string, op: unknown) =>
    api.post<{ touched: number; flags: number }>(
      `/api/campaigns/${enc(campaignId)}/inventory/operations`,
      op,
    ),
};
