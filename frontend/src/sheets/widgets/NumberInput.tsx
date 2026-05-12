import type { WidgetProps } from "../types";

export function NumberWidget({
  property,
  name,
  value,
  onChange,
  readOnly,
}: WidgetProps<number | null>) {
  const isInt = property.type === "integer";
  return (
    <input
      type="number"
      className="sheet-widget sheet-number"
      id={`sheet-${name}`}
      step={isInt ? 1 : "any"}
      min={property.min}
      max={property.max}
      value={value === null || value === undefined ? "" : value}
      readOnly={readOnly}
      onChange={(e) => {
        const raw = e.target.value;
        if (raw === "") {
          onChange(null);
          return;
        }
        const parsed = isInt ? parseInt(raw, 10) : parseFloat(raw);
        onChange(Number.isNaN(parsed) ? null : parsed);
      }}
    />
  );
}
