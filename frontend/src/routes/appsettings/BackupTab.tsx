import { SaveIndicator } from "../../components/SaveIndicator";
import { useAppConfig } from "./shared";

export function BackupTab() {
  const { data, patch, status, error } = useAppConfig();
  const backup = data?.backup ?? { schedule: "off", retention_days: 30, location: "data/backups" };

  return (
    <div className="settings-form">
      <label className="wizard-field">
        <span>Default schedule</span>
        <select
          value={backup.schedule}
          onChange={(e) => patch({ backup: { ...backup, schedule: e.target.value } })}
          disabled={!data}
        >
          <option value="off">Off</option>
          <option value="hourly">Hourly</option>
          <option value="daily">Daily</option>
          <option value="weekly">Weekly</option>
        </select>
      </label>
      <label className="wizard-field">
        <span>Retention (days)</span>
        <input
          type="number"
          min={0}
          value={backup.retention_days}
          onChange={(e) =>
            patch({
              backup: {
                ...backup,
                retention_days: Number.isFinite(Number(e.target.value))
                  ? Number(e.target.value)
                  : backup.retention_days,
              },
            })
          }
          disabled={!data}
        />
      </label>
      <label className="wizard-field">
        <span>Destination</span>
        <input
          type="text"
          value={backup.location}
          onChange={(e) => patch({ backup: { ...backup, location: e.target.value } })}
          disabled={!data}
        />
      </label>
      <SaveIndicator status={status} error={error} />
    </div>
  );
}
