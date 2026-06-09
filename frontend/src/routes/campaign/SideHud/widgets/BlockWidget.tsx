/**
 * BlockWidget — title + multi-line content. Used for recent events,
 * commitments, threads, scene summary.
 */

import type { WidgetSnapshot } from "../../../../api/hud";
import {
  asArray,
  asRecord,
  asString,
  errorMessage,
  primaryScalar,
  statusLabel,
} from "./widget-common";

interface Props {
  snapshot: WidgetSnapshot;
  onRefresh?: () => void;
}

function extractItems(data: unknown): { key: string; text: string }[] | null {
  const arr = asArray(data);
  if (arr) {
    return arr
      .map((item, i): { key: string; text: string } | null => {
        if (typeof item === "string") return { key: String(i), text: item };
        const r = asRecord(item);
        if (!r) return null;
        const text =
          asString(r.text) ??
          asString(r.statement) ??
          asString(r.label) ??
          asString(r.summary) ??
          asString(r.title) ??
          null;
        if (text === null) return null;
        const id = asString(r.id) ?? String(i);
        return { key: id, text };
      })
      .filter((x): x is { key: string; text: string } => x !== null);
  }
  return null;
}

export function BlockWidget({ snapshot, onRefresh }: Props) {
  const title = snapshot.title ?? snapshot.id;
  const status = statusLabel(snapshot);

  if (snapshot.status === "hidden") return null;

  return (
    <section
      className={`hud-widget hud-widget-block hud-status-${snapshot.status}${snapshot.stale ? " hud-stale" : ""}`}
      aria-label={title}
    >
      <header className="hud-block-header">
        <h3>{title}</h3>
        {status && <span className="hud-block-badge">{status}</span>}
        {(snapshot.status !== "ok" || snapshot.stale) && onRefresh && (
          <button
            type="button"
            className="hud-block-refresh"
            aria-label={`Refresh ${title}`}
            onClick={onRefresh}
          >
            ↻
          </button>
        )}
      </header>
      <div className="hud-block-body">{renderBody(snapshot)}</div>
    </section>
  );
}

function renderBody(snapshot: WidgetSnapshot): React.ReactNode {
  if (snapshot.status !== "ok") {
    return <p className="hud-block-error">{errorMessage(snapshot)}</p>;
  }
  const items = extractItems(snapshot.data);
  if (items !== null) {
    if (items.length === 0) {
      return <p className="empty-state">Nothing yet.</p>;
    }
    return (
      <ul className="hud-block-list">
        {items.map((it) => (
          <li key={it.key}>{it.text}</li>
        ))}
      </ul>
    );
  }
  const scalar = primaryScalar(snapshot.data);
  if (scalar !== null) return <p className="hud-block-text">{scalar}</p>;
  return <p className="empty-state">No data.</p>;
}
