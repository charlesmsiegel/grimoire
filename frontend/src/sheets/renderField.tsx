import type { ReactNode } from "react";
import type { SchemaProperty } from "./types";
import { resolveWidget } from "./widgets";

interface RenderFieldArgs {
  name: string;
  property: SchemaProperty;
  value: unknown;
  onChange: (next: unknown) => void;
  readOnly?: boolean;
}

/**
 * Render a single schema property as a labeled field, dispatching to the
 * widget named in `property.widget`. Falls back to the generic editor (with
 * an inline warning) when the widget name is unknown — the widget itself
 * surfaces the warning so it lives close to the field that triggered it.
 */
export function renderField({
  name,
  property,
  value,
  onChange,
  readOnly,
}: RenderFieldArgs): ReactNode {
  const Widget = resolveWidget(property.widget);
  const label = property.title ?? name;
  const hideLabel = property.widget === "boolean" || property.widget === "nested-section";

  return (
    <div className="sheet-field" data-widget={property.widget ?? "fallback"} key={name}>
      {!hideLabel && (
        <label className="sheet-field-label" htmlFor={`sheet-${name}`}>
          {label}
        </label>
      )}
      <Widget
        name={name}
        property={property}
        value={value as never}
        onChange={onChange as (next: never) => void}
        readOnly={readOnly}
      />
      {property.description && !hideLabel && (
        <p className="sheet-field-description">{property.description}</p>
      )}
    </div>
  );
}
