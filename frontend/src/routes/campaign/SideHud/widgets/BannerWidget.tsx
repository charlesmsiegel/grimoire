/**
 * BannerWidget — full-width alert strip, e.g. drift-alerts.
 *
 * Empty alerts collapse the strip rather than showing a placeholder; the
 * surrounding HUD already has enough chrome.
 */

import type { WidgetSnapshot } from "../../../../api/hud";
import {
  asArray,
  asRecord,
  asString,
  errorMessage,
  statusLabel,
} from "./widget-common";

interface Props {
  snapshot: WidgetSnapshot;
  onRefresh?: () => void;
}

interface AlertItem {
  key: string;
  text: string;
  detail?: string;
}

function extractAlerts(data: unknown): AlertItem[] {
  const arr = asArray(data);
  if (!arr) return [];
  return arr
    .map((item, i): AlertItem | null => {
      if (typeof item === "string") return { key: String(i), text: item };
      const r = asRecord(item);
      if (!r) return null;
      const text =
        asString(r.text) ??
        asString(r.statement) ??
        asString(r.message) ??
        asString(r.character_ref) ??
        null;
      if (text === null) return null;
      const detail = asString(r.detail) ?? asString(r.reason) ?? undefined;
      return { key: asString(r.id) ?? text + i, text, detail };
    })
    .filter((x): x is AlertItem => x !== null);
}

export function BannerWidget({ snapshot, onRefresh }: Props) {
  if (snapshot.status === "hidden") return null;

  if (snapshot.status !== "ok") {
    return (
      <div
        className="hud-widget hud-widget-banner hud-banner-error"
        role="alert"
        aria-label={snapshot.title ?? snapshot.id}
      >
        <span className="hud-banner-text">{errorMessage(snapshot)}</span>
        {onRefresh && (
          <button type="button" className="hud-banner-refresh" onClick={onRefresh}>
            Retry
          </button>
        )}
      </div>
    );
  }

  const alerts = extractAlerts(snapshot.data);
  if (alerts.length === 0) return null;

  const status = statusLabel(snapshot);
  return (
    <div
      className={`hud-widget hud-widget-banner${snapshot.stale ? " hud-stale" : ""}`}
      role="alert"
      aria-label={snapshot.title ?? snapshot.id}
    >
      <strong className="hud-banner-title">{snapshot.title ?? "Alerts"}</strong>
      <ul className="hud-banner-list">
        {alerts.map((a) => (
          <li key={a.key} title={a.detail}>
            {a.text}
          </li>
        ))}
      </ul>
      {status && <span className="hud-banner-badge">{status}</span>}
    </div>
  );
}
