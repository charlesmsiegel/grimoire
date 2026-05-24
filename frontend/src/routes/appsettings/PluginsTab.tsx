import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../../api/client";
import { type PluginSummary, fetchInstalledPlugins } from "../../api/wizard";
import { errorMessage } from "./shared";

export function PluginsTab() {
  const [plugins, setPlugins] = useState<PluginSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [rescanning, setRescanning] = useState(false);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchInstalledPlugins();
      setPlugins(data);
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
      await api.post("/api/plugins/rescan");
      await load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setRescanning(false);
    }
  };

  return (
    <div className="settings-form">
      <p className="wizard-step-help">
        All plugin kinds. Drop new plugins into <code>~/.grimoire/plugins</code> and rescan. Click a
        plugin to open its configuration form.
      </p>
      <button type="button" disabled={rescanning} onClick={() => void rescan()}>
        {rescanning ? "Rescanning…" : "Rescan"}
      </button>
      {loading && <p className="wizard-meta">Loading…</p>}
      {error && <p className="wizard-error">{error}</p>}
      {!loading && plugins.length === 0 && <p className="wizard-meta">No plugins installed.</p>}
      <ul className="provider-list">
        {plugins.map((p) => (
          <li key={`${p.kind}:${p.id}`} className={p.load_error ? "has-error" : undefined}>
            <Link to={`/library/plugins/${encodeURIComponent(p.id)}`}>
              <strong>{p.name ?? p.id}</strong>
              <small> · {p.kind}</small>
              {p.version && <small> v{p.version}</small>}
            </Link>
            {p.load_error && <p className="wizard-error">Load error: {p.load_error}</p>}
          </li>
        ))}
      </ul>
    </div>
  );
}
