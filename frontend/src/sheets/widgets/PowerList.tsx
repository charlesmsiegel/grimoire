import type { PowerItem, WidgetProps } from "../types";

export function PowerListWidget({
  property,
  name,
  value,
  onChange,
  readOnly,
}: WidgetProps<ReadonlyArray<PowerItem> | null>) {
  const items: PowerItem[] = (value ?? []).slice();
  const update = (idx: number, patch: Partial<PowerItem>) => {
    if (readOnly) return;
    const next = items.slice();
    const prev = next[idx] ?? { name: "" };
    next[idx] = { ...prev, ...patch };
    onChange(next);
  };
  const add = () => {
    if (readOnly) return;
    onChange([...items, { name: "" }]);
  };
  const remove = (idx: number) => {
    if (readOnly) return;
    onChange(items.filter((_, i) => i !== idx));
  };

  return (
    <div
      className="sheet-widget sheet-power-list"
      id={`sheet-${name}`}
      role="group"
      aria-label={property.title ?? name}
    >
      <ul className="sheet-power-items">
        {items.map((item, idx) => (
          <li key={idx} className="sheet-power-item">
            <input
              type="text"
              className="sheet-power-name"
              placeholder="Name"
              value={item.name}
              readOnly={readOnly}
              onChange={(e) => update(idx, { name: e.target.value })}
            />
            <input
              type="number"
              className="sheet-power-rating"
              placeholder="•"
              value={item.rating ?? ""}
              readOnly={readOnly}
              onChange={(e) =>
                update(idx, {
                  rating: e.target.value === "" ? undefined : parseInt(e.target.value, 10) || 0,
                })
              }
            />
            <input
              type="text"
              className="sheet-power-source"
              placeholder="Source"
              value={item.source ?? ""}
              readOnly={readOnly}
              onChange={(e) => update(idx, { source: e.target.value })}
            />
            <textarea
              className="sheet-power-description"
              placeholder="Description"
              rows={2}
              value={item.description ?? ""}
              readOnly={readOnly}
              onChange={(e) => update(idx, { description: e.target.value })}
            />
            {!readOnly && (
              <button
                type="button"
                className="sheet-power-remove"
                onClick={() => remove(idx)}
                aria-label={`Remove ${item.name || "power"}`}
              >
                ×
              </button>
            )}
          </li>
        ))}
      </ul>
      {!readOnly && (
        <button type="button" className="sheet-power-add" onClick={add}>
          + Add
        </button>
      )}
    </div>
  );
}
