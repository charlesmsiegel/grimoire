import { SaveIndicator } from "./SaveIndicator";
import { useAutoSavedResource } from "./shared";

interface AdvancedValue {
  debug_log: boolean;
  per_task_prompts: Record<string, string>;
}

export function AdvancedTab({ campaignId }: { campaignId: string }) {
  const { value, setValue, status, error, ready } = useAutoSavedResource<AdvancedValue>(
    campaignId,
    "/advanced",
    { debug_log: false, per_task_prompts: {} },
  );

  const setPromptFor = (task: string, text: string) =>
    setValue((prev) => {
      const next = { ...prev.per_task_prompts };
      if (text) next[task] = text;
      else delete next[task];
      return { ...prev, per_task_prompts: next };
    });

  return (
    <div className="settings-form">
      {!ready && <p className="wizard-meta">Loading saved settings…</p>}
      <label className="wizard-toggle">
        <input
          type="checkbox"
          checked={value.debug_log}
          onChange={(e) =>
            setValue((prev) => ({ ...prev, debug_log: e.target.checked }))
          }
          disabled={!ready}
        />
        <span>Verbose debug log for this campaign</span>
      </label>
      <label className="wizard-field">
        <span>Per-task system prompt override (main)</span>
        <textarea
          rows={6}
          value={value.per_task_prompts.main ?? ""}
          onChange={(e) => setPromptFor("main", e.target.value)}
          placeholder="Override the main-task system prompt for this campaign."
          disabled={!ready}
        />
      </label>
      <SaveIndicator status={status} error={error} />
    </div>
  );
}
