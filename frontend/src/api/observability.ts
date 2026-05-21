/**
 * REST client for observability endpoints. Mirrors the routes in
 * ``backend/src/grimoire/api/observability.py``.
 *
 * Covers performance metrics (#355), the per-turn delta diff (#351), the
 * Frontend Health panel (#357) — latest probe results, manual re-probes,
 * and errors grouped by module — the "What did the model see?" debug
 * view (#350): per-turn assembled prompt with tier annotations and
 * per-source attribution, plus a diff helper that compares two turns'
 * prompts — cost-breakdown debug surfaces (#353) that read from the
 * per-turn audit / cost tables — the "Why this character?" debug
 * view (#352): per-turn audit summaries plus the captured ContextSources
 * (each carrying its inclusion_reasons) — and cost-surfacing endpoints
 * (#354): cost-config read, session / rollup / today totals for the
 * status bar and Budget tab.
 *
 * Cost spec: docs/superpowers/specs/2026-05-20-cost-breakdown-design.md.
 * Why-character spec: docs/superpowers/specs/2026-05-20-why-this-character-design.md.
 */

import { api } from "./client";
import type { ContextTier, InclusionReason } from "./inspector";

export type { ContextTier, InclusionReason };

export interface MetricsKnownPair {
  module: string;
  operation: string;
  last_recorded_at: string | null;
}

export interface MetricsSummary {
  count: number;
  successes: number;
  failures: number;
  p50_ms: number;
  p95_ms: number;
  p99_ms: number;
  max_ms: number;
}

export interface MetricsTrendBucket {
  bucket_start: string;
  count: number;
  successes: number;
  failures: number;
  p50_ms: number;
  p95_ms: number;
  p99_ms: number;
}

export type TrendBucketSize = "minute" | "hour" | "day";

/** One entry in the per-turn delta diff (issue #351).
 *
 * Mirrors the dict returned by ``AuditStore.deltas_for_turn`` — a join of
 * the audit's applied/queued ids against the state-store delta log, with
 * ``evidence`` / ``extra`` folded back in from the audit blob.
 */
export interface TurnDeltaEntry {
  id: string;
  kind: string;
  target_scope: string | null;
  target_table: string | null;
  target_path: string | null;
  target_id: string | null;
  before: unknown;
  after: unknown;
  confidence: number | null;
  /** The producing strategy, e.g. "extractor:wod-mechanics", "mechanics", "user". */
  source: string | null;
  /** Alias for ``source`` — kept for spec-vocabulary fidelity. */
  strategy: string | null;
  evidence: string;
  extra: Record<string, unknown>;
  notes: string;
  applied_at: string | null;
  reversed_at: string | null;
  status: "auto" | "queued";
  review_id?: string;
  review_status?: string;
}

export interface TurnDeltaDiff {
  applied: TurnDeltaEntry[];
  queued: TurnDeltaEntry[];
}

export type HealthLevel = "healthy" | "degraded" | "unhealthy" | "unconfigured";

export interface HealthStatus {
  level: HealthLevel;
  target_id: string;
  message: string;
  checked_at: string | null;
  details: Record<string, unknown>;
}

export type HealthLatest = Record<string, HealthStatus>;

export type ErrorAggregate = Record<string, Record<string, number>>;

export interface ErrorRecord {
  timestamp: string;
  module: string;
  operation: string;
  error_kind: string;
  message: string;
  turn_id: string | null;
  traceback: string | null;
  context: Record<string, unknown>;
  user_visible: boolean;
  user_action_taken: string | null;
}

export interface PromptMessage {
  role: string;
  content: string;
  name: string | null;
  tier: string | null;
  tokens: number;
  metadata: Record<string, unknown>;
}

export interface PromptSource {
  source_id: string;
  kind: string;
  scope: string;
  owner_id: string | null;
  tier: ContextTier;
  library_version: number | null;
  override_applied: boolean;
  tokens: number;
  summary: string;
}

export interface PromptCompositionSnapshot {
  mechanics_module: string | null;
  world_refs: Array<Record<string, unknown>>;
  style_guide_id: string | null;
  image_preset_id: string | null;
}

export interface PromptContextSummary {
  total_tokens: number;
  per_tier: Record<string, number>;
  source_count: number;
  spotlight_characters: string[];
}

export interface PromptResponse {
  messages: PromptMessage[];
  sources: PromptSource[];
  budget_used: Record<string, number>;
  messages_hash: string;
  composition_snapshot: PromptCompositionSnapshot | null;
  summary: PromptContextSummary | null;
}

export interface PromptDiffMessage {
  role: string;
  tier: string | null;
  tokens: number;
  content: string;
}

export interface PromptDiffChange {
  role: string;
  tier: string | null;
  before: PromptDiffMessage;
  after: PromptDiffMessage;
}

export interface PromptDiffSource {
  source_id: string;
  kind: string;
  owner_id: string | null;
  scope: string | null;
  tier: string | null;
  tokens: number;
  override_applied: boolean;
  summary: string;
}

