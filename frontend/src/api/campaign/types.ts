import type { CreationStep } from "../library";

export type { CreationStep };

export interface PCEntry {
  character_ref: string;
  name: string;
  owner: string;
  active: boolean;
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

export type NarratorResponseMode = "all_at_once" | "per_character";

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
  narrator_response_mode?: NarratorResponseMode | null;
}

export interface SceneAnalysisResult {
  summary: string;
  key_beats: string[];
  threads_introduced: { text: string; at_post: number | null }[];
  threads_paid_off: { text: string; at_post: number | null }[];
  deltas_applied: number;
  deltas_queued: number;
  entity_candidates: {
    kind: string;
    proposed_id: string;
    proposed_name: string;
    confidence: number;
  }[];
}

export interface SceneDetail {
  scene: ApiScene;
  body: string;
  posts: ApiPost[];
}

export interface PaginatedPostsResponse {
  posts: ApiPost[];
  has_more: boolean;
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

export interface ContentEntry {
  id: string;
  payload: Record<string, unknown>;
  mechanics_id: string;
}

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

export interface RollResolution {
  label: string;
  accepted: boolean;
  modifications?: Partial<ProposedRoll> | null;
}

export interface PreRollPendingEvent {
  type: "pre_roll_pending";
  turn_id: string;
  scene_id: string;
  proposals: ProposedRoll[];
}

export interface SceneBreakSuggestedEvent {
  type: "scene_break_suggested";
  turn_id: string;
  scene_id: string;
  confidence: number;
  reason: string;
}

export type SceneBreakChoice = "continue" | "new_scene";

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

// ---------------------------------------------------------------------------
// New scene workflow
// ---------------------------------------------------------------------------

export interface LedgerItem {
  ledger_id: string;
  summary: string;
  greeting_id: string | null;
  source: "greeting" | "llm" | "user";
}

export interface GeneratedSuggestion {
  summary: string;
  proposed_location: string | null;
  proposed_cast: string[];
}

export interface SuggestResponse {
  ledger_picks: LedgerItem[];
  generated: GeneratedSuggestion[];
}

export interface PreviewResponse {
  title: string;
  location_ref: string | null;
  in_game_start: string | null;
  present_character_refs: string[];
  present_pc_refs: string[];
  greeting_id: string | null;
  first_post_source: "greeting" | "adapted_greeting" | "generated";
  ledger_id: string | null;
  original_summary: string | null;
}

export interface LedgerEntry {
  id: string;
  campaign_id: string;
  summary: string;
  greeting_id: string | null;
  source: "greeting" | "llm" | "user";
  status: "active" | "used" | "dismissed";
  created_at: string;
  used_in_scene_id: string | null;
}

export interface InGameTime {
  moment: string;
  calendar_id?: string | null;
}

export interface DurationLike {
  iso8601: string;
}

export interface NpcTickSummary {
  character_id: string;
  duration: DurationLike;
  state_at_end: Record<string, unknown>;
  activities: string[];
  relationships_changed?: unknown[];
  new_facts_about_them?: unknown[];
  secrets_kept?: string[];
  next_intent?: string;
  should_seek_pc?: boolean;
  events_pc_would_witness?: string[];
}

export interface ScheduledEvent {
  id: string;
  at: InGameTime;
  label: string;
  kind: string;
  triggered: boolean;
}

export interface WeatherChange {
  location_ref: string;
  at: InGameTime;
  summary: string;
}

export interface DriftWarning {
  character_id: string;
  severity: "info" | "warning" | "critical";
  summary: string;
  evidence?: string[];
}

export interface TimeAdvanceResult {
  from_time: InGameTime;
  to_time: InGameTime;
  duration: DurationLike;
  digest: string;
  npc_summaries?: Record<string, NpcTickSummary>;
  scheduled_events_triggered?: ScheduledEvent[];
  weather_changes?: WeatherChange[];
  drift_warnings?: DriftWarning[];
  scheduled_events_upcoming?: ScheduledEvent[];
}
