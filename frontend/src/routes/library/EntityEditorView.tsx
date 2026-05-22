import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, NavLink, Route, Routes, useParams } from "react-router-dom";

import {
  ApiError,
  ENTITY_KIND_SINGULAR,
  type CampaignRef,
  type Greeting,
  type LibraryEntity,
  libraryApi,
} from "../../api/library";
import { useResource } from "../../api/useResource";
import { Markdown } from "../../components/Markdown";
import { AsyncBoundary } from "./AsyncBoundary";
import { CharacterExtras } from "./CharacterExtras";
import { ConfirmDestructiveDialog } from "./ConfirmDestructiveDialog";
import { ExtrasTable } from "./ExtrasTable";
import { FrontmatterEditor } from "./FrontmatterEditor";
import { ensureFrontmatter, type Frontmatter } from "./frontmatter";
import { greetingFormToPayload, type GreetingFormValue } from "./greeting-form";
import { GreetingFormFields } from "./GreetingFormFields";
import { VariantsBreadcrumb } from "./VariantsBreadcrumb";
import { VariantsPanel } from "./VariantsPanel";

const CHARACTER_HIDDEN_KEYS = ["voice", "image", "name", "id", "extras"];
const ENTITY_HIDDEN_KEYS = ["extras"];
const EXTRAS_SUPPORTED_KINDS = new Set(["characters", "locations", "items", "factions"]);

export function EntityEditorView() {
  const { worldId = "", kind = "characters", entityId = "" } = useParams();
  const singular = ENTITY_KIND_SINGULAR[kind] ?? kind;

  const { data, loading, error, reload } = useResource(
    useCallback(() => libraryApi.getEntity(worldId, kind, entityId), [worldId, kind, entityId]),
  );

  const isCharacter = singular === "character";

  return (
    <div className="library-section entity-editor">
      <p className="library-breadcrumb">
        <Link to={`/library/worlds/${encodeURIComponent(worldId)}`}>{worldId}</Link>
        {" / "}
        <Link to={`/library/worlds/${encodeURIComponent(worldId)}/${kind}`}>{kind}</Link>
        {" / "}
        {entityId}
      </p>
      <AsyncBoundary loading={loading} error={error} onRetry={reload}>
        {data && "frontmatter" in data && (
          <EntityEditorBody
            entity={data as LibraryEntity}
            worldId={worldId}
            kindPlural={kind}
            entityId={entityId}
            isCharacter={isCharacter}
            onReload={reload}
          />
        )}
        {data && !("frontmatter" in data) && (
          <GreetingEditorBody
            greeting={data as Greeting}
            worldId={worldId}
            entityId={entityId}
            onReload={reload}
          />
        )}
      </AsyncBoundary>
    </div>
  );
}

interface EditorBodyProps {
  entity: LibraryEntity;
  worldId: string;
  kindPlural: string;
  entityId: string;
  isCharacter: boolean;
  onReload: () => void;
}

const SUB_TABS = (isCharacter: boolean) => [
  { to: "", label: "Editor", end: true },
  ...(isCharacter ? [{ to: "capabilities", label: "Capabilities", end: false }] : []),
  { to: "variants", label: "Variants", end: false },
  { to: "preview", label: "Preview", end: false },
];

