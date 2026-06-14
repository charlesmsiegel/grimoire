/**
 * Composition view (spec 14 §Composition view).
 *
 * Editable list of world refs (priority, include filters, track_latest) +
 * the mechanics / style guide / image preset ids. Reorders mutate locally and
 * PUT the new composition to the backend; the upgrade-available banner
 * surfaces refs that drifted from their bound version and offers a one-click
 * upgrade per ref plus a preview-diff modal.
 *
 * Reorder uses native HTML5 drag-and-drop. The ▲/▼ buttons remain as a
 * keyboard-accessible fallback.
 *
 * Mechanics / style guide / image preset selectors render as dropdowns
 * populated from the existing catalog endpoints.
 */

import { useCallback, useState } from "react";
import { useParams } from "react-router-dom";

import { viewsApi } from "../../api/views";
import type {
  Composition,
  RegisteredMechanicsModule,
  WorldDiff,
  WorldRef,
  WorldMeta,
} from "../../api/types";
import { useResource } from "../../api/useResource";
import { Dialog, DialogClose } from "../../components/Dialog";
import { AsyncSection } from "../../components/AsyncSection";

const KINDS = [
  "characters",
  "items",
  "locations",
  "lore",
  "factions",
  "greetings",
  "monsters",
] as const;

interface CatalogOption {
  id: string;
  name: string;
}

export function CompositionView() {
  const { campaignId = "" } = useParams();
  const composition = useResource(
    useCallback(() => viewsApi.getComposition(campaignId), [campaignId]),
  );
  const worlds = useResource(useCallback(() => viewsApi.listWorlds(), []));
  const mechanics = useResource(useCallback(() => viewsApi.installedMechanics(), []));
  const styleGuides = useResource(useCallback(() => viewsApi.listStyleGuides(), []));
  const imagePresets = useResource(useCallback(() => viewsApi.listImagePresets(), []));

  return (
    <section className="route campaign-composition" aria-labelledby="comp-heading">
      <header className="route-header">
        <h2 id="comp-heading">Composition</h2>
      </header>
      <AsyncSection state={composition}>
        {(comp) => (
          <AsyncSection state={worlds}>
            {(worldsList) => (
              <CompositionEditor
                campaignId={campaignId}
                initial={comp}
                catalog={worldsList}
                mechanicsList={mechanics.data ?? []}
                styleGuides={styleGuides.data?.map(asOption) ?? []}
                imagePresets={imagePresets.data?.map(asOption) ?? []}
              />
            )}
          </AsyncSection>
        )}
      </AsyncSection>
    </section>
  );
}

function asOption(row: { id?: string; asset_id?: string; name?: string }): CatalogOption {
  const id = row.asset_id ?? row.id ?? "";
  return { id, name: row.name ?? id };
}

interface EditorProps {
  campaignId: string;
  initial: Composition;
  catalog: WorldMeta[];
  mechanicsList: RegisteredMechanicsModule[];
  styleGuides: CatalogOption[];
  imagePresets: CatalogOption[];
}

