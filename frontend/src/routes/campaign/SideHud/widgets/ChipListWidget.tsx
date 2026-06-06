/**
 * ChipListWidget — grid of chips. The ``core.present-cast`` widget gets
 * the richer ``PresentCastChip`` renderer; other chip-list widgets render
 * generic label chips so a mechanics module can use this render hint
 * without needing a custom component.
 */

import type { WidgetSnapshot } from "../../../../api/hud";
import { PresentCastChip } from "../PresentCastChip";
import { parsePresentCast } from "../presentCastShape";
import { asArray, asRecord, asString, errorMessage, statusLabel } from "./widget-common";

interface Props {
  snapshot: WidgetSnapshot;
  campaignId?: string;
  onRefresh?: () => void;
}

interface GenericChip {
  key: string;
  label: string;
  tone?: string;
}

function extractGenericChips(data: unknown): GenericChip[] {
  const arr = asArray(data);
  if (!arr) return [];
  return arr
    .map((item, i): GenericChip | null => {
      if (typeof item === "string") return { key: String(i), label: item };
      const r = asRecord(item);
      if (!r) return null;
      const label = asString(r.label) ?? asString(r.name) ?? asString(r.text);
      if (!label) return null;
      return {
        key: asString(r.id) ?? String(i),
        label,
        tone: asString(r.tone) ?? asString(r.kind) ?? undefined,
      };
    })
    .filter((x): x is GenericChip => x !== null);
}

export function ChipListWidget({ snapshot, campaignId, onRefresh }: Props) {
  if (snapshot.status === "hidden") return null;

  const title = snapshot.title ?? snapshot.id;
  const status = statusLabel(snapshot);
  const isPresentCast = snapshot.id === "core.present-cast";

  return (
    <section
      className={`hud-widget hud-widget-chip-list hud-status-${snapshot.status}${snapshot.stale ? " hud-stale" : ""}`}
      aria-label={title}
    >
      <header className="hud-chip-list-header">
        <h3>{title}</h3>
        {!isPresentCast && status && <span className="hud-chip-list-badge">{status}</span>}
        {!isPresentCast && (snapshot.status !== "ok" || snapshot.stale) && onRefresh && (
          <button
            type="button"
            className="hud-chip-list-refresh"
            aria-label={`Refresh ${title}`}
            onClick={onRefresh}
          >
            ↻
          </button>
        )}
      </header>
      <div className="hud-chip-list-body">{renderBody(snapshot, isPresentCast, campaignId)}</div>
    </section>
  );
}

function renderBody(
  snapshot: WidgetSnapshot,
  isPresentCast: boolean,
  campaignId?: string,
): React.ReactNode {
  if (snapshot.status !== "ok") {
    return <p className="hud-chip-list-error">{errorMessage(snapshot)}</p>;
  }
  if (isPresentCast && campaignId) {
    const chips = parsePresentCast(snapshot.data);
    if (chips.length === 0) {
      return <p className="hud-chip-list-empty">No present characters.</p>;
    }
    return (
      <ul className="hud-chip-list hud-present-cast-list">
        {chips.map((chip) => (
          <li key={chip.character_id}>
            <PresentCastChip chip={chip} campaignId={campaignId} />
          </li>
        ))}
      </ul>
    );
  }
  const generic = extractGenericChips(snapshot.data);
  if (generic.length === 0) {
    return <p className="hud-chip-list-empty">Nothing yet.</p>;
  }
  return (
    <ul className="hud-chip-list hud-generic-chip-list">
      {generic.map((c) => (
        <li key={c.key} className={`hud-generic-chip${c.tone ? ` hud-chip-tone-${c.tone}` : ""}`}>
          {c.label}
        </li>
      ))}
    </ul>
  );
}
