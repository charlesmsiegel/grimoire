import { useCallback, useMemo, useReducer, useState } from "react";
import { Link, NavLink, Route, Routes, useNavigate, useParams } from "react-router-dom";

import {
  ApiError,
  ENTITY_KIND_SINGULAR,
  type CampaignRef,
  type Greeting,
  type LibraryEntity,
  libraryApi,
} from "../../api/library";
import { useResource } from "../../api/useResource";
import { deepEqual } from "../../lib/deepEqual";
import { Markdown } from "../../components/Markdown";
import { TokenBadge } from "../../components/TokenBadge";
import { AsyncBoundary } from "./AsyncBoundary";
import { ConfirmDestructiveDialog } from "../../components/ConfirmDestructiveDialog";
import { editReducer, initialEditState } from "./editState";
import { EntityForm } from "./EntityForm";
import { getDescriptor } from "./entitySchemas";
import { ExtrasTable } from "./ExtrasTable";
import { FrontmatterEditor } from "./FrontmatterEditor";
import { ensureFrontmatter, type Frontmatter } from "./frontmatter";
import { greetingFormToPayload, type GreetingFormValue } from "./greeting-form";
import { GreetingFormFields } from "./GreetingFormFields";
import { VariantsPanel } from "./VariantsPanel";

const ENTITY_HIDDEN_KEYS = ["extras"];
const EXTRAS_SUPPORTED_KINDS = new Set([
  "characters",
  "locations",
  "items",
  "factions",
  "monsters",
]);

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
            key={`${worldId}/${kind}/${entityId}`}
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
            key={`${worldId}/greetings/${entityId}`}
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

interface EntityDraft {
  frontmatter: Frontmatter;
  body: string;
}

function toEntityDraft(entity: LibraryEntity): EntityDraft {
  return { frontmatter: ensureFrontmatter(entity.frontmatter), body: entity.body };
}

const SUB_TABS = (isCharacter: boolean, basePath: string) => [
  { to: basePath, label: "Editor", end: true },
  ...(isCharacter
    ? [
        { to: `${basePath}/capabilities`, label: "Capabilities", end: false },
        { to: `${basePath}/variants`, label: "Variants", end: false },
      ]
    : []),
  { to: `${basePath}/preview`, label: "Preview", end: false },
];

