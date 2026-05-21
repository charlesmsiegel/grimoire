/**
 * Narrative-digest modal shown after a player-initiated time advance.
 *
 * Spec 07 §Digest generation calls for the digest to surface "when the player
 * returns to the campaign post-advancement, before the next scene starts."
 * In practice the engine produces `TimeAdvanceResult.digest`; this component
 * is the UI surface that displays it after `POST /time/advance` returns.
 */

import { useEffect, useRef } from "react";

import type { TimeAdvanceResult } from "../../api/campaign";

interface Props {
  result: TimeAdvanceResult | null;
  onDismiss: () => void;
}

export function TimeAdvanceDigest({ result, onDismiss }: Props) {
  const continueRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (result) continueRef.current?.focus();
  }, [result]);

  if (!result) return null;

  const npcs = Object.values(result.npc_summaries ?? {}).filter(
    (n) => (n.activities ?? []).length > 0,
  );
  const triggered = result.scheduled_events_triggered ?? [];
  const weather = result.weather_changes ?? [];
  const drift = result.drift_warnings ?? [];

  return (
    <div
      className="modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-labelledby="time-digest-title"
    >
      <div className="modal time-digest">
        <h2 id="time-digest-title">While you were away</h2>
        <p className="muted time-digest-range">
          <span>{result.from_time.moment}</span>
          <span aria-hidden="true"> → </span>
          <span>{result.to_time.moment}</span>
          <span className="time-digest-duration"> ({result.duration.iso8601})</span>
        </p>

        <div className="time-digest-prose">
          {result.digest ? (
            result.digest.split(/\n{2,}/).map((para, i) => <p key={i}>{para}</p>)
          ) : (
            <p className="muted">Time passed with no notable events.</p>
          )}
        </div>

        {npcs.length > 0 && (
          <section className="time-digest-section" aria-labelledby="time-digest-npcs">
            <h3 id="time-digest-npcs">Characters</h3>
            <ul className="time-digest-list">
              {npcs.map((n) => (
                <li key={n.character_id}>
                  <strong>{n.character_id}</strong>
                  <ul>
                    {n.activities.map((a, i) => (
                      <li key={i}>{a}</li>
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
          </section>
        )}

        {triggered.length > 0 && (
          <section className="time-digest-section" aria-labelledby="time-digest-events">
            <h3 id="time-digest-events">Scheduled events</h3>
            <ul className="time-digest-list">
              {triggered.map((e) => (
                <li key={e.id}>
                  <strong>{e.label}</strong>
                  <span className="muted"> — {e.at.moment}</span>
                </li>
              ))}
            </ul>
          </section>
        )}

        {weather.length > 0 && (
          <section className="time-digest-section" aria-labelledby="time-digest-weather">
            <h3 id="time-digest-weather">Weather</h3>
            <ul className="time-digest-list">
              {weather.map((w, i) => (
                <li key={i}>
                  <strong>{w.location_ref}</strong>: {w.summary}
                </li>
              ))}
            </ul>
          </section>
        )}

        {drift.length > 0 && (
          <section className="time-digest-section" aria-labelledby="time-digest-drift">
            <h3 id="time-digest-drift">Drift warnings</h3>
            <ul className="time-digest-list">
              {drift.map((d, i) => (
                <li key={i} data-severity={d.severity}>
                  <strong>{d.character_id}</strong>: {d.summary}
                </li>
              ))}
            </ul>
          </section>
        )}

        <div className="modal-actions">
          <button ref={continueRef} type="button" className="primary" onClick={onDismiss}>
            Continue
          </button>
        </div>
      </div>
    </div>
  );
}
