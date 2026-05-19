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
import type { CreationStep } from "./library";

export interface PCEntry {
  character_ref: string;
  name: string;
  owner: string;
  active: boolean;
  // Rich switcher fields (backend extends these in spec frontend §8).
  // Missing on older servers; the switcher tolerates undefined.
  current_scene_id?: string | null;
  current_location_ref?: string | null;
  last_played_at?: string | null;
}

export interface ApiAlternate {
  id: string;
  post_id: string;
  text: string;
  delta_set_id: string;
  author_kind: "pc" | "narrator" | "npc" | "system";
  model?: string | null;
  prompt_hash?: string | null;
  steering_hint?: string | null;
  tokens?: number | null;
  pinned: boolean;
  is_primary: boolean;
  created_at?: string | null;
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
  alternates?: ApiAlternate[];
  primary_alternate_id?: string | null;
}

export interface AlternateListResponse {
  post_id: string;
  primary_alternate_id: string | null;
  alternates: ApiAlternate[];
}

export interface RegeneratePostResult {
  post_id: string;
  new_alternate_id: string;
  delta_set_id: string;
}

export interface SwitchPrimaryResult {
  unchanged: boolean;
  post_id: string;
  from?: string | null;
  to?: string | null;
  delta_swap?: boolean;
  alternate_id?: string;
}

export interface RetconResultPayload {
  post_id: string;
  original_text: string;
  new_text: string;
  reversed_delta_ids: string[];
  new_delta_ids: string[];
  downstream_flagged_turns: string[];
  replay_batch_id: string | null;
  replayed_post_ids: string[];
  cancelled_at_post_id: string | null;
  contradictions_detected: string[];
}

export interface ReplayBatchView {
  batch_id: string;
  campaign_id: string;
  edited_post_id: string;
  subsequent_post_ids: string[];
  current_index: number;
  current_post_id: string | null;
  current_alternate_id: string | null;
  accepted_post_ids: string[];
  contradictions: string[];
  completed: boolean;
  cancelled_at_post_id: string | null;
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

export type Commitment = OpenCommitment;

export interface FactScope {
  character_ids?: string[];
  location_ids?: string[];
  faction_ids?: string[];
  item_ids?: string[];
  scope?: string;
}

export interface Fact {
  id: string;
  text: string;
  established_in_post: string;
  confidence: number;
  about: FactScope;
}

export interface ContradictionCandidate {
  existing_fact: Fact;
  similarity: number;
  verdict: string;
  confidence: number;
  rationale: string;
}

export interface ContradictionReport {
  id: string;
  candidate_fact: Fact;
  conflicts: ContradictionCandidate[];
  resolved: boolean;
}

export interface ContinuityLedger {
  campaign_id: string;
  open_commitments: Commitment[];
  overdue_commitments: Commitment[];
  stale_commitments: Commitment[];
  recent_facts: Fact[];
  unresolved_contradictions: ContradictionReport[];
}

// ---------------------------------------------------------------------------
// Mechanics: content browsers (spec 06 §Responsibilities)
// ---------------------------------------------------------------------------

/** One content entry under a campaign's active mechanics module. */
export interface ContentEntry {
  id: string;
  payload: Record<string, unknown>;
  mechanics_id: string;
}

// ---------------------------------------------------------------------------
// Mechanics: pre-roll confirmation (spec 06 §Pre-roll evaluation)
// ---------------------------------------------------------------------------

export interface RollModifier {
  label: string;
  delta: number;
  multiplier: number;
}

export interface ProposedRoll {
  label: string;
  kind: string;
  pool: number;
  difficulty?: number | null;
  actor_ref?: string | null;
  target_ref?: string | null;
  rationale: string;
  high_stakes: boolean;
  modifiers: RollModifier[];
  metadata: Record<string, unknown>;
}

/** Player decision for a single proposed roll. */
export interface RollResolution {
  label: string;
  accepted: boolean;
  /**
   * Patch over the original proposal applied before resolving. Used when the
   * player accepts with edits (e.g. dropping the pool by one). Ignored when
   * `accepted` is `false`.
   */
  modifications?: Partial<ProposedRoll> | null;
}

/**
 * Inbound `pre_roll_pending` WebSocket event shape. The backend
 * `_emit_turn_event` does not include `campaign_id` on the WS payload
 * because the stream is already campaign-scoped (`/ws/campaigns/{id}/stream`),
 * so consumers filter by event `type` + `turn_id` rather than re-checking
 * the campaign id.
 */
export interface PreRollPendingEvent {
  type: "pre_roll_pending";
  turn_id: string;
  scene_id: string;
  proposals: ProposedRoll[];
}

// ---------------------------------------------------------------------------
// Mechanics: mid-campaign switch (spec 06 §Switching modules mid-campaign)
// ---------------------------------------------------------------------------

export interface MissingSheet {
  kind: string;
  entity_id: string;
  character_name: string | null;
}

export interface MechanicsSwitchResult {
  previous: string | null;
  current: string | null;
  missing_sheets: MissingSheet[];
}

// Campaign-level fork (spec 2026-05-19-fork)
export interface ForkCampaignRequest {
  new_campaign_id: string;
  new_name: string;
  fork_at_post_id?: string | null;
  description?: string | null;
  make_active?: boolean;
}

export interface ForkCampaignResult {
  new_campaign_id: string;
  new_name: string;
  forked_from_campaign_id: string;
  forked_at_post_id: string | null;
  image_handling: string;
  files_copied: number;
  deltas_replayed: number;
  fingerprint_match: boolean;
  degraded: boolean;
  queued: boolean;
  created_at: string;
}

export interface LineageNode {
  id: string;
  name?: string | null;
  forked_from_campaign_id: string | null;
  forked_at_post_id?: string | null;
  forked_at_turn_id?: string | null;
  created_at?: string | null;
  depth?: number;
}

export interface LineageTree {
  root: string;
  ancestors: LineageNode[];
  descendants: LineageNode[];
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

