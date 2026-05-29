import { api } from "../client";
import type {
  ApiScene,
  GeneratedSuggestion,
  LedgerEntry,
  PreviewResponse,
  SuggestResponse,
} from "./types";

const enc = encodeURIComponent;

export const newSceneApi = {
  suggest: (campaignId: string) =>
    api.post<SuggestResponse>(
      `/api/campaigns/${enc(campaignId)}/scenes/suggest`,
    ),

  preview: (
    campaignId: string,
    body: {
      ledger_id?: string;
      generated_suggestion?: GeneratedSuggestion;
      custom_text?: string;
      greeting_id?: string;
    },
  ) =>
    api.post<PreviewResponse>(
      `/api/campaigns/${enc(campaignId)}/scenes/preview`,
      body,
    ),

  start: (
    campaignId: string,
    body: PreviewResponse & { unchosen_generated: GeneratedSuggestion[] },
  ) =>
    api.post<{ scene: ApiScene; first_post: unknown }>(
      `/api/campaigns/${enc(campaignId)}/scenes/start`,
      body,
    ),

  listLedger: (campaignId: string, status?: string) =>
    api.get<LedgerEntry[]>(
      `/api/campaigns/${enc(campaignId)}/scene-ledger${status ? `?status=${enc(status)}` : ""}`,
    ),

  updateLedger: (
    campaignId: string,
    itemId: string,
    status: "active" | "dismissed",
  ) =>
    api.patch<{ id: string; status: string }>(
      `/api/campaigns/${enc(campaignId)}/scene-ledger/${enc(itemId)}`,
      { status },
    ),

  backfillLedger: (campaignId: string) =>
    api.post<{ added: number }>(
      `/api/campaigns/${enc(campaignId)}/scene-ledger/backfill`,
    ),
};
