interface DriftEntry {
  ref: string;
  score: number;
}

interface Props {
  warnings: DriftEntry[];
  onSuppress: (ref: string) => void;
}

export function DriftBanner({ warnings, onSuppress }: Props) {
  if (warnings.length === 0) return null;
  return (
    <div className="drift-banner" role="status" aria-live="polite">
      {warnings.map((w) => (
        <div key={w.ref} className="drift-banner-row">
          <strong>{w.ref}</strong>’s voice is drifting (score{" "}
          <span className="drift-score">{w.score.toFixed(2)}</span>). Corrective context will be
          added to the next prompt.
          <button
            type="button"
            className="drift-banner-suppress"
            onClick={() => onSuppress(w.ref)}
            aria-label={`Suppress drift warning for ${w.ref} for this session`}
          >
            Dismiss
          </button>
        </div>
      ))}
    </div>
  );
}
