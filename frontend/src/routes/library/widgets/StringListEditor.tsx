/**
 * Editable list of strings — multiline rows with add/remove. Shared by the
 * structured entity form widgets and the character voice sub-editor.
 */
export function StringListEditor({
  label,
  value,
  onChange,
  textarea,
}: {
  label: string;
  value: string[];
  onChange: (next: string[]) => void;
  textarea?: boolean;
}) {
  return (
    <div className="string-list-editor">
      <span className="string-list-label">{label}</span>
      <ul>
        {value.map((s, idx) => (
          <li key={idx}>
            {textarea ? (
              <textarea
                rows={2}
                value={s}
                onChange={(e) => {
                  const next = [...value];
                  next[idx] = e.target.value;
                  onChange(next);
                }}
              />
            ) : (
              <input
                type="text"
                value={s}
                onChange={(e) => {
                  const next = [...value];
                  next[idx] = e.target.value;
                  onChange(next);
                }}
              />
            )}
            {/* eslint-disable-next-line local/no-bespoke-delete -- list-item remover widget, not a card */}
            <button
              type="button"
              aria-label="Remove"
              onClick={() => onChange(value.filter((_, i) => i !== idx))}
            >
              ×
            </button>
          </li>
        ))}
      </ul>
      <button type="button" onClick={() => onChange([...value, ""])}>
        + Add
      </button>
    </div>
  );
}
