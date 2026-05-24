import { z } from "zod";

import { api } from "../client";
import { CampaignSummarySchema } from "../schemas/campaign";
import type {
  AdvanceTurnResult,
  AlternateListResponse,
  ApiPost,
  ApiScene,
  CampaignSummary,
  ContentEntry,
  ContinuityLedger,
  CreationStep,
  ForkCampaignRequest,
  ForkCampaignResult,
  LineageNode,
  LineageTree,
  MechanicsSwitchResult,
  NarratorResponseMode,
  OpenCommitment,
  PCEntry,
  RegeneratePostResult,
  ReplayBatchView,
  RetconResultPayload,
  RollResolution,
  SceneBreakChoice,
  SceneDetail,
  SubmitTurnResult,
  SwitchPrimaryResult,
  TimeAdvanceResult,
} from "./types";

const enc = encodeURIComponent;

export const campaignApi = {
  list: () =>
    api.get<CampaignSummary[]>("/api/campaigns", {
      schema: z.array(CampaignSummarySchema),
    }),

  get: (id: string) => api.get<CampaignSummary>(`/api/campaigns/${enc(id)}`),

  listPCs: (id: string) => api.get<PCEntry[]>(`/api/campaigns/${enc(id)}/pcs`),

  setActivePC: (id: string, characterRef: string) =>
    api.post<{ ok: boolean }>(`/api/campaigns/${enc(id)}/pcs/${enc(characterRef)}/set-active`),

  listScenes: (id: string) => api.get<ApiScene[]>(`/api/campaigns/${enc(id)}/scenes`),

  getScene: (id: string, sceneId: string) =>
    api.get<SceneDetail>(`/api/campaigns/${enc(id)}/scenes/${enc(sceneId)}`),

  endScene: (id: string, sceneId: string) =>
    api.post<ApiScene>(`/api/campaigns/${enc(id)}/scenes/${enc(sceneId)}/end`),

  updateSceneNarratorMode: (
    id: string,
    sceneId: string,
    next: "all_at_once" | "per_character" | null,
  ) =>
    api.patch<{
      scene: ApiScene & { narrator_response_mode: NarratorResponseMode | null };
      narrator_response_mode: {
        scene_override: NarratorResponseMode | null;
        effective: NarratorResponseMode;
      };
    }>(
      `/api/campaigns/${enc(id)}/scenes/${enc(sceneId)}`,
      next === null
        ? { clear_narrator_response_mode: true }
        : { narrator_response_mode: next },
    ),

  submitTurn: (id: string, pcRef: string, text: string) =>
    api.post<SubmitTurnResult>(`/api/campaigns/${enc(id)}/turns`, { pc_ref: pcRef, text }),

  advance: (id: string, sceneId: string) =>
    api.post<AdvanceTurnResult>(`/api/campaigns/${enc(id)}/turns/advance`, { scene_id: sceneId }),

  regenerate: (id: string) => api.post<unknown>(`/api/campaigns/${enc(id)}/turns/regenerate`),

  regeneratePost: (
    campaignId: string,
    sceneId: string,
    postId: string,
    options?: { steering_hint?: string; model_override?: string },
  ) =>
    api.post<RegeneratePostResult>(
      `/api/campaigns/${enc(campaignId)}/scenes/${enc(sceneId)}/posts/${enc(postId)}/regenerate`,
      options ?? {},
    ),

  listAlternates: (campaignId: string, sceneId: string, postId: string) =>
    api.get<AlternateListResponse>(
      `/api/campaigns/${enc(campaignId)}/scenes/${enc(sceneId)}/posts/${enc(postId)}/alternates`,
    ),

  switchPrimaryAlternate: (
    campaignId: string,
    sceneId: string,
    postId: string,
    alternateId: string,
  ) =>
    api.post<SwitchPrimaryResult>(
      `/api/campaigns/${enc(campaignId)}/scenes/${enc(sceneId)}/posts/${enc(postId)}/alternates/${enc(alternateId)}/primary`,
    ),

  pinAlternate: (
    campaignId: string,
    sceneId: string,
    postId: string,
    alternateId: string,
    pinned: boolean,
  ) =>
    api.post<{ post_id: string; alternate_id: string; pinned: boolean }>(
      `/api/campaigns/${enc(campaignId)}/scenes/${enc(sceneId)}/posts/${enc(postId)}/alternates/${enc(alternateId)}/pin`,
      { pinned },
    ),

  deleteAlternate: (
    campaignId: string,
    sceneId: string,
    postId: string,
    alternateId: string,
  ) =>
    api.delete<void>(
      `/api/campaigns/${enc(campaignId)}/scenes/${enc(sceneId)}/posts/${enc(postId)}/alternates/${enc(alternateId)}`,
    ),

  editPostBody: (campaignId: string, sceneId: string, postId: string, body: string) =>
    api.patch<ApiPost>(
      `/api/campaigns/${enc(campaignId)}/scenes/${enc(sceneId)}/posts/${enc(postId)}`,
      { body, source: "manual_edit" },
    ),

  getTiers: (campaignId: string) =>
    api.get<{ heavy: string | null; light: string | null; embedding: string | null }>(
      `/api/campaigns/${enc(campaignId)}/tiers`,
    ),

  setTiers: (
    campaignId: string,
    body: { heavy: string | null; light: string | null; embedding: string | null },
  ) =>
    api.put<{ heavy: string | null; light: string | null; embedding: string | null }>(
      `/api/campaigns/${enc(campaignId)}/tiers`,
      body,
    ),

  getSummaries: (campaignId: string) =>
    api.get<{ running_every_n_posts: number; final_on_close: boolean }>(
      `/api/campaigns/${enc(campaignId)}/summaries`,
    ),

  setSummaries: (
    campaignId: string,
    body: { running_every_n_posts: number; final_on_close: boolean },
  ) =>
    api.put<{ running_every_n_posts: number; final_on_close: boolean }>(
      `/api/campaigns/${enc(campaignId)}/summaries`,
      body,
    ),

  getIntegratedDeltas: (campaignId: string) =>
    api.get<{ enabled: boolean }>(
      `/api/campaigns/${enc(campaignId)}/integrated-deltas`,
    ),

  setIntegratedDeltas: (campaignId: string, body: { enabled: boolean }) =>
    api.put<{ enabled: boolean }>(
      `/api/campaigns/${enc(campaignId)}/integrated-deltas`,
      body,
    ),

  retconPost: (
    campaignId: string,
    turnId: string,
    payload: { post_id: string; new_text: string; replay_subsequent?: boolean },
  ) =>
    api.post<RetconResultPayload>(
      `/api/campaigns/${enc(campaignId)}/turns/${enc(turnId)}/retcon`,
      payload,
    ),

  getRetconReplay: (campaignId: string, batchId: string) =>
    api.get<ReplayBatchView>(
      `/api/campaigns/${enc(campaignId)}/retcon/replay/${enc(batchId)}`,
    ),

  acceptRetconReplay: (campaignId: string, batchId: string) =>
    api.post<ReplayBatchView>(
      `/api/campaigns/${enc(campaignId)}/retcon/replay/${enc(batchId)}/accept`,
    ),

  tryAgainRetconReplay: (campaignId: string, batchId: string) =>
    api.post<ReplayBatchView>(
      `/api/campaigns/${enc(campaignId)}/retcon/replay/${enc(batchId)}/try-again`,
    ),

  cancelRetconReplay: (campaignId: string, batchId: string) =>
    api.post<ReplayBatchView>(
      `/api/campaigns/${enc(campaignId)}/retcon/replay/${enc(batchId)}/cancel`,
    ),

  forkBranch: (campaignId: string, fromTurnId: string, label: string) =>
    api.post<{ new_branch_id: string; from_turn_id: string; label: string; created_at: string }>(
      `/api/campaigns/${enc(campaignId)}/branches`,
      { from_turn_id: fromTurnId, label },
    ),

  undo: (id: string, count = 1) =>
    api.post<{ turns_undone: string[] }>(`/api/campaigns/${enc(id)}/turns/undo`, { count }),

  timeAdvance: (
    id: string,
    payload: { duration?: Record<string, number>; target?: string; reason?: string },
  ) =>
    api.post<TimeAdvanceResult>(`/api/campaigns/${enc(id)}/time/advance`, {
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

  getLedger: (id: string) =>
    api.get<ContinuityLedger>(`/api/campaigns/${enc(id)}/continuity/ledger`),

  listImages: (id: string, sceneId?: string) =>
    api.get<{ id: string; thumb_path?: string; image_path?: string; post_id?: string }[]>(
      `/api/campaigns/${enc(id)}/images`,
      { query: { scene_id: sceneId } },
    ),

  listContent: (campaignId: string, kind: string) =>
    api.get<ContentEntry[]>(`/api/campaigns/${enc(campaignId)}/content/${enc(kind)}`),

  getContent: (campaignId: string, kind: string, contentId: string) =>
    api.get<Record<string, unknown>>(
      `/api/campaigns/${enc(campaignId)}/content/${enc(kind)}/${enc(contentId)}`,
    ),

  putContent: (
    campaignId: string,
    kind: string,
    contentId: string,
    payload: Record<string, unknown>,
  ) =>
    api.put<Record<string, unknown>>(
      `/api/campaigns/${enc(campaignId)}/content/${enc(kind)}/${enc(contentId)}`,
      payload,
    ),

  characterCreationSteps: (campaignId: string, characterId: string) =>
    api.get<CreationStep[]>(
      `/api/campaigns/${enc(campaignId)}/characters/${enc(characterId)}/creation`,
    ),

  submitCharacterCreation: (
    campaignId: string,
    characterId: string,
    payload: { step_outputs: Record<string, Record<string, unknown>>; source?: string },
  ) =>
    api.post<Record<string, unknown>>(
      `/api/campaigns/${enc(campaignId)}/characters/${enc(characterId)}/creation/submit`,
      payload,
    ),

  resolveProposals: (campaignId: string, turnId: string, resolutions: RollResolution[]) =>
    api.post<{ ok: boolean }>(
      `/api/campaigns/${enc(campaignId)}/turns/${enc(turnId)}/resolve-proposals`,
      { resolutions },
    ),

  resolveSceneBreak: (campaignId: string, turnId: string, choice: SceneBreakChoice) =>
    api.post<{ resolved: boolean; turn_id: string; choice: SceneBreakChoice }>(
      `/api/campaigns/${enc(campaignId)}/turns/${enc(turnId)}/resolve-scene-break`,
      { choice },
    ),

  switchMechanics: (campaignId: string, mechanics: string | null, source: string = "user") =>
    api.post<MechanicsSwitchResult>(`/api/campaigns/${enc(campaignId)}/mechanics/switch`, {
      mechanics,
      source,
    }),

  preservedSheets: (campaignId: string) =>
    api.get<{ active: string | null; preserved: { mechanics_id: string; count: number }[] }>(
      `/api/campaigns/${enc(campaignId)}/mechanics/preserved-sheets`,
    ),

  forkCampaign: (campaignId: string, payload: ForkCampaignRequest) =>
    api.post<ForkCampaignResult>(`/api/campaigns/${enc(campaignId)}/forks`, payload),

  getLineage: (campaignId: string) =>
    api.get<LineageTree>(`/api/campaigns/${enc(campaignId)}/lineage`),

  getLineageAncestors: (campaignId: string) =>
    api.get<LineageNode[]>(`/api/campaigns/${enc(campaignId)}/lineage/ancestors`),

  listPendingForks: (campaignId: string) =>
    api.get<
      {
        id: string;
        new_campaign_id: string;
        new_name: string;
        fork_at_post_id: string | null;
        enqueued_at: string;
        started_at: string | null;
        completed_at: string | null;
        error: string | null;
      }[]
    >(`/api/campaigns/${enc(campaignId)}/forks/pending`),
};
