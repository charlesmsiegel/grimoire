import { useCallback, useEffect, useState } from "react";
import { Link, Route, Routes, useNavigate, useParams } from "react-router-dom";

import {
  ApiError,
  libraryApi,
  type ImagePresetEditPayload,
  type LibraryEntity,
} from "../../api/library";
import { useResource } from "../../api/useResource";
import { CardIconBar } from "../../components/CardIconBar";
import { deleteAction } from "../../components/cardActions";
import { AsyncBoundary } from "./AsyncBoundary";
import { ConfirmDestructiveDialog } from "../../components/ConfirmDestructiveDialog";
import { useDestructiveConfirm } from "../../hooks/useDestructiveConfirm";

export function ImagePresetsView() {
  return (
    <Routes>
      <Route index element={<ImagePresetList />} />
      <Route path="new" element={<ImagePresetCreate />} />
      <Route path=":presetId" element={<ImagePresetDetail />} />
      <Route path=":presetId/edit" element={<ImagePresetEdit />} />
    </Routes>
  );
}

function ImagePresetList() {
  const navigate = useNavigate();
  const { data, loading, error, reload } = useResource(
    useCallback(() => libraryApi.listImagePresets(), []),
  );
  const del = useDestructiveConfirm<{ id: string; name: string }>(async ({ id }) => {
    await libraryApi.deleteImagePreset(id);
    reload();
  });
  return (
    <section className="library-section">
      <header className="library-section-header">
        <h3>Image presets</h3>
        <button onClick={() => navigate("/library/image-presets/new")}>+ New image preset</button>
      </header>
      {del.target && (
        <ConfirmDestructiveDialog
          open
          title={`Delete image preset "${del.target.name}"?`}
          body={<p>This cannot be undone.</p>}
          busy={del.busy}
          error={del.error}
          onConfirm={del.confirm}
          onCancel={del.cancel}
        />
      )}
      <AsyncBoundary
        loading={loading}
        error={error}
        empty={!data || data.length === 0}
        emptyMessage="No image presets yet."
        onRetry={reload}
      >
        <ul className="library-card-grid">
          {data?.map((p) => (
            <li key={p.id} className="library-card">
              <Link to={`/library/image-presets/${encodeURIComponent(p.asset_id)}`}>
                <h4>{p.name || p.asset_id}</h4>
                <small>{p.asset_id}</small>
                <PresetSummary preset={p} />
              </Link>
              <div className="library-card-actions">
                <Link to={`/library/image-presets/${encodeURIComponent(p.asset_id)}/edit`}>
                  Edit
                </Link>
              </div>
              <CardIconBar
                actions={[
                  deleteAction({
                    onClick: () => del.request({ id: p.asset_id, name: p.name || p.asset_id }),
                    label: `Delete image preset ${p.name || p.asset_id}`,
                  }),
                ]}
              />
            </li>
          ))}
        </ul>
      </AsyncBoundary>
    </section>
  );
}

function PresetSummary({ preset }: { preset: LibraryEntity }) {
  const fm = preset.frontmatter as Record<string, unknown>;
  const styleSummary = typeof fm.style_preamble === "string" ? fm.style_preamble : null;
  return styleSummary ? <p className="library-card-desc">{styleSummary}</p> : null;
}

