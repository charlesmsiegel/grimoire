import { useEffect, useState } from "react";
import { api, type WorldMeta } from "../api/client";
import { EditableRow } from "../components/EditableRow";

function countLabel(counts: Record<string, number> | undefined): string {
  const total = Object.values(counts ?? {}).reduce((a, b) => a + b, 0);
  return total === 1 ? "1 entity" : `${total} entities`;
}

export default function WorldsView() {
  const [worlds, setWorlds] = useState<WorldMeta[]>([]);
  const [name, setName] = useState("");

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

  async function rename(id: string, next: string) {
    await api.renameWorld(id, next);
    setWorlds(await api.listWorlds());
  }

  async function remove(w: WorldMeta) {
    if (!window.confirm(`Delete world '${w.name}'? Campaigns already made from it keep their copies.`)) return;
    await api.deleteWorld(w.id);
    setWorlds(await api.listWorlds());
  }

  return (
    <div className="view">
      <h2>Worlds</h2>

      <div className="picker">
        <input placeholder="World name…" value={name} onChange={(e) => setName(e.target.value)} />
        <button className="primary" onClick={create} disabled={!name.trim()}>
          Create world
        </button>
      </div>

      <div className="list">
        {worlds.map((w) => (
          <EditableRow
            key={w.id}
            label={w.name}
            subtitle={countLabel(w.counts)}
            onRename={(next) => rename(w.id, next)}
            onDelete={() => remove(w)}
          />
        ))}
      </div>
    </div>
  );
}
