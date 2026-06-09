/**
 * Cast view (spec 14 §Cast view).
 *
 * The dramatis personae: PCs and emergent characters always, plus library
 * characters once they have appeared in a scene. The full resolved
 * composition lives in World → Characters. Each cast member gets a detail
 * panel showing resolved card, source chain, voice anchor with samples, and
 * mechanical sheet rendered through the widget library.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";

import { ApiError } from "../../api/client";
import { campaignApi, canonicalizeCharacterRef, characterRefFor } from "../../api/campaign";
import { viewsApi } from "../../api/views";
import type { ResolvedCharacter, WorldMeta } from "../../api/types";
import { useResource } from "../../api/useResource";
import { CardIconBar } from "../../components/CardIconBar";
import { deleteAction } from "../../components/cardActions";
import { CardFilters } from "../../components/CardFilters";
import { useCardFilters } from "../../hooks/useCardFilters";
import { Markdown } from "../../components/Markdown";
import { SheetRenderer } from "../../sheets";
import type { SheetValue } from "../../sheets/types";
import { AsyncSection } from "../../components/AsyncSection";
import { ChainBadge } from "./common";
import { ConfirmDestructiveDialog } from "../../components/ConfirmDestructiveDialog";
import { Dialog } from "../../components/Dialog";
import { useDestructiveConfirm } from "../../hooks/useDestructiveConfirm";

type SourceFilter = "all" | "library" | "emergent" | "override";

export function CastView() {
  const { campaignId = "" } = useParams();
  const state = useResource(useCallback(() => viewsApi.listCast(campaignId), [campaignId]));
  const composition = useResource(
    useCallback(() => viewsApi.getComposition(campaignId), [campaignId]),
  );
  const pcState = useResource(useCallback(() => campaignApi.listPCs(campaignId), [campaignId]));
  const moduleId = composition.data?.mechanics ?? null;

  // PCs are stored under whatever ref spelling the campaign wizard registered
  // (e.g. the `<world>/<id>` shorthand), while `refForRow` produces the canonical
  // form. Normalize both sides so the remove-PC action shows up regardless of
  // which spelling was stored (#517).
  const pcRefs = new Set(pcState.data?.map((p) => canonicalizeCharacterRef(p.character_ref)) ?? []);
  const refForRow = (r: ResolvedCharacter): string =>
    canonicalizeCharacterRef(characterRefFor(r.character));
  const removePc = useDestructiveConfirm<{ ref: string; name: string }>(async ({ ref }) => {
    await campaignApi.removePc(campaignId, ref);
    state.reload();
    pcState.reload();
  });

  const [searchParams] = useSearchParams();
  const [selectedId, setSelectedId] = useState<string | null>(searchParams.get("character"));
  const [initialRef] = useState(() => searchParams.get("ref"));

  return (
    <section className="route campaign-cast" aria-labelledby="cast-heading">
      <header className="route-header">
        <h2 id="cast-heading">Cast</h2>
      </header>
      {removePc.target && (
        <ConfirmDestructiveDialog
          open
          title={`Remove "${removePc.target.name}" as a player character?`}
          body={<p>The character itself is not deleted; it just stops being a PC.</p>}
          confirmLabel="Remove"
          busyLabel="Removing…"
          busy={removePc.busy}
          error={removePc.error}
          onConfirm={removePc.confirm}
          onCancel={removePc.cancel}
        />
      )}
      <AsyncSection
        state={state}
        emptyMessage="No cast yet. PCs and emergent characters join automatically; library characters join once they appear in a scene."
      >
        {(rows) => (
          <CastBody
            rows={rows}
            campaignId={campaignId}
            moduleId={moduleId}
            selectedId={selectedId}
            setSelectedId={setSelectedId}
            initialRef={initialRef}
            pcRefs={pcRefs}
            refForRow={refForRow}
            onRemovePc={removePc.request}
            onReload={() => state.reload()}
          />
        )}
      </AsyncSection>
    </section>
  );
}

interface CastBodyProps {
  rows: ResolvedCharacter[];
  campaignId: string;
  moduleId: string | null;
  selectedId: string | null;
  setSelectedId: (id: string) => void;
  initialRef: string | null;
  pcRefs: Set<string>;
  refForRow: (r: ResolvedCharacter) => string;
  onRemovePc: (target: { ref: string; name: string }) => void;
  onReload: () => void;
}

function CastBody({
  rows,
  campaignId,
  moduleId,
  selectedId,
  setSelectedId,
  initialRef,
  pcRefs,
  refForRow,
  onRemovePc,
  onReload,
}: CastBodyProps) {
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>("all");
  const [roleFilter, setRoleFilter] = useState<string>("all");
  const roles = collectRoles(rows);
  // Search + tag filtering goes through the app-standard CardFilters; the
  // cast-specific source/role facets apply on top.
  const {
    filtered: textFiltered,
    search,
    setSearch,
    selectedTags,
    toggleTag,
    clearTags,
    availableTags,
  } = useCardFilters(rows, {
    text: (r) => [r.character.name, r.character.id, r.character.description, r.character.body],
    tags: (r) => r.character.tags,
  });
  const filtered = textFiltered.filter((r) => matchesFacets(r, sourceFilter, roleFilter));
  const byId = filtered.find((r) => r.character.id === selectedId);
  const byRef = !byId && initialRef ? filtered.find((r) => refForRow(r) === initialRef) : undefined;
  const selected = byId ?? byRef ?? filtered[0] ?? null;
  return (
    <div className="cast-layout">
      <aside className="cast-list" aria-label="Character list">
        <CardFilters
          search={search}
          onSearch={setSearch}
          availableTags={availableTags}
          selectedTags={selectedTags}
          onToggleTag={toggleTag}
          onClearTags={clearTags}
          searchPlaceholder="Search cast by name, id, or text…"
          searchLabel="Search cast"
          resultSummary={
            filtered.length === rows.length
              ? `${rows.length} in cast`
              : `${filtered.length} of ${rows.length}`
          }
        />
        <div className="cast-filters">
          <label className="field">
            <span>Source</span>
            <select
              value={sourceFilter}
              onChange={(e) => setSourceFilter(e.target.value as SourceFilter)}
            >
              <option value="all">All</option>
              <option value="library">Library</option>
              <option value="emergent">Emergent</option>
              <option value="override">Has override</option>
            </select>
          </label>
          <label className="field">
            <span>Role</span>
            <select value={roleFilter} onChange={(e) => setRoleFilter(e.target.value)}>
              <option value="all">All</option>
              {roles.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </label>
        </div>
        <ul className="entity-list">
          {filtered.map((c) => (
            <li key={c.character.id} className="cast-entity">
              <button
                type="button"
                className={
                  selected?.character.id === c.character.id ? "entity-card active" : "entity-card"
                }
                onClick={() => setSelectedId(c.character.id)}
              >
                <div className="entity-card-head">
                  <span className="entity-name">{c.character.name}</span>
                  <ChainBadge chain={c.source_chain} overrides={c.overrides_applied} />
                </div>
                <small className="entity-meta">
                  {c.character.role} · {c.character.tags.slice(0, 3).join(", ")}
                </small>
              </button>
              <CardIconBar
                actions={
                  pcRefs.has(refForRow(c))
                    ? [
                        deleteAction({
                          onClick: () =>
                            onRemovePc({
                              ref: refForRow(c),
                              name: c.character.name,
                            }),
                          label: `Remove ${c.character.name} as PC`,
                        }),
                      ]
                    : []
                }
              />
            </li>
          ))}
          {filtered.length === 0 && (
            <li className="muted">No characters match the current filters.</li>
          )}
        </ul>
      </aside>
      <article className="cast-detail" aria-live="polite">
        {selected ? (
          <CharacterDetail
            campaignId={campaignId}
            moduleId={moduleId}
            character={selected}
            onReload={onReload}
          />
        ) : (
          <p className="muted">Select a character to see details.</p>
        )}
      </article>
    </div>
  );
}

interface CharacterDetailProps {
  campaignId: string;
  moduleId: string | null;
  character: ResolvedCharacter;
  onReload: () => void;
}

function CharacterDetail({ campaignId, moduleId, character: row, onReload }: CharacterDetailProps) {
  const navigate = useNavigate();
  const { character, source_chain, overrides_applied, capabilities } = row;
  const samples = character.voice.samples;
  const [sampleIdx, setSampleIdx] = useState(0);
  const currentSample = useMemo(() => {
    if (samples.length === 0) return null;
    return samples[sampleIdx % samples.length];
  }, [samples, sampleIdx]);

  const [overrideOpen, setOverrideOpen] = useState(false);
  const [promoteOpen, setPromoteOpen] = useState(false);

  const isEmergent = source_chain[0]?.layer === "emergent";

  const handleEditLibrary = () => {
    const worldId = character.world_id;
    if (!worldId) return;
    navigate(
      `/library/worlds/${encodeURIComponent(worldId)}/characters/${encodeURIComponent(character.id)}`,
    );
  };

  return (
    <div className="character-detail">
      <header>
        <h3>{character.name}</h3>
        <ChainBadge chain={source_chain} overrides={overrides_applied} />
      </header>
      <p className="muted">
        {character.role}
        {character.age && ` · age ${character.age}`}
        {character.world_id && ` · ${character.world_id}`}
      </p>

      {character.description && <p>{character.description}</p>}
      {character.body && (
        <details>
          <summary>Card body</summary>
          <Markdown>{character.body}</Markdown>
        </details>
      )}

      <section>
        <h4>Source chain</h4>
        <ol className="source-chain">
          {source_chain.map((src, i) => (
            <li key={i}>
              <ChainBadge chain={[src]} />
              {src.library_id && <code> {src.library_id}</code>}
            </li>
          ))}
          {source_chain.length === 0 && <li className="muted">Emergent only.</li>}
        </ol>
        {overrides_applied.length > 0 && (
          <p className="muted">
            Overrides applied: <code>{overrides_applied.join(", ")}</code>
          </p>
        )}
      </section>

      <section>
        <h4>Voice anchor</h4>
        {character.voice.summary ? (
          <p>{character.voice.summary}</p>
        ) : (
          <p className="muted">No voice summary recorded.</p>
        )}
        {character.voice.voice_register && (
          <p className="muted">Register: {character.voice.voice_register}</p>
        )}
        {samples.length > 0 && (
          <blockquote className="voice-sample">
            <p>“{currentSample}”</p>
            <button type="button" onClick={() => setSampleIdx((i) => i + 1)}>
              Next sample ({(sampleIdx % samples.length) + 1}/{samples.length})
            </button>
          </blockquote>
        )}
      </section>

      {capabilities.length > 0 && (
        <section>
          <h4>Capabilities</h4>
          <ul className="capability-list">
            {capabilities.map((cap, i) => (
              <li key={i}>
                <code>{JSON.stringify(cap)}</code>
              </li>
            ))}
          </ul>
        </section>
      )}

      {moduleId && (
        <CastMechanicalSheet
          campaignId={campaignId}
          moduleId={moduleId}
          characterId={character.id}
        />
      )}

      <section>
        <h4>Actions</h4>
        <div className="button-row">
          <button
            type="button"
            onClick={() => setOverrideOpen(true)}
            disabled={!character.world_id}
            title={
              character.world_id ? undefined : "Overrides only apply to library-backed characters."
            }
          >
            Edit override
          </button>
          <button
            type="button"
            onClick={handleEditLibrary}
            disabled={!character.world_id}
            title={
              character.world_id
                ? undefined
                : "Emergent characters live in the campaign, not the library."
            }
          >
            Edit library
          </button>
          {isEmergent && (
            <button type="button" onClick={() => setPromoteOpen(true)}>
              Promote to library
            </button>
          )}
        </div>
      </section>

      {overrideOpen && character.world_id && (
        <EditOverrideDialog
          campaignId={campaignId}
          characterId={character.id}
          worldId={character.world_id}
          onClose={() => setOverrideOpen(false)}
          onSaved={() => {
            setOverrideOpen(false);
            onReload();
          }}
        />
      )}
      {promoteOpen && isEmergent && (
        <PromoteToLibraryDialog
          campaignId={campaignId}
          characterId={character.id}
          onClose={() => setPromoteOpen(false)}
          onPromoted={() => {
            setPromoteOpen(false);
            onReload();
          }}
        />
      )}
    </div>
  );
}

interface CastMechanicalSheetProps {
  campaignId: string;
  moduleId: string;
  characterId: string;
}

function CastMechanicalSheet({ campaignId, moduleId, characterId }: CastMechanicalSheetProps) {
  // The sheet value is wrapped so a 404 ("entity has no sheet") stays
  // distinguishable from "not loaded yet" in the Resource shape.
  const sheet = useResource<{ value: Record<string, unknown> | null }>(
    useCallback(
      () =>
        viewsApi.getSheet(campaignId, "character", characterId).then(
          (value) => ({ value }),
          (err: unknown) => {
            if (err instanceof ApiError && err.status === 404) return { value: null };
            throw err;
          },
        ),
      [campaignId, characterId],
    ),
  );
  const schema = useResource(
    useCallback(() => viewsApi.getSheetSchema(moduleId, "character"), [moduleId]),
  );
  const theme = useResource(useCallback(() => viewsApi.getMechanicsThemeCss(moduleId), [moduleId]));

  if (sheet.data?.value === null) return null;
  if (sheet.error) return null;
  return (
    <section>
      <h4>Mechanical sheet</h4>
      {sheet.loading || schema.loading ? (
        <p className="muted">Loading sheet…</p>
      ) : sheet.data && schema.data ? (
        <SheetRenderer
          moduleId={moduleId}
          schema={schema.data}
          value={(sheet.data.value ?? {}) as SheetValue}
          themeCss={theme.data || undefined}
          onChange={() => {
            /* read-only */
          }}
          readOnly
        />
      ) : (
        <p className="muted">Sheet schema unavailable.</p>
      )}
    </section>
  );
}

