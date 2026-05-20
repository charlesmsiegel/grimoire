/**
 * PresentCastChip — one chip per character in the scene.
 *
 * Shape (best-effort, since the backend ``present_cast_payload`` fetcher
 * still has to be wired):
 *   {
 *     character_id: string,
 *     character_ref?: string,
 *     name: string,
 *     portrait_url?: string,
 *     mood?: { emoji?: string, label?: string },
 *     current_action?: string,
 *     internal_thought?: string,
 *     drift?: { score: number, threshold?: number },
 *     source?: "library" | "emergent" | "override",
 *     pinned_extras?: { key: string, value: string }[],
 *   }
 *
 * Unknown fields are silently ignored — the chip degrades gracefully if
 * the owner module ships a partial payload.
 */

import type { PresentCastChipData } from "./presentCastShape";

interface Props {
  chip: PresentCastChipData;
}

const SOURCE_BADGE: Record<string, string> = {
  library: "📚",
  emergent: "🌿",
  override: "✏️",
};

export function PresentCastChip({ chip }: Props) {
  const driftScore = chip.drift?.score ?? null;
  const driftThreshold = chip.drift?.threshold ?? 0.5;
  const driftAlert = driftScore !== null && driftScore >= driftThreshold;

  return (
    <article
      className="hud-chip hud-present-cast-chip"
      aria-label={`Present cast: ${chip.name}`}
    >
      <div className="hud-chip-header">
        {chip.portrait_url ? (
          <img
            className="hud-chip-portrait"
            src={chip.portrait_url}
            alt=""
            aria-hidden="true"
          />
        ) : (
          <div className="hud-chip-portrait hud-chip-portrait-empty" aria-hidden="true">
            {chip.name.charAt(0).toUpperCase()}
          </div>
        )}
        <div className="hud-chip-titles">
          <strong className="hud-chip-name">{chip.name}</strong>
          {chip.source && (
            <span
              className={`hud-chip-source hud-chip-source-${chip.source}`}
              title={`source: ${chip.source}`}
            >
              {SOURCE_BADGE[chip.source] ?? "•"}
            </span>
          )}
          {driftAlert && (
            <span
              className="hud-chip-drift"
              role="status"
              title={`drift ${driftScore!.toFixed(2)} (threshold ${driftThreshold})`}
            >
              ●
            </span>
          )}
        </div>
      </div>

      {chip.mood && (chip.mood.emoji || chip.mood.label) && (
        <div className="hud-chip-mood">
          {chip.mood.emoji && <span className="hud-chip-mood-emoji">{chip.mood.emoji}</span>}
          {chip.mood.label && <span className="hud-chip-mood-label">{chip.mood.label}</span>}
        </div>
      )}

      {chip.current_action && (
        <p className="hud-chip-action">{chip.current_action}</p>
      )}

      {chip.internal_thought && (
        <p className="hud-chip-thought">
          <span className="hud-chip-thought-bubble" aria-hidden="true">
            💭
          </span>
          <em>{chip.internal_thought}</em>
        </p>
      )}

      {chip.pinned_extras && chip.pinned_extras.length > 0 && (
        <ul className="hud-chip-extras">
          {chip.pinned_extras.map((extra) => (
            <li key={extra.key} className="hud-chip-extra">
              <span className="hud-chip-extra-pin" aria-hidden="true">
                📌
              </span>
              {extra.value}
            </li>
          ))}
        </ul>
      )}
    </article>
  );
}
