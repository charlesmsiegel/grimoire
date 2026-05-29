import { useState } from "react";

/** Editor for an object of string → string (e.g. a character's address terms). */
export function MapEditor({
  value,
  onChange,
}: {
  value: Record<string, string>;
  onChange: (next: Record<string, string>) => void;
}) {
  const [k, setK] = useState("");
  const [v, setV] = useState("");
  return (
    <div className="map-editor">
      <ul>
        {Object.entries(value).map(([key, val]) => (
          <li key={key}>
            <span className="map-key">{key}</span>
            <input value={val} onChange={(e) => onChange({ ...value, [key]: e.target.value })} />
            <button
              type="button"
              aria-label={`Remove ${key}`}
              onClick={() => {
                const next = { ...value };
                delete next[key];
                onChange(next);
              }}
            >
              ×
            </button>
          </li>
        ))}
      </ul>
      <div className="map-add">
        <input placeholder="key" value={k} onChange={(e) => setK(e.target.value)} />
        <input placeholder="value" value={v} onChange={(e) => setV(e.target.value)} />
        <button
          type="button"
          disabled={!k.trim() || k in value}
          onClick={() => {
            onChange({ ...value, [k.trim()]: v });
            setK("");
            setV("");
          }}
        >
          + Add
        </button>
      </div>
    </div>
  );
}
