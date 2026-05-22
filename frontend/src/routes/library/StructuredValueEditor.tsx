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

  if (kind === "null") {
    return <NullRow onChange={onChange} readOnly={readOnly} />;
  }
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
  if (kind === "boolean") {
    return (
      <input
        type="checkbox"
        checked={value as boolean}
        disabled={readOnly}
        onChange={(e) => onChange(e.target.checked)}
      />
    );
  }
  // list + object layouts arrive in later tasks
  return <p className="library-status">(complex value — editor coming)</p>;
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
