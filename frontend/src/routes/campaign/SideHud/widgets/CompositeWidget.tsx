/**
 * CompositeWidget — the catch-all render hint. Used by widgets that need
 * a custom layout (today: review-queue count + jump-to affordance).
 *
 * We pull a few well-known shape fragments out of the data (count, items,
 * label) and render a compact two-line summary; the canonical owner
 * module is responsible for the deeper UI in its own route.
 */

import type { WidgetSnapshot } from "../../../../api/hud";
import { asArray, asNumber, asRecord, asString, errorMessage, statusLabel } from "./widget-common";

interface Props {
  snapshot: WidgetSnapshot;
  onRefresh?: () => void;
}

interface CompositeSummary {
  count: number | null;
  label: string | null;
  items: string[];
}

function extractSummary(data: unknown): CompositeSummary {
  const r = asRecord(data);
  if (!r) {
    const arr = asArray(data);
    if (arr) {
      return {
        count: arr.length,
        label: null,
        items: arr
          .map((x) => (typeof x === "string" ? x : asString(asRecord(x)?.text)))
          .filter((s): s is string => s !== null),
      };
    }
    return { count: null, label: null, items: [] };
  }
  const count = asNumber(r.count) ?? asNumber(r.total) ?? asNumber(r.pending) ?? null;
  const label = asString(r.label) ?? asString(r.summary) ?? null;
  const itemsRaw = asArray(r.items);
  const items = itemsRaw
    ? itemsRaw
        .map((x) =>
          typeof x === "string"
            ? x
            : (asString(asRecord(x)?.text) ?? asString(asRecord(x)?.summary) ?? null),
        )
        .filter((s): s is string => s !== null)
    : [];
  return { count, label, items };
}

export function CompositeWidget({ snapshot, onRefresh }: Props) {
  if (snapshot.status === "hidden") return null;
  const title = snapshot.title ?? snapshot.id;
  const status = statusLabel(snapshot);

  if (snapshot.status !== "ok") {
    return (
      <section className={`hud-widget hud-widget-composite hud-status-${snapshot.status}`}>
        <header className="hud-composite-header">
          <h3>{title}</h3>
          {onRefresh && (
            <button
              type="button"
              className="hud-composite-refresh"
              aria-label={`Refresh ${title}`}
              onClick={onRefresh}
            >
              ↻
            </button>
          )}
        </header>
        <p className="hud-composite-error">{errorMessage(snapshot)}</p>
      </section>
    );
  }

  const { count, label, items } = extractSummary(snapshot.data);
  return (
    <section
      className={`hud-widget hud-widget-composite${snapshot.stale ? " hud-stale" : ""}`}
      aria-label={title}
    >
      <header className="hud-composite-header">
        <h3>{title}</h3>
        {count !== null && <span className="hud-composite-count">{count}</span>}
        {status && <span className="hud-composite-badge">{status}</span>}
      </header>
      {label && <p className="hud-composite-label">{label}</p>}
      {items.length > 0 && (
        <ul className="hud-composite-items">
          {items.slice(0, 3).map((t, i) => (
            <li key={i}>{t}</li>
          ))}
        </ul>
      )}
      {count === null && items.length === 0 && !label && (
        <p className="hud-composite-empty">Nothing to show.</p>
      )}
    </section>
  );
}