function EntityEditorBody({
  entity,
  worldId,
  kindPlural,
  entityId,
  isCharacter,
  onReload,
}: EditorBodyProps) {
  const [frontmatter, setFrontmatter] = useState<Frontmatter>(
    ensureFrontmatter(entity.frontmatter),
  );
  const [body, setBody] = useState(entity.body);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState<string | null>(null);
  const [confirmEdit, setConfirmEdit] = useState<null | { dependents: CampaignRef[] }>(null);
  const [pendingSave, setPendingSave] = useState(false);

  useEffect(() => {
    setFrontmatter(ensureFrontmatter(entity.frontmatter));
    setBody(entity.body);
    setDirty(false);
  }, [entity]);

  const dependents = useResource(
    useCallback(
      () => libraryApi.dependents(worldId, kindPlural, entityId),
      [worldId, kindPlural, entityId],
    ),
  );

  function patchFrontmatter(next: Frontmatter) {
    setFrontmatter(next);
    setDirty(true);
  }

  async function performSave() {
    setSaving(true);
    setSaveErr(null);
    try {
      await libraryApi.updateEntity(worldId, kindPlural, entityId, {
        frontmatter_patch: frontmatter,
        body,
      });
      setDirty(false);
      onReload();
      dependents.reload();
    } catch (err) {
      setSaveErr(err instanceof ApiError ? err.message : String(err));
    } finally {
      setSaving(false);
      setConfirmEdit(null);
      setPendingSave(false);
    }
  }

  function handleSaveClick() {
    setPendingSave(true);
    if (dependents.data && dependents.data.length > 0) {
      setConfirmEdit({ dependents: dependents.data });
    } else {
      void performSave();
    }
  }

  const subTabs = useMemo(() => SUB_TABS(isCharacter), [isCharacter]);

  return (
    <div className="entity-editor-body">
      <header className="entity-editor-header">
        <div>
          <h3>{entity.name || entity.asset_id}</h3>
          <small>
            <code>{entity.path}</code> · v{entity.version}
          </small>
          <VariantsBreadcrumb
            kindPlural={kindPlural}
            assetId={entity.asset_id}
            currentWorldId={worldId}
          />
        </div>
        <div className="entity-editor-actions">
          <button onClick={handleSaveClick} disabled={!dirty || saving}>
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </header>

      {saveErr && (
        <p className="library-error" role="alert">
          {saveErr}
        </p>
      )}

      <DependentsBanner dependents={dependents.data ?? []} loading={dependents.loading} />

      <nav className="entity-subtabs" aria-label="Entity sections">
        {subTabs.map((tab) => (
          <NavLink
            key={tab.to || "editor"}
            to={tab.to}
            end={tab.end}
            className={({ isActive }) => (isActive ? "entity-subtab active" : "entity-subtab")}
          >
            {tab.label}
          </NavLink>
        ))}
      </nav>

      <Routes>
        <Route
          index
          element={
            <>
              <EditorPanel
                frontmatter={frontmatter}
                onFrontmatterChange={patchFrontmatter}
                body={body}
                onBodyChange={(b) => {
                  setBody(b);
                  setDirty(true);
                }}
                isCharacter={isCharacter}
              />
              {EXTRAS_SUPPORTED_KINDS.has(kindPlural) ? (
                <ExtrasTable
                  worldId={worldId}
                  kind={ENTITY_KIND_SINGULAR[kindPlural] ?? kindPlural}
                  entityId={entityId}
                />
              ) : null}
            </>
          }
        />
        {isCharacter && (
          <Route path="capabilities" element={<CapabilitiesPanel entity={entity} />} />
        )}
        <Route
          path="variants"
          element={<VariantsPanel kindPlural={kindPlural} assetId={entity.asset_id} />}
        />
        <Route
          path="preview"
          element={
            <article className="entity-preview">
              <Markdown>{body}</Markdown>
            </article>
          }
        />
      </Routes>

      {confirmEdit && pendingSave && (
        <ConfirmDestructiveDialog
          open
          title="Save edit to library?"
          body={
            <>
              <p>
                This entity is referenced by {confirmEdit.dependents.length} campaign
                {confirmEdit.dependents.length === 1 ? "" : "s"}:
              </p>
              <ul>
                {confirmEdit.dependents.map((c) => (
                  <li key={c.id}>{c.name || c.id}</li>
                ))}
              </ul>
              <p>
                Pinned campaigns will continue to see the previous version until they explicitly
                upgrade. Tracking-latest campaigns pick up the change immediately.
              </p>
            </>
          }
          dependents={[]}
          busy={saving}
          busyLabel="Saving…"
          confirmLabel="Save anyway"
          onConfirm={() => void performSave()}
          onCancel={() => {
            setConfirmEdit(null);
            setPendingSave(false);
          }}
        />
      )}
    </div>
  );
}

function EditorPanel({
  frontmatter,
  onFrontmatterChange,
  body,
  onBodyChange,
  isCharacter,
}: {
  frontmatter: Frontmatter;
  onFrontmatterChange: (next: Frontmatter) => void;
  body: string;
  onBodyChange: (next: string) => void;
  isCharacter: boolean;
}) {
  return (
    <div className="entity-editor-panels">
      <section className="entity-editor-panel" aria-labelledby="frontmatter-heading">
        <h4 id="frontmatter-heading">Frontmatter</h4>
        {isCharacter && (
          <CharacterExtras frontmatter={frontmatter} onChange={onFrontmatterChange} />
        )}
        <FrontmatterEditor
          value={frontmatter}
          onChange={onFrontmatterChange}
          hiddenKeys={isCharacter ? CHARACTER_HIDDEN_KEYS : ENTITY_HIDDEN_KEYS}
        />
      </section>
      <section className="entity-editor-panel" aria-labelledby="body-heading">
        <h4 id="body-heading">Markdown body</h4>
        <textarea
          className="entity-body-editor"
          value={body}
          rows={24}
          onChange={(e) => onBodyChange(e.target.value)}
        />
      </section>
    </div>
  );
}

