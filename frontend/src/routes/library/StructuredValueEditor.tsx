import { useEffect, useId, useState } from "react";

export type StructuredValue =
  | string
  | number
  | boolean
  | null
  | StructuredValue[]
  | { [key: string]: StructuredValue };

interface Props {
  value: unknown;
  onChange: (next: StructuredValue) => void;
  readOnly?: boolean;
}

type Kind = "text" | "number" | "boolean" | "list" | "object" | "null";

function kindOf(v: unknown): Kind {
  if (v === null || v === undefined) return "null";
  if (typeof v === "string") return "text";
  if (typeof v === "number") return "number";
  if (typeof v === "boolean") return "boolean";
  if (Array.isArray(v)) return "list";
  return "object";
}

function defaultFor(kind: Kind): StructuredValue {
  switch (kind) {
    case "text":
      return "";
    case "number":
      return 0;
    case "boolean":
      return false;
    case "list":
      return [];
    case "object":
      return {};
    case "null":
      return null;
  }
}

export function StructuredValueEditor({ value, onChange, readOnly = false }: Props) {
  const kind = kindOf(value);

  if (kind === "null") return <NullRow onChange={onChange} readOnly={readOnly} />;
  if (kind === "text" || kind === "number" || kind === "boolean") {
    return (
      <ScalarRow
        value={value as string | number | boolean}
        kind={kind}
        onChange={onChange}
        readOnly={readOnly}
      />
    );
  }
  if (kind === "list") {
    return <ArrayRows items={value as StructuredValue[]} onChange={onChange} readOnly={readOnly} />;
  }
  return (
    <ObjectRows
      record={value as Record<string, StructuredValue>}
      onChange={onChange}
      readOnly={readOnly}
    />
  );
}

