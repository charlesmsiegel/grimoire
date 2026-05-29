import { useState } from "react";

import type { FieldModel } from "./schemaModel";
import { WIDGET_CONFIG, WIDGET_NAMES, type ConfigFieldDef } from "./widgetConfig";

interface Props {
  field: FieldModel;
  onChange: (next: FieldModel) => void;
  onRemove: () => void;
}

export function FieldEditor({ field, onChange, onRemove }: Props) {
  const [raw, setRaw] = useState(false);
  const def = WIDGET_CONFIG[field.widget];

  function setConfig(key: string, value: unknown) {
    onChange({ ...field, config: { ...field.config, [key]: value } });
  }

  function renderConfigInput(cf: ConfigFieldDef) {
    const value = field.config[cf.key];
    const id = `cfg-${field.key}-${cf.key}`;
    switch (cf.input) {
      case "number":
        return (
          <input
            id={id}
            type="number"
            value={value === undefined ? "" : String(value)}
            onChange={(e) =>
              setConfig(cf.key, e.target.value === "" ? undefined : Number(e.target.value))
            }
          />
        );
      case "boolean":
        return (
          <input
            id={id}
            type="checkbox"
            checked={Boolean(value)}
            onChange={(e) => setConfig(cf.key, e.target.checked)}
          />
        );
      case "string-list":
        return (
          <input
            id={id}
            type="text"
            value={Array.isArray(value) ? value.join(", ") : ""}
            placeholder="comma,separated"
            onChange={(e) =>
              setConfig(
                cf.key,
                e.target.value
                  .split(",")
                  .map((s) => s.trim())
                  .filter(Boolean),
              )
            }
          />
        );
      case "json":
        return (
          <textarea
            id={id}
            rows={3}
            value={value === undefined ? "" : JSON.stringify(value)}
            onChange={(e) => {
              try {
                setConfig(cf.key, e.target.value === "" ? undefined : JSON.parse(e.target.value));
              } catch {
                /* keep typing; ignore parse errors until valid */
              }
            }}
          />
        );
      default:
        return (
          <input
            id={id}
            type="text"
            value={value === undefined ? "" : String(value)}
            onChange={(e) => setConfig(cf.key, e.target.value || undefined)}
          />
        );
    }
  }

  return (
    <fieldset className="field-editor">
      <div className="field-editor-row">
        <label htmlFor={`key-${field.key}`}>Field key</label>
        <input
          id={`key-${field.key}`}
          aria-label="Field key"
          value={field.key}
          onChange={(e) => onChange({ ...field, key: e.target.value })}
        />
        <label htmlFor={`widget-${field.key}`}>Widget</label>
        <select
          id={`widget-${field.key}`}
          aria-label="Widget"
          value={field.widget}
          onChange={(e) =>
            onChange({ ...field, widget: e.target.value as FieldModel["widget"], config: {} })
          }
        >
          {WIDGET_NAMES.map((w) => (
            <option key={w} value={w}>
              {w}
            </option>
          ))}
        </select>
        <label>
          <input
            type="checkbox"
            checked={field.required}
            onChange={(e) => onChange({ ...field, required: e.target.checked })}
          />
          Required
        </label>
        <button type="button" onClick={() => setRaw((r) => !r)}>
          {raw ? "Form" : "Raw JSON"}
        </button>
        <button type="button" onClick={onRemove}>
          Remove
        </button>
      </div>

      {raw ? (
        <textarea
          aria-label={`raw config for ${field.key}`}
          rows={4}
          value={JSON.stringify(field.config, null, 2)}
          onChange={(e) => {
            try {
              onChange({ ...field, config: JSON.parse(e.target.value) });
            } catch {
              /* ignore until valid JSON */
            }
          }}
        />
      ) : (
        <>
          <label htmlFor={`title-${field.key}`}>Label</label>
          <input
            id={`title-${field.key}`}
            value={(field.config.title as string) ?? ""}
            onChange={(e) => setConfig("title", e.target.value || undefined)}
          />
          {def.fields.map((cf) => (
            <div className="field-editor-config" key={cf.key}>
              <label htmlFor={`cfg-${field.key}-${cf.key}`}>{cf.label}</label>
              {renderConfigInput(cf)}
            </div>
          ))}
        </>
      )}
    </fieldset>
  );
}
