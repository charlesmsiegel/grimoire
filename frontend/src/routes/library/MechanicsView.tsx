import { useCallback, useState } from "react";
import { Link, Route, Routes, useNavigate, useParams } from "react-router-dom";

import { ApiError, mechanicsApi, type RegisteredModule } from "../../api/library";
import { useResource } from "../../api/useResource";
import { LibraryCharacterCreationPreview } from "../campaign/CharacterCreation";
import { AsyncBoundary } from "./AsyncBoundary";
import { MechanicsEditor } from "./mechanics/MechanicsEditor";
import { CardIconBar } from "../../components/CardIconBar";
import { ModuleCreateForm } from "./mechanics/ModuleCreateForm";

export function MechanicsView() {
  return (
    <Routes>
      <Route index element={<MechanicsList />} />
      <Route path=":moduleId" element={<MechanicsDetail />} />
    </Routes>
  );
}

function MechanicsList() {
  const { data, loading, error, reload } = useResource(
    useCallback(() => mechanicsApi.listInstalled(), []),
  );
  const [rescanning, setRescanning] = useState(false);
  const [rescanErr, setRescanErr] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const navigate = useNavigate();

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
        <div className="library-section-actions">
          <button onClick={() => setCreating((c) => !c)}>
            {creating ? "Cancel" : "New module"}
          </button>
          <button onClick={rescan} disabled={rescanning}>
            {rescanning ? "Rescanning…" : "Rescan"}
          </button>
        </div>
      </header>
      {creating && (
        <ModuleCreateForm
          onCreated={(id) => navigate(`/library/mechanics/${encodeURIComponent(id)}`)}
        />
      )}
      <p className="library-status">
        Mechanics modules ship as Python packages dropped into <code>data/mechanics/</code>. Install
        or remove a module on disk and rescan.
      </p>
      <MechanicsRequirements />
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
              <CardIconBar actions={[]} />
            </li>
          ))}
        </ul>
      </AsyncBoundary>
    </section>
  );
}

function MechanicsDetail() {
  const { moduleId = "" } = useParams();
  const { data, loading, error, reload } = useResource(
    useCallback(() => mechanicsApi.listInstalled(), []),
  );

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
          <ModuleDetailCard module={module} onChanged={reload} />
        )}
      </AsyncBoundary>
    </section>
  );
}

function ModuleDetailCard({
  module: m,
  onChanged,
}: {
  module: RegisteredModule;
  onChanged: () => void;
}) {
  const manifest = m.manifest;
  const [previewing, setPreviewing] = useState(false);
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
        {m.theme_css ? (
          <details>
            <summary>Inlined theme.css ({m.theme_css.length} chars)</summary>
            <pre className="preset-text">{m.theme_css}</pre>
          </details>
        ) : (
          <p className="library-status muted">
            This module does not ship a <code>theme.css</code>.
          </p>
        )}
      </Section>

      <Section title="Character creation">
        <p className="library-status">
          Preview the module's character-creation wizard against the library baseline (no campaign,
          no persistence) to verify step ordering and schemas.
        </p>
        <button type="button" onClick={() => setPreviewing(true)}>
          Preview character creation
        </button>
      </Section>

      <Section title="Edit declarative parts">
        <p className="library-status">
          Edit the manifest, sheet/content schemas, and theme CSS. The behavioral logic in{" "}
          <code>mechanics.py</code> is generated once at creation and hand-edited on disk — this
          editor never rewrites it.
        </p>
        <MechanicsEditor
          manifest={manifest}
          themeCss={m.theme_css ?? null}
          sheetSchemas={m.sheet_schemas}
          contentSchemas={m.content_schemas}
          onSaved={onChanged}
        />
      </Section>

      {previewing && (
        <div
          className="modal-backdrop"
          role="dialog"
          aria-modal="true"
          aria-label="Preview character creation"
        >
          <div className="modal character-creation-modal">
            <LibraryCharacterCreationPreview
              moduleId={manifest.id}
              themeCss={m.theme_css ?? null}
              onCancel={() => setPreviewing(false)}
            />
          </div>
        </div>
      )}
    </div>
  );
}

