import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type WorldMeta } from "../api/client";
import { errorText } from "../api/errors";
import LibraryPage from "../components/LibraryPage";

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
  const [importing, setImporting] = useState(false);
  const [forking, setForking] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

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

  /** Fork a world into a copy of its own, from the card of the world to copy.
   *
   *  The grid is refreshed rather than navigated to: `listWorlds` orders by
   *  `updated` and the fork stamps its own, so the copy lands at the front of
   *  the grid the user is already looking at. An import navigates instead
   *  because it has no such anchor — nothing on screen said where it came
   *  from.
   *
   *  Reported, not dropped, and with the row's button disabled meanwhile: a
   *  world runs to a gigabyte of character art, so this is a request that can
   *  take real time, and a second click on a pending one would make a second
   *  copy. `rename` and `remove` above let a rejection become an unhandled
   *  promise — the card simply does not change, which reads as "nothing
   *  happened" and is near enough true for them. It is not true here.
   */
  async function fork(w: WorldMeta) {
    const name = window.prompt(`Fork '${w.name}' as?`, `${w.name} (fork)`)?.trim();
    if (!name) return;
    setError(null);
    setForking(w.id);
    try {
      await api.forkWorld(w.id, name);
    } catch (err) {
      // `errorText` rather than this file's older `catch (err: any)`: the
      // `any` bought nothing and cost the compiler's check on the rest of the
      // handler (see `api/errors.ts`).
      setError(`'${w.name}' could not be forked: ${errorText(err)}`);
      return;
    } finally {
      setForking(null);
    }
    setWorlds(await api.listWorlds());
  }

  // An import always lands as a NEW world, so the grid is sorted by the
  // imported world's own `updated` stamp and it can appear anywhere in it --
  // navigating there is what makes the import visible.
  async function onImportFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow re-picking the same file later
    if (!file) return;
    setError(null);
    setImporting(true);
    let imported: string;
    try {
      ({ id: imported } = await api.importWorld(file));
    } catch (err: any) {
      setError(err?.detail ?? err?.message ?? "Could not import that bundle.");
      setImporting(false);
      return;
    }
    // Receiving the id IS the commit point: the world exists from here on.
    // A failing refresh below must not be reported as a failed import, or the
    // user retries and imports a second copy (Codex review).
    setImporting(false);
    try {
      setWorlds(await api.listWorlds());
    } catch {
      /* the grid is stale; navigating to the new world is what matters */
    }
    navigate(`/worlds/${imported}`);
  }

  return (
    <LibraryPage>
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
            <button className="subtle" disabled={importing} onClick={() => fileRef.current?.click()}>
              {importing ? "Importing…" : "Import"}
            </button>
            <input
              ref={fileRef}
              type="file"
              accept=".zip"
              aria-label="Import world bundle"
              style={{ display: "none" }}
              onChange={onImportFile}
            />
          </div>
        </div>
        {error && <div className="error" role="alert">{error}</div>}
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
                <a aria-label={`Export ${w.name}`} title="Export as a bundle"
                   href={api.exportWorldUrl(w.id)} download>⭳</a>
                <button aria-label={`Rename ${w.name}`} onClick={() => setRenaming({ id: w.id, name: w.name })}>✎</button>
                <button aria-label={`Fork ${w.name}`} title="Fork into a new world"
                        disabled={forking !== null} onClick={() => { void fork(w); }}>
                  {forking === w.id ? "…" : "⑂"}
                </button>
                <button aria-label={`Delete ${w.name}`} onClick={() => remove(w)}>✕</button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </LibraryPage>
  );
}
