import { SaveIndicator } from "./SaveIndicator";
import { useAutoSavedResource } from "./shared";

interface SummariesValue {
  running_every_n_posts: number;
  final_on_close: boolean;
}

export function SummariesTab({ campaignId }: { campaignId: string }) {
  const { value, setValue, status, error, ready } = useAutoSavedResource<SummariesValue>(
    campaignId,
    "/summaries",
    { running_every_n_posts: 5, final_on_close: true },
  );

  return (
    <div className="settings-form">
      <p className="wizard-step-help">
        Controls how often the running scene summary is regenerated and
        whether a final summary is produced when the scene closes. Set
        <code> Running every N posts </code> to <code>0</code> to disable
        in-scene summaries entirely.
      </p>
      {!ready && <p className="wizard-meta">Loading saved settings…</p>}
      <label className="wizard-field">
        <span>Running summary every N posts</span>
        <input
          type="number"
          min={0}
          max={1000}
          step={1}
          placeholder="5 (default)"
          value={value.running_every_n_posts}
          onChange={(e) => {
            const n = Number(e.target.value);
            setValue((prev) => ({
              ...prev,
              running_every_n_posts: Number.isFinite(n) && n >= 0 ? n : prev.running_every_n_posts,
            }));
          }}
          disabled={!ready}
        />
      </label>
      <label className="wizard-field wizard-field-inline">
        <input
          type="checkbox"
          checked={value.final_on_close}
          onChange={(e) =>
            setValue((prev) => ({ ...prev, final_on_close: e.target.checked }))
          }
          disabled={!ready}
        />
        <span>Generate final summary when scene closes</span>
      </label>
      <SaveIndicator status={status} error={error} />
    </div>
  );
}