function DependentsBanner({
  dependents,
  loading,
}: {
  dependents: CampaignRef[];
  loading: boolean;
}) {
  if (loading) return null;
  if (dependents.length === 0) return null;
  return (
    <div className="dependents-banner" role="status">
      <strong>
        {dependents.length} dependent campaign{dependents.length === 1 ? "" : "s"}.
      </strong>{" "}
      Edits will be visible to these campaigns when they upgrade their ref; pinned campaigns
      continue seeing the previous version.
      <ul>
        {dependents.map((c) => (
          <li key={c.id}>
            <Link to={`/campaigns/${encodeURIComponent(c.id)}`}>{c.name || c.id}</Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

function greetingToForm(g: Greeting): GreetingFormValue {
  return {
    name: g.name ?? "",
    tagsText: (g.tags ?? []).join(", "),
    body: g.body ?? "",
    presentCharacters: g.present_characters ?? [],
    povCharacter: g.pov_character ?? "",
    startingLocation: g.starting_location ?? "",
    startingTime: g.starting_time ?? "",
    mood: g.mood ?? "",
  };
}

function GreetingEditorBody({
  greeting,
  worldId,
  entityId,
  onReload,
}: {
  greeting: Greeting;
  worldId: string;
  entityId: string;
  onReload: () => void;
}) {
  const [form, setForm] = useState<GreetingFormValue>(() => greetingToForm(greeting));
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState<string | null>(null);
  const [confirmEdit, setConfirmEdit] = useState<null | { dependents: CampaignRef[] }>(null);
  const [pendingSave, setPendingSave] = useState(false);

  useEffect(() => {
    setForm(greetingToForm(greeting));
    setDirty(false);
  }, [greeting]);

  const dependents = useResource(
    useCallback(
      () => libraryApi.dependents(worldId, "greetings", entityId),
      [worldId, entityId],
    ),
  );

  function patch(next: GreetingFormValue) {
    setForm(next);
    setDirty(true);
  }

  async function performSave() {
    setSaving(true);
    setSaveErr(null);
    try {
      const { frontmatter, body } = greetingFormToPayload(form, greeting.id);
      await libraryApi.updateEntity(worldId, "greetings", entityId, {
        frontmatter_patch: frontmatter,
        body,
      });
      setDirty(false);
      onReload();
      dependents.reload();
    } catch (err) {
      setSaveErr(err instanceof ApiError ? err.message : String(err));
    } finally {
      setSaving(false);
      setConfirmEdit(null);
      setPendingSave(false);
    }
  }

  function handleSaveClick() {
    setPendingSave(true);
    if (dependents.data && dependents.data.length > 0) {
      setConfirmEdit({ dependents: dependents.data });
    } else {
      void performSave();
    }
  }

  return (
    <div className="entity-editor-body">
      <header className="entity-editor-header">
        <div>
          <h3>{form.name || greeting.id}</h3>
          <small>greeting · {greeting.id}</small>
        </div>
        <div className="entity-editor-actions">
          <button onClick={handleSaveClick} disabled={!dirty || saving}>
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </header>

      {saveErr && (
        <p className="library-error" role="alert">
          {saveErr}
        </p>
      )}

      <DependentsBanner dependents={dependents.data ?? []} loading={dependents.loading} />

      <form
        className="library-form"
        onSubmit={(e) => {
          e.preventDefault();
          handleSaveClick();
        }}
      >
        <GreetingFormFields worldId={worldId} value={form} onChange={patch} />
      </form>

      {confirmEdit && pendingSave && (
        <ConfirmDestructiveDialog
          open
          title="Save edit to library?"
          body={
            <>
              <p>
                This entity is referenced by {confirmEdit.dependents.length} campaign
                {confirmEdit.dependents.length === 1 ? "" : "s"}:
              </p>
              <ul>
                {confirmEdit.dependents.map((c) => (
                  <li key={c.id}>{c.name || c.id}</li>
                ))}
              </ul>
              <p>
                Pinned campaigns will continue to see the previous version until they explicitly
                upgrade. Tracking-latest campaigns pick up the change immediately.
              </p>
            </>
          }
          dependents={[]}
          busy={saving}
          busyLabel="Saving…"
          confirmLabel="Save anyway"
          onConfirm={() => void performSave()}
          onCancel={() => {
            setConfirmEdit(null);
            setPendingSave(false);
          }}
        />
      )}
    </div>
  );
}

function CapabilitiesPanel({ entity }: { entity: LibraryEntity }) {
  return (
    <section className="entity-capabilities">
      <p>
        Mechanical sheets and capabilities live <em>per-campaign</em>: the library card describes
        what the character is, while sheets attach when a campaign with a mechanics module includes
        this character. View resolved sheets from a campaign's Cast view.
      </p>
      <p>
        Library file: <code>{entity.path}</code>
      </p>
    </section>
  );
}
