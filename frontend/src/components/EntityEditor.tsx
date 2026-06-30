import { useCallback, useEffect, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, type EntityKind, type EntitySummary } from "../api/client";
import { loreOwnerOptions, type LoreOwner } from "../api/loreOwners";
import { Field } from "./Field";
import { OwnedLorePanel } from "./OwnedLorePanel";

export function EntityEditor({ wid, kind, nav, onNavConsumed, onOpenOwner, onOpenLore }: {
  wid: string;
  kind: EntityKind;
  nav?: { focusEntry?: string; newOwner?: string } | null;
  onNavConsumed?: () => void;
  onOpenOwner?: (ref: string) => void;
  onOpenLore?: (nav: { focusEntry?: string; newOwner?: string }) => void;
}) {
  const scope = { kind: "world" as const, id: wid };
  const [items, setItems] = useState<EntitySummary[]>([]);
  const [editing, setEditing] = useState<string | null>(null); // entity id, or null = new
  const [name, setName] = useState("");
  const [body, setBody] = useState("");
  const [keys, setKeys] = useState("");
  const [owners, setOwners] = useState<string[]>([]);          // selected owner refs (lore only)
  const [ownerOpts, setOwnerOpts] = useState<LoreOwner[]>([]); // candidates for the picker
  const [mode, setMode] = useState<"view" | "edit">("edit"); // existing entries open read-only
  const [error, setError] = useState<string | null>(null);
  const label = kind === "lore" ? "lore entry" : "location";

  const reload = useCallback(() => api.listEntities(scope, kind).then(setItems), [wid, kind]);
  useEffect(() => {
    reload();
    resetForm();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wid, kind]);

  useEffect(() => {
    if (kind === "lore") loreOwnerOptions(wid).then(setOwnerOpts);
  }, [wid, kind]);

  const ownerLabel = useCallback(
    (ref: string) => ownerOpts.find((o) => o.ref === ref)?.label ?? ref,
    [ownerOpts],
  );

  // inbound navigation from an owner editor: open an entry, or start a new pre-owned entry.
  // Clear it via onNavConsumed so it doesn't leak into later manual "+ New" / re-entry.
  useEffect(() => {
    if (!nav) return;
    if (nav.focusEntry) {
      select(nav.focusEntry);
    } else {
      setEditing(null);
      setName("");
      setBody("");
      setKeys("");
      setOwners(nav.newOwner ? [nav.newOwner] : []);
      setMode("edit");
    }
    onNavConsumed?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nav]);

  function resetForm() {
    setEditing(null);
    setName("");
    setBody("");
    setKeys("");
    setOwners([]); // manual "+ New" / post-save: always world-level, never a stale nav owner
    setMode("edit"); // a brand-new entry goes straight to the form
  }

  async function select(id: string) {
    setError(null);
    const e = await api.readEntity(scope, kind, id);
    setEditing(id);
    setName(e.meta.name);
    setBody(e.body);
    setKeys(e.meta.keys ?? "");
    setOwners((e.meta.owners ?? "").split(",").map((o) => o.trim()).filter(Boolean));
    setMode("view");
  }

  async function save() {
    if (!name.trim()) return;
    setError(null);
    const ownerStr = owners.join(", ");
    try {
      if (editing) {
        await api.updateEntity(scope, kind, editing, { name, body, keys, owners: ownerStr });
        await reload();
        await select(editing); // back to the read-only view
      } else {
        await api.createEntity(scope, kind, { name, body, keys, owners: ownerStr });
        await reload();
        resetForm();
      }
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  async function remove(e: EntitySummary) {
    if (!window.confirm(`Delete '${e.name}'?`)) return;
    await api.deleteEntity(scope, kind, e.id);
    if (editing === e.id) resetForm();
    await reload();
  }

  const keyList = keys.split(",").map((k) => k.trim()).filter(Boolean);

  // Group lore rows: "Unowned (world)" first, then one group per distinct owner ref.
  const ownersOf = (e: EntitySummary) => (e.owners ?? "").split(",").map((o) => o.trim()).filter(Boolean);
  const groups: { key: string; label: string; rows: EntitySummary[] }[] = [];
  if (kind === "lore") {
    const unowned = items.filter((e) => ownersOf(e).length === 0);
    if (unowned.length) groups.push({ key: "", label: "Unowned (world)", rows: unowned });
    const seen = new Set<string>();
    for (const e of items) {
      for (const ref of ownersOf(e)) {
        if (seen.has(ref)) continue;
        seen.add(ref);
        groups.push({ key: ref, label: ownerLabel(ref), rows: items.filter((x) => ownersOf(x).includes(ref)) });
      }
    }
  }

  const row = (e: EntitySummary) => (
    <button key={e.id} className={"row" + (editing === e.id ? " active" : "")} onClick={() => select(e.id)}>
      {e.name}
    </button>
  );

  return (
    <div className="editor">
      <div className="editor-list">
        <button className="primary new" onClick={resetForm}>+ New {label}</button>
        {kind === "lore"
          ? groups.map((g) => (
              <div key={g.key} className="rail-group">
                <div className="rail-group-head">{g.label}</div>
                {g.rows.map(row)}
              </div>
            ))
          : items.map(row)}
        {items.length === 0 && <div className="editor-empty">No {kind} yet.</div>}
      </div>

      <div className="editor-body">
        {error && <div className="banner">{error}</div>}
        {mode === "view" && editing ? (
          <div className="detail-view">
            <div className="detail-main">
              <h3>{name}</h3>
              <div className="detail-rendered">
                <Markdown remarkPlugins={[remarkGfm]}>{body}</Markdown>
              </div>
            </div>
            <aside className="detail-sidebar">
              <div className="form-actions">
                <button className="subtle" onClick={() => setMode("edit")}>Edit</button>
              </div>
              <div className="side-section">
                <h4>Keys</h4>
                {keyList.length > 0
                  ? <div className="chips">{keyList.map((k) => <span key={k} className="chip on">{k}</span>)}</div>
                  : <div className="field-hint">always-on</div>}
              </div>
              {kind === "lore" && (
                <div className="side-section">
                  <h4>Owners</h4>
                  {owners.length > 0 ? (
                    <div className="chips">
                      {owners.map((ref) => (
                        <button key={ref} className="chip" onClick={() => onOpenOwner?.(ref)}>
                          {ownerLabel(ref)}
                        </button>
                      ))}
                    </div>
                  ) : (
                    <div className="field-hint">world-level</div>
                  )}
                </div>
              )}
              {kind === "locations" && editing && onOpenLore && (
                <OwnedLorePanel
                  wid={wid}
                  ownerRef={`locations:${editing}`}
                  onOpenEntry={(id) => onOpenLore({ focusEntry: id })}
                  onNewEntry={() => onOpenLore({ newOwner: `locations:${editing}` })}
                />
              )}
            </aside>
          </div>
        ) : (
          <div className="form">
            <h3>{editing ? `Edit ${label}` : `New ${label}`}</h3>
            <Field label="Name">
              <input type="text" value={name} onChange={(e) => setName(e.target.value)} />
            </Field>
            <Field label="Body">
              <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={10} />
            </Field>
            <Field label="Keys" hint="comma-separated activation triggers; blank = always-on">
              <input type="text" value={keys} onChange={(e) => setKeys(e.target.value)} />
            </Field>
            {kind === "lore" && (
              <Field label="Owners" hint="lore activates only when an owner is in the scene; none = world-level">
                <div className="chips owner-picker">
                  {ownerOpts.map((o) => (
                    <label key={o.ref} className="owner-option">
                      <input
                        type="checkbox"
                        aria-label={o.label}
                        checked={owners.includes(o.ref)}
                        onChange={(e) =>
                          setOwners(e.target.checked ? [...owners, o.ref] : owners.filter((r) => r !== o.ref))
                        }
                      />
                      {o.label}
                    </label>
                  ))}
                  {ownerOpts.length === 0 && <span className="field-hint">No characters, PCs, or locations yet.</span>}
                </div>
              </Field>
            )}
            <div className="form-actions">
              {editing && <button className="subtle" onClick={() => remove(items.find((x) => x.id === editing)!)}>Delete</button>}
              {editing && <button className="subtle" onClick={() => select(editing)}>Cancel</button>}
              <button className="primary" onClick={save} disabled={!name.trim()}>
                {editing ? "Save" : `Create ${label}`}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
