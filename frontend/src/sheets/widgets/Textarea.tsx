import type { WidgetProps } from "../types";

export function TextareaWidget({ name, value, onChange, readOnly }: WidgetProps<string>) {
  return (
    <textarea
      className="sheet-widget sheet-textarea"
      id={`sheet-${name}`}
      rows={4}
      value={value ?? ""}
      readOnly={readOnly}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}
