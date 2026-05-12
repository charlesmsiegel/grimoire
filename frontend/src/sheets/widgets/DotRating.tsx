import type { WidgetProps } from "../types";

export function DotRatingWidget({
  property,
  name,
  value,
  onChange,
  readOnly,
}: WidgetProps<number>) {
  const min = property.min ?? 0;
  const max = property.max ?? 5;
  const halves = property.halves ?? false;
  const step = halves ? 0.5 : 1;
  const current = typeof value === "number" ? value : min;
  const stops: number[] = [];
  for (let v = min; v <= max; v = +(v + step).toFixed(2)) stops.push(v);

  const setValue = (next: number) => {
    if (readOnly) return;
    onChange(next === current ? Math.max(min, next - step) : next);
  };

  return (
    <div
      className="sheet-widget sheet-dot-rating"
      role="slider"
      id={`sheet-${name}`}
      aria-valuemin={min}
      aria-valuemax={max}
      aria-valuenow={current}
      aria-label={property.title ?? name}
      tabIndex={readOnly ? -1 : 0}
      onKeyDown={(e) => {
        if (readOnly) return;
        if (e.key === "ArrowRight" || e.key === "ArrowUp") {
          e.preventDefault();
          onChange(Math.min(max, current + step));
        } else if (e.key === "ArrowLeft" || e.key === "ArrowDown") {
          e.preventDefault();
          onChange(Math.max(min, current - step));
        }
      }}
    >
      {stops
        .filter((v) => v > 0 || min < 0)
        .map((stop) => {
          const filled = current >= stop;
          const half = halves && current + 0.5 === stop;
          return (
            <button
              type="button"
              key={stop}
              className={`sheet-dot${filled ? " filled" : ""}${half ? " half" : ""}`}
              aria-label={`${stop}`}
              onClick={() => setValue(stop)}
              disabled={readOnly}
            />
          );
        })}
      <span className="sheet-dot-readout" aria-hidden="true">
        {current}
      </span>
    </div>
  );
}
