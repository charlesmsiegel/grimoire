import { api } from "../client";

export interface PCProfilePayload {
  description: string;
  goals: string[];
  player_notes: string;
  character_ref?: string;
}

export interface PCProfileRevision {
  timestamp: string;
  description: string;
  goals: string[];
  player_notes: string;
  character_ref?: string;
}

const enc = encodeURIComponent;

export const pcProfileApi = {
  get: (campaignId: string, characterRef: string) =>
    api.get<PCProfilePayload>(`/api/campaigns/${enc(campaignId)}/pcs/${enc(characterRef)}/profile`),

  save: (campaignId: string, characterRef: string, profile: PCProfilePayload) =>
    api.put<PCProfilePayload>(
      `/api/campaigns/${enc(campaignId)}/pcs/${enc(characterRef)}/profile`,
      profile,
    ),

  listRevisions: (campaignId: string, characterRef: string) =>
    api.get<PCProfileRevision[]>(
      `/api/campaigns/${enc(campaignId)}/pcs/${enc(characterRef)}/profile/revisions`,
    ),

  getRevision: (campaignId: string, characterRef: string, timestamp: string) =>
    api.get<PCProfileRevision>(
      `/api/campaigns/${enc(campaignId)}/pcs/${enc(characterRef)}/profile/revisions/${enc(timestamp)}`,
    ),
};