function ScalarRow({
  value,
  kind,
  onChange,
  readOnly,
}: {
  value: string | number | boolean;
  kind: "text" | "number" | "boolean";
  onChange: (next: StructuredValue) => void;
  readOnly: boolean;
}) {
  if (kind === "text") {
    return (
      <input
        type="text"
        value={value as string}
        readOnly={readOnly}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  }
  if (kind === "number") {
    return (
      <input
        type="number"
        value={value as number}
        readOnly={readOnly}
        onChange={(e) => onChange(Number(e.target.value))}
      />
    );
  }
  return (
    <input
      type="checkbox"
      checked={value as boolean}
      disabled={readOnly}
      onChange={(e) => onChange(e.target.checked)}
    />
  );
}

function ArrayRows({
  items,
  onChange,
  readOnly,
}: {
  items: StructuredValue[];
  onChange: (next: StructuredValue) => void;
  readOnly: boolean;
}) {
  const updateAt = (i: number, next: StructuredValue) => {
    const out = items.slice();
    out[i] = next;
    onChange(out);
  };
  const removeAt = (i: number) => {
    const out = items.slice();
    out.splice(i, 1);
    onChange(out);
  };
  const append = () => onChange([...items, null]);
  return (
    <ol className="structured-list">
      {items.map((item, i) => (
        <li key={i} className="structured-list-row">
          <StructuredValueEditor
            value={item}
            onChange={(next) => updateAt(i, next)}
            readOnly={readOnly}
          />
          {!readOnly && (
            <button
              type="button"
              className="structured-remove"
              onClick={() => removeAt(i)}
              aria-label={`Remove item ${i + 1}`}
            >
              ×
            </button>
          )}
        </li>
      ))}
      {!readOnly && (
        <li>
          <button type="button" className="structured-add" onClick={append}>
            + add item
          </button>
        </li>
      )}
    </ol>
  );
}

function ObjectRows({
  record,
  onChange,
  readOnly,
}: {
  record: Record<string, StructuredValue>;
  onChange: (next: StructuredValue) => void;
  readOnly: boolean;
}) {
  const entries = Object.entries(record);
  const existingKeys = new Set(Object.keys(record));

  const updateValue = (key: string, next: StructuredValue) => {
    onChange({ ...record, [key]: next });
  };
  const removeKey = (key: string) => {
    const next = { ...record };
    delete next[key];
    onChange(next);
  };
  // Rebuild the object preserving insertion order so a key rename doesn't
  // jump to the end.
  const renameKey = (oldKey: string, newKey: string) => {
    const next: Record<string, StructuredValue> = {};
    for (const [k, v] of Object.entries(record)) {
      next[k === oldKey ? newKey : k] = v;
    }
    onChange(next);
  };

  return (
    <div className="structured-object">
      {entries.map(([key, val]) => (
        <ObjectRow
          key={key}
          name={key}
          value={val}
          existingKeys={existingKeys}
          onRename={(next) => renameKey(key, next)}
          onValueChange={(next) => updateValue(key, next)}
          onRemove={() => removeKey(key)}
          readOnly={readOnly}
        />
      ))}
      {!readOnly && (
        <PendingObjectRow
          existingKeys={existingKeys}
          onCommit={(name) => onChange({ ...record, [name]: null })}
        />
      )}
    </div>
  );
}

function ObjectRow({
  name,
  value,
  existingKeys,
  onRename,
  onValueChange,
  onRemove,
  readOnly,
}: {
  name: string;
  value: StructuredValue;
  existingKeys: Set<string>;
  onRename: (next: string) => void;
  onValueChange: (next: StructuredValue) => void;
  onRemove: () => void;
  readOnly: boolean;
}) {
  const [draft, setDraft] = useState(name);
  const [err, setErr] = useState<string | null>(null);
  // Keep the local draft in sync when the underlying key changes from
  // outside (e.g. a successful rename re-keyed the object and we are now
  // rendered for the new name).
  useEffect(() => {
    setDraft(name);
    setErr(null);
  }, [name]);

  return (
    <div className="structured-object-row">
      <input
        type="text"
        value={draft}
        readOnly={readOnly}
        aria-label={`Key for ${name}`}
        onChange={(e) => {
          const next = e.target.value;
          setDraft(next);
          const trimmed = next.trim();
          if (!trimmed) {
            setErr(null);
            return;
          }
          if (trimmed === name) {
            setErr(null);
            return;
          }
          if (existingKeys.has(trimmed)) {
            setErr("key already exists");
            return;
          }
          setErr(null);
          onRename(trimmed);
        }}
      />
      <div className="structured-object-value">
        <StructuredValueEditor value={value} onChange={onValueChange} readOnly={readOnly} />
      </div>
      {!readOnly && (
        <button
          type="button"
          className="structured-remove"
          onClick={onRemove}
          aria-label={`Remove field ${name}`}
        >
          ×
        </button>
      )}
      {err && <p className="frontmatter-error">{err}</p>}
    </div>
  );
}

function PendingObjectRow({
  existingKeys,
  onCommit,
}: {
  existingKeys: Set<string>;
  onCommit: (name: string) => void;
}) {
  const [draft, setDraft] = useState("");
  const trimmed = draft.trim();
  const valid = trimmed.length > 0 && !existingKeys.has(trimmed);
  return (
    <div className="structured-object-pending">
      <input
        type="text"
        value={draft}
        placeholder="add field…"
        onChange={(e) => setDraft(e.target.value)}
      />
      <button
        type="button"
        className="structured-add"
        disabled={!valid}
        onClick={() => {
          if (!valid) return;
          onCommit(trimmed);
          setDraft("");
        }}
      >
        + add field
      </button>
    </div>
  );
}

function NullRow({
  onChange,
  readOnly,
}: {
  onChange: (next: StructuredValue) => void;
  readOnly: boolean;
}) {
  const id = useId();
  return (
    <div className="structured-null">
      <span>(empty)</span>
      <label htmlFor={id} className="structured-type-label">
        Type
      </label>
      <select
        id={id}
        disabled={readOnly}
        defaultValue="null"
        onChange={(e) => {
          const k = e.target.value as Kind;
          if (k === "null") return;
          onChange(defaultFor(k));
        }}
      >
        <option value="null">(choose)</option>
        <option value="text">text</option>
        <option value="number">number</option>
        <option value="boolean">boolean</option>
        <option value="list">list</option>
        <option value="object">object</option>
      </select>
    </div>
  );
}