function CompositionEditor({
  campaignId,
  initial,
  catalog,
  mechanicsList,
  styleGuides,
  imagePresets,
}: EditorProps) {
  const [comp, setComp] = useState<Composition>(initial);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [diffOpenFor, setDiffOpenFor] = useState<UpgradeHint | null>(null);
  const [dragIndex, setDragIndex] = useState<number | null>(null);

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
    reorder(idx, target);
  };

  const reorder = (from: number, to: number) => {
    if (from === to) return;
    const swap = reorderedRefs.slice();
    const [picked] = swap.splice(from, 1);
    if (!picked) return;
    swap.splice(to, 0, picked);
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
      include: null,
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
                <button type="button" onClick={() => setDiffOpenFor(u)}>
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
            <li
              key={ref.world_id}
              className={dragIndex === idx ? "world-ref world-ref-dragging" : "world-ref"}
              draggable
              onDragStart={(e) => {
                setDragIndex(idx);
                e.dataTransfer.effectAllowed = "move";
                // Some browsers require setData for the drag to register.
                e.dataTransfer.setData("text/plain", ref.world_id);
              }}
              onDragOver={(e) => {
                e.preventDefault();
                e.dataTransfer.dropEffect = "move";
              }}
              onDrop={(e) => {
                e.preventDefault();
                if (dragIndex === null || dragIndex === idx) {
                  setDragIndex(null);
                  return;
                }
                reorder(dragIndex, idx);
                setDragIndex(null);
              }}
              onDragEnd={() => setDragIndex(null)}
            >
              <div className="world-ref-head">
                <span className="drag-handle" aria-hidden="true" title="Drag to reorder">
                  ⠿
                </span>
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
                  {/* eslint-disable-next-line local/no-bespoke-delete -- composition ref remover, not a card */}
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
          <label className="form-field field">
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
        <label className="form-field field">
          <span>Mechanics module</span>
          <select
            value={comp.mechanics ?? ""}
            onChange={(e) => mutate({ ...comp, mechanics: e.target.value || null })}
          >
            <option value="">— none —</option>
            {mechanicsList.map((m) => (
              <option key={m.manifest.id} value={m.manifest.id}>
                {m.manifest.name} ({m.manifest.id})
              </option>
            ))}
          </select>
        </label>
        <label className="form-field field">
          <span>Style guide</span>
          <select
            value={comp.style_guide_id ?? ""}
            onChange={(e) => mutate({ ...comp, style_guide_id: e.target.value || null })}
          >
            <option value="">— none —</option>
            {styleGuides.map((g) => (
              <option key={g.id} value={g.id}>
                {g.name}
              </option>
            ))}
          </select>
        </label>
        <label className="form-field field">
          <span>Image preset</span>
          <select
            value={comp.image_preset_id ?? ""}
            onChange={(e) => mutate({ ...comp, image_preset_id: e.target.value || null })}
          >
            <option value="">— none —</option>
            {imagePresets.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
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

      {diffOpenFor && <DiffPreviewModal hint={diffOpenFor} onClose={() => setDiffOpenFor(null)} />}
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
  // null/missing = include every kind; an explicit list (even []) is literal
  // — `[]` means "include nothing from this world" (types/composition.py).
  const all = worldRef.include === null;
  const toggle = (kind: string) => {
    if (all) {
      onChange({ include: KINDS.filter((k) => k !== kind) });
      return;
    }
    const current = worldRef.include ?? [];
    const next = current.includes(kind) ? current.filter((k) => k !== kind) : [...current, kind];
    onChange({ include: next });
  };
  return (
    <div className="include-editor">
      <label className="field-inline">
        <input
          type="checkbox"
          checked={all}
          onChange={(e) => onChange({ include: e.target.checked ? null : [...KINDS] })}
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
                  checked={(worldRef.include ?? []).includes(k)}
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

function DiffPreviewModal({ hint, onClose }: { hint: UpgradeHint; onClose: () => void }) {
  const state = useResource(
    useCallback(
      () => viewsApi.worldDiff(hint.world_id, hint.bound, hint.latest),
      [hint.world_id, hint.bound, hint.latest],
    ),
  );

  return (
    <Dialog
      open
      onClose={onClose}
      title={`Diff: ${hint.world_id} v${hint.bound} → v${hint.latest}`}
      panelClassName="diff-modal"
    >
      <DialogClose />
      <div className="diff-body">
        <AsyncSection state={state}>{(diff) => <DiffRenderer diff={diff} />}</AsyncSection>
      </div>
    </Dialog>
  );
}

function DiffRenderer({ diff }: { diff: WorldDiff }) {
  const nothing = diff.added.length === 0 && diff.removed.length === 0 && diff.changed.length === 0;
  if (nothing) {
    return <p className="muted">No changes detected between these versions.</p>;
  }
  return (
    <div className="diff-sections">
      {diff.added.length > 0 && (
        <section>
          <h4>Added ({diff.added.length})</h4>
          <ul>
            {diff.added.map((path) => (
              <li key={path}>{path}</li>
            ))}
          </ul>
        </section>
      )}
      {diff.removed.length > 0 && (
        <section>
          <h4>Removed ({diff.removed.length})</h4>
          <ul>
            {diff.removed.map((path) => (
              <li key={path}>{path}</li>
            ))}
          </ul>
        </section>
      )}
      {diff.changed.length > 0 && (
        <section>
          <h4>Changed ({diff.changed.length})</h4>
          <ul className="diff-changed">
            {diff.changed.map((change) => (
              <li key={change.path}>
                <div className="diff-change-head">
                  <strong>{change.path}</strong>
                </div>
                <div className="diff-sides">
                  <div className="diff-side diff-before">
                    <h5>Before</h5>
                    <pre>
                      {change.before === null
                        ? "(not available — only the latest entity content is retained)"
                        : JSON.stringify(change.before, null, 2)}
                    </pre>
                  </div>
                  <div className="diff-side diff-after">
                    <h5>After</h5>
                    <pre>
                      {change.after === null ? "(removed)" : JSON.stringify(change.after, null, 2)}
                    </pre>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}
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
