import { useEffect, useState } from "react";
import { api, type PromptLayoutSection } from "../api/client";

/** The prompt layout editor (#29): reorder the system message's sections,
 *  switch them off, and rename the row the scene inspector shows.
 *
 *  Three things it deliberately does NOT do, each for a reason worth keeping:
 *
 *  - It does not edit a section's TEXT. Each template emits its own `# Heading`
 *    and its own body; `templates/` is loaded from disk with auto-reload, so
 *    editing the file is already how a reader changes what a section says.
 *  - It does not edit a section's TIER. The tier is the packer's drop order,
 *    and `context/pack.py` keeps recalled lore below the archive precisely so
 *    semantic recall can only ever ADD to a prompt. Prompt order and drop order
 *    are separate axes; only the first is a preference.
 *  - It does not drag-and-drop. ↑/↓ buttons need no dependency and are
 *    reachable from a keyboard, which a drag handle is not.
 *
 *  Save replaces the whole list rather than sending a patch: the server merges
 *  anything omitted back beside its catalog neighbours, so a partial list would
 *  barely reorder anything. */
export function PromptLayoutEditor() {
  const [rows, setRows] = useState<PromptLayoutSection[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let live = true;
    api.getPromptLayout()
      .then((l) => { if (live) { setRows(l.sections); setFailed(false); } })
      .catch(() => { if (live) setFailed(true); });
    return () => { live = false; };
  }, []);

  function move(index: number, by: -1 | 1) {
    setRows((current) => {
      if (!current) return current;
      const to = index + by;
      if (to < 0 || to >= current.length) return current;
      const next = [...current];
      [next[index], next[to]] = [next[to], next[index]];
      return next;
    });
    setDirty(true);
  }

  function edit(id: string, patch: Partial<PromptLayoutSection>) {
    setRows((current) =>
      current ? current.map((r) => (r.id === id ? { ...r, ...patch } : r)) : current);
    setDirty(true);
  }

  async function save(next: PromptLayoutSection[] | null) {
    setSaving(true);
    try {
      // `null` is Reset: an empty list clears the stored layout, and the
      // response is the catalog, so the panel redraws from the server's answer
      // rather than from a guess about what reset means.
      const body = (next ?? []).map((r) => ({ id: r.id, label: r.label, enabled: r.enabled }));
      const saved = await api.putPromptLayout(body);
      setRows(saved.sections);
      setDirty(false);
    } catch {
      setFailed(true);
    } finally {
      setSaving(false);
    }
  }

  if (failed) {
    return <p className="config-caption">Could not load the prompt layout.</p>;
  }
  if (!rows) return <p className="config-caption">Loading…</p>;

  return (
    <div className="prompt-layout">
      <p className="config-caption">
        The order the system message's sections are assembled in. A label here
        renames the row in the scene inspector — not the heading the model
        reads, which each section's template writes itself.
      </p>
      <ul className="prompt-layout-list">
        {rows.map((row, i) => (
          <li className={"prompt-layout-row" + (row.enabled ? "" : " off")}
              key={row.id} data-testid="layout-row" data-id={row.id}>
            <input type="checkbox" checked={row.enabled}
                   aria-label={`Include ${row.default_label}`}
                   onChange={(e) => edit(row.id, { enabled: e.target.checked })} />
            <input type="text" className="prompt-layout-label"
                   aria-label={`Label for ${row.default_label}`}
                   value={row.label} placeholder={row.default_label}
                   onChange={(e) => edit(row.id, { label: e.target.value })} />
            <span className="chip on prompt-layout-tier">{row.tier}</span>
            <button type="button" aria-label={`Move ${row.default_label} up`}
                    disabled={i === 0} onClick={() => move(i, -1)}>↑</button>
            <button type="button" aria-label={`Move ${row.default_label} down`}
                    disabled={i === rows.length - 1} onClick={() => move(i, 1)}>↓</button>
          </li>
        ))}
      </ul>
      <div className="form-actions">
        <button type="button" disabled={!dirty || saving} onClick={() => save(rows)}>
          Save layout
        </button>
        <button type="button" disabled={saving} onClick={() => save(null)}>
          Reset to default order
        </button>
      </div>
    </div>
  );
}
