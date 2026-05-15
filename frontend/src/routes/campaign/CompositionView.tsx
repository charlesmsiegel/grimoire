/**
 * Composition view (spec 14 §Composition view).
 *
 * Editable list of world refs (priority, include filters, track_latest) +
 * the mechanics / style guide / image preset ids. Reorders mutate locally and
 * PUT the new composition to the backend; the upgrade-available banner
 * surfaces refs that drifted from their bound version and offers a one-click
 * upgrade per ref.
 */

import { useCallback, useState } from "react";
import { useParams } from "react-router-dom";

import { viewsApi } from "../../api/views";
import type { Composition, WorldRef, WorldMeta } from "../../api/types";
import { useApi } from "../../api/useApi";
import { Loading } from "./common";

const KINDS = ["characters", "items", "locations", "lore", "factions", "greetings"] as const;

export function CompositionView() {
  const { campaignId = "" } = useParams();
  const composition = useApi(() => viewsApi.getComposition(campaignId), [campaignId]);
  const worlds = useApi(() => viewsApi.listWorlds(), []);

  return (
    <section className="route campaign-composition" aria-labelledby="comp-heading">
      <header className="route-header">
        <h2 id="comp-heading">Composition</h2>
      </header>
      <Loading state={composition}>
        {(comp) => (
          <Loading state={worlds}>
            {(worldsList) => (
              <CompositionEditor campaignId={campaignId} initial={comp} catalog={worldsList} />
            )}
          </Loading>
        )}
      </Loading>
    </section>
  );
}

interface EditorProps {
  campaignId: string;
  initial: Composition;
  catalog: WorldMeta[];
}

