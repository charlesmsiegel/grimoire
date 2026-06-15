/**
 * Renders a single JSON-schema property as a form field.
 *
 * Pulled out of the library plugin-config form so the startup wizard
 * can render the same config schemas without duplicating the renderer.
 * Honors the ``x-source: "models"`` extension by routing into
 * {@link PluginModelPicker} for catalog-aware model selection.
 */

import { useState } from "react";

import { FilePathPicker, type GGUFInfo } from "./FilePathPicker";
import { JsonField } from "./JsonField";
import { PluginModelPicker } from "./PluginModelPicker";
import type { JsonSchema } from "./schemaForm";
import { MapEditor } from "./widgets/MapEditor";
import { ObjectListEditor } from "./widgets/ObjectListEditor";
import { StringListEditor } from "./widgets/StringListEditor";

interface Props {
  pluginId: string;
  name: string;
  schema: JsonSchema;
  required: boolean;
  value: unknown;
  onChange: (v: unknown) => void;
  onFileIntrospect?: (info: GGUFInfo) => void;
}

export function SchemaField({
  pluginId,
  name,
  schema,
  required,
  value,
  onChange,
  onFileIntrospect,
}: Props) {
  const type = Array.isArray(schema.type) ? schema.type[0] : (schema.type ?? "string");
  const label = schema.title ?? name;
  const placeholder = schema.description ?? "";
  const isSecret = schema.format === "password" || /secret|token|key/i.test(name);

  if (schema.format === "file-path") {
    return (
      <FilePathPicker
        label={label}
        description={schema.description}
        required={required}
        value={typeof value === "string" ? value : ""}
        glob={typeof schema["x-glob"] === "string" ? schema["x-glob"] : undefined}
        onChange={(v) => onChange(v)}
        onIntrospect={onFileIntrospect}
      />
    );
  }

  if (schema["x-source"] === "models") {
    return (
      <PluginModelPicker
        pluginId={pluginId}
        label={label}
        description={schema.description}
        required={required}
        value={typeof value === "string" ? value : ""}
        onChange={onChange}
      />
    );
  }

  if (Array.isArray(schema.enum) && schema.enum.length > 0) {
    return (
      <label>
        <span>
          {label} {required && <em>*</em>}
        </span>
        <select
          value={typeof value === "string" || typeof value === "number" ? String(value) : ""}
          onChange={(e) => onChange(e.target.value)}
        >
          <option value="">(unset)</option>
          {schema.enum.map((opt) => (
            <option key={String(opt)} value={String(opt)}>
              {String(opt)}
            </option>
          ))}
        </select>
        {schema.description && <small>{schema.description}</small>}
      </label>
    );
  }

  if (type === "boolean") {
    return (
      <label className="checkbox-label">
        <input
          type="checkbox"
          checked={Boolean(value)}
          onChange={(e) => onChange(e.target.checked)}
        />
        <span>
          {label} {required && <em>*</em>}
        </span>
        {schema.description && <small>{schema.description}</small>}
      </label>
    );
  }

  if (type === "integer" || type === "number") {
    return (
      <label>
        <span>
          {label} {required && <em>*</em>}
        </span>
        <input
          type="number"
          value={typeof value === "number" ? value : ""}
          step={type === "integer" ? 1 : "any"}
          onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))}
        />
        {schema.description && <small>{schema.description}</small>}
      </label>
    );
  }

  if (type === "object") {
    const obj =
      value && typeof value === "object" && !Array.isArray(value)
        ? (value as Record<string, unknown>)
        : {};
    const props = (schema.properties ?? undefined) as Record<string, JsonSchema> | undefined;
    const ap = schema.additionalProperties;
    const reqd = Array.isArray(schema.required)
      ? new Set(schema.required as string[])
      : new Set<string>();

    // 1. Typed object: render declared properties; offer a custom-keys editor
    //    when the schema also allows arbitrary keys.
    if (props && Object.keys(props).length > 0) {
      const declared = new Set(Object.keys(props));
      const custom: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(obj)) if (!declared.has(k)) custom[k] = v;
      return (
        <fieldset className="schema-object">
          <legend>
            {label} {required && <em>*</em>}
          </legend>
          {schema.description && <small>{schema.description}</small>}
          {Object.entries(props).map(([k, sub]) => (
            <SchemaField
              key={k}
              pluginId={pluginId}
              name={k}
              schema={sub}
              required={reqd.has(k)}
              value={obj[k]}
              onChange={(v) => onChange({ ...obj, [k]: v })}
            />
          ))}
          {ap === true && (
            <ObjectKeyValueField
              pluginId={pluginId}
              label="Custom keys"
              valueSchema={true}
              value={custom}
              onChange={(next) => {
                const merged: Record<string, unknown> = {};
                for (const k of declared) if (k in obj) merged[k] = obj[k];
                onChange({ ...merged, ...next });
              }}
            />
          )}
        </fieldset>
      );
    }

    // 2. String map (e.g. extra_headers).
    if (ap && typeof ap === "object" && (ap as JsonSchema).type === "string") {
      const strMap: Record<string, string> = {};
      for (const [k, v] of Object.entries(obj))
        strMap[k] = typeof v === "string" ? v : String(v ?? "");
      return (
        <label>
          <span>
            {label} {required && <em>*</em>}
          </span>
          <MapEditor value={strMap} onChange={(next) => onChange(next)} />
          {schema.description && <small>{schema.description}</small>}
        </label>
      );
    }

    // 3. Object-valued map (e.g. provider_overrides) or free-form object.
    if ((ap && typeof ap === "object") || ap === true) {
      const valueSchema: JsonSchema | true = ap === true ? true : (ap as JsonSchema);
      return (
        <ObjectKeyValueField
          pluginId={pluginId}
          label={`${label}${required ? " *" : ""}`}
          description={schema.description}
          valueSchema={valueSchema}
          value={obj}
          onChange={onChange}
        />
      );
    }

    // 4. Opaque object: fixed JSON editor.
    return (
      <label>
        <span>
          {label} {required && <em>*</em>}
        </span>
        <JsonField value={value} onChange={onChange} />
        {schema.description && <small>{schema.description}</small>}
      </label>
    );
  }

  if (type === "array") {
    const arr = Array.isArray(value) ? (value as unknown[]) : [];
    const items = (schema.items ?? {}) as JsonSchema;
    if (items.type === "string") {
      return (
        <label>
          <span>
            {label} {required && <em>*</em>}
          </span>
          <StringListEditor
            label=""
            value={arr.map((x) => String(x ?? ""))}
            onChange={(next) => onChange(next)}
          />
          {schema.description && <small>{schema.description}</small>}
        </label>
      );
    }
    if (items.type === "object" && items.properties) {
      const itemProps = items.properties as Record<string, JsonSchema>;
      return (
        <label>
          <span>
            {label} {required && <em>*</em>}
          </span>
          <ObjectListEditor
            value={arr as Record<string, unknown>[]}
            fieldKeys={Object.keys(itemProps)}
            onChange={(next) => onChange(next)}
            renderRow={(row, patch) => (
              <>
                {Object.entries(itemProps).map(([k, sub]) => (
                  <SchemaField
                    key={k}
                    pluginId={pluginId}
                    name={k}
                    schema={sub}
                    required={false}
                    value={row[k]}
                    onChange={(v) => patch({ ...row, [k]: v })}
                  />
                ))}
              </>
            )}
          />
          {schema.description && <small>{schema.description}</small>}
        </label>
      );
    }
    return (
      <label>
        <span>
          {label} {required && <em>*</em>}
        </span>
        <JsonField value={value} onChange={onChange} />
        {schema.description && <small>{schema.description}</small>}
      </label>
    );
  }

  return (
    <label>
      <span>
        {label} {required && <em>*</em>}
      </span>
      <input
        type={isSecret ? "password" : "text"}
        value={typeof value === "string" ? value : ""}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
      />
      {schema.description && <small>{schema.description}</small>}
    </label>
  );
}