function EntityEditorBody({
  entity,
  worldId,
  kindPlural,
  entityId,
  isCharacter,
  onReload,
}: EditorBodyProps) {
  const navigate = useNavigate();
  const [state, dispatch] = useReducer(editReducer<EntityDraft>, entity, (e) =>
    initialEditState(toEntityDraft(e)),
  );
  // Re-seed when a fresh entity revision arrives (reload after save, retry),
  // via React's render-time "previous prop" pattern — no mirror effect. A new
  // load object replaces the draft + baseline, so a server that normalizes on
  // save (e.g. trims fields) leaves the editor clean and canonical.
  const [seededFrom, setSeededFrom] = useState(entity);
  if (entity !== seededFrom) {
    setSeededFrom(entity);
    dispatch({ type: "reset", draft: toEntityDraft(entity) });
  }
  const { draft, baseline, saving, saveErr, confirm, deleting } = state;
  const { frontmatter, body } = draft;
  const dirty = !deepEqual(draft, baseline);

  async function confirmDelete() {
    dispatch({ type: "delete-start" });
    try {
      await libraryApi.deleteEntity(worldId, kindPlural, entityId);
      navigate(`/library/worlds/${encodeURIComponent(worldId)}/${kindPlural}`);
    } catch (err) {
      dispatch({
        type: "delete-fail",
        message: err instanceof ApiError ? err.message : String(err),
      });
    }
  }

  const dependents = useResource(
    useCallback(
      () => libraryApi.dependents(worldId, kindPlural, entityId),
      [worldId, kindPlural, entityId],
    ),
  );

  function patchFrontmatter(next: Frontmatter) {
    dispatch({ type: "edit", draft: { frontmatter: next, body } });
  }

  async function performSave() {
    dispatch({ type: "save-start" });
    try {
      await libraryApi.updateEntity(worldId, kindPlural, entityId, {
        frontmatter_patch: frontmatter,
        body,
      });
      dispatch({ type: "save-ok" });
      onReload();
      dependents.reload();
    } catch (err) {
      dispatch({ type: "save-fail", message: err instanceof ApiError ? err.message : String(err) });
    }
  }

  function handleSaveClick() {
    if (dependents.data && dependents.data.length > 0) {
      dispatch({ type: "ask-confirm", dependents: dependents.data });
    } else {
      void performSave();
    }
  }

  const basePath = `/library/worlds/${encodeURIComponent(worldId)}/${encodeURIComponent(kindPlural)}/${encodeURIComponent(entityId)}`;
  const subTabs = useMemo(() => SUB_TABS(isCharacter, basePath), [isCharacter, basePath]);

  return (
    <div className="entity-editor-body">
      <header className="entity-editor-header">
        <div>
          <h3>{entity.name || entity.asset_id}</h3>
          <small>
            <code>{entity.path}</code> · v{entity.version} ·{" "}
            <TokenBadge text={`${JSON.stringify(frontmatter)}\n${body}`} />
          </small>
        </div>
        <div className="entity-editor-actions">
          <button onClick={handleSaveClick} disabled={!dirty || saving}>
            {saving ? "Saving…" : "Save"}
          </button>
          {/* eslint-disable-next-line local/no-bespoke-delete -- entity detail delete action, not a card */}
          <button
            type="button"
            className="entity-editor-delete"
            onClick={() => dispatch({ type: "delete-open" })}
          >
            Delete
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
            key={tab.label}
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
                worldId={worldId}
                kindPlural={kindPlural}
                frontmatter={frontmatter}
                onFrontmatterChange={patchFrontmatter}
                body={body}
                onBodyChange={(b) => dispatch({ type: "edit", draft: { frontmatter, body: b } })}
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
        {isCharacter && (
          <Route
            path="variants"
            element={<VariantsPanel worldId={worldId} characterId={entity.asset_id} />}
          />
        )}
        <Route
          path="preview"
          element={
            <article className="entity-preview">
              <Markdown>{body}</Markdown>
            </article>
          }
        />
      </Routes>

      {confirm && (
        <ConfirmDestructiveDialog
          open
          title="Save edit to library?"
          body={
            <>
              <p>
                This entity is referenced by {confirm.dependents.length} campaign
                {confirm.dependents.length === 1 ? "" : "s"}:
              </p>
              <ul>
                {confirm.dependents.map((c) => (
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
          onCancel={() => dispatch({ type: "cancel-save" })}
        />
      )}

      {deleting && (
        <ConfirmDestructiveDialog
          open
          title={`Delete ${ENTITY_KIND_SINGULAR[kindPlural] ?? kindPlural} "${entity.name || entity.asset_id}"?`}
          body={
            <p>
              This permanently removes <code>{entity.path}</code>. Cannot be undone.
            </p>
          }
          dependents={dependents.data ?? "loading"}
          busy={deleting.busy}
          error={
            deleting.err ??
            (dependents.error
              ? `Dependents lookup failed: ${dependents.error.message}. Reload to retry.`
              : null)
          }
          onConfirm={() => void confirmDelete()}
          onCancel={() => dispatch({ type: "delete-close" })}
        />
      )}
    </div>
  );
}

function EditorPanel({
  worldId,
  kindPlural,
  frontmatter,
  onFrontmatterChange,
  body,
  onBodyChange,
}: {
  worldId: string;
  kindPlural: string;
  frontmatter: Frontmatter;
  onFrontmatterChange: (next: Frontmatter) => void;
  body: string;
  onBodyChange: (next: string) => void;
}) {
  const descriptor = getDescriptor(ENTITY_KIND_SINGULAR[kindPlural] ?? kindPlural);
  if (descriptor) {
    return (
      <EntityForm
        descriptor={descriptor}
        worldId={worldId}
        frontmatter={frontmatter}
        body={body}
        onFrontmatterChange={onFrontmatterChange}
        onBodyChange={onBodyChange}
      />
    );
  }
  return (
    <div className="entity-editor-panels">
      <section className="entity-editor-panel" aria-labelledby="frontmatter-heading">
        <h4 id="frontmatter-heading">Frontmatter</h4>
        <FrontmatterEditor
          value={frontmatter}
          onChange={onFrontmatterChange}
          hiddenKeys={ENTITY_HIDDEN_KEYS}
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
    roleTagsText: (g.role_tags ?? []).join(", "),
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
  const navigate = useNavigate();
  const [state, dispatch] = useReducer(editReducer<GreetingFormValue>, greeting, (g) =>
    initialEditState(greetingToForm(g)),
  );
  // Re-seed when a fresh greeting revision arrives (see EntityEditorBody).
  // greetingFormToPayload trims on save, so this keeps the form canonical and
  // clean after the reload returns the trimmed values.
  const [seededFrom, setSeededFrom] = useState(greeting);
  if (greeting !== seededFrom) {
    setSeededFrom(greeting);
    dispatch({ type: "reset", draft: greetingToForm(greeting) });
  }
  const { draft: form, baseline, saving, saveErr, confirm, deleting } = state;
  const dirty = !deepEqual(form, baseline);

  async function confirmDelete() {
    dispatch({ type: "delete-start" });
    try {
      await libraryApi.deleteEntity(worldId, "greetings", entityId);
      navigate(`/library/worlds/${encodeURIComponent(worldId)}/greetings`);
    } catch (err) {
      dispatch({
        type: "delete-fail",
        message: err instanceof ApiError ? err.message : String(err),
      });
    }
  }

  const dependents = useResource(
    useCallback(() => libraryApi.dependents(worldId, "greetings", entityId), [worldId, entityId]),
  );

  function patch(next: GreetingFormValue) {
    dispatch({ type: "edit", draft: next });
  }

  async function performSave() {
    dispatch({ type: "save-start" });
    try {
      const { frontmatter, body } = greetingFormToPayload(form, greeting.id);
      await libraryApi.updateEntity(worldId, "greetings", entityId, {
        frontmatter_patch: frontmatter,
        body,
      });
      dispatch({ type: "save-ok" });
      onReload();
      dependents.reload();
    } catch (err) {
      dispatch({ type: "save-fail", message: err instanceof ApiError ? err.message : String(err) });
    }
  }

  function handleSaveClick() {
    if (dependents.data && dependents.data.length > 0) {
      dispatch({ type: "ask-confirm", dependents: dependents.data });
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
          {/* eslint-disable-next-line local/no-bespoke-delete -- entity detail delete action, not a card */}
          <button
            type="button"
            className="entity-editor-delete"
            onClick={() => dispatch({ type: "delete-open" })}
          >
            Delete
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

      {confirm && (
        <ConfirmDestructiveDialog
          open
          title="Save edit to library?"
          body={
            <>
              <p>
                This entity is referenced by {confirm.dependents.length} campaign
                {confirm.dependents.length === 1 ? "" : "s"}:
              </p>
              <ul>
                {confirm.dependents.map((c) => (
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
          onCancel={() => dispatch({ type: "cancel-save" })}
        />
      )}

      {deleting && (
        <ConfirmDestructiveDialog
          open
          title={`Delete greeting "${form.name || greeting.id}"?`}
          body={
            <p>
              This permanently removes greeting <code>{greeting.id}</code>. Cannot be undone.
            </p>
          }
          dependents={dependents.data ?? "loading"}
          busy={deleting.busy}
          error={
            deleting.err ??
            (dependents.error
              ? `Dependents lookup failed: ${dependents.error.message}. Reload to retry.`
              : null)
          }
          onConfirm={() => void confirmDelete()}
          onCancel={() => dispatch({ type: "delete-close" })}
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
