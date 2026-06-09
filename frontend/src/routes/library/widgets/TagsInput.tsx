import { useState } from "react";

/** Chip-style editor for a list of unique string tags. */
export function TagsInput({
  value,
  onChange,
}: {
  value: string[];
  onChange: (next: string[]) => void;
}) {
  const [draft, setDraft] = useState("");
  function add() {
    const t = draft.trim();
    if (!t || value.includes(t)) {
      setDraft("");
      return;
    }
    onChange([...value, t]);
    setDraft("");
  }
  return (
    <div className="tags-input">
      <ul className="tags-input-chips">
        {value.map((t) => (
          <li key={t} className="chip tags-input-chip">
            {t}
            <button
              type="button"
              aria-label={`Remove ${t}`}
              onClick={() => onChange(value.filter((x) => x !== t))}
            >
              ×
            </button>
          </li>
        ))}
      </ul>
      <input
        type="text"
        placeholder="add tag…"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === ",") {
            e.preventDefault();
            add();
          }
        }}
        onBlur={add}
      />
    </div>
  );
}
