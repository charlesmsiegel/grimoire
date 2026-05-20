/**
 * RowWidget — compact ``title: value`` line for scalar-ish widgets
 * (date, time of day, weather, location, temperature).
 */

import type { WidgetSnapshot } from "../../../../api/hud";
import { errorMessage, primaryScalar, statusLabel } from "./widget-common";

interface Props {
  snapshot: WidgetSnapshot;
  onRefresh?: () => void;
}

export function RowWidget({ snapshot, onRefresh }: Props) {
  const title = snapshot.title ?? snapshot.id;
  const status = statusLabel(snapshot);

  let body: React.ReactNode;
  if (snapshot.status === "ok") {
    const scalar = primaryScalar(snapshot.data);
    body = scalar !== null ? <span className="hud-row-value">{scalar}</span> : (
      <span className="hud-row-value hud-row-empty">—</span>
    );
  } else if (snapshot.status === "hidden") {
    return null;
  } else {
    body = (
      <span className="hud-row-error" title={errorMessage(snapshot)}>
        {errorMessage(snapshot)}
      </span>
    );
  }

  return (
    <div
      className={`hud-widget hud-widget-row hud-status-${snapshot.status}${snapshot.stale ? " hud-stale" : ""}`}
      role="group"
      aria-label={title}
    >
      <span className="hud-row-label">{title}</span>
      {body}
      {status && <span className="hud-row-badge">{status}</span>}
      {(snapshot.status !== "ok" || snapshot.stale) && onRefresh && (
        <button
          type="button"
          className="hud-row-refresh"
          aria-label={`Refresh ${title}`}
          onClick={onRefresh}
        >
          ↻
        </button>
      )}
    </div>
  );
}