interface EditOverrideDialogProps {
  campaignId: string;
  characterId: string;
  worldId: string;
  onClose: () => void;
  onSaved: () => void;
}

function EditOverrideDialog({
  campaignId,
  characterId,
  worldId,
  onClose,
  onSaved,
}: EditOverrideDialogProps) {
  const [text, setText] = useState("{}");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const save = useCallback(async () => {
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(text);
    } catch (err) {
      setError(`Invalid JSON: ${err instanceof Error ? err.message : String(err)}`);
      return;
    }
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
      setError("Override must be a JSON object.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await viewsApi.patchCharacterOverride(campaignId, characterId, {
        override: parsed,
        world_id: worldId,
      });
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [campaignId, characterId, worldId, text, onSaved]);

  return (
    <Dialog open onClose={onClose} title="Edit override">
      <p className="muted">
        Patch frontmatter for <code>{characterId}</code> in world <code>{worldId}</code>. Submitted
        as a JSON object; existing override is overwritten.
      </p>
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        rows={12}
        spellCheck={false}
        aria-label="Override JSON"
      />
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="modal-actions">
        <button type="button" onClick={onClose} disabled={busy}>
          Cancel
        </button>
        <button type="button" onClick={() => void save()} disabled={busy}>
          {busy ? "Saving…" : "Save override"}
        </button>
      </div>
    </Dialog>
  );
}

