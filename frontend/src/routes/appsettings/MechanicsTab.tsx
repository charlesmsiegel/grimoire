import { useEffect, useState } from "react";

import { api } from "../../api/client";
import {
  type MechanicsModuleSummary,
  fetchInstalledMechanics,
} from "../../api/wizard";
import { errorMessage } from "./shared";

export function MechanicsTab() {
  const [modules, setModules] = useState<MechanicsModuleSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [rescanning, setRescanning] = useState(false);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchInstalledMechanics();
      setModules(data);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const rescan = async () => {
    setRescanning(true);
    try {
      await api.post("/api/mechanics/rescan");
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setRescanning(false);
    }
  };

  return (
    <div className="settings-form">
      <p className="wizard-step-help">Discovery paths configured in app.yaml.</p>
      <button type="button" disabled={rescanning} onClick={() => void rescan()}>
        {rescanning ? "Rescanning…" : "Rescan"}
      </button>
      {loading && <p className="wizard-meta">Loading…</p>}
      {error && <p className="wizard-error">{error}</p>}
      {!loading && modules.length === 0 && (
        <p className="wizard-meta">No mechanics modules installed.</p>
      )}
      <ul className="provider-list">
        {modules.map((m) => (
          <li key={m.id} className={m.load_error ? "has-error" : undefined}>
            <strong>{m.name ?? m.id}</strong>
            {m.version && <small> v{m.version}</small>}
            {m.api_version && <small> · api {m.api_version}</small>}
            {m.load_error && <p className="wizard-error">Load error: {m.load_error}</p>}
          </li>
        ))}
      </ul>
    </div>
  );
}
