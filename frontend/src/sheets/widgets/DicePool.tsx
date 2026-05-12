import type { DicePoolValue, WidgetProps } from "../types";

export function DicePoolWidget({
  property,
  name,
  value,
  onChange,
  readOnly,
}: WidgetProps<DicePoolValue>) {
  const currentField = property.currentField ?? "current";
  const maxField = property.maxField ?? "max";
  const safe: DicePoolValue = value ?? {};
  const current = safe[currentField] ?? 0;
  const max = safe[maxField] ?? 0;

  const update = (field: string, next: number) => {
    if (readOnly) return;
    onChange({ ...safe, [field]: next });
  };

  return (
    <div className="sheet-widget sheet-dice-pool" id={`sheet-${name}`}>
      <label className="sheet-dice-pool-field">
        <span>Current</span>
        <input
          type="number"
          value={current}
          readOnly={readOnly}
          onChange={(e) => update(currentField, parseInt(e.target.value, 10) || 0)}
        />
      </label>
      <span aria-hidden="true">/</span>
      <label className="sheet-dice-pool-field">
        <span>Max</span>
        <input
          type="number"
          value={max}
          readOnly={readOnly}
          onChange={(e) => update(maxField, parseInt(e.target.value, 10) || 0)}
        />
      </label>
      <button
        type="button"
        className="sheet-dice-pool-roll"
        disabled={readOnly || current <= 0}
        data-action="roll"
        data-field={currentField}
        data-pool={current}
      >
        Roll {current}
      </button>
    </div>
  );
}