export interface PromptDiff {
  turn_id_a: string;
  turn_id_b: string;
  messages_hash_changed: boolean;
  added_messages: PromptDiffMessage[];
  removed_messages: PromptDiffMessage[];
  changed_messages: PromptDiffChange[];
  added_sources: PromptDiffSource[];
  removed_sources: PromptDiffSource[];
  tier_budget_shifts: Record<string, number>;
}

export interface TurnAuditSummary {
  turn_id: string;
  campaign_id: string;
  branch_id: string;
  scene_id: string;
  started_at: string;
  completed_at: string | null;
  player_input: string;
  llm_model: string;
  llm_provider: string;
  context_messages_hash: string;
}

export interface ContextSourceFromAudit {
  source_id: string;
  owner_id: string | null;
  kind: string;
  scope: string;
  tier: ContextTier;
  library_version: number | null;
  override_applied: boolean;
  tokens: number;
  summary: string;
  inclusion_reasons: InclusionReason[];
}

export interface TurnPromptResponse {
  messages: unknown[];
  sources: ContextSourceFromAudit[];
  budget_used: Record<string, number>;
  messages_hash: string;
  composition_snapshot: unknown;
  summary: unknown;
}

export interface TaskCostRow {
  task: string;
  total_usd: number;
  input_tokens: number;
  output_tokens: number;
  call_count: number;
}

export interface CostConfig {
  surface_in_status_bar: boolean;
  daily_budget_warn_usd: number;
  daily_budget_alert_usd: number;
}

export interface CostTotal {
  total_usd: number;
  input_tokens: number;
  output_tokens: number;
  call_count: number;
}

export interface DailyCost {
  date: string;
  total_usd: number;
  call_count: number;
}

function base(turnId: string) {
  return `/api/observability/turns/${encodeURIComponent(turnId)}`;
}

export const observabilityApi = {
  getMetricsKnown(): Promise<MetricsKnownPair[]> {
    return api.get<MetricsKnownPair[]>("/api/observability/metrics/known");
  },

  getMetricsSummary(
    module: string,
    operation: string,
    windowSeconds?: number,
  ): Promise<MetricsSummary> {
    return api.get<MetricsSummary>("/api/observability/metrics/summary", {
      query: { module, operation, window_seconds: windowSeconds },
    });
  },

  getMetricsTrend(
    module: string,
    operation: string,
    bucket: TrendBucketSize,
    windowSeconds: number,
  ): Promise<MetricsTrendBucket[]> {
    return api.get<MetricsTrendBucket[]>("/api/observability/metrics/trend", {
      query: { module, operation, bucket, window_seconds: windowSeconds },
    });
  },

  turnDeltas(turnId: string, signal?: AbortSignal): Promise<TurnDeltaDiff> {
    return api.get<TurnDeltaDiff>(
      `/api/observability/turns/${encodeURIComponent(turnId)}/deltas`,
      { signal },
    );
  },

  healthLatest: (signal?: AbortSignal) =>
    api.get<HealthLatest>("/api/observability/health/latest", { signal }),
  probe: (targetId: string) =>
    api.post<HealthStatus>("/api/observability/health/probe", undefined, {
      query: { target_id: targetId },
    }),
  errorsAggregate: (since?: string, signal?: AbortSignal) =>
    api.get<ErrorAggregate>("/api/observability/errors/aggregate", {
      query: since ? { since } : undefined,
      signal,
    }),
  errorsRecent: (limit = 50, signal?: AbortSignal) =>
    api.get<ErrorRecord[]>("/api/observability/errors/recent", {
      query: { limit },
      signal,
    }),

  listTurns(campaignId: string, limit = 50): Promise<TurnAuditSummary[]> {
    return api.get<TurnAuditSummary[]>("/api/observability/turns", {
      query: { campaign_id: campaignId, limit },
    });
  },
  getPrompt(turnId: string): Promise<PromptResponse> {
    return api.get(`${base(turnId)}/prompt`);
  },
  getTurnPrompt(turnId: string): Promise<TurnPromptResponse> {
    return api.get<TurnPromptResponse>(`${base(turnId)}/prompt`);
  },
  diffPrompts(turnId: string, against: string): Promise<PromptDiff> {
    return api.get(`${base(turnId)}/prompt/diff`, { query: { against } });
  },
  turnCosts(turnId: string): Promise<TaskCostRow[]> {
    return api.get(`${base(turnId)}/costs`);
  },

  getCostConfig(signal?: AbortSignal): Promise<CostConfig> {
    return api.get<CostConfig>("/api/observability/config/cost", { signal });
  },

  getSessionCost(campaignId: string, since?: string, signal?: AbortSignal): Promise<CostTotal> {
    return api.get<CostTotal>("/api/observability/costs/session", {
      signal,
      query: { campaign_id: campaignId, since },
    });
  },

  getCostRollup(campaignId: string, days = 30, signal?: AbortSignal): Promise<DailyCost[]> {
    return api.get<DailyCost[]>("/api/observability/costs/rollup", {
      signal,
      query: { campaign_id: campaignId, days },
    });
  },

  getTotalToday(campaignId: string, signal?: AbortSignal): Promise<{ total_usd: number }> {
    return api.get<{ total_usd: number }>("/api/observability/costs/total_today", {
      signal,
      query: { campaign_id: campaignId },
    });
  },
};
