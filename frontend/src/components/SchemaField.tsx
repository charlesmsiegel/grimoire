/**
 * Renders a single JSON-schema property as a form field.
 *
 * Pulled out of the library plugin-config form so the startup wizard
 * can render the same config schemas without duplicating the renderer.
 * Honors the ``x-source: "models"`` extension by routing into
 * {@link PluginModelPicker} for catalog-aware model selection.
 */

import { FilePathPicker, type GGUFInfo } from "./FilePathPicker";
import { PluginModelPicker } from "./PluginModelPicker";
import type { JsonSchema } from "./schemaForm";

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

  if (type === "object" || type === "array") {
    return (
      <label>
        <span>
          {label} {required && <em>*</em>}
        </span>
        <textarea
          rows={4}
          value={JSON.stringify(value ?? (type === "array" ? [] : {}), null, 2)}
          onChange={(e) => {
            try {
              onChange(JSON.parse(e.target.value));
            } catch {
              /* keep last good value */
            }
          }}
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
