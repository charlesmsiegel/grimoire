import { StructuredValueEditor } from "./StructuredValueEditor";

interface MonthRow {
  name: string;
  days: number;
}
interface SeasonRow {
  name: string;
  start_month: number;
  start_day: number;
  palette: string;
  weather_bias: Record<string, number>;
}
interface HolidayRow {
  name: string;
  month: number;
  day: number;
  description: string;
  tags: string[];
}

export interface WorldCalendar {
  epoch: string;
  days_per_week: number;
  week_day_names: string[];
  months: MonthRow[];
  seasons: SeasonRow[];
  holidays: HolidayRow[];
  extras: Record<string, unknown>;
}

const CANONICAL_KEYS = new Set([
  "epoch",
  "days_per_week",
  "week_day_names",
  "months",
  "seasons",
  "holidays",
]);

export function parseCalendar(raw: unknown): WorldCalendar {
  const obj =
    raw && typeof raw === "object" && !Array.isArray(raw)
      ? (raw as Record<string, unknown>)
      : ({} as Record<string, unknown>);
  const extras: Record<string, unknown> = {};
  for (const [k, v] of Object.entries(obj)) {
    if (!CANONICAL_KEYS.has(k)) extras[k] = v;
  }
  return {
    epoch: typeof obj.epoch === "string" ? obj.epoch : "",
    days_per_week: typeof obj.days_per_week === "number" ? obj.days_per_week : 7,
    week_day_names: Array.isArray(obj.week_day_names)
      ? obj.week_day_names.filter((s): s is string => typeof s === "string")
      : [],
    months: Array.isArray(obj.months) ? (obj.months as MonthRow[]) : [],
    seasons: Array.isArray(obj.seasons) ? (obj.seasons as SeasonRow[]) : [],
    holidays: Array.isArray(obj.holidays) ? (obj.holidays as HolidayRow[]) : [],
    extras,
  };
}

export function serializeCalendar(cal: WorldCalendar): Record<string, unknown> {
  return {
    epoch: cal.epoch,
    days_per_week: cal.days_per_week,
    week_day_names: cal.week_day_names,
    months: cal.months,
    seasons: cal.seasons,
    holidays: cal.holidays,
    ...cal.extras,
  };
}

interface Props {
  value: WorldCalendar;
  onChange: (next: WorldCalendar) => void;
}

export function WorldCalendarForm({ value, onChange }: Props) {
  const patch = (next: Partial<WorldCalendar>) => onChange({ ...value, ...next });

  return (
    <fieldset className="world-meta-fieldset">
      <legend>Calendar</legend>
      <label>
        <span>Epoch</span>
        <input
          type="date"
          value={value.epoch}
          onChange={(e) => patch({ epoch: e.target.value })}
        />
      </label>
      <label>
        <span>Days per week</span>
        <input
          type="number"
          min={1}
          max={20}
          value={value.days_per_week}
          onChange={(e) => patch({ days_per_week: Number(e.target.value) })}
        />
      </label>
      <fieldset>
        <legend>Week day names</legend>
        <StructuredValueEditor
          value={value.week_day_names}
          onChange={(next) =>
            patch({
              week_day_names: Array.isArray(next)
                ? (next as unknown[]).filter((s): s is string => typeof s === "string")
                : [],
            })
          }
        />
      </fieldset>
      <fieldset className="world-meta-extras">
        <legend>Other calendar fields</legend>
        <StructuredValueEditor
          value={value.extras}
          onChange={(next) =>
            patch({
              extras:
                next && typeof next === "object" && !Array.isArray(next)
                  ? (next as Record<string, unknown>)
                  : {},
            })
          }
        />
      </fieldset>
      {/* months, seasons, holidays arrive in the next task */}
    </fieldset>
  );
}
