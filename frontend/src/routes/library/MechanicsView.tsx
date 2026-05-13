import { useState } from "react";
import { Link, Route, Routes, useParams } from "react-router-dom";

import { ApiError, mechanicsApi, type RegisteredModule } from "../../api/library";
import { useResource } from "../../api/useResource";
import { AsyncBoundary } from "./AsyncBoundary";

export function MechanicsView() {
  return (
    <Routes>
      <Route index element={<MechanicsList />} />
      <Route path=":moduleId" element={<MechanicsDetail />} />
    </Routes>
  );
}

function MechanicsList() {
  const { data, loading, error, reload } = useResource(() => mechanicsApi.listInstalled(), []);
  const [rescanning, setRescanning] = useState(false);
  const [rescanErr, setRescanErr] = useState<string | null>(null);

  async function rescan() {
    setRescanning(true);
    setRescanErr(null);
    try {
      await mechanicsApi.rescan();
      reload();
    } catch (err) {
      setRescanErr(err instanceof ApiError ? err.message : String(err));
    } finally {
      setRescanning(false);
    }
  }

  return (
    <section className="library-section">
      <header className="library-section-header">
        <h3>Installed mechanics</h3>
        <button onClick={rescan} disabled={rescanning}>
          {rescanning ? "Rescanning…" : "Rescan"}
        </button>
      </header>
      <p className="library-status">
        Mechanics modules ship as Python packages dropped into <code>data/mechanics/</code>. Install
        or remove a module on disk and rescan.
      </p>
      {rescanErr && (
        <p className="library-error" role="alert">
          {rescanErr}
        </p>
      )}
      <AsyncBoundary
        loading={loading}
        error={error}
        empty={!data || data.length === 0}
        emptyMessage="No mechanics modules installed."
        onRetry={reload}
      >
        <ul className="library-card-grid">
          {data?.map((m) => (
            <li key={m.manifest.id} className="library-card">
              <Link to={`/library/mechanics/${encodeURIComponent(m.manifest.id)}`}>
                <h4>{m.manifest.name}</h4>
                <small>
                  {m.manifest.id} · v{m.manifest.version} · API v{m.manifest.api_version}
                </small>
                {m.manifest.description && (
                  <p className="library-card-desc">{m.manifest.description}</p>
                )}
                <p className="library-card-meta">
                  {m.manifest.sheet_kinds.length} sheet kind
                  {m.manifest.sheet_kinds.length === 1 ? "" : "s"} ·{" "}
                  {m.manifest.capabilities.length} capabilit
                  {m.manifest.capabilities.length === 1 ? "y" : "ies"}
                </p>
              </Link>
            </li>
          ))}
        </ul>
      </AsyncBoundary>
    </section>
  );
}

function MechanicsDetail() {
  const { moduleId = "" } = useParams();
  const { data, loading, error, reload } = useResource(() => mechanicsApi.listInstalled(), []);

  const module = (data ?? []).find((m: RegisteredModule) => m.manifest.id === moduleId);

  return (
    <section className="library-section">
      <p className="library-breadcrumb">
        <Link to="/library/mechanics">Installed mechanics</Link> / {moduleId}
      </p>
      <AsyncBoundary loading={loading} error={error} onRetry={reload}>
        {!module ? (
          <p className="library-status">Module {moduleId} is not installed.</p>
        ) : (
          <ModuleDetailCard module={module} />
        )}
      </AsyncBoundary>
    </section>
  );
}

function ModuleDetailCard({ module: m }: { module: RegisteredModule }) {
  const manifest = m.manifest;
  return (
    <div className="mechanics-detail">
      <h3>{manifest.name}</h3>
      <p className="library-card-meta">
        <code>{manifest.id}</code> · v{manifest.version} · API v{manifest.api_version}
      </p>
      {manifest.description && <p>{manifest.description}</p>}
      {manifest.author && (
        <p>
          <strong>Author:</strong> {manifest.author}
        </p>
      )}
      {manifest.homepage && (
        <p>
          <strong>Homepage:</strong>{" "}
          <a href={manifest.homepage} target="_blank" rel="noreferrer">
            {manifest.homepage}
          </a>
        </p>
      )}

      <Section title="Sheet kinds">
        {manifest.sheet_kinds.length === 0 ? (
          <p className="library-status">No declared sheet kinds.</p>
        ) : (
          <ul className="chip-list">
            {manifest.sheet_kinds.map((k) => (
              <li key={k} className="chip">
                {k}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Content kinds">
        {manifest.content_kinds.length === 0 ? (
          <p className="library-status">No declared content kinds.</p>
        ) : (
          <ul className="chip-list">
            {manifest.content_kinds.map((k) => (
              <li key={k} className="chip">
                {k}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Capabilities">
        {manifest.capabilities.length === 0 ? (
          <p className="library-status">No declared capabilities.</p>
        ) : (
          <ul className="chip-list">
            {manifest.capabilities.map((c) => (
              <li key={c} className="chip">
                {c}
              </li>
            ))}
          </ul>
        )}
      </Section>

      {Object.keys(manifest.ui).length > 0 && (
        <Section title="UI manifest">
          <pre className="preset-text">{JSON.stringify(manifest.ui, null, 2)}</pre>
        </Section>
      )}

      <Section title="theme.css preview">
        <p className="library-status">
          Theme CSS is loaded by the per-mechanics scoping wrapper at sheet render time. The
          Frontend prefixes its selectors with <code>.mechanics-{manifest.id}</code> to isolate
          styling.
        </p>
      </Section>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mechanics-section">
      <h4>{title}</h4>
      {children}
    </section>
  );
}
