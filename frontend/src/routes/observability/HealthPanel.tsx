/**
 * Frontend Health panel (spec 16 §health checks + §error reporting).
 *
 * Renders the latest result for every registered HealthMonitor target and
 * groups recent errors by module + kind. Each target row exposes a
 * re-probe button. When the panel mounts inside an active campaign the
 * CampaignStreamProvider WebSocket also pushes ``health_status_changed``
 * and ``error_reported`` events through, so the panel updates live; outside
 * a campaign we fall back to a slow poll.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError } from "../../api/client";
import {
  type ErrorAggregate,
  type HealthLatest,
  type HealthLevel,
  type HealthStatus,
  observabilityApi,
} from "../../api/observability";
import { useCampaignEvent, useCampaignId } from "../../state/useCampaignEvent";

const POLL_MS = 15_000;

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return `${err.status}: ${err.message}`;
  if (err instanceof Error) return err.message;
  return String(err);
}

function levelLabel(level: HealthLevel): string {
  switch (level) {
    case "healthy":
      return "Healthy";
    case "degraded":
      return "Degraded";
    case "unhealthy":
      return "Unhealthy";
    case "unconfigured":
      return "Not configured";
  }
}

function isLevel(value: unknown): value is HealthLevel {
  return (
    value === "healthy" || value === "degraded" || value === "unhealthy" || value === "unconfigured"
  );
}

function formatChecked(checked: string | null): string {
  if (!checked) return "never";
  try {
    return new Date(checked).toLocaleTimeString();
  } catch {
    return checked;
  }
}

interface ErrorRow {
  module: string;
  kind: string;
  count: number;
}

function aggregateToRows(agg: ErrorAggregate): ErrorRow[] {
  const rows: ErrorRow[] = [];
  for (const [module, byKind] of Object.entries(agg)) {
    for (const [kind, count] of Object.entries(byKind)) {
      rows.push({ module, kind, count });
    }
  }
  rows.sort((a, b) => b.count - a.count);
  return rows;
}

export function HealthPanel() {
  const [latest, setLatest] = useState<HealthLatest>({});
  const [errors, setErrors] = useState<ErrorAggregate>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [probing, setProbing] = useState<string | null>(null);
  const campaignId = useCampaignId();

  const refresh = useCallback(async (signal?: AbortSignal) => {
    try {
      const [h, e] = await Promise.all([
        observabilityApi.healthLatest(signal),
        observabilityApi.errorsAggregate(undefined, signal),
      ]);
      if (!signal?.aborted) {
        setLatest(h);
        setErrors(e);
        setError(null);
      }
    } catch (err) {
      if (signal?.aborted) return;
      setError(errorMessage(err));
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const ctl = new AbortController();
    void refresh(ctl.signal);
    const timer = window.setInterval(() => {
      void refresh();
    }, POLL_MS);
    return () => {
      ctl.abort();
      window.clearInterval(timer);
    };
  }, [refresh]);

  // Apply incoming health_status_changed events directly so we don't wait
  // for the next poll. The backend broadcasts these via the campaign WS so
  // the live-update path is only active when the user is in a campaign.
  useCampaignEvent("health_status_changed", (message) => {
    const targetId = message.target_id;
    const level = message.level;
    if (typeof targetId !== "string" || !isLevel(level)) return;
    const status: HealthStatus = {
      target_id: targetId,
      level,
      message: typeof message.message === "string" ? message.message : "",
      checked_at: typeof message.checked_at === "string" ? message.checked_at : null,
      details:
        message.details && typeof message.details === "object"
          ? (message.details as Record<string, unknown>)
          : {},
    };
    setLatest((prev) => ({ ...prev, [targetId]: status }));
  });

  useCampaignEvent("error_reported", () => {
    // Aggregate counts come from the database, not the event payload, so
    // refresh on each notification rather than trying to mutate locally.
    void refresh();
  });

  const onProbe = useCallback(async (targetId: string) => {
    setProbing(targetId);
    try {
      const result = await observabilityApi.probe(targetId);
      setLatest((prev) => ({ ...prev, [targetId]: result }));
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setProbing(null);
    }
  }, []);

  const rows = useMemo(() => {
    const entries = Object.values(latest);
    entries.sort((a, b) => a.target_id.localeCompare(b.target_id));
    return entries;
  }, [latest]);

  const errorRows = useMemo(() => aggregateToRows(errors), [errors]);
  const failing = rows.filter((r) => r.level === "unhealthy" || r.level === "degraded").length;

  return (
    <section className="route observability-health" aria-labelledby="health-panel-heading">
      <header className="observability-header">
        <h2 id="health-panel-heading">Health</h2>
        <p className="observability-sub">
          Probes for installed providers and backends.{" "}
          {failing > 0 ? `${failing} need attention.` : "All clear."}
          {campaignId ? " Live updates active." : " Polling — open a campaign for live updates."}
        </p>
      </header>

      {error && (
        <p className="wizard-error" role="alert">
          {error}
        </p>
      )}

      <section aria-labelledby="health-targets-heading" className="observability-section">
        <h3 id="health-targets-heading">Targets</h3>
        {loading && rows.length === 0 && <p className="wizard-meta">Loading…</p>}
        {!loading && rows.length === 0 && (
          <p className="wizard-meta">
            No targets registered. Configure an LLM provider, embedding plugin, or imagegen backend.
          </p>
        )}
        {rows.length > 0 && (
          <table className="health-table">
            <thead>
              <tr>
                <th scope="col">Target</th>
                <th scope="col">Status</th>
                <th scope="col">Message</th>
                <th scope="col">Checked</th>
                <th scope="col" aria-label="actions" />
              </tr>
            </thead>
            <tbody>
              {rows.map((status) => (
                <tr key={status.target_id} data-level={status.level}>
                  <th scope="row">{status.target_id}</th>
                  <td>
                    <span className="health-badge" data-level={status.level}>
                      {levelLabel(status.level)}
                    </span>
                  </td>
                  <td className="health-message">{status.message || "—"}</td>
                  <td>{formatChecked(status.checked_at)}</td>
                  <td>
                    <button
                      type="button"
                      onClick={() => void onProbe(status.target_id)}
                      disabled={probing === status.target_id}
                    >
                      {probing === status.target_id ? "Probing…" : "Re-probe"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section aria-labelledby="health-errors-heading" className="observability-section">
        <h3 id="health-errors-heading">Recent errors by module</h3>
        {errorRows.length === 0 ? (
          <p className="wizard-meta">No errors recorded.</p>
        ) : (
          <table className="health-table">
            <thead>
              <tr>
                <th scope="col">Module</th>
                <th scope="col">Kind</th>
                <th scope="col">Count</th>
              </tr>
            </thead>
            <tbody>
              {errorRows.map((row) => (
                <tr key={`${row.module}::${row.kind}`}>
                  <th scope="row">{row.module}</th>
                  <td>{row.kind || "—"}</td>
                  <td>{row.count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </section>
  );
}