  // ----- Alternates (swipes) --------------------------------------------

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

  // ----- Retcon (leave-as-is + replay) ----------------------------------

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

  forkCampaign: (campaignId: string, fromTurnId: string, label: string) =>
    api.post<{ new_branch_id: string; from_turn_id: string; label: string; created_at: string }>(
      `/api/campaigns/${enc(campaignId)}/forks`,
      { from_turn_id: fromTurnId, label },
    ),

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

  getLedger: (id: string) =>
    api.get<ContinuityLedger>(`/api/campaigns/${enc(id)}/continuity/ledger`),

  listImages: (id: string, sceneId?: string) =>
    api.get<{ id: string; thumb_path?: string; image_path?: string; post_id?: string }[]>(
      `/api/campaigns/${enc(id)}/images`,
      { query: { scene_id: sceneId } },
    ),

  // ----- Content browsers ------------------------------------------------

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

  // ----- Character creation ---------------------------------------------

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

  // ----- Pre-roll confirmation ------------------------------------------

  resolveProposals: (campaignId: string, turnId: string, resolutions: RollResolution[]) =>
    api.post<{ ok: boolean }>(
      `/api/campaigns/${enc(campaignId)}/turns/${enc(turnId)}/resolve-proposals`,
      { resolutions },
    ),

  // ----- Mechanics switch -----------------------------------------------

  switchMechanics: (campaignId: string, mechanics: string | null, source: string = "user") =>
    api.post<MechanicsSwitchResult>(`/api/campaigns/${enc(campaignId)}/mechanics/switch`, {
      mechanics,
      source,
    }),

  /**
   * Best-effort look-up: summarise the campaign's stored sheets by mechanics
   * id. Used by the preserved-sheets banner — the route is expected to be
   * additive on the backend; callers should treat 404 as "no preserved
   * sheets to surface" and continue silently.
   */
  preservedSheets: (campaignId: string) =>
    api.get<{ active: string | null; preserved: { mechanics_id: string; count: number }[] }>(
      `/api/campaigns/${enc(campaignId)}/mechanics/preserved-sheets`,
    ),

  // ----- Campaign-level fork --------------------------------------------

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
