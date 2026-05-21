/**
 * Observability client. Mirrors the routes in
 * ``backend/src/grimoire/api/observability.py`` (#355).
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
};
