import { useState } from "react";

export function EditableRow({
  label,
  prefix,
  subtitle,
  done,
  active,
  locked,
  lockedReason,
  onSelect,
  onRename,
  onDelete,
}: {
  label: string;
  /** display-only ordinal shown before the name; excluded from rename */
  prefix?: string;
  subtitle?: string;
  /** Finished record — a scene absorbed into the chronicle. Marked, not hidden. */
  done?: boolean;
  active?: boolean;
  /** Disable rename and delete — the record is busy being written to. Selecting
   *  it stays live, since reading a row is never what makes a write unsafe. */
  locked?: boolean;
  /** Tooltip explaining the lock; disabled controls with no reason read as bugs. */
  lockedReason?: string;
  onSelect?: () => void;
  onRename: (next: string) => void;
  onDelete: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(label);

  function start() {
    setDraft(label);
    setEditing(true);
  }

  function save() {
    setEditing(false);
    // The lock can arrive while the editor is already open — a turn started
    // after the player clicked ✎ — and disabling the buttons does nothing about
    // an input that is already mounted. Review caught Enter still renaming the
    // scene mid-stream through exactly that gap (#95).
    if (locked) return;
    const next = draft.trim();
    if (next && next !== label) onRename(next);
  }

  return (
    <div className={"row" + (active ? " active" : "")}>
      {editing && !locked ? (
        <input
          className="row-rename"
          autoFocus
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") save();
            else if (e.key === "Escape") setEditing(false);
          }}
          onBlur={() => setEditing(false)}
        />
      ) : (
        <>
          <span className="row-label" onClick={onSelect}>
            <span className="row-name">
              {/* The title is its own element so the ellipsis truncates IT and
                  leaves the mark standing; a long name would otherwise clip the
                  mark away with the rest of the run. */}
              <span className="row-title">{prefix ? `${prefix} · ${label}` : label}</span>
              {/* Carries a title as well as a glyph: a bare ✓ beside a name is
                  not self-explanatory, and it is the only thing distinguishing a
                  finished scene from an unfinished one. */}
              {done && <span className="row-done" title="Scene complete">✓</span>}
            </span>
            {subtitle && <span className="row-subtitle">{subtitle}</span>}
          </span>
          <span className="row-actions">
            <button
              aria-label="Rename"
              title={locked ? lockedReason ?? "Rename" : "Rename"}
              disabled={locked}
              onClick={(e) => {
                e.stopPropagation();
                start();
              }}
            >
              ✎
            </button>
            <button
              aria-label="Delete"
              title={locked ? lockedReason ?? "Delete" : "Delete"}
              disabled={locked}
              onClick={(e) => {
                e.stopPropagation();
                onDelete();
              }}
            >
              🗑
            </button>
          </span>
        </>
      )}
    </div>
  );
}
