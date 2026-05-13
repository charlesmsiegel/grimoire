import { Link, Route, Routes, useParams } from "react-router-dom";

import { libraryApi, type LibraryEntity } from "../../api/library";
import { useResource } from "../../api/useResource";
import { AsyncBoundary } from "./AsyncBoundary";

export function ImagePresetsView() {
  return (
    <Routes>
      <Route index element={<ImagePresetList />} />
      <Route path=":presetId" element={<ImagePresetDetail />} />
    </Routes>
  );
}

function ImagePresetList() {
  const { data, loading, error, reload } = useResource(() => libraryApi.listImagePresets(), []);
  return (
    <section className="library-section">
      <header className="library-section-header">
        <h3>Image presets</h3>
      </header>
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

function ImagePresetDetail() {
  const { presetId = "" } = useParams();
  const { data, loading, error, reload } = useResource(
    () => libraryApi.getImagePreset(presetId),
    [presetId],
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
      <h3>{preset.name || preset.asset_id}</h3>
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

      <section aria-labelledby="preset-preview-heading" className="preset-block">
        <h4 id="preset-preview-heading">Sample preview</h4>
        <div className="preset-preview" role="img" aria-label="Sample preview placeholder">
          <p>Sample preview unavailable until an ImageGen backend is configured.</p>
          <p className="preset-preview-hint">
            Tip: combine the style preamble with any prompt to see the rendered effect.
          </p>
        </div>
      </section>
    </div>
  );
}
