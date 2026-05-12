import { useTheme } from "../state/useTheme";
import { useAppState } from "../state/useStore";
import type { WSStatus } from "../ws/client";

interface StatusBarProps {
  wsStatus: WSStatus;
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

export function StatusBar({ wsStatus }: StatusBarProps) {
  const state = useAppState();
  const { mode, resolved, cycle } = useTheme();
  const active = state.campaigns.find((c) => c.id === state.activeCampaignId) ?? null;
  const { modelLabel, tokenBudget, queueDepth, driftAlerts } = state.status;

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
