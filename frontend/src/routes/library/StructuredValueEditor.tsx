import { useId } from "react";

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
    return (
      <ArrayRows items={value as StructuredValue[]} onChange={onChange} readOnly={readOnly} />
    );
  }
  // object branch arrives in Task 5
  return <p className="library-status">(object editor coming)</p>;
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
