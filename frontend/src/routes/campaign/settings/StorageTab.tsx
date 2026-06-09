import { SaveIndicator } from "./SaveIndicator";
import { useAutoSavedResource } from "./shared";

interface StorageValue {
  schedule: string;
  retention_days: number;
}

export function StorageTab({ campaignId }: { campaignId: string }) {
  const { value, setValue, status, error, ready } = useAutoSavedResource<StorageValue>(
    campaignId,
    "/storage",
    { schedule: "off", retention_days: 30 },
  );

  return (
    <div className="settings-form">
      {!ready && <p className="wizard-meta">Loading saved settings…</p>}
      <label className="form-field wizard-field">
        <span>Backup schedule</span>
        <select
          value={value.schedule}
          onChange={(e) => setValue((prev) => ({ ...prev, schedule: e.target.value }))}
          disabled={!ready}
        >
          <option value="off">Off</option>
          <option value="hourly">Hourly</option>
          <option value="daily">Daily</option>
          <option value="weekly">Weekly</option>
        </select>
      </label>
      <label className="form-field wizard-field">
        <span>Retention (days)</span>
        <input
          type="number"
          min={1}
          value={value.retention_days}
          onChange={(e) =>
            setValue((prev) => ({
              ...prev,
              retention_days: Number.isFinite(Number(e.target.value))
                ? Number(e.target.value)
                : prev.retention_days,
            }))
          }
          disabled={!ready}
        />
      </label>
      <SaveIndicator status={status} error={error} />
    </div>
  );
}
