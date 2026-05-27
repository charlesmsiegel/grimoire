/**
 * Cast view (spec 14 §Cast view).
 *
 * Grid of resolved characters with filters by source and role plus a detail
 * panel showing resolved card, source chain, voice anchor with samples, and
 * mechanical sheet rendered through the widget library.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";

import { ApiError } from "../../api/client";
import { viewsApi } from "../../api/views";
import type { ResolvedCharacter, WorldMeta } from "../../api/types";
import { useApi } from "../../api/useApi";
import { Markdown } from "../../components/Markdown";
import { SheetRenderer } from "../../sheets";
import type { SheetSchema, SheetValue } from "../../sheets/types";
import { ChainBadge, Loading } from "./common";

type SourceFilter = "all" | "library" | "emergent" | "override";

export function CastView() {
  const { campaignId = "" } = useParams();
  const state = useApi(useCallback(() => viewsApi.listCharacters(campaignId), [campaignId]));
  const composition = useApi(
    useCallback(() => viewsApi.getComposition(campaignId), [campaignId]),
  );
  const moduleId = composition.status === "ok" ? composition.data.mechanics : null;

  const [sourceFilter, setSourceFilter] = useState<SourceFilter>("all");
  const [roleFilter, setRoleFilter] = useState<string>("all");
  const [tagFilter, setTagFilter] = useState("");
  const [searchFilter, setSearchFilter] = useState("");
  const [searchParams] = useSearchParams();
  const [selectedId, setSelectedId] = useState<string | null>(
    searchParams.get("character"),
  );
  const [initialRef] = useState(() => searchParams.get("ref"));

  return (
    <section className="route campaign-cast" aria-labelledby="cast-heading">
      <header className="route-header">
        <h2 id="cast-heading">Cast</h2>
      </header>
      <Loading state={state} emptyMessage="No characters resolved for this campaign yet.">
        {(rows) => {
          const roles = collectRoles(rows);
          const filtered = applyFilters(rows, sourceFilter, roleFilter, tagFilter, searchFilter);
          const byId = filtered.find((r) => r.character.id === selectedId);
          const byRef =
            !byId && initialRef
              ? filtered.find((r) => {
                  const ref =
                    r.character.world_id !== null
                      ? `library:worlds/${r.character.world_id}/characters/${r.character.id}`
                      : `campaign:emergent/character/${r.character.id}`;
                  return ref === initialRef;
                })
              : undefined;
          const selected = byId ?? byRef ?? filtered[0] ?? null;
          return (
            <div className="cast-layout">
              <aside className="cast-list" aria-label="Character list">
                <Filters
                  source={sourceFilter}
                  onSource={setSourceFilter}
                  role={roleFilter}
                  roles={roles}
                  onRole={setRoleFilter}
                  tag={tagFilter}
                  onTag={setTagFilter}
                  search={searchFilter}
                  onSearch={setSearchFilter}
                />
                <ul className="entity-list">
                  {filtered.map((c) => (
                    <li key={c.character.id}>
                      <button
                        type="button"
                        className={
                          selected?.character.id === c.character.id
                            ? "entity-card active"
                            : "entity-card"
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
                    onReload={() => state.reload()}
                  />
                ) : (
                  <p className="muted">Select a character to see details.</p>
                )}
              </article>
            </div>
          );
        }}
      </Loading>
    </section>
  );
}

interface FiltersProps {
  source: SourceFilter;
  onSource: (s: SourceFilter) => void;
  role: string;
  roles: string[];
  onRole: (r: string) => void;
  tag: string;
  onTag: (t: string) => void;
  search: string;
  onSearch: (q: string) => void;
}

function Filters({
  source,
  onSource,
  role,
  roles,
  onRole,
  tag,
  onTag,
  search,
  onSearch,
}: FiltersProps) {
  return (
    <div className="cast-filters">
      <label className="field">
        <span>Search</span>
        <input
          type="search"
          value={search}
          onChange={(e) => onSearch(e.target.value)}
          placeholder="Name, id, description…"
          aria-label="Search characters"
        />
      </label>
      <label className="field">
        <span>Source</span>
        <select value={source} onChange={(e) => onSource(e.target.value as SourceFilter)}>
          <option value="all">All</option>
          <option value="library">Library</option>
          <option value="emergent">Emergent</option>
          <option value="override">Has override</option>
        </select>
      </label>
      <label className="field">
        <span>Role</span>
        <select value={role} onChange={(e) => onRole(e.target.value)}>
          <option value="all">All</option>
          {roles.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        <span>Tag contains</span>
        <input type="text" value={tag} onChange={(e) => onTag(e.target.value)} />
      </label>
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
              character.world_id ? undefined : "Emergent characters live in the campaign, not the library."
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
  const sheet = useApi<Record<string, unknown> | null>(
    useCallback(
      () =>
        viewsApi.getSheet(campaignId, "character", characterId).catch((err: unknown) => {
          if (err instanceof ApiError && err.status === 404) return null;
          throw err;
        }),
      [campaignId, characterId],
    ),
  );
  const schema = useApi(
    useCallback(() => viewsApi.getSheetSchema(moduleId, "character"), [moduleId]),
  );
  const theme = useApi(useCallback(() => viewsApi.getMechanicsThemeCss(moduleId), [moduleId]));

  if (sheet.status === "ok" && sheet.data === null) return null;
  if (sheet.status === "error") return null;
  return (
    <section>
      <h4>Mechanical sheet</h4>
      {sheet.status === "loading" || schema.status === "loading" ? (
        <p className="muted">Loading sheet…</p>
      ) : sheet.status === "ok" && schema.status === "ok" ? (
        <SheetRenderer
          moduleId={moduleId}
          schema={schema.data as unknown as SheetSchema}
          value={(sheet.data ?? {}) as SheetValue}
          themeCss={theme.status === "ok" ? theme.data || undefined : undefined}
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
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="edit-override">
      <div className="modal">
        <h4 id="edit-override">Edit override</h4>
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
      </div>
    </div>
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
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="promote-char">
      <div className="modal">
        <h4 id="promote-char">Promote {characterId} to library</h4>
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
      </div>
    </div>
  );
}

function collectRoles(rows: ResolvedCharacter[]): string[] {
  const set = new Set<string>();
  for (const r of rows) set.add(r.character.role);
  return [...set].sort();
}

function applyFilters(
  rows: ResolvedCharacter[],
  source: SourceFilter,
  role: string,
  tag: string,
  search: string,
): ResolvedCharacter[] {
  const tagLower = tag.trim().toLowerCase();
  const searchLower = search.trim().toLowerCase();
  return rows.filter((r) => {
    if (role !== "all" && r.character.role !== role) return false;
    if (tagLower && !r.character.tags.some((t) => t.toLowerCase().includes(tagLower))) {
      return false;
    }
    if (searchLower) {
      const c = r.character;
      const haystack = [c.name, c.id, c.description, c.body]
        .filter((s): s is string => typeof s === "string" && s.length > 0)
        .join(" ")
        .toLowerCase();
      if (!haystack.includes(searchLower)) return false;
    }
    if (source === "all") return true;
    const top = r.source_chain[0];
    const isLibrary = top
      ? top.layer === "library_live" || top.layer === "library_snapshot"
      : false;
    const isEmergent = !top || top.layer === "emergent";
    const hasOverride = r.overrides_applied.length > 0 || (top?.override_applied ?? false);
    if (source === "library") return isLibrary;
    if (source === "emergent") return isEmergent;
    if (source === "override") return hasOverride;
    return true;
  });
}
