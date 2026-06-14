import { useCallback, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

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
import type { ResolvedEntity } from "../../api/types";
import { useResource } from "../../api/useResource";
import { type CardIconAction } from "../../components/CardIconBar";
import { deleteAction } from "../../components/cardActions";
import { SwapIcon } from "../../components/icons";
import { EntityBrowser } from "../../components/EntityBrowser";
import { AsyncBoundary } from "./AsyncBoundary";
import { ConfirmDestructiveDialog } from "../../components/ConfirmDestructiveDialog";
import { ConvertModal } from "./ConvertModal";
import { EntityForm } from "./EntityForm";
import { getDescriptor, primaryLabelKey } from "./entitySchemas";
import { type Frontmatter } from "./frontmatter";
import { emptyGreetingForm, greetingFormToPayload, type GreetingFormValue } from "./greeting-form";
import { GreetingFormFields } from "./GreetingFormFields";
import { IdField } from "./IdField";
import { errorMessage } from "../../api/client";

interface Props {
  /** Plural kind from URL: characters, items, locations, lore, factions, greetings. */
  kindOverride?: string;
}

function isGreeting(v: LibraryEntity | Greeting): v is Greeting {
  return "starting_location" in v && !("frontmatter" in v);
}

/** Adapt a raw library row into the common resolved-row shape (#601). */
function libraryEntityToRow(e: LibraryEntity): ResolvedEntity {
  return {
    kind: e.kind,
    asset_id: e.asset_id,
    world_id: e.world_id,
    name: e.name || e.asset_id,
    frontmatter: e.frontmatter,
    body: e.body,
    source_chain: [
      {
        layer: "library_live",
        scope: "library",
        library_id: e.id,
        world_id: e.world_id,
        version: e.version,
        override_applied: false,
      },
    ],
    overrides_applied: [],
    extras: {},
  };
}

/** Greetings come back as typed models; their fields are the frontmatter. */
function greetingToRow(g: Greeting): ResolvedEntity {
  return {
    kind: "greeting",
    asset_id: g.id,
    world_id: g.world_id,
    name: g.name || g.id,
    frontmatter: {
      id: g.id,
      name: g.name,
      tags: g.tags,
      starting_location: g.starting_location,
      starting_time: g.starting_time,
      mood: g.mood,
    },
    body: g.body,
    source_chain: [
      {
        layer: "library_live",
        scope: "library",
        library_id: `worlds/${g.world_id}/greetings/${g.id}`,
        world_id: g.world_id,
        version: null,
        override_applied: false,
      },
    ],
    overrides_applied: [],
    extras: {},
  };
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
    dependents: CampaignRef[] | "loading";
    busy: boolean;
    err: string | null;
  } | null>(null);

  const isGreetingKind = kindPlural === "greetings";

  async function openDelete(entityId: string, entityName: string) {
    setDeleting({ entityId, entityName, dependents: "loading", busy: false, err: null });
    try {
      const deps = await libraryApi.dependents(worldId, kindPlural, entityId);
      setDeleting((d) => (d && d.entityId === entityId ? { ...d, dependents: deps } : d));
    } catch (err) {
      // A failed lookup is not "no dependents": keep confirm blocked
      // (dependents stays "loading") and say why.
      setDeleting((d) =>
        d && d.entityId === entityId
          ? { ...d, err: `Dependents lookup failed: ${errorMessage(err)}` }
          : d,
      );
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
        <EntityBrowser
          // Keyed by kind so filter state resets when the :kind route changes.
          key={kindPlural}
          rows={(data ?? []).map((e) => (isGreeting(e) ? greetingToRow(e) : libraryEntityToRow(e)))}
          kindPlural={kindPlural}
          scope="library"
          linkFor={(row) =>
            `/library/worlds/${encodeURIComponent(worldId)}/${kindPlural}/${encodeURIComponent(
              row.asset_id,
            )}`
          }
          actionsFor={(row) => {
            const name = row.name || row.asset_id;
            return [
              ...(kindPlural === "lore"
                ? [
                    {
                      key: "convert",
                      icon: <SwapIcon />,
                      label: `Convert ${name} to another category`,
                      align: "start",
                      onClick: () => setConvertingId(row.asset_id),
                    } satisfies CardIconAction,
                  ]
                : []),
              deleteAction({
                onClick: () => openDelete(row.asset_id, name),
                label: `Delete ${name}`,
              }),
            ];
          }}
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

export const ENTITY_KIND_LABELS = ENTITY_KIND_PLURAL;
