/**
 * Generic editor for the YAML frontmatter dict that backs every library
 * entity. Scalar fields (string / number / boolean) get inline inputs; lists
 * and nested objects fall back to a JSON textarea so the user can still
 * change them. Schema-aware editors (voice, image) layer on top of this in
 * the per-kind editor components.
 */

import { useEffect, useRef, useState } from "react";

import type { Frontmatter, FrontmatterValue } from "./frontmatter";

interface Props {
  value: Frontmatter;
  onChange: (next: Frontmatter) => void;
  /** Field names whose editors are rendered elsewhere and should be hidden here. */
  hiddenKeys?: string[];
  readOnly?: boolean;
}

function fieldKind(v: FrontmatterValue): "string" | "number" | "boolean" | "json" {
  if (typeof v === "string") return "string";
  if (typeof v === "number") return "number";
  if (typeof v === "boolean") return "boolean";
  return "json";
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
    const rest = next;
    onChange(rest);
  };
  const add = (key: string, kind: "string" | "number" | "boolean" | "json") => {
    if (readOnly || !key || key in value || hidden.has(key)) return;
    const initial: FrontmatterValue =
      kind === "string" ? "" : kind === "number" ? 0 : kind === "boolean" ? false : [];
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
      {kind === "json" && (
        <JsonField value={value as FrontmatterValue} onChange={onChange} readOnly={readOnly} />
      )}
    </div>
  );
}

function JsonField({
  value,
  onChange,
  readOnly,
}: {
  value: FrontmatterValue;
  onChange: (v: FrontmatterValue) => void;
  readOnly?: boolean;
}) {
  const [text, setText] = useState(() => JSON.stringify(value ?? null, null, 2));
  const [err, setErr] = useState<string | null>(null);
  // Keep ``text`` readable from the effect below without re-running it on
  // every keystroke. The effect should only fire for genuine value-prop
  // changes from outside, not for the round-trip caused by our own
  // onChange.
  const textRef = useRef(text);
  textRef.current = text;
  useEffect(() => {
    // Skip the reset if the textarea already represents this value —
    // that is, this prop update is the echo of an onChange we just
    // dispatched. Otherwise the reset would reformat the user's
    // in-progress JSON (collapsing extra whitespace, moving the cursor,
    // discarding the next character they were about to type).
    let mirrorsValue = false;
    try {
      mirrorsValue =
        JSON.stringify(JSON.parse(textRef.current || "null")) ===
        JSON.stringify(value ?? null);
    } catch {
      // Mid-typing partial — leave the user's text alone; we'd rather
      // silently drop a rare external update than blow away an edit in
      // progress.
      mirrorsValue = true;
    }
    if (mirrorsValue) return;
    setText(JSON.stringify(value ?? null, null, 2));
  }, [value]);

  return (
    <div className="frontmatter-json">
      <textarea
        rows={Math.min(10, Math.max(2, text.split("\n").length))}
        value={text}
        readOnly={readOnly}
        onChange={(e) => {
          setText(e.target.value);
          try {
            const parsed = JSON.parse(e.target.value || "null");
            setErr(null);
            onChange(parsed as FrontmatterValue);
          } catch (parseErr) {
            setErr(parseErr instanceof Error ? parseErr.message : String(parseErr));
          }
        }}
      />
      {err && <p className="frontmatter-error">{err}</p>}
    </div>
  );
}

function AddFieldRow({
  onAdd,
  existingKeys,
}: {
  onAdd: (key: string, kind: "string" | "number" | "boolean" | "json") => void;
  existingKeys: Set<string>;
}) {
  const [key, setKey] = useState("");
  const [kind, setKind] = useState<"string" | "number" | "boolean" | "json">("string");
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
      <select value={kind} onChange={(e) => setKind(e.target.value as typeof kind)}>
        <option value="string">text</option>
        <option value="number">number</option>
        <option value="boolean">boolean</option>
        <option value="json">json (list/object)</option>
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
