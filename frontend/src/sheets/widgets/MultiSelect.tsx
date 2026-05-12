import type { WidgetProps } from "../types";

export function MultiSelectWidget({
  property,
  name,
  value,
  onChange,
  readOnly,
}: WidgetProps<ReadonlyArray<string | number> | null>) {
  const options = property.enum ?? [];
  const selected = new Set((value ?? []).map((v) => String(v)));
  const toggle = (opt: string | number) => {
    if (readOnly) return;
    const key = String(opt);
    const next = new Set(selected);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    onChange(options.filter((o) => next.has(String(o))));
  };
  return (
    <div
      className="sheet-widget sheet-multi-select"
      id={`sheet-${name}`}
      role="group"
      aria-label={property.title ?? name}
    >
      {options.map((opt) => {
        const key = String(opt);
        return (
          <label key={key} className="sheet-multi-select-option">
            <input
              type="checkbox"
              checked={selected.has(key)}
              disabled={readOnly}
              onChange={() => toggle(opt)}
            />
            {key}
          </label>
        );
      })}
    </div>
  );
}
