import { useEffect, useState } from "react";

import { templatesApi, type TemplateSummary } from "../../api/library";
import { errorMessage } from "./shared";

export function TemplatesTab() {
  const [data, setData] = useState<{
    templates: TemplateSummary[];
    user_dir: string;
    default_variant: string;
  } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [variant, setVariant] = useState<string>("default");
  const [body, setBody] = useState<string>("");
  const [editable, setEditable] = useState<boolean>(false);
  const [bodyLoading, setBodyLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [newVariantName, setNewVariantName] = useState("");

  const refresh = async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await templatesApi.list();
      setData(result);
      if (!selected && result.templates.length > 0) {
        const first = result.templates[0]!;
        setSelected(first.name);
        setVariant(first.active || "default");
      }
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const current = data?.templates.find((t) => t.name === selected) ?? null;

  useEffect(() => {
    if (!selected || !variant) {
      setBody("");
      setEditable(false);
      return;
    }
    let cancelled = false;
    setBodyLoading(true);
    setStatus(null);
    void (async () => {
      try {
        const text = await templatesApi.read(selected, variant);
        if (!cancelled) {
          setBody(text.body);
          setEditable(text.editable);
        }
      } catch (err) {
        if (!cancelled) setError(errorMessage(err));
      } finally {
        if (!cancelled) setBodyLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selected, variant]);

  const onSelectTemplate = (name: string) => {
    setSelected(name);
    const t = data?.templates.find((x) => x.name === name);
    setVariant(t?.active ?? "default");
  };

  const onSave = async () => {
    if (!selected || !variant) return;
    setSaving(true);
    setStatus(null);
    setError(null);
    try {
      await templatesApi.write(selected, variant, body);
      setStatus("Saved.");
      await refresh();
      setEditable(true);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  const onSetActive = async () => {
    if (!selected || !variant) return;
    setSaving(true);
    setStatus(null);
    setError(null);
    try {
      await templatesApi.setActive(selected, variant);
      setStatus(`Active variant set to ${variant}.`);
      await refresh();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  const onDelete = async () => {
    if (!selected || !variant || !editable) return;
    if (!window.confirm(`Delete user variant "${variant}" of ${selected}?`)) return;
    setSaving(true);
    setError(null);
    try {
      await templatesApi.remove(selected, variant);
      setStatus(`Deleted ${variant}.`);
      setVariant("default");
      await refresh();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  const onCreateVariant = async () => {
    if (!selected) return;
    const name = newVariantName.trim();
    if (!name) return;
    if (!/^[A-Za-z0-9][A-Za-z0-9_-]*$/.test(name)) {
      setError("Variant name must be letters/digits/_/- and start with a letter or digit.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await templatesApi.write(selected, name, body);
      setStatus(`Created variant ${name}.`);
      setNewVariantName("");
      setVariant(name);
      await refresh();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="settings-form templates-form">
      <p className="wizard-step-help">
        Pick a prompt template, choose a variant, and edit. Bundled defaults are read-only; saving
        creates a new variant under your data directory. The active variant is used for new renders.
      </p>

      {loading && <p className="wizard-meta">Loading templates…</p>}
      {error && (
        <p className="wizard-error" role="alert">
          {error}
        </p>
      )}

      {data && (
        <div className="templates-layout">
          <aside className="templates-list">
            <h3>Templates</h3>
            <ul>
              {data.templates.map((t) => (
                <li key={t.name}>
                  <button
                    type="button"
                    className={t.name === selected ? "templates-item active" : "templates-item"}
                    onClick={() => onSelectTemplate(t.name)}
                  >
                    <span className="templates-item-name">{t.name}</span>
                    <span className="templates-item-meta">
                      {t.variants.length} {t.variants.length === 1 ? "variant" : "variants"}
                      {t.active !== data.default_variant && (
                        <span className="badge badge-ok"> {t.active}</span>
                      )}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
            <small className="muted">User dir: {data.user_dir}</small>
          </aside>

          <section className="templates-editor">
            {!current ? (
              <p className="wizard-meta">Select a template to edit.</p>
            ) : (
              <>
                <header className="templates-editor-head">
                  <div>
                    <h3>{current.name}</h3>
                    <p className="provider-card-sub">
                      Active variant: <strong>{current.active}</strong>
                    </p>
                  </div>
                  <div className="templates-editor-actions">
                    <label className="provider-combobox templates-variant-picker">
                      <span className="provider-combobox-label">Variant</span>
                      <select value={variant} onChange={(e) => setVariant(e.target.value)}>
                        {current.variants.map((v) => (
                          <option key={v} value={v}>
                            {v}
                            {current.editable.includes(v) ? "  ·  user" : "  ·  bundled"}
                            {v === current.active ? "  ·  active" : ""}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>
                </header>

                {bodyLoading ? (
                  <p className="wizard-meta">Loading…</p>
                ) : (
                  <textarea
                    className="templates-body"
                    value={body}
                    onChange={(e) => setBody(e.target.value)}
                    spellCheck={false}
                    rows={18}
                    aria-label={`${current.name} / ${variant} template body`}
                  />
                )}

                {!editable && (
                  <p className="wizard-meta">
                    Bundled variant — read-only. Save below to create an editable user copy.
                  </p>
                )}

                <div className="templates-action-row">
                  {editable ? (
                    <button
                      type="button"
                      className="primary"
                      onClick={() => void onSave()}
                      disabled={saving}
                    >
                      {saving ? "Saving…" : "Save"}
                    </button>
                  ) : (
                    <div className="templates-create-variant">
                      <input
                        type="text"
                        placeholder="new-variant-name"
                        value={newVariantName}
                        onChange={(e) => setNewVariantName(e.target.value)}
                      />
                      <button
                        type="button"
                        className="primary"
                        onClick={() => void onCreateVariant()}
                        disabled={saving || !newVariantName.trim()}
                      >
                        Save as new variant
                      </button>
                    </div>
                  )}
                  {variant !== current.active && (
                    <button type="button" onClick={() => void onSetActive()} disabled={saving}>
                      Make active
                    </button>
                  )}
                  {editable && (
                    // eslint-disable-next-line local/no-bespoke-delete -- template variant action, not a card
                    <button
                      type="button"
                      onClick={() => void onDelete()}
                      disabled={saving}
                      className="templates-delete"
                    >
                      Delete variant
                    </button>
                  )}
                </div>

                {status && <p className="library-ok">{status}</p>}
              </>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
