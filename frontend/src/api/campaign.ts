/**
 * Campaign Play view REST client.
 *
 * Wraps the endpoints documented in spec 14 §Backend contract that the Play
 * view consumes: PC roster, scenes/posts, turn submission/advance/undo/regen,
 * time skips, manual facts. Types here mirror the backend payload shape but
 * stay loose (`unknown` for opaque blobs) so the client doesn't drift the
 * second a backend model gains a field.
 */

import { api } from "./client";

export interface PCEntry {
  character_ref: string;
  name: string;
  owner: string;
  active: boolean;
}

export interface ApiPost {
  id: string;
  scene_id: string;
  order_in_scene: number;
  author_kind: "pc" | "narrator" | "npc" | "system";
  body: string;
  is_player: boolean;
  created_at: string;
  turn_id: string;
  author_pc_ref?: string | null;
  author_npc_ref?: string | null;
}

export interface ApiScene {
  id: string;
  campaign_id: string;
  branch_id: string;
  ordinal: number;
  slug: string;
  title: string;
  location_ref: string | null;
  in_game_start: { moment?: string | null } | null;
  in_game_end: { moment?: string | null } | null;
  present_character_refs: string[];
  present_pc_refs: string[];
  mood: string;
  post_count: number;
  closed: boolean;
  last_advance_at_post: number | null;
  running_summary: string;
  summary: string;
  threads_introduced?: { text: string }[];
  threads_paid_off?: { text: string }[];
}

export interface SceneDetail {
  scene: ApiScene;
  body: string;
  posts: ApiPost[];
}

export interface SubmitTurnResult {
  accepted: boolean;
  turn_id?: string | null;
  auto_responding?: boolean;
  queue_position?: number | null;
  reason?: string;
}

export interface AdvanceTurnResult {
  scene: ApiScene;
  pending_posts: ApiPost[];
  turn_id?: string | null;
  note?: string;
}

export interface CampaignSummary {
  id: string;
  name: string;
  description?: string | null;
  mechanics_module?: string | null;
}

export interface OpenCommitment {
  id: string;
  text: string;
  status: string;
  owed_by?: string | null;
  owed_to?: string | null;
}

const enc = encodeURIComponent;

export const campaignApi = {
  list: () => api.get<CampaignSummary[]>("/api/campaigns"),

  get: (id: string) => api.get<CampaignSummary>(`/api/campaigns/${enc(id)}`),

  listPCs: (id: string) => api.get<PCEntry[]>(`/api/campaigns/${enc(id)}/pcs`),

  setActivePC: (id: string, characterRef: string) =>
    api.post<{ ok: boolean }>(`/api/campaigns/${enc(id)}/pcs/${enc(characterRef)}/set-active`),

  listScenes: (id: string) => api.get<ApiScene[]>(`/api/campaigns/${enc(id)}/scenes`),

  getScene: (id: string, sceneId: string) =>
    api.get<SceneDetail>(`/api/campaigns/${enc(id)}/scenes/${enc(sceneId)}`),

  endScene: (id: string, sceneId: string) =>
    api.post<ApiScene>(`/api/campaigns/${enc(id)}/scenes/${enc(sceneId)}/end`),

  submitTurn: (id: string, pcRef: string, text: string) =>
    api.post<SubmitTurnResult>(`/api/campaigns/${enc(id)}/turns`, { pc_ref: pcRef, text }),

  advance: (id: string, sceneId: string) =>
    api.post<AdvanceTurnResult>(`/api/campaigns/${enc(id)}/turns/advance`, { scene_id: sceneId }),

  regenerate: (id: string) => api.post<unknown>(`/api/campaigns/${enc(id)}/turns/regenerate`),

  undo: (id: string, count = 1) =>
    api.post<{ turns_undone: string[] }>(`/api/campaigns/${enc(id)}/turns/undo`, { count }),

  timeAdvance: (
    id: string,
    payload: { duration?: Record<string, number>; target?: string; reason?: string },
  ) =>
    api.post<unknown>(`/api/campaigns/${enc(id)}/time/advance`, {
      reason: payload.reason ?? "narrative",
      duration: payload.duration,
      target: payload.target,
    }),

  createFact: (
    id: string,
    fact: { subject_ref?: string; predicate: string; object_ref?: string; statement: string },
  ) => api.post<{ fact_id: string }>(`/api/campaigns/${enc(id)}/facts`, { fact, source: "user" }),

  listCommitments: (id: string) =>
    api.get<OpenCommitment[]>(`/api/campaigns/${enc(id)}/commitments`),

  listImages: (id: string, sceneId?: string) =>
    api.get<{ id: string; thumb_path?: string; image_path?: string; post_id?: string }[]>(
      `/api/campaigns/${enc(id)}/images`,
      { query: { scene_id: sceneId } },
    ),
};
