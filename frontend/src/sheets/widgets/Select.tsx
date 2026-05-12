import type { WidgetProps } from "../types";

export function SelectWidget({
  property,
  name,
  value,
  onChange,
  readOnly,
}: WidgetProps<string | number | null>) {
  const options = property.enum ?? [];
  return (
    <select
      className="sheet-widget sheet-select"
      id={`sheet-${name}`}
      disabled={readOnly}
      value={value === null || value === undefined ? "" : String(value)}
      onChange={(e) => {
        const raw = e.target.value;
        if (raw === "") {
          onChange(null);
          return;
        }
        const match = options.find((opt) => String(opt) === raw);
        onChange(match ?? raw);
      }}
    >
      <option value="">—</option>
      {options.map((opt) => (
        <option key={String(opt)} value={String(opt)}>
          {String(opt)}
        </option>
      ))}
    </select>
  );
}