function ImagePresetCreate() {
  const navigate = useNavigate();
  const [id, setId] = useState("");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [tagsStr, setTagsStr] = useState("");
  const [stylePreamble, setStylePreamble] = useState("");
  const [defaultNegative, setDefaultNegative] = useState("");
  const [paramsText, setParamsText] = useState("");
  const [busy, setBusy] = useState(false);
  const [submitErr, setSubmitErr] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitErr(null);
    setBusy(true);
    try {
      const tags = tagsStr
        .split(",")
        .map((t) => t.trim())
        .filter(Boolean);
      const default_params = parseParamsText(paramsText);
      const created = await libraryApi.createImagePreset({
        id: id.trim(),
        name: name.trim(),
        description: description.trim(),
        tags,
        style_preamble: stylePreamble.trim(),
        default_negative_prompt: defaultNegative.trim(),
        default_params,
      });
      navigate(`/library/image-presets/${encodeURIComponent(created.asset_id)}`);
    } catch (err) {
      setSubmitErr(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="library-section">
      <p className="library-breadcrumb">
        <Link to="/library/image-presets">Image presets</Link> / new
      </p>
      <header className="library-section-header">
        <h3>New image preset</h3>
      </header>
      <form onSubmit={submit} className="library-form" aria-label="Create image preset">
        <label>
          <span>ID</span>
          <input
            required
            value={id}
            pattern="[a-zA-Z0-9][a-zA-Z0-9._-]*"
            title="letters, digits, dots, underscores, hyphens; must start with a letter or digit"
            onChange={(e) => setId(e.target.value)}
          />
          <small>
            Used as the filename (e.g. <code>oil-painting</code>).
          </small>
        </label>
        <label>
          <span>Name</span>
          <input required value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label>
          <span>Description</span>
          <input
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="One-line summary shown in pickers"
          />
        </label>
        <label>
          <span>Tags</span>
          <input
            value={tagsStr}
            onChange={(e) => setTagsStr(e.target.value)}
            placeholder="comma, separated"
          />
        </label>
        <label>
          <span>Style preamble</span>
          <textarea
            rows={3}
            value={stylePreamble}
            onChange={(e) => setStylePreamble(e.target.value)}
            placeholder="oil painting, dark academia, dramatic lighting"
          />
        </label>
        <label>
          <span>Default negative prompt</span>
          <textarea
            rows={2}
            value={defaultNegative}
            onChange={(e) => setDefaultNegative(e.target.value)}
            placeholder="blurry, low quality, watermark"
          />
        </label>
        <label>
          <span>Default params</span>
          <textarea
            rows={4}
            value={paramsText}
            onChange={(e) => setParamsText(e.target.value)}
            placeholder='{"steps": 28, "cfg_scale": 6.5, "width": 1024, "height": 1024}'
          />
          <small>JSON object; leave empty for backend defaults.</small>
        </label>
        <div className="library-form-actions">
          <button type="submit" disabled={busy}>
            {busy ? "Creating…" : "Create"}
          </button>
          <button type="button" onClick={() => navigate("/library/image-presets")} disabled={busy}>
            Cancel
          </button>
        </div>
        {submitErr && (
          <p className="library-error" role="alert">
            {submitErr}
          </p>
        )}
      </form>
    </section>
  );
}

function ImagePresetEdit() {
  const { presetId = "" } = useParams();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [tagsStr, setTagsStr] = useState("");
  const [stylePreamble, setStylePreamble] = useState("");
  const [defaultNegative, setDefaultNegative] = useState("");
  const [paramsText, setParamsText] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [submitErr, setSubmitErr] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadErr(null);
    libraryApi
      .getImagePresetEdit(presetId)
      .then((payload: ImagePresetEditPayload) => {
        if (cancelled) return;
        setName(payload.name);
        setDescription(payload.description);
        setTagsStr(payload.tags.join(", "));
        setStylePreamble(payload.style_preamble);
        setDefaultNegative(payload.default_negative_prompt);
        setParamsText(
          Object.keys(payload.default_params).length > 0
            ? JSON.stringify(payload.default_params, null, 2)
            : "",
        );
        setLoading(false);
      })
      .catch((err) => {
        if (cancelled) return;
        setLoadErr(err instanceof ApiError ? err.message : String(err));
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [presetId]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitErr(null);
    setBusy(true);
    try {
      const tags = tagsStr
        .split(",")
        .map((t) => t.trim())
        .filter(Boolean);
      const default_params = parseParamsText(paramsText);
      await libraryApi.updateImagePreset(presetId, {
        name: name.trim(),
        description: description.trim(),
        tags,
        style_preamble: stylePreamble.trim(),
        default_negative_prompt: defaultNegative.trim(),
        default_params,
      });
      navigate(`/library/image-presets/${encodeURIComponent(presetId)}`);
    } catch (err) {
      setSubmitErr(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function doDelete() {
    if (!confirm(`Delete image preset ${presetId}? This cannot be undone.`)) return;
    setDeleting(true);
    try {
      await libraryApi.deleteImagePreset(presetId);
      navigate("/library/image-presets");
    } catch (err) {
      setSubmitErr(err instanceof ApiError ? err.message : String(err));
    } finally {
      setDeleting(false);
    }
  }

  if (loading) return <p>Loading…</p>;
  if (loadErr)
    return (
      <p className="library-error" role="alert">
        {loadErr}
      </p>
    );

  return (
    <section className="library-section">
      <p className="library-breadcrumb">
        <Link to="/library/image-presets">Image presets</Link> /{" "}
        <Link to={`/library/image-presets/${encodeURIComponent(presetId)}`}>{presetId}</Link> / edit
      </p>
      <header className="library-section-header">
        <h3>Edit image preset</h3>
      </header>
      <form onSubmit={submit} className="library-form" aria-label="Edit image preset">
        <label>
          <span>Name</span>
          <input required value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label>
          <span>Description</span>
          <input value={description} onChange={(e) => setDescription(e.target.value)} />
        </label>
        <label>
          <span>Tags</span>
          <input
            value={tagsStr}
            onChange={(e) => setTagsStr(e.target.value)}
            placeholder="comma, separated"
          />
        </label>
        <label>
          <span>Style preamble</span>
          <textarea
            rows={3}
            value={stylePreamble}
            onChange={(e) => setStylePreamble(e.target.value)}
          />
        </label>
        <label>
          <span>Default negative prompt</span>
          <textarea
            rows={2}
            value={defaultNegative}
            onChange={(e) => setDefaultNegative(e.target.value)}
          />
        </label>
        <label>
          <span>Default params (JSON)</span>
          <textarea rows={4} value={paramsText} onChange={(e) => setParamsText(e.target.value)} />
        </label>
        <div className="library-form-actions">
          <button type="submit" disabled={busy || deleting}>
            {busy ? "Saving…" : "Save"}
          </button>
          <button
            type="button"
            onClick={() => navigate(`/library/image-presets/${encodeURIComponent(presetId)}`)}
            disabled={busy || deleting}
          >
            Cancel
          </button>
          <button
            type="button"
            className="library-button-danger"
            onClick={doDelete}
            disabled={busy || deleting}
          >
            {deleting ? "Deleting…" : "Delete"}
          </button>
        </div>
        {submitErr && (
          <p className="library-error" role="alert">
            {submitErr}
          </p>
        )}
      </form>
    </section>
  );
}

function ImagePresetDetail() {
  const { presetId = "" } = useParams();
  const { data, loading, error, reload } = useResource(
    useCallback(() => libraryApi.getImagePreset(presetId), [presetId]),
  );
  return (
    <section className="library-section">
      <p className="library-breadcrumb">
        <Link to="/library/image-presets">Image presets</Link> / {presetId}
      </p>
      <AsyncBoundary loading={loading} error={error} onRetry={reload}>
        {data && <ImagePresetCard preset={data} />}
      </AsyncBoundary>
    </section>
  );
}

function ImagePresetCard({ preset }: { preset: LibraryEntity }) {
  const fm = preset.frontmatter as Record<string, unknown>;
  const stylePreamble = typeof fm.style_preamble === "string" ? fm.style_preamble : "(no preamble)";
  const negative = typeof fm.default_negative_prompt === "string" ? fm.default_negative_prompt : "";
  const params = (fm.default_params ?? {}) as Record<string, unknown>;
  const tags = Array.isArray(fm.tags) ? (fm.tags as string[]) : [];

  return (
    <div className="image-preset-detail">
      <header className="library-section-header">
        <h3>{preset.name || preset.asset_id}</h3>
        <Link
          to={`/library/image-presets/${encodeURIComponent(preset.asset_id)}/edit`}
          className="button-link"
        >
          Edit
        </Link>
      </header>
      <p>
        <code>{preset.path}</code>
      </p>

      <section aria-labelledby="preset-style-heading" className="preset-block">
        <h4 id="preset-style-heading">Style preamble</h4>
        <pre className="preset-text">{stylePreamble}</pre>
      </section>

      {negative && (
        <section aria-labelledby="preset-neg-heading" className="preset-block">
          <h4 id="preset-neg-heading">Negative prompt</h4>
          <pre className="preset-text">{negative}</pre>
        </section>
      )}

      {Object.keys(params).length > 0 && (
        <section aria-labelledby="preset-params-heading" className="preset-block">
          <h4 id="preset-params-heading">Default parameters</h4>
          <table className="library-table">
            <tbody>
              {Object.entries(params).map(([k, v]) => (
                <tr key={k}>
                  <th>{k}</th>
                  <td>{String(v)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {tags.length > 0 && <p className="library-card-meta">tags: {tags.join(" · ")}</p>}

      <PresetPreview presetId={preset.asset_id} />
    </div>
  );
}

function PresetPreview({ presetId }: { presetId: string }) {
  // §13 — Image preset live sample preview. Calls
  // POST /api/library/image-presets/{id}/preview, which runs a single sync
  // gen against the active ImageGen backend (or the in-memory stub in
  // tests) and returns a data: URL.
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [samplePrompt, setSamplePrompt] = useState("a portrait of a wizard in a library");

  async function regenerate() {
    setLoading(true);
    setError(null);
    try {
      const result = await libraryApi.previewImagePreset(presetId, {
        prompt: samplePrompt,
      });
      setImageUrl(result.image_data_url);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <section aria-labelledby="preset-preview-heading" className="preset-block">
      <h4 id="preset-preview-heading">Sample preview</h4>
      <div className="preset-preview-controls">
        <input
          aria-label="Sample prompt"
          value={samplePrompt}
          onChange={(e) => setSamplePrompt(e.target.value)}
        />
        <button type="button" onClick={regenerate} disabled={loading}>
          {loading ? "Generating…" : imageUrl ? "Regenerate sample" : "Generate sample"}
        </button>
      </div>
      {error && (
        <p className="library-error" role="alert">
          {error}
        </p>
      )}
      {imageUrl ? (
        <figure className="preset-preview">
          <img src={imageUrl} alt={`Sample preview for ${presetId}`} loading="lazy" />
          <figcaption className="preset-preview-hint">
            Generated with the active ImageGen backend at the preset's style + params.
          </figcaption>
        </figure>
      ) : (
        <div className="preset-preview" role="img" aria-label="Sample preview placeholder">
          <p className="preset-preview-hint">
            Click "Generate sample" to render the preset against a default prompt.
          </p>
        </div>
      )}
    </section>
  );
}

function parseParamsText(text: string): Record<string, unknown> {
  const trimmed = text.trim();
  if (!trimmed) return {};
  try {
    const parsed = JSON.parse(trimmed);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>;
    }
  } catch {
    // fallthrough — caller will see the empty-object fallback land server-side
  }
  return {};
}
