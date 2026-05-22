/**
 * Generic editor for the YAML frontmatter dict that backs every library
 * entity. Scalar fields (string / number / boolean) get inline inputs; lists
 * and nested objects render via StructuredValueEditor so they remain
 * editable as forms (no JSON). Schema-aware editors (voice, image) layer
 * on top of this in the per-kind editor components.
 */

import { useState } from "react";

import type { Frontmatter, FrontmatterValue } from "./frontmatter";
import { StructuredValueEditor } from "./StructuredValueEditor";

interface Props {
  value: Frontmatter;
  onChange: (next: Frontmatter) => void;
  /** Field names whose editors are rendered elsewhere and should be hidden here. */
  hiddenKeys?: string[];
  readOnly?: boolean;
}

type Kind = "string" | "number" | "boolean" | "list" | "object";

function fieldKind(v: FrontmatterValue): Kind {
  if (typeof v === "string") return "string";
  if (typeof v === "number") return "number";
  if (typeof v === "boolean") return "boolean";
  if (Array.isArray(v)) return "list";
  return "object";
}

export function FrontmatterEditor({ value, onChange, hiddenKeys = [], readOnly }: Props) {
  const hidden = new Set(hiddenKeys);
  const entries = Object.entries(value).filter(([k]) => !hidden.has(k));

  const update = (key: string, next: FrontmatterValue) => {
    if (readOnly) return;
    onChange({ ...value, [key]: next });
  };
  const remove = (key: string) => {
    if (readOnly) return;
    const next = { ...value };
    delete next[key];
    onChange(next);
  };
  const add = (key: string, kind: Kind) => {
    if (readOnly || !key || key in value || hidden.has(key)) return;
    const initial: FrontmatterValue =
      kind === "string"
        ? ""
        : kind === "number"
          ? 0
          : kind === "boolean"
            ? false
            : kind === "list"
              ? []
              : {};
    onChange({ ...value, [key]: initial });
  };

  return (
    <div className="frontmatter-editor">
      {entries.length === 0 && <p className="library-status">No frontmatter fields yet.</p>}
      {entries.map(([key, v]) => (
        <FrontmatterField
          key={key}
          name={key}
          value={v}
          onChange={(next) => update(key, next)}
          onRemove={() => remove(key)}
          readOnly={readOnly}
        />
      ))}
      {!readOnly && <AddFieldRow onAdd={add} existingKeys={new Set(Object.keys(value))} />}
    </div>
  );
}

function FrontmatterField({
  name,
  value,
  onChange,
  onRemove,
  readOnly,
}: {
  name: string;
  value: FrontmatterValue;
  onChange: (v: FrontmatterValue) => void;
  onRemove: () => void;
  readOnly?: boolean;
}) {
  const kind = fieldKind(value);
  return (
    <div className="frontmatter-field" data-kind={kind}>
      <label className="frontmatter-label">
        <span>{name}</span>
        {!readOnly && (
          <button
            type="button"
            className="frontmatter-remove"
            onClick={onRemove}
            aria-label={`Remove ${name}`}
          >
            ×
          </button>
        )}
      </label>
      {kind === "string" && (
        <input
          type="text"
          value={String(value ?? "")}
          readOnly={readOnly}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
      {kind === "number" && (
        <input
          type="number"
          value={Number(value)}
          readOnly={readOnly}
          onChange={(e) => onChange(Number(e.target.value))}
        />
      )}
      {kind === "boolean" && (
        <input
          type="checkbox"
          checked={Boolean(value)}
          disabled={readOnly}
          onChange={(e) => onChange(e.target.checked)}
        />
      )}
      {(kind === "list" || kind === "object") && (
        <StructuredValueEditor
          value={value as unknown}
          onChange={(next) => onChange(next as FrontmatterValue)}
          readOnly={readOnly}
        />
      )}
    </div>
  );
}

function AddFieldRow({
  onAdd,
  existingKeys,
}: {
  onAdd: (key: string, kind: Kind) => void;
  existingKeys: Set<string>;
}) {
  const [key, setKey] = useState("");
  const [kind, setKind] = useState<Kind>("string");
  const trimmed = key.trim();
  const valid = trimmed.length > 0 && !existingKeys.has(trimmed);
  return (
    <div className="frontmatter-add">
      <input
        type="text"
        placeholder="add field…"
        value={key}
        onChange={(e) => setKey(e.target.value)}
      />
      <select value={kind} onChange={(e) => setKind(e.target.value as Kind)}>
        <option value="string">text</option>
        <option value="number">number</option>
        <option value="boolean">boolean</option>
        <option value="list">list</option>
        <option value="object">object</option>
      </select>
      <button
        type="button"
        disabled={!valid}
        onClick={() => {
          if (!valid) return;
          onAdd(trimmed, kind);
          setKey("");
        }}
      >
        Add
      </button>
    </div>
  );
}
