import { useState } from "react";

import { patchCampaign } from "../../../api/wizard";
import {
  type CampaignRecord,
  SaveIndicator,
  errorMessage,
  useAutoSavedResource,
} from "./shared";

export function GeneralTab({
  campaign,
  onUpdate,
}: {
  campaign: CampaignRecord;
  onUpdate: (next: CampaignRecord) => void;
}) {
  const [name, setName] = useState(campaign.name ?? "");
  const [description, setDescription] = useState(campaign.description ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await patchCampaign(campaign.id, { name, description });
      onUpdate({ ...campaign, name, description });
      setSavedAt(Date.now());
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <form
        className="settings-form"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <label className="wizard-field">
          <span>Name</span>
          <input type="text" value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="wizard-field">
          <span>Description</span>
          <textarea rows={4} value={description} onChange={(e) => setDescription(e.target.value)} />
        </label>
        {error && (
          <p className="wizard-error" role="alert">
            {error}
          </p>
        )}
        {savedAt && <p className="wizard-meta">Saved.</p>}
        <button type="submit" className="primary" disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </button>
      </form>
      <IntegratedDeltasToggle campaignId={campaign.id} />
    </>
  );
}

function IntegratedDeltasToggle({ campaignId }: { campaignId: string }) {
  const { value, setValue, status, error, ready } = useAutoSavedResource<{ enabled: boolean }>(
    campaignId,
    "/integrated-deltas",
    { enabled: false },
  );

  return (
    <div className="settings-form" style={{ marginTop: "var(--space-4)" }}>
      <label className="wizard-field wizard-field-inline">
        <input
          type="checkbox"
          checked={value.enabled}
          onChange={(e) => setValue({ enabled: e.target.checked })}
          disabled={!ready}
        />
        <span>Combine narrator + delta extraction into one LLM call</span>
      </label>
      <p className="wizard-step-help">
        When enabled, the narrator response includes a structured delta block inline, eliminating
        the separate extraction LLM call. Falls back automatically if the model omits or malforms
        the block.
      </p>
      <SaveIndicator status={status} error={error} />
    </div>
  );
}