/**
 * Edits an object whose keys are user-chosen (a map). Each value is rendered by
 * recursing into {@link SchemaField} with `valueSchema`, except when the values
 * are fully free-form (`valueSchema === true`), in which case a {@link JsonField}
 * is used. Powers free-form objects, per-model routing maps, and the custom-keys
 * escape hatch on typed objects.
 */
function ObjectKeyValueField({
  pluginId,
  label,
  description,
  valueSchema,
  value,
  onChange,
}: {
  pluginId: string;
  label: string;
  description?: string;
  valueSchema: JsonSchema | true;
  value: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
}) {
  const [newKey, setNewKey] = useState("");
  function setKey(k: string, v: unknown) {
    onChange({ ...value, [k]: v });
  }
  function removeKey(k: string) {
    const next = { ...value };
    delete next[k];
    onChange(next);
  }
  return (
    <fieldset className="schema-object schema-kv">
      <legend>{label}</legend>
      {description && <small>{description}</small>}
      <ul>
        {Object.entries(value).map(([k, v]) => (
          <li key={k} className="schema-kv-row">
            <span className="schema-kv-key">{k}</span>
            {valueSchema === true ? (
              <JsonField value={v} onChange={(nv) => setKey(k, nv)} />
            ) : (
              <SchemaField
                pluginId={pluginId}
                name={k}
                schema={valueSchema}
                required={false}
                value={v}
                onChange={(nv) => setKey(k, nv)}
              />
            )}
            <button type="button" aria-label={`Remove ${k}`} onClick={() => removeKey(k)}>
              ×
            </button>
          </li>
        ))}
      </ul>
      <div className="schema-kv-add">
        <input placeholder="key" value={newKey} onChange={(e) => setNewKey(e.target.value)} />
        <button
          type="button"
          disabled={!newKey.trim() || newKey in value}
          onClick={() => {
            setKey(newKey.trim(), {});
            setNewKey("");
          }}
        >
          + add
        </button>
      </div>
    </fieldset>
  );
}
