/**
 * Cast view (spec 14 §Cast view).
 *
 * Grid of resolved characters with filters by source and role plus a detail
 * panel showing resolved card, source chain, voice anchor with samples, and
 * mechanical sheet rendered through the widget library.
 */

import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";

import { viewsApi } from "../../api/views";
import type { ResolvedCharacter } from "../../api/types";
import { useApi } from "../../api/useApi";
import { Markdown } from "../../components/Markdown";
import { ChainBadge, Loading } from "./common";

type SourceFilter = "all" | "library" | "emergent" | "override";

export function CastView() {
  const { campaignId = "" } = useParams();
  const state = useApi(() => viewsApi.listCharacters(campaignId), [campaignId]);

  const [sourceFilter, setSourceFilter] = useState<SourceFilter>("all");
  const [roleFilter, setRoleFilter] = useState<string>("all");
  const [tagFilter, setTagFilter] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  return (
    <section className="route campaign-cast" aria-labelledby="cast-heading">
      <header className="route-header">
        <h2 id="cast-heading">Cast</h2>
      </header>
      <Loading state={state} emptyMessage="No characters resolved for this campaign yet.">
        {(rows) => {
          const roles = collectRoles(rows);
          const filtered = applyFilters(rows, sourceFilter, roleFilter, tagFilter);
          const selected =
            filtered.find((r) => r.character.id === selectedId) ?? filtered[0] ?? null;
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
                  <CharacterDetail character={selected} />
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
}

function Filters({ source, onSource, role, roles, onRole, tag, onTag }: FiltersProps) {
  return (
    <div className="cast-filters">
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

function CharacterDetail({ character: row }: { character: ResolvedCharacter }) {
  const { character, source_chain, overrides_applied, capabilities } = row;
  const samples = character.voice.samples;
  const [sampleIdx, setSampleIdx] = useState(0);
  const currentSample = useMemo(() => {
    if (samples.length === 0) return null;
    return samples[sampleIdx % samples.length];
  }, [samples, sampleIdx]);

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

      <section>
        <h4>Actions</h4>
        <div className="button-row">
          <button type="button" disabled title="Wired in a follow-up task.">
            Edit override
          </button>
          <button type="button" disabled title="Wired in a follow-up task.">
            Edit library
          </button>
          {!character.world_id && (
            <button type="button" disabled title="Wired in a follow-up task.">
              Promote to library
            </button>
          )}
        </div>
      </section>
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
): ResolvedCharacter[] {
  const tagLower = tag.trim().toLowerCase();
  return rows.filter((r) => {
    if (role !== "all" && r.character.role !== role) return false;
    if (tagLower && !r.character.tags.some((t) => t.toLowerCase().includes(tagLower))) {
      return false;
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