function MechanicsRequirements() {
  return (
    <details className="mechanics-requirements">
      <summary>What a mechanics module requires</summary>
      <div className="mechanics-requirements-body">
        <p>
          A module lives in <code>data/mechanics/&lt;id&gt;/</code>. The directory name must match
          the manifest <code>id</code>. The loader rejects modules whose manifest fails schema
          validation or whose entry class does not satisfy the <code>MechanicsModule</code>{" "}
          protocol.
        </p>

        <h5>Directory layout</h5>
        <pre className="preset-text">{`data/mechanics/<id>/
  manifest.yaml      # required — module metadata
  mechanics.py       # required — defines Mechanics / MECHANICS / MechanicsModule
  sheets/            # JSON Schema files per declared sheet kind
    <kind>.json`}</pre>

        <h5>
          Required <code>manifest.yaml</code> fields
        </h5>
        <ul className="mechanics-requirements-list">
          <li>
            <code>id</code> — lowercase slug matching <code>^[a-z0-9][a-z0-9_-]*$</code>; must equal
            the directory name.
          </li>
          <li>
            <code>name</code> — non-empty display name.
          </li>
          <li>
            <code>version</code> — semver, e.g. <code>1.0.0</code>.
          </li>
          <li>
            <code>api_version</code> — currently only <code>"1"</code> is supported.
          </li>
        </ul>

        <h5>Optional manifest fields</h5>
        <ul className="mechanics-requirements-list">
          <li>
            <code>author</code>, <code>homepage</code>, <code>description</code> — surfaced in the
            module detail view.
          </li>
          <li>
            <code>sheet_kinds</code> — entity kinds the module supplies a sheet schema for (e.g.{" "}
            <code>character</code>, <code>item</code>).
          </li>
          <li>
            <code>content_kinds</code> — content types the module owns (e.g. <code>spells</code>,{" "}
            <code>disciplines</code>).
          </li>
          <li>
            <code>capabilities</code> — declared system capabilities (e.g. <code>dice</code>,{" "}
            <code>combat</code>, <code>character_creation</code>).
          </li>
          <li>
            <code>ui.theme_css</code> — path to a CSS file scoped under{" "}
            <code>.mechanics-&lt;id&gt;</code> at render time.
          </li>
          <li>
            <code>entry_class</code> — overrides the default class lookup in{" "}
            <code>mechanics.py</code>.
          </li>
        </ul>

        <h5>
          Required <code>mechanics.py</code> entry
        </h5>
        <p>
          Define one of <code>MECHANICS</code> (instance), <code>Mechanics</code> (class), or{" "}
          <code>MechanicsModule</code> (class), or set <code>entry_class</code> in the manifest. The
          resulting instance must expose:
        </p>
        <ul className="mechanics-requirements-list">
          <li>
            String attributes: <code>id</code>, <code>name</code>, <code>version</code>,{" "}
            <code>api_version</code> — and <code>instance.id</code> must match{" "}
            <code>manifest.id</code>.
          </li>
          <li>
            Methods: <code>sheet_schema</code>, <code>validate_sheet</code>,{" "}
            <code>initialize_sheet</code>, <code>list_content_kinds</code>,{" "}
            <code>content_schema</code>, <code>capabilities_of</code>,{" "}
            <code>power_definitions</code>, <code>power_definition</code>,{" "}
            <code>evaluate_pre_roll</code>, <code>resolve_roll</code>,{" "}
            <code>validate_narrated_event</code>, <code>character_creation_steps</code>,{" "}
            <code>time_tick</code>, <code>system_summary</code>.
          </li>
        </ul>

        <p className="library-status">
          See <code>specs/06-mechanics.md</code> for the full protocol reference.
        </p>
      </div>
    </details>
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
