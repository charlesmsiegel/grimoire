import { useState } from "react";
import type { SchemaProperty, SheetValue, WidgetProps } from "../types";
import { renderField } from "../renderField";

export function NestedSectionWidget({
  property,
  name,
  value,
  onChange,
  readOnly,
}: WidgetProps<SheetValue | null>) {
  const [open, setOpen] = useState(true);
  const child = value ?? {};
  const props: Record<string, SchemaProperty> = property.properties ?? {};

  const update = (key: string, next: unknown) => {
    if (readOnly) return;
    onChange({ ...child, [key]: next });
  };

  const title = property.title ?? name;

  return (
    <fieldset className="sheet-widget sheet-nested-section" id={`sheet-${name}`} data-open={open}>
      <legend>
        <button
          type="button"
          className="sheet-nested-toggle"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
        >
          <span className="sheet-nested-caret" aria-hidden="true">
            {open ? "▾" : "▸"}
          </span>
          {title}
        </button>
      </legend>
      {open && (
        <div className="sheet-nested-body">
          {Object.entries(props).map(([key, propSchema]) =>
            renderField({
              name: key,
              property: propSchema,
              value: child[key],
              onChange: (next) => update(key, next),
              readOnly,
            }),
          )}
        </div>
      )}
    </fieldset>
  );
}
