import type { ReactNode } from "react";

type Row = Record<string, unknown>;

/**
 * Editable list of object rows. The caller supplies `renderRow` to draw each
 * row's fields, so this widget stays free of descriptor knowledge. New rows
 * start as `{}`. Powers e.g. a character's structural relationships.
 */
export function ObjectListEditor({
  value,
  fieldKeys,
  onChange,
  renderRow,
}: {
  value: Row[];
  /** Keys this row manages — reserved for seeding/validation. */
  fieldKeys: string[];
  onChange: (next: Row[]) => void;
  renderRow: (row: Row, patch: (next: Row) => void) => ReactNode;
}) {
  void fieldKeys;
  return (
    <div className="object-list-editor">
      <ul>
        {value.map((row, idx) => (
          <li key={idx} className="object-list-row">
            {renderRow(row, (next) => {
              const copy = [...value];
              copy[idx] = next;
              onChange(copy);
            })}
            {/* eslint-disable-next-line local/no-bespoke-delete -- list-item remover widget, not a card */}
            <button
              type="button"
              aria-label="Remove row"
              onClick={() => onChange(value.filter((_, i) => i !== idx))}
            >
              ×
            </button>
          </li>
        ))}
      </ul>
      <button type="button" onClick={() => onChange([...value, {}])}>
        + Add
      </button>
    </div>
  );
}
