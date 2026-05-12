import type { WidgetProps } from "../types";

export function TextWidget({ name, value, onChange, readOnly }: WidgetProps<string>) {
  return (
    <input
      type="text"
      className="sheet-widget sheet-text"
      id={`sheet-${name}`}
      value={value ?? ""}
      readOnly={readOnly}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}
