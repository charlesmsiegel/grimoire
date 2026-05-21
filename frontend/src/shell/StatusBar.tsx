import { useEffect, useState } from "react";

import { observabilityApi, type CostConfig } from "../api/observability";
import { useCampaignEvent } from "../state/useCampaignEvent";
import { useTheme } from "../state/useTheme";
import { useAppState } from "../state/useStore";
import type { WSStatus } from "../ws/client";

interface StatusBarProps {
  wsStatus: WSStatus;
}

interface CostState {
  sessionUsd: number | null;
  todayUsd: number | null;
}

function statusLabel(status: WSStatus): string {
  switch (status) {
    case "idle":
      return "no campaign";
    case "connecting":
      return "connecting";
    case "open":
      return "live";
    case "reconnecting":
      return "reconnecting";
    case "closed":
      return "offline";
  }
}

function formatUsd(value: number): string {
  return `$${value.toFixed(2)}`;
}

function costSeverity(today: number | null, config: CostConfig | null): "alert" | "warn" | null {
  if (today === null || config === null) return null;
  if (today >= config.daily_budget_alert_usd) return "alert";
  if (today >= config.daily_budget_warn_usd) return "warn";
  return null;
}

function useCostConfig(): CostConfig | null {
  const [config, setConfig] = useState<CostConfig | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    observabilityApi
      .getCostConfig(controller.signal)
      .then(setConfig)
      .catch(() => setConfig(null));
    return () => controller.abort();
  }, []);
  return config;
}

function useCostTotals(campaignId: string | null): CostState {
  const [totals, setTotals] = useState<CostState>({ sessionUsd: null, todayUsd: null });
  const [refreshKey, setRefreshKey] = useState(0);

  useCampaignEvent("turn_complete", () => setRefreshKey((k) => k + 1));

  useEffect(() => {
    if (!campaignId) {
      setTotals({ sessionUsd: null, todayUsd: null });
      return;
    }
    const controller = new AbortController();
    let cancelled = false;
    (async () => {
      try {
        const [session, today] = await Promise.all([
          observabilityApi.getSessionCost(campaignId, undefined, controller.signal),
          observabilityApi.getTotalToday(campaignId, controller.signal),
        ]);
        if (!cancelled) {
          setTotals({ sessionUsd: session.total_usd, todayUsd: today.total_usd });
        }
      } catch {
        // Network blip / aborted fetch — leave the previous value visible.
      }
    })();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [campaignId, refreshKey]);

  return totals;
}

export function StatusBar({ wsStatus }: StatusBarProps) {
  const state = useAppState();
  const { mode, resolved, cycle } = useTheme();
  const active = state.campaigns.find((c) => c.id === state.activeCampaignId) ?? null;
  const { modelLabel, tokenBudget, queueDepth, driftAlerts } = state.status;
  const costConfig = useCostConfig();
  const { sessionUsd, todayUsd } = useCostTotals(active?.id ?? null);
  const showCost = costConfig?.surface_in_status_bar !== false;
  const severity = costSeverity(todayUsd, costConfig);

  return (
    <footer className="status-bar" aria-label="Status">
      <span className="status-item" data-status={wsStatus}>
        <span className="dot" aria-hidden />
        {statusLabel(wsStatus)}
      </span>
      <span className="status-item">campaign: {active?.name ?? "—"}</span>
      <span className="status-item">model: {modelLabel ?? "—"}</span>
      <span className="status-item">
        budget:{" "}
        {tokenBudget
          ? `${tokenBudget.used.toLocaleString()} / ${tokenBudget.total.toLocaleString()}`
          : "—"}
      </span>
      {showCost && (
        <span
          className="status-item"
          data-warn={severity === "warn" ? "true" : undefined}
          data-alert={severity === "alert" ? "true" : undefined}
          title={
            todayUsd !== null && costConfig
              ? `today: ${formatUsd(todayUsd)} (warn ${formatUsd(costConfig.daily_budget_warn_usd)} / alert ${formatUsd(costConfig.daily_budget_alert_usd)})`
              : undefined
          }
        >
          cost: {sessionUsd !== null ? formatUsd(sessionUsd) : "—"}
        </span>
      )}
      <span className="status-item">queue: {queueDepth}</span>
      <span className="status-item" data-warn={driftAlerts.length > 0 ? "true" : undefined}>
        drift: {driftAlerts.length}
      </span>
      <button
        type="button"
        className="status-theme"
        onClick={cycle}
        aria-label={`Theme: ${mode} (resolved ${resolved}). Click to cycle.`}
        title="Toggle theme (T)"
      >
        theme: {mode}
      </button>
    </footer>
  );
}
