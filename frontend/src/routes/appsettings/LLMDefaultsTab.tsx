import { useEffect, useState } from "react";

import { configApi } from "../../api/config";

export function LLMDefaultsTab() {
  const [heavy, setHeavy] = useState("deepseek.deepseek-v4-pro");
  const [light, setLight] = useState("deepseek.deepseek-v4-flash");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const d = await configApi.getLLMDefaults();
        if (!cancelled) {
          setHeavy(d.heavy);
          setLight(d.light);
        }
      } catch {
        // first-run / empty config → keep the seeded UI defaults
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const updated = await configApi.setLLMDefaults({ heavy, light });
      setHeavy(updated.heavy);
      setLight(updated.light);
      setSavedAt(Date.now());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="settings-section">
      <h3>LLM defaults</h3>
      <p className="wizard-step-help">
        New campaigns get these as their Heavy and Light tier routes.
        Existing campaigns are unaffected. Format is{" "}
        <code>provider.model</code>. Heavy handles generation
        (narrator, summaries, rewrites); Light handles classification
        and short transforms (drift checks, scene-break, translate).
      </p>
      {loading && <p className="wizard-meta">Loading saved defaults…</p>}
      <label className="wizard-field">
        <span>Heavy (generation)</span>
        <input
          type="text"
          value={heavy}
          onChange={(e) => setHeavy(e.target.value)}
          disabled={loading}
        />
      </label>
      <label className="wizard-field">
        <span>Light (classification)</span>
        <input
          type="text"
          value={light}
          onChange={(e) => setLight(e.target.value)}
          disabled={loading}
        />
      </label>
      <button
        type="button"
        onClick={() => void save()}
        disabled={loading || saving}
        className="primary"
      >
        {saving ? "Saving…" : "Save"}
      </button>
      {savedAt && !saving && <p className="wizard-meta">Saved.</p>}
      {error && (
        <p className="wizard-error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
