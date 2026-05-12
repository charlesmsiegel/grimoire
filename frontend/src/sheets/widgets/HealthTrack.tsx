import type { HealthRow, SeverityLevel, WidgetProps } from "../types";

type RowValue = "" | "/" | "x" | "*";

interface HealthTrackValue {
  rows: ReadonlyArray<RowValue>;
}

const CYCLE: RowValue[] = ["", "/", "x", "*"];

function defaultRows(spec: HealthRow[] | number, severities: SeverityLevel[]): HealthRow[] {
  if (Array.isArray(spec)) return spec;
  const count = spec;
  if (severities.length === 0) {
    return Array.from({ length: count }, () => ({}));
  }
  // Distribute rows across severity levels (e.g. WoD 7-row classic).
  const perLevel = Math.max(1, Math.floor(count / severities.length));
  const out: HealthRow[] = [];
  for (let i = 0; i < count; i++) {
    const level = severities[Math.min(severities.length - 1, Math.floor(i / perLevel))];
    out.push({ severity: level?.name, label: level?.name });
  }
  return out;
}

export function HealthTrackWidget({
  property,
  name,
  value,
  onChange,
  readOnly,
}: WidgetProps<HealthTrackValue | null>) {
  const severities = (property.severity_levels ?? []) as SeverityLevel[];
  const rowSpec = property.rows ?? 7;
  const rows = defaultRows(
    Array.isArray(rowSpec) ? (rowSpec as HealthRow[]) : (rowSpec as number),
    severities,
  );
  const marks: ReadonlyArray<RowValue> = value?.rows ?? rows.map<RowValue>(() => "");

  const cycle = (idx: number) => {
    if (readOnly) return;
    const current = marks[idx] ?? "";
    const nextIdx = (CYCLE.indexOf(current) + 1) % CYCLE.length;
    const next = CYCLE[nextIdx] ?? "";
    const nextRows = marks.slice();
    nextRows[idx] = next;
    onChange({ rows: nextRows });
  };

  return (
    <div
      className="sheet-widget sheet-health-track"
      id={`sheet-${name}`}
      role="group"
      aria-label={property.title ?? name}
    >
      {rows.map((row, idx) => {
        const mark = marks[idx] ?? "";
        return (
          <button
            type="button"
            key={idx}
            className={`sheet-health-cell sheet-health-${mark || "empty"}`}
            data-severity={row.severity}
            aria-label={`${row.label ?? row.severity ?? "row"} ${idx + 1}: ${mark || "empty"}`}
            onClick={() => cycle(idx)}
            disabled={readOnly}
          >
            <span className="sheet-health-mark">{mark}</span>
            {row.label ? <span className="sheet-health-label">{row.label}</span> : null}
          </button>
        );
      })}
    </div>
  );
}
