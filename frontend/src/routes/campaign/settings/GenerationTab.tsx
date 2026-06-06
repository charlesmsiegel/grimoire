import { SaveIndicator } from "./SaveIndicator";
import { useAutoSavedResource } from "./shared";

interface GenerationValue {
  max_tokens: number | null;
  temperature: number | null;
}

export function GenerationTab({ campaignId }: { campaignId: string }) {
  const { value, setValue, status, error, ready } = useAutoSavedResource<GenerationValue>(
    campaignId,
    "/generation",
    { max_tokens: null, temperature: null },
  );

  function parseOptional(value: string): number | null {
    const trimmed = value.trim();
    if (!trimmed) return null;
    const n = Number(trimmed);
    return Number.isFinite(n) ? n : null;
  }

  return (
    <div className="settings-form">
      <p className="wizard-step-help">
        Per-campaign overrides for the language model's generation parameters. Leave a field blank
        to use the app-wide default (currently 4096 max output tokens, temperature 1.0). Raise{" "}
        <code>max_tokens</code> if long narrator responses are being cut off.
      </p>
      {!ready && <p className="wizard-meta">Loading saved settings…</p>}
      <label className="wizard-field">
        <span>Max output tokens</span>
        <input
          type="number"
          min={1}
          max={200000}
          step={1}
          placeholder="4096 (default)"
          value={value.max_tokens ?? ""}
          onChange={(e) =>
            setValue((prev) => ({ ...prev, max_tokens: parseOptional(e.target.value) }))
          }
          disabled={!ready}
        />
      </label>
      <label className="wizard-field">
        <span>Temperature</span>
        <input
          type="number"
          min={0}
          max={2}
          step={0.1}
          placeholder="1.0 (default)"
          value={value.temperature ?? ""}
          onChange={(e) =>
            setValue((prev) => ({ ...prev, temperature: parseOptional(e.target.value) }))
          }
          disabled={!ready}
        />
      </label>
      <SaveIndicator status={status} error={error} />
    </div>
  );
}
