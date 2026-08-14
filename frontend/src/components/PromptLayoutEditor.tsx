import { type PromptLayoutSection } from "../api/client";

/** The prompt layout editor (#29): reorder the system message's sections,
 *  switch them off, and rename the row the scene inspector shows.
 *
 *  PRESENTATIONAL. The rows, the dirty state and the writing all belong to
 *  `ConfigView`, which owns the page's single Save. This panel used to hold its
 *  own draft and its own Save button, and that made the page lie: a reordered
 *  section left the footer reading "No unsaved changes" while the page's Save
 *  wrote every setting except the reordering the reader had just done.
 *
 *  Three things it deliberately does NOT offer, each for a reason worth keeping:
 *
 *  - A section's TEXT. Each template emits its own `# Heading` and its own
 *    body, and `templates/` is loaded from disk with auto-reload, so editing
 *    the file is already how a reader changes what a section says.
 *  - A section's TIER. The tier is the packer's drop order, and
 *    `context/pack.py` keeps recalled lore below the recalled archive
 *    precisely so a semantic hit can only ever ADD to a prompt. Where a
 *    section sits and what gives way under pressure are separate questions.
 *  - Drag and drop. ↑/↓ buttons need no dependency and are reachable from a
 *    keyboard, which a drag handle is not.
 */
export function PromptLayoutEditor(
  { rows, failed, busy, onChange, onReset }: {
    rows: PromptLayoutSection[] | null;
    failed: boolean;
    busy: boolean;
    onChange: (next: PromptLayoutSection[]) => void;
    onReset: () => void;
  },
) {
  if (failed) return <p className="config-caption">Could not load the prompt layout.</p>;
  if (!rows) return <p className="config-caption">Loading…</p>;

  function move(index: number, by: -1 | 1) {
    const list = rows!;
    const to = index + by;
    if (to < 0 || to >= list.length) return;
    const next = [...list];
    [next[index], next[to]] = [next[to], next[index]];
    onChange(next);
  }

  function edit(id: string, patch: Partial<PromptLayoutSection>) {
    onChange(rows!.map((r) => (r.id === id ? { ...r, ...patch } : r)));
  }

  return (
    <div className="prompt-layout">
      <p className="config-caption">
        The order the system message's sections are assembled in. A label here
        renames the row in the scene inspector — not the heading the model
        reads, which each section's template writes itself. Leave a label blank
        to keep the default.
      </p>
      <ul className="prompt-layout-list">
        {rows.map((row, i) => (
          <li className={"prompt-layout-row" + (row.enabled ? "" : " off")}
              key={row.id} data-testid="layout-row" data-id={row.id}>
            <input type="checkbox" checked={row.enabled}
                   aria-label={`Include ${row.default_label}`}
                   onChange={(e) => edit(row.id, { enabled: e.target.checked })} />
            <input type="text" className="prompt-layout-label" maxLength={60}
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
      {/* Reset writes immediately rather than joining the page's draft: it
          CLEARS the stored layout, and there is no client-side way to
          reconstruct the catalog's order once the rows have been reordered —
          the server is the only thing that still knows it. */}
      <div className="form-actions">
        <button type="button" className="btn-outline" disabled={busy} onClick={onReset}>
          Reset to default order
        </button>
      </div>
    </div>
  );
}
