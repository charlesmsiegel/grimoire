import { useRowIds } from "../../hooks/useRowIds";
import type { WidgetProps } from "../types";

export function SlotListWidget({
  property,
  name,
  value,
  onChange,
  readOnly,
}: WidgetProps<ReadonlyArray<string | null> | null>) {
  const size = property.size ?? 0;
  const slots = Array.from({ length: size }, (_, i) => value?.[i] ?? null);
  const { keys } = useRowIds(slots.length);

  const update = (idx: number, raw: string) => {
    if (readOnly) return;
    const next = slots.slice();
    next[idx] = raw === "" ? null : raw;
    onChange(next);
  };

  return (
    <ol
      className="sheet-widget sheet-slot-list"
      id={`sheet-${name}`}
      aria-label={property.title ?? name}
    >
      {slots.map((slot, idx) => (
        <li key={keys[idx]} className="sheet-slot">
          <span className="sheet-slot-index" aria-hidden="true">
            {idx + 1}
          </span>
          <input
            type="text"
            value={slot ?? ""}
            placeholder="—"
            readOnly={readOnly}
            onChange={(e) => update(idx, e.target.value)}
          />
        </li>
      ))}
    </ol>
  );
}