interface PromoteToLibraryDialogProps {
  campaignId: string;
  characterId: string;
  onClose: () => void;
  onPromoted: () => void;
}

function PromoteToLibraryDialog({
  campaignId,
  characterId,
  onClose,
  onPromoted,
}: PromoteToLibraryDialogProps) {
  const [worlds, setWorlds] = useState<WorldMeta[] | null>(null);
  const [target, setTarget] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    viewsApi
      .listWorlds()
      .then((rows) => {
        if (cancelled) return;
        setWorlds(rows);
        setTarget(rows[0]?.id ?? "");
      })
      .catch((err) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const promote = useCallback(async () => {
    if (!target) return;
    setBusy(true);
    setError(null);
    try {
      await viewsApi.promoteCharacterToLibrary(campaignId, characterId, {
        target_world_id: target,
        confirm: true,
      });
      onPromoted();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [campaignId, characterId, target, onPromoted]);

  return (
    <Dialog open onClose={onClose} title={`Promote ${characterId} to library`}>
      {worlds === null ? (
        <p className="muted">Loading worlds…</p>
      ) : worlds.length === 0 ? (
        <p className="error">No worlds available. Create a world first.</p>
      ) : (
        <label className="field">
          <span>Target world</span>
          <select value={target} onChange={(e) => setTarget(e.target.value)}>
            {worlds.map((w) => (
              <option key={w.id} value={w.id}>
                {w.name || w.id}
              </option>
            ))}
          </select>
        </label>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="modal-actions">
        <button type="button" onClick={onClose} disabled={busy}>
          Cancel
        </button>
        <button
          type="button"
          onClick={() => void promote()}
          disabled={busy || !target || worlds === null || worlds.length === 0}
        >
          {busy ? "Promoting…" : "Promote"}
        </button>
      </div>
    </Dialog>
  );
}

function collectRoles(rows: ResolvedCharacter[]): string[] {
  const set = new Set<string>();
  for (const r of rows) set.add(r.character.role);
  return [...set].sort();
}

function matchesFacets(r: ResolvedCharacter, source: SourceFilter, role: string): boolean {
  if (role !== "all" && r.character.role !== role) return false;
  if (source === "all") return true;
  const top = r.source_chain[0];
  const isLibrary = top ? top.layer === "library_live" || top.layer === "library_snapshot" : false;
  const isEmergent = !top || top.layer === "emergent";
  const hasOverride = r.overrides_applied.length > 0 || (top?.override_applied ?? false);
  if (source === "library") return isLibrary;
  if (source === "emergent") return isEmergent;
  return hasOverride;
}
