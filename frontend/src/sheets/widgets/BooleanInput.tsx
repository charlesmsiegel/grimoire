import type { WidgetProps } from "../types";

export function BooleanWidget({ property, name, value, onChange, readOnly }: WidgetProps<boolean>) {
  return (
    <label className="sheet-widget sheet-boolean" htmlFor={`sheet-${name}`}>
      <input
        type="checkbox"
        id={`sheet-${name}`}
        checked={Boolean(value)}
        disabled={readOnly}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span>{property.title ?? name}</span>
    </label>
  );
}
