import { useState } from "react";
import type { WidgetProps } from "../types";

export function KeywordListWidget({
  property,
  name,
  value,
  onChange,
  readOnly,
}: WidgetProps<ReadonlyArray<string> | null>) {
  const [draft, setDraft] = useState("");
  const tags = (value ?? []) as ReadonlyArray<string>;

  const commit = () => {
    const trimmed = draft.trim();
    if (!trimmed || readOnly) {
      setDraft("");
      return;
    }
    if (!tags.includes(trimmed)) onChange([...tags, trimmed]);
    setDraft("");
  };

  const remove = (tag: string) => {
    if (readOnly) return;
    onChange(tags.filter((t) => t !== tag));
  };

  return (
    <div
      className="sheet-widget sheet-keyword-list"
      id={`sheet-${name}`}
      role="group"
      aria-label={property.title ?? name}
    >
      <ul className="sheet-keyword-chips">
        {tags.map((tag) => (
          <li key={tag} className="chip sheet-keyword-chip">
            <span>{tag}</span>
            {!readOnly && (
              <button type="button" onClick={() => remove(tag)} aria-label={`Remove ${tag}`}>
                ×
              </button>
            )}
          </li>
        ))}
      </ul>
      {!readOnly && (
        <input
          type="text"
          className="sheet-keyword-input"
          placeholder="Add tag…"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === ",") {
              e.preventDefault();
              commit();
            } else if (e.key === "Backspace" && draft === "" && tags.length > 0) {
              e.preventDefault();
              const last = tags[tags.length - 1];
              if (last) remove(last);
            }
          }}
          onBlur={commit}
        />
      )}
    </div>
  );
}
