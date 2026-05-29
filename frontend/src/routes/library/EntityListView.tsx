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
import { CardFilters } from "../../components/CardFilters";
import { useCardFilters } from "../../hooks/useCardFilters";
import { TokenBadge } from "../../components/TokenBadge";
import { AsyncBoundary } from "./AsyncBoundary";
import { ConfirmDestructiveDialog } from "./ConfirmDestructiveDialog";
import { ConvertModal } from "./ConvertModal";
import { EntityForm } from "./EntityForm";
import { getDescriptor, primaryLabelKey } from "./entitySchemas";
import { type Frontmatter } from "./frontmatter";
import { emptyGreetingForm, greetingFormToPayload, type GreetingFormValue } from "./greeting-form";
import { GreetingFormFields } from "./GreetingFormFields";
import { IdField } from "./IdField";

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
  const [createDraft, setCreateDraft] = useState<Frontmatter>({});
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
      const descriptor = getDescriptor(singular);
      let frontmatter: Record<string, unknown>;
      let body = "";
      if (isGreetingKind) {
        ({ frontmatter, body } = greetingFormToPayload(
          { ...greetingForm, name: newName.trim() },
          id,
        ));
      } else if (descriptor) {
        frontmatter = { ...createDraft, id, [primaryLabelKey(descriptor)]: newName.trim() };
      } else {
        frontmatter = { name: newName.trim(), id };
      }
      const created = await libraryApi.createEntity(worldId, kindPlural, {
        id,
        frontmatter,
        body,
      });
      setCreating(false);
      setNewId("");
      setNewName("");
      setCreateDraft({});
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
          <IdField
            nameLabel={
              getDescriptor(singular) && primaryLabelKey(getDescriptor(singular)!) === "title"
                ? "Title"
                : "Name"
            }
            name={newName}
            id={newId}
            onNameChange={setNewName}
            onIdChange={setNewId}
          />
          {!isGreetingKind && getDescriptor(singular) && (
            <EntityForm
              descriptor={getDescriptor(singular)!}
              worldId={worldId}
              mode="create"
              frontmatter={createDraft}
              body=""
              onFrontmatterChange={setCreateDraft}
              onBodyChange={() => {}}
            />
          )}
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
        <EntityListBody
          entities={data ?? []}
          worldId={worldId}
          kindPlural={kindPlural}
          onConvert={setConvertingId}
          onDelete={openDelete}
        />
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

interface EntityListBodyProps {
  entities: Array<LibraryEntity | Greeting>;
  worldId: string;
  kindPlural: string;
  onConvert: (id: string) => void;
  onDelete: (id: string, name: string) => void;
}

function EntityListBody({
  entities,
  worldId,
  kindPlural,
  onConvert,
  onDelete,
}: EntityListBodyProps) {
  const { filtered, search, setSearch, selectedTags, toggleTag, clearTags, availableTags } =
    useCardFilters(entities, {
      text: (e) => {
        const id = "asset_id" in e ? e.asset_id : e.id;
        const body = "body" in e ? e.body : "";
        return [e.name, id, body];
      },
      tags: (e) => (isGreeting(e) ? e.tags : (e as LibraryEntity).tags) ?? [],
    });

  return (
    <>
      <CardFilters
        search={search}
        onSearch={setSearch}
        availableTags={availableTags}
        selectedTags={selectedTags}
        onToggleTag={toggleTag}
        onClearTags={clearTags}
        searchPlaceholder={`Search ${kindPlural} by name, id, or text…`}
        searchLabel={`Search ${kindPlural}`}
        resultSummary={
          filtered.length === entities.length
            ? `${entities.length} entr${entities.length === 1 ? "y" : "ies"}`
            : `${filtered.length} of ${entities.length}`
        }
      />
      {filtered.length === 0 ? (
        <p className="library-status">No {kindPlural} match the current filters.</p>
      ) : (
        <ul className="library-card-grid">
          {filtered.map((e) => {
            const id = "asset_id" in e ? e.asset_id : e.id;
            const name = e.name || id;
            const tags = isGreeting(e) ? e.tags : (e as LibraryEntity).tags;
            const tokenText =
              "frontmatter" in e
                ? `${JSON.stringify(e.frontmatter)}\n${e.body ?? ""}`
                : (e.body ?? "");
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
                <p className="library-card-meta">
                  <TokenBadge text={tokenText} />
                </p>
                {kindPlural === "lore" && (
                  <button
                    type="button"
                    className="library-card-action"
                    onClick={(ev) => {
                      ev.preventDefault();
                      ev.stopPropagation();
                      onConvert(id);
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
                    onDelete(id, name);
                  }}
                >
                  Delete
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </>
  );
}

export const ENTITY_KIND_LABELS = ENTITY_KIND_PLURAL;
