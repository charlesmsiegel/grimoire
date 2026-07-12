import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type WorldMeta } from "../api/client";

function footerLabel(counts: Record<string, number> | undefined): string {
  const c = counts ?? {};
  const chars = (c.characters ?? 0) + (c.pcs ?? 0);
  const parts = [`${c.locations ?? 0} LOCATIONS`, `${chars} CHARACTERS`, `${c.lore ?? 0} LORE`];
  for (const [key, label] of [["items", "ITEMS"], ["groups", "GROUPS"], ["creatures", "CREATURES"]] as const) {
    if (c[key]) parts.push(`${c[key]} ${label}`);
  }
  return parts.join(" · ");
}

export default function WorldsView() {
  const navigate = useNavigate();
  const [worlds, setWorlds] = useState<WorldMeta[]>([]);
  const [name, setName] = useState("");
  const [renaming, setRenaming] = useState<{ id: string; name: string } | null>(null);

  useEffect(() => {
    api.listWorlds().then(setWorlds);
  }, []);

  async function create() {
    const trimmed = name.trim();
    if (!trimmed) return;
    await api.createWorld(trimmed);
    setName("");
    setWorlds(await api.listWorlds());
  }

  async function rename() {
    if (!renaming) return;
    await api.renameWorld(renaming.id, renaming.name);
    setRenaming(null);
    setWorlds(await api.listWorlds());
  }

  async function remove(w: WorldMeta) {
    if (!window.confirm(`Delete world '${w.name}'?`)) return;
    try {
      await api.deleteWorld(w.id);
    } catch (err: any) {
      window.alert(err?.message ?? "Could not delete the world.");
      return;
    }
    setWorlds(await api.listWorlds());
  }

  return (
    <div className="page view-anim">
      <div className="page-head">
        <h1 className="page-h1">Worlds</h1>
        <div className="joined">
          <input
            placeholder="World name…" aria-label="World name"
            value={name} onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") create(); }}
          />
          <button className="btn-accent" onClick={create} disabled={!name.trim()}>Create</button>
        </div>
      </div>
      <div className="count-label">{worlds.length} {worlds.length === 1 ? "world" : "worlds"}</div>
      <div className="world-grid">
        {worlds.map((w) => (
          <div className="world-card" key={w.id}>
            {renaming?.id === w.id ? (
              <input
                className="row-rename" aria-label="Rename world" autoFocus
                value={renaming.name}
                onChange={(e) => setRenaming({ id: w.id, name: e.target.value })}
                onKeyDown={(e) => { if (e.key === "Enter") rename(); if (e.key === "Escape") setRenaming(null); }}
              />
            ) : (
              <button className="world-card-main" onClick={() => navigate(`/worlds/${w.id}`)}>
                <h3>{w.name}</h3>
                <footer>{footerLabel(w.counts)}</footer>
              </button>
            )}
            <div className="row-actions">
              <button aria-label={`Rename ${w.name}`} onClick={() => setRenaming({ id: w.id, name: w.name })}>✎</button>
              <button aria-label={`Delete ${w.name}`} onClick={() => remove(w)}>✕</button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
