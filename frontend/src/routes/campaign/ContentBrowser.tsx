/**
 * Mechanics content browser (spec 06 §Responsibilities — "Provide content
 * browsers (spells, items, vis sources, etc.) per system").
 *
 * For the campaign's bound mechanics module, the manifest declares
 * `content_kinds: [...]`. This component renders one tab per kind: a list of
 * the campaign's stored entries on the left, and an editor (driven by the
 * module's JSON Schema, fed through `SheetRenderer`) on the right. PUT on
 * save sends the raw payload back.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { campaignApi, type ContentEntry } from "../../api/campaign";
import { errorMessage } from "../../api/client";
import { mechanicsApi, type RegisteredModule } from "../../api/library";
import { useResource } from "../../api/useResource";
import { SheetRenderer } from "../../sheets/SheetRenderer";
import type { SheetSchema } from "../../sheets/types";
import { CardIconBar } from "../../components/CardIconBar";
import { Tabs } from "../../components/Tabs";

interface ContentBrowserProps {
  campaignId: string;
  module: RegisteredModule;
  /** Override list of kinds (defaults to `module.manifest.content_kinds`). */
  kinds?: string[];
}

export function ContentBrowser({ campaignId, module, kinds }: ContentBrowserProps) {
  const declared = kinds ?? module.manifest.content_kinds;
  const [activeKind, setActiveKind] = useState<string | null>(declared[0] ?? null);

  useEffect(() => {
    if (!declared.includes(activeKind ?? "")) {
      setActiveKind(declared[0] ?? null);
    }
  }, [declared, activeKind]);

  if (declared.length === 0) {
    return (
      <section className="placeholder-panel">
        <h3>Content browser</h3>
        <p className="muted">
          The active mechanics module ({module.manifest.id}) does not declare any content kinds.
        </p>
      </section>
    );
  }

  return (
    <section className="content-browser" aria-labelledby="content-browser-heading">
      <header className="route-header">
        <h3 id="content-browser-heading">Content</h3>
      </header>
      <Tabs
        tabs={declared.map((k) => ({ key: k, label: k }))}
        active={activeKind ?? ""}
        onSelect={setActiveKind}
        ariaLabel="Content kinds"
      />
      {activeKind && (
        <ContentKindPanel
          key={activeKind}
          campaignId={campaignId}
          module={module}
          kind={activeKind}
        />
      )}
    </section>
  );
}

interface PanelProps {
  campaignId: string;
  module: RegisteredModule;
  kind: string;
}

function ContentKindPanel({ campaignId, module, kind }: PanelProps) {
  const moduleId = module.manifest.id;
  const {
    data: loaded,
    error,
    loading,
  } = useResource(
    useCallback(
      () =>
        Promise.all([
          campaignApi.listContent(campaignId, kind),
          mechanicsApi.contentSchema(moduleId, kind),
        ]).then(([list, sch]) => ({ entries: list, schema: sch as unknown as SheetSchema })),
      [campaignId, kind, moduleId],
    ),
  );

  if (loading) return <p className="muted">Loading {kind}…</p>;
  if (error) {
    return (
      <p className="error" role="alert">
        Failed to load {kind}: {errorMessage(error)}
      </p>
    );
  }
  if (!loaded) return <p className="muted">Loading {kind}…</p>;

  return (
    <ContentKindEditor
      campaignId={campaignId}
      module={module}
      kind={kind}
      initialEntries={loaded.entries}
      schema={loaded.schema}
    />
  );
}

interface KindEditorProps {
  campaignId: string;
  module: RegisteredModule;
  kind: string;
  initialEntries: ContentEntry[];
  schema: SheetSchema;
}

function ContentKindEditor({ campaignId, module, kind, initialEntries, schema }: KindEditorProps) {
  const moduleId = module.manifest.id;
  const themeCss = module.theme_css ?? null;
  const [entries, setEntries] = useState<ContentEntry[]>(initialEntries);
  const [selectedId, setSelectedId] = useState<string | null>(initialEntries[0]?.id ?? null);

  const selected = useMemo(
    () => entries.find((e) => e.id === selectedId) ?? null,
    [entries, selectedId],
  );

  return (
    <div className="content-browser-layout">
      <aside className="content-list">
        <h4>{kind}</h4>
        <ul className="entity-list">
          {entries.map((e) => {
            const friendly = friendlyName(e);
            return (
              <li key={e.id}>
                <button
                  type="button"
                  className={selectedId === e.id ? "card entity-card active" : "card entity-card"}
                  onClick={() => setSelectedId(e.id)}
                >
                  <div className="entity-card-head">
                    <span className="entity-name">{friendly}</span>
                  </div>
                  <small className="entity-meta">{e.id}</small>
                </button>
                <CardIconBar actions={[]} />
              </li>
            );
          })}
          {entries.length === 0 && (
            <li className="muted">No {kind} stored for this campaign yet.</li>
          )}
        </ul>
      </aside>
      <section className="content-detail">
        {selected && schema ? (
          <ContentEditor
            campaignId={campaignId}
            kind={kind}
            entry={selected}
            schema={schema}
            moduleId={moduleId}
            themeCss={themeCss}
            onSaved={(next) =>
              setEntries((prev) =>
                prev.map((e) => (e.id === selected.id ? { ...e, payload: next } : e)),
              )
            }
          />
        ) : (
          <p className="muted">Select an entry to edit.</p>
        )}
      </section>
    </div>
  );
}

interface EditorProps {
  campaignId: string;
  kind: string;
  entry: ContentEntry;
  schema: SheetSchema;
  moduleId: string;
  themeCss: string | null;
  onSaved: (payload: Record<string, unknown>) => void;
}

function ContentEditor({
  campaignId,
  kind,
  entry,
  schema,
  moduleId,
  themeCss,
  onSaved,
}: EditorProps) {
  const [value, setValue] = useState<Record<string, unknown>>(entry.payload);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  // Reset when the selected entry changes.
  useEffect(() => {
    setValue(entry.payload);
    setError(null);
    setSavedAt(null);
  }, [entry.id, entry.payload]);

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const next = await campaignApi.putContent(campaignId, kind, entry.id, value);
      onSaved(next);
      setSavedAt(Date.now());
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="content-editor">
      <SheetRenderer
        moduleId={moduleId}
        schema={schema}
        value={value}
        onChange={setValue}
        themeCss={themeCss ?? undefined}
      />
      {error && (
        <p className="wizard-error" role="alert">
          {error}
        </p>
      )}
      {savedAt && <p className="wizard-meta">Saved.</p>}
      <div className="modal-actions">
        <button type="button" className="primary" disabled={saving} onClick={() => void save()}>
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
    </div>
  );
}

function friendlyName(entry: ContentEntry): string {
  const p = entry.payload as Record<string, unknown>;
  const name = p?.name;
  if (typeof name === "string" && name) return name;
  const id = p?.id;
  if (typeof id === "string" && id) return id;
  return entry.id;
}
