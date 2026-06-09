import { useEffect, useState } from "react";

import {
  observabilityApi,
  type MetricsKnownPair,
  type MetricsSummary,
  type MetricsTrendBucket,
  type TrendBucketSize,
} from "../../api/observability";
import { Sparkline } from "./Sparkline";
import { useObservabilityPolling } from "./useObservabilityPolling";

const WINDOW_OPTIONS = [
  { label: "last 1h", seconds: 3600 },
  { label: "last 6h", seconds: 21600 },
  { label: "last 24h", seconds: 86400 },
] as const;

const BUCKETS: TrendBucketSize[] = ["minute", "hour", "day"];

const POLL_INTERVAL_MS = 10_000;

type PairKey = string;

function keyOf(p: MetricsKnownPair): PairKey {
  return `${p.module}/${p.operation}`;
}

function formatMs(value: number): string {
  if (value >= 1000) return `${(value / 1000).toFixed(1)}s`;
  return `${value.toFixed(0)}ms`;
}

export function PerformanceTab() {
  const [windowSeconds, setWindowSeconds] = useState<number>(WINDOW_OPTIONS[0].seconds);
  const [bucket, setBucket] = useState<TrendBucketSize>("minute");
  const [pairs, setPairs] = useState<MetricsKnownPair[]>([]);
  const [summaries, setSummaries] = useState<Record<PairKey, MetricsSummary>>({});
  const [trends, setTrends] = useState<Record<PairKey, MetricsTrendBucket[]>>({});
  const [error, setError] = useState<string | null>(null);

  const refresh = async () => {
    try {
      const known = await observabilityApi.getMetricsKnown();
      setPairs(known);
      setError(null);
      const summaryEntries = await Promise.all(
        known.map(
          async (p) =>
            [
              keyOf(p),
              await observabilityApi.getMetricsSummary(p.module, p.operation, windowSeconds),
            ] as const,
        ),
      );
      const trendEntries = await Promise.all(
        known.map(
          async (p) =>
            [
              keyOf(p),
              await observabilityApi.getMetricsTrend(p.module, p.operation, bucket, windowSeconds),
            ] as const,
        ),
      );
      setSummaries(Object.fromEntries(summaryEntries));
      setTrends(Object.fromEntries(trendEntries));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [windowSeconds, bucket]);

  useObservabilityPolling(refresh, POLL_INTERVAL_MS);

  if (error !== null) {
    return (
      <div className="observability-performance error" role="alert">
        Metrics unavailable: {error}
      </div>
    );
  }

  return (
    <div className="observability-performance">
      <header className="observability-controls">
        <label>
          Window:&nbsp;
          <select value={windowSeconds} onChange={(e) => setWindowSeconds(Number(e.target.value))}>
            {WINDOW_OPTIONS.map((opt) => (
              <option key={opt.seconds} value={opt.seconds}>
                {opt.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Bucket:&nbsp;
          <select value={bucket} onChange={(e) => setBucket(e.target.value as TrendBucketSize)}>
            {BUCKETS.map((b) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))}
          </select>
        </label>
        <button type="button" onClick={() => void refresh()}>
          Refresh
        </button>
      </header>

      {pairs.length === 0 ? (
        <p className="empty-state">No metrics recorded yet.</p>
      ) : (
        <ul className="observability-pair-list">
          {pairs.map((p) => {
            const k = keyOf(p);
            const s = summaries[k];
            const t = trends[k] ?? [];
            return (
              <li key={k} className="observability-pair">
                <header>
                  <strong>{p.module}</strong> / {p.operation}
                </header>
                {s && (
                  <p className="observability-summary">
                    count {s.count}{" "}
                    {s.failures > 0 && <span className="failed">({s.failures} failed)</span>} p50{" "}
                    {formatMs(s.p50_ms)} p95 {formatMs(s.p95_ms)} p99 {formatMs(s.p99_ms)}
                  </p>
                )}
                <Sparkline
                  values={t.map((b) => b.p95_ms)}
                  ariaLabel={`p95 trend for ${p.module}/${p.operation}`}
                />
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
