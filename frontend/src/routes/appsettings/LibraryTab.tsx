import { ConfigSaveIndicator } from "./ConfigSaveIndicator";
import { useAppConfig } from "./shared";

export function LibraryTab() {
  const { data, patch, status, error } = useAppConfig();
  return (
    <div className="settings-form">
      <label className="wizard-field">
        <span>Library path</span>
        <input
          type="text"
          value={data?.library_path ?? ""}
          onChange={(e) => patch({ library_path: e.target.value })}
          disabled={!data}
        />
        <small>Filesystem directory scanned for settings, style guides, presets.</small>
      </label>
      <p className="wizard-meta">
        Persisted to <code>data/config/app.yaml</code>. Changes save automatically.
      </p>
      <ConfigSaveIndicator status={status} error={error} />
    </div>
  );
}
