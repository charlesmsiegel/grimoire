import { useCallback, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import {
  ApiError,
  ENTITY_KIND_PLURAL,
  ENTITY_KIND_SINGULAR,
  type CampaignRef,
  type EntityKind,
  type Greeting,
  type LibraryEntity,
  libraryApi,
} from "../../api/library";
import { useResource } from "../../api/useResource";
import { AsyncBoundary } from "./AsyncBoundary";
import { ConfirmDestructiveDialog } from "./ConfirmDestructiveDialog";
import { ConvertModal } from "./ConvertModal";
import { emptyGreetingForm, greetingFormToPayload, type GreetingFormValue } from "./greeting-form";
import { GreetingFormFields } from "./GreetingFormFields";

interface Props {
  /** Plural kind from URL: characters, items, locations, lore, factions, greetings. */
  kindOverride?: string;
}

function isGreeting(v: LibraryEntity | Greeting): v is Greeting {
  return "starting_location" in v && !("frontmatter" in v);
}

export function EntityListView({ kindOverride }: Props) {
  const params = useParams();
  const worldId = params.worldId ?? "";
  const kindPlural = kindOverride ?? params.kind ?? "characters";
  const singular = ENTITY_KIND_SINGULAR[kindPlural] ?? "character";

  const navigate = useNavigate();
  const { data, loading, error, reload } = useResource(
    useCallback(() => libraryApi.listEntities(worldId, kindPlural), [worldId, kindPlural]),
  );

  const [creating, setCreating] = useState(false);
  const [newId, setNewId] = useState("");
  const [newName, setNewName] = useState("");
  const [greetingForm, setGreetingForm] = useState<GreetingFormValue>(emptyGreetingForm);
  const [submitErr, setSubmitErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [convertingId, setConvertingId] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<{
    entityId: string;
    entityName: string;
    dependents: CampaignRef[] | undefined;
    busy: boolean;
    err: string | null;
  } | null>(null);

  const isGreetingKind = kindPlural === "greetings";

  async function openDelete(entityId: string, entityName: string) {
    setDeleting({ entityId, entityName, dependents: undefined, busy: false, err: null });
    try {
      const deps = await libraryApi.dependents(worldId, kindPlural, entityId);
      setDeleting((d) => (d && d.entityId === entityId ? { ...d, dependents: deps } : d));
    } catch {
      setDeleting((d) => (d && d.entityId === entityId ? { ...d, dependents: [] } : d));
    }
  }

  async function confirmDelete() {
    if (!deleting) return;
    setDeleting({ ...deleting, busy: true, err: null });
    try {
      await libraryApi.deleteEntity(worldId, kindPlural, deleting.entityId);
      setDeleting(null);
      reload();
    } catch (err) {
      setDeleting({
        ...deleting,
        busy: false,
        err: err instanceof ApiError ? err.message : String(err),
      });
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitErr(null);
    setBusy(true);
    try {
      const id = newId.trim();
      const { frontmatter, body } = isGreetingKind
        ? greetingFormToPayload({ ...greetingForm, name: newName.trim() }, id)
        : { frontmatter: { name: newName.trim(), id }, body: "" };
      const created = await libraryApi.createEntity(worldId, kindPlural, {
        id,
        frontmatter,
        body,
      });
      setCreating(false);
      setNewId("");
      setNewName("");
      setGreetingForm(emptyGreetingForm());
      navigate(
        `/library/worlds/${encodeURIComponent(worldId)}/${kindPlural}/${encodeURIComponent(
          created.asset_id,
        )}`,
      );
    } catch (err) {
      setSubmitErr(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="library-section entity-list">
      <header className="library-section-header">
        <h4>{kindPlural}</h4>
        <button onClick={() => setCreating((c) => !c)} aria-expanded={creating}>
          {creating ? "Cancel" : `+ New ${singular}`}
        </button>
      </header>

      {creating && (
        <form onSubmit={submit} className="library-form" aria-label={`Create ${singular}`}>
          <label>
            <span>ID</span>
            <input
              required
              value={newId}
              pattern="[a-z0-9][a-z0-9-]*"
              title="lowercase letters, digits, and hyphens"
              onChange={(e) => setNewId(e.target.value)}
            />
          </label>
          <label>
            <span>Name</span>
            <input required value={newName} onChange={(e) => setNewName(e.target.value)} />
          </label>
          {isGreetingKind && (
            <GreetingFormFields
              worldId={worldId}
              value={greetingForm}
              onChange={setGreetingForm}
              hideName
            />
          )}
          <button type="submit" disabled={busy}>
            {busy ? "Creating…" : "Create"}
          </button>
          {submitErr && (
            <p className="library-error" role="alert">
              {submitErr}
            </p>
          )}
        </form>
      )}

      <AsyncBoundary
        loading={loading}
        error={error}
        empty={!data || data.length === 0}
        emptyMessage={`No ${kindPlural} yet.`}
        onRetry={reload}
      >
        <ul className="library-card-grid">
          {(data ?? []).map((e) => {
            const id = "asset_id" in e ? e.asset_id : e.id;
            const name = e.name || id;
            const tags = isGreeting(e) ? e.tags : (e as LibraryEntity).tags;
            return (
              <li key={id} className="library-card">
                <Link
                  to={`/library/worlds/${encodeURIComponent(worldId)}/${kindPlural}/${encodeURIComponent(id)}`}
                >
                  <h4>{name}</h4>
                  <small>{id}</small>
                  {tags && tags.length > 0 && (
                    <p className="library-card-meta">{tags.join(" · ")}</p>
                  )}
                </Link>
                {kindPlural === "lore" && (
                  <button
                    type="button"
                    className="library-card-action"
                    onClick={(ev) => {
                      ev.preventDefault();
                      ev.stopPropagation();
                      setConvertingId(id);
                    }}
                  >
                    Convert
                  </button>
                )}
                <button
                  type="button"
                  className="library-card-action"
                  onClick={(ev) => {
                    ev.preventDefault();
                    ev.stopPropagation();
                    void openDelete(id, name);
                  }}
                >
                  Delete
                </button>
              </li>
            );
          })}
        </ul>
      </AsyncBoundary>

      {convertingId && (
        <ConvertModal
          worldId={worldId}
          sourceId={convertingId}
          onClose={() => setConvertingId(null)}
          onConverted={(kind: EntityKind, targetId: string) => {
            setConvertingId(null);
            reload();
            navigate(
              `/library/worlds/${encodeURIComponent(worldId)}/${ENTITY_KIND_PLURAL[kind]}/${encodeURIComponent(targetId)}`,
            );
          }}
        />
      )}
      {deleting && (
        <ConfirmDestructiveDialog
          open
          title={`Delete ${singular} "${deleting.entityName}"?`}
          body={
            <p>
              This permanently removes <code>{deleting.entityId}</code> from this world. Cannot be
              undone.
            </p>
          }
          dependents={deleting.dependents}
          busy={deleting.busy}
          error={deleting.err}
          onConfirm={() => void confirmDelete()}
          onCancel={() => setDeleting(null)}
        />
      )}
    </div>
  );
}

export const ENTITY_KIND_LABELS = ENTITY_KIND_PLURAL;
