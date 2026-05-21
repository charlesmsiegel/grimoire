/**
 * REST client for observability endpoints. Mirrors the routes in
 * ``backend/src/grimoire/api/observability.py``.
 *
 * Covers performance metrics (#355), the per-turn delta diff (#351), and
 * the Frontend Health panel (#357): latest probe results, manual
 * re-probes, and errors grouped by module.
 */

import { api } from "./client";

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
};
