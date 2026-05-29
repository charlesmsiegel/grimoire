import { useState } from "react";

import { SheetRenderer } from "../../../sheets/SheetRenderer";
import type { SheetSchema } from "../../../sheets/types";
import { FieldEditor } from "./FieldEditor";
import { fieldsToSchema, schemaToFields, type FieldModel } from "./schemaModel";
import { WIDGET_NAMES } from "./widgetConfig";

interface Props {
  title: string;
  value: SheetSchema;
  onChange: (next: SheetSchema) => void;
  /** Module id used to scope the live preview (defaults to "preview"). */
  moduleId?: string;
  /** Optional theme CSS to apply in the live preview. */
  themeCss?: string;
}

export function SchemaBuilder({ title, value, onChange, moduleId = "preview", themeCss }: Props) {
  const [raw, setRaw] = useState(false);
  const [rawText, setRawText] = useState(() => JSON.stringify(value, null, 2));
  const [rawError, setRawError] = useState<string | null>(null);
  const fields = schemaToFields(value);

  function emit(next: FieldModel[]) {
    onChange(fieldsToSchema(next, title));
  }

  function addField() {
    emit([
      ...fields,
      {
        key: `field_${fields.length + 1}`,
        widget: WIDGET_NAMES[0]!,
        required: false,
        config: {},
      },
    ]);
  }

  function updateField(index: number, next: FieldModel) {
    emit(fields.map((f, i) => (i === index ? next : f)));
  }

  function removeField(index: number) {
    emit(fields.filter((_, i) => i !== index));
  }

  function enterRaw() {
    setRawText(JSON.stringify(value, null, 2));
    setRawError(null);
    setRaw(true);
  }

  function applyRaw(text: string) {
    setRawText(text);
    try {
      const parsed = JSON.parse(text) as SheetSchema;
      setRawError(null);
      onChange(parsed);
    } catch (err) {
      setRawError(err instanceof Error ? err.message : "invalid JSON");
    }
  }

  return (
    <div className="schema-builder">
      <div className="schema-builder-toolbar">
        {raw ? (
          <button type="button" onClick={() => setRaw(false)}>
            Visual editor
          </button>
        ) : (
          <button type="button" onClick={enterRaw}>
            Raw JSON
          </button>
        )}
      </div>

      <div className="schema-builder-body">
        <div className="schema-builder-edit">
          {raw ? (
            <>
              <label htmlFor="schema-json">Schema JSON</label>
              <textarea
                id="schema-json"
                aria-label="Schema JSON"
                rows={20}
                value={rawText}
                onChange={(e) => applyRaw(e.target.value)}
              />
              {rawError && (
                <p className="library-error" role="alert">
                  {rawError}
                </p>
              )}
            </>
          ) : (
            <>
              {fields.map((field, i) => (
                <FieldEditor
                  key={i}
                  field={field}
                  onChange={(next) => updateField(i, next)}
                  onRemove={() => removeField(i)}
                />
              ))}
              <button type="button" onClick={addField}>
                Add field
              </button>
            </>
          )}
        </div>

        <div className="schema-builder-preview">
          <h5>Preview</h5>
          <SheetRenderer
            moduleId={moduleId}
            schema={value}
            value={{}}
            onChange={() => {}}
            themeCss={themeCss}
            readOnly
          />
        </div>
      </div>
    </div>
  );
}
