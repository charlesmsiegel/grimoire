import { useState } from "react";

export function EditableRow({
  label,
  subtitle,
  active,
  onSelect,
  onRename,
  onDelete,
}: {
  label: string;
  subtitle?: string;
  active?: boolean;
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
    const next = draft.trim();
    if (next && next !== label) onRename(next);
  }

  return (
    <div className={"row" + (active ? " active" : "")}>
      {editing ? (
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
            <span className="row-name">{label}</span>
            {subtitle && <span className="row-subtitle">{subtitle}</span>}
          </span>
          <span className="row-actions">
            <button
              aria-label="Rename"
              title="Rename"
              onClick={(e) => {
                e.stopPropagation();
                start();
              }}
            >
              ✎
            </button>
            <button
              aria-label="Delete"
              title="Delete"
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
