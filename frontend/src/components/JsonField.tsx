import { useState } from "react";

function serialize(value: unknown): string {
  return value === undefined ? "" : JSON.stringify(value, null, 2);
}

/**
 * A JSON textarea that is actually editable: the text lives in local state so
 * keystrokes are never reverted mid-edit, parsing happens on blur, and invalid
 * JSON surfaces an inline error instead of being silently discarded. Empty text
 * means "unset" and emits `undefined`.
 */
export function JsonField({
  value,
  onChange,
  rows = 4,
}: {
  value: unknown;
  onChange: (v: unknown) => void;
  rows?: number;
}) {
  const [text, setText] = useState(() => serialize(value));
  const [error, setError] = useState<string | null>(null);

  function commit() {
    const trimmed = text.trim();
    if (trimmed === "") {
      setError(null);
      onChange(undefined);
      return;
    }
    try {
      onChange(JSON.parse(trimmed));
      setError(null);
    } catch (e) {
      setError(`Invalid JSON: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  return (
    <div className="json-field">
      <textarea
        rows={rows}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          if (error) setError(null);
        }}
        onBlur={commit}
      />
      {error && <small className="json-field-error">{error}</small>}
    </div>
  );
}