function CompositionEditor({ campaignId, initial, catalog }: EditorProps) {
  const [comp, setComp] = useState<Composition>(initial);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const catalogById = new Map(catalog.map((s) => [s.id, s]));
  const upgrades = collectUpgrades(comp.worlds, catalogById);

  const mutate = useCallback((next: Composition) => {
    setComp(next);
    setDirty(true);
  }, []);

  const reorderedRefs = [...comp.worlds].sort((a, b) => a.priority - b.priority);

  const move = (idx: number, dir: -1 | 1) => {
    const target = idx + dir;
    if (target < 0 || target >= reorderedRefs.length) return;
    const swap = reorderedRefs.slice();
    const a = swap[idx];
    const b = swap[target];
    if (!a || !b) return;
    swap[idx] = b;
    swap[target] = a;
    const renumbered = swap.map((r, i) => ({ ...r, priority: i + 1 }));
    mutate({ ...comp, worlds: renumbered });
  };

  const remove = (worldId: string) => {
    const next = comp.worlds.filter((r) => r.world_id !== worldId);
    mutate({ ...comp, worlds: renumber(next) });
  };

  const update = (worldId: string, patch: Partial<WorldRef>) => {
    mutate({
      ...comp,
      worlds: comp.worlds.map((r) => (r.world_id === worldId ? { ...r, ...patch } : r)),
    });
  };

  const addRef = (worldId: string) => {
    if (comp.worlds.some((r) => r.world_id === worldId)) return;
    const meta = catalogById.get(worldId);
    const next: WorldRef = {
      world_id: worldId,
      priority: comp.worlds.length + 1,
      include: [],
      bound_at_version: meta?.version ?? 0,
      track_latest: false,
    };
    mutate({ ...comp, worlds: [...comp.worlds, next] });
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const updated = await viewsApi.setComposition(campaignId, comp);
      setComp(updated);
      setDirty(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const upgrade = async (worldId: string) => {
    setError(null);
    try {
      await viewsApi.upgradeRef(campaignId, worldId);
      const refreshed = await viewsApi.getComposition(campaignId);
      setComp(refreshed);
      setDirty(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const addable = catalog.filter((s) => !comp.worlds.some((r) => r.world_id === s.id));

  return (
    <div className="composition-editor">
      {upgrades.length > 0 && (
        <aside className="upgrade-banner" role="status">
          <h3>Upgrade available</h3>
          <ul>
            {upgrades.map((u) => (
              <li key={u.world_id}>
                <strong>{u.world_id}</strong> has new version {u.latest} (currently v{u.bound}).
                <button type="button" disabled title="Diff preview ships in a follow-up task.">
                  Preview diff
                </button>
                <button type="button" onClick={() => upgrade(u.world_id)}>
                  Upgrade
                </button>
              </li>
            ))}
          </ul>
        </aside>
      )}

      <section>
        <h3>Worlds (priority order)</h3>
        <ol className="world-refs">
          {reorderedRefs.map((ref, idx) => (
            <li key={ref.world_id} className="world-ref">
              <div className="world-ref-head">
                <span className="priority">{idx + 1}.</span>
                <strong>{ref.world_id}</strong>
                <span className="muted">
                  v{ref.bound_at_version}
                  {ref.track_latest ? " · track_latest" : " · pinned"}
                </span>
                <div className="row-actions">
                  <button
                    type="button"
                    aria-label="Move up"
                    onClick={() => move(idx, -1)}
                    disabled={idx === 0}
                  >
                    ▲
                  </button>
                  <button
                    type="button"
                    aria-label="Move down"
                    onClick={() => move(idx, 1)}
                    disabled={idx === reorderedRefs.length - 1}
                  >
                    ▼
                  </button>
                  <button type="button" aria-label="Remove" onClick={() => remove(ref.world_id)}>
                    ⨯
                  </button>
                </div>
              </div>
              <IncludeEditor worldRef={ref} onChange={(patch) => update(ref.world_id, patch)} />
            </li>
          ))}
        </ol>
        {addable.length > 0 ? (
          <label className="field">
            <span>Add world ref</span>
            <select
              value=""
              onChange={(e) => {
                if (e.target.value) addRef(e.target.value);
              }}
            >
              <option value="">— select —</option>
              {addable.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name} ({s.id})
                </option>
              ))}
            </select>
          </label>
        ) : (
          <p className="muted">All known worlds are already in this composition.</p>
        )}
      </section>

      <section className="composition-extras">
        <label className="field">
          <span>Mechanics module</span>
          <input
            type="text"
            value={comp.mechanics ?? ""}
            placeholder="e.g. wod-mechanics or empty for none"
            onChange={(e) => mutate({ ...comp, mechanics: e.target.value || null })}
          />
        </label>
        <label className="field">
          <span>Style guide</span>
          <input
            type="text"
            value={comp.style_guide_id ?? ""}
            onChange={(e) => mutate({ ...comp, style_guide_id: e.target.value || null })}
          />
        </label>
        <label className="field">
          <span>Image preset</span>
          <input
            type="text"
            value={comp.image_preset_id ?? ""}
            onChange={(e) => mutate({ ...comp, image_preset_id: e.target.value || null })}
          />
        </label>
      </section>

      <div className="composition-actions">
        <button type="button" className="primary" disabled={!dirty || saving} onClick={save}>
          {saving ? "Saving…" : dirty ? "Save changes" : "No changes"}
        </button>
        {dirty && (
          <button
            type="button"
            onClick={() => {
              setComp(initial);
              setDirty(false);
            }}
          >
            Discard
          </button>
        )}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
      </div>
    </div>
  );
}

function IncludeEditor({
  worldRef,
  onChange,
}: {
  worldRef: WorldRef;
  onChange: (patch: Partial<WorldRef>) => void;
}) {
  const all = worldRef.include.length === 0;
  const toggle = (kind: string) => {
    if (all) {
      onChange({ include: KINDS.filter((k) => k !== kind) });
      return;
    }
    const next = worldRef.include.includes(kind)
      ? worldRef.include.filter((k) => k !== kind)
      : [...worldRef.include, kind];
    onChange({ include: next });
  };
  return (
    <div className="include-editor">
      <label className="field-inline">
        <input
          type="checkbox"
          checked={all}
          onChange={(e) => onChange({ include: e.target.checked ? [] : [...KINDS] })}
        />
        include all
      </label>
      {!all && (
        <ul className="include-kinds">
          {KINDS.map((k) => (
            <li key={k}>
              <label className="field-inline">
                <input
                  type="checkbox"
                  checked={worldRef.include.includes(k)}
                  onChange={() => toggle(k)}
                />
                {k}
              </label>
            </li>
          ))}
        </ul>
      )}
      <label className="field-inline">
        <input
          type="checkbox"
          checked={worldRef.track_latest}
          onChange={(e) => onChange({ track_latest: e.target.checked })}
        />
        track_latest (auto-pull library updates)
      </label>
    </div>
  );
}

function renumber(refs: WorldRef[]): WorldRef[] {
  return refs.map((r, i) => ({ ...r, priority: i + 1 }));
}

interface UpgradeHint {
  world_id: string;
  bound: number;
  latest: number;
}

function collectUpgrades(refs: WorldRef[], catalogById: Map<string, WorldMeta>): UpgradeHint[] {
  const out: UpgradeHint[] = [];
  for (const ref of refs) {
    if (ref.track_latest) continue;
    const meta = catalogById.get(ref.world_id);
    if (!meta) continue;
    if (meta.version > ref.bound_at_version) {
      out.push({
        world_id: ref.world_id,
        bound: ref.bound_at_version,
        latest: meta.version,
      });
    }
  }
  return out;
}
