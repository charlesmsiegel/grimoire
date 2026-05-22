import { StructuredValueEditor } from "./StructuredValueEditor";
import type { HolidayRow, MonthRow, SeasonRow, WorldCalendar } from "./world-calendar";

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
        <input type="date" value={value.epoch} onChange={(e) => patch({ epoch: e.target.value })} />
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
      <MonthsRows months={value.months} onChange={(next) => patch({ months: next })} />
      <SeasonsRows seasons={value.seasons} onChange={(next) => patch({ seasons: next })} />
      <HolidaysRows holidays={value.holidays} onChange={(next) => patch({ holidays: next })} />
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
    </fieldset>
  );
}

function MonthsRows({
  months,
  onChange,
}: {
  months: MonthRow[];
  onChange: (next: MonthRow[]) => void;
}) {
  const updateAt = (i: number, patch: Partial<MonthRow>) => {
    const out = months.slice();
    out[i] = { ...out[i]!, ...patch };
    onChange(out);
  };
  const removeAt = (i: number) => {
    const out = months.slice();
    out.splice(i, 1);
    onChange(out);
  };
  const append = () => onChange([...months, { name: "", days: 30 }]);
  return (
    <fieldset>
      <legend>Months</legend>
      {months.map((m, i) => (
        <div key={i} className="world-calendar-row">
          <label>
            <span>Name</span>
            <input
              type="text"
              value={m.name}
              onChange={(e) => updateAt(i, { name: e.target.value })}
            />
          </label>
          <label>
            <span>Days</span>
            <input
              type="number"
              min={1}
              max={400}
              value={m.days}
              onChange={(e) => updateAt(i, { days: Number(e.target.value) })}
            />
          </label>
          <button
            type="button"
            className="structured-remove"
            aria-label={`Remove month ${i + 1}`}
            onClick={() => removeAt(i)}
          >
            ×
          </button>
        </div>
      ))}
      <button type="button" className="structured-add" onClick={append}>
        + add month
      </button>
    </fieldset>
  );
}

function SeasonsRows({
  seasons,
  onChange,
}: {
  seasons: SeasonRow[];
  onChange: (next: SeasonRow[]) => void;
}) {
  const updateAt = (i: number, patch: Partial<SeasonRow>) => {
    const out = seasons.slice();
    out[i] = { ...out[i]!, ...patch };
    onChange(out);
  };
  const removeAt = (i: number) => {
    const out = seasons.slice();
    out.splice(i, 1);
    onChange(out);
  };
  const append = () =>
    onChange([
      ...seasons,
      { name: "", start_month: 1, start_day: 1, palette: "", weather_bias: {} },
    ]);
  return (
    <fieldset>
      <legend>Seasons</legend>
      {seasons.map((s, i) => (
        <div key={i} className="world-calendar-row">
          <label>
            <span>Name</span>
            <input
              type="text"
              value={s.name}
              onChange={(e) => updateAt(i, { name: e.target.value })}
            />
          </label>
          <label>
            <span>Start month</span>
            <input
              type="number"
              min={1}
              max={12}
              value={s.start_month}
              onChange={(e) => updateAt(i, { start_month: Number(e.target.value) })}
            />
          </label>
          <label>
            <span>Start day</span>
            <input
              type="number"
              min={1}
              max={31}
              value={s.start_day}
              onChange={(e) => updateAt(i, { start_day: Number(e.target.value) })}
            />
          </label>
          <label>
            <span>Palette</span>
            <input
              type="text"
              value={s.palette}
              onChange={(e) => updateAt(i, { palette: e.target.value })}
            />
          </label>
          <fieldset>
            <legend>Weather bias</legend>
            <StructuredValueEditor
              value={s.weather_bias}
              onChange={(next) =>
                updateAt(i, {
                  weather_bias:
                    next && typeof next === "object" && !Array.isArray(next)
                      ? (next as Record<string, number>)
                      : {},
                })
              }
            />
          </fieldset>
          <button
            type="button"
            className="structured-remove"
            aria-label={`Remove season ${i + 1}`}
            onClick={() => removeAt(i)}
          >
            ×
          </button>
        </div>
      ))}
      <button type="button" className="structured-add" onClick={append}>
        + add season
      </button>
    </fieldset>
  );
}

function HolidaysRows({
  holidays,
  onChange,
}: {
  holidays: HolidayRow[];
  onChange: (next: HolidayRow[]) => void;
}) {
  const updateAt = (i: number, patch: Partial<HolidayRow>) => {
    const out = holidays.slice();
    out[i] = { ...out[i]!, ...patch };
    onChange(out);
  };
  const removeAt = (i: number) => {
    const out = holidays.slice();
    out.splice(i, 1);
    onChange(out);
  };
  const append = () =>
    onChange([...holidays, { name: "", month: 1, day: 1, description: "", tags: [] }]);
  return (
    <fieldset>
      <legend>Holidays</legend>
      {holidays.map((h, i) => (
        <div key={i} className="world-calendar-row">
          <label>
            <span>Name</span>
            <input
              type="text"
              value={h.name}
              onChange={(e) => updateAt(i, { name: e.target.value })}
            />
          </label>
          <label>
            <span>Month</span>
            <input
              type="number"
              min={1}
              max={12}
              value={h.month}
              onChange={(e) => updateAt(i, { month: Number(e.target.value) })}
            />
          </label>
          <label>
            <span>Day</span>
            <input
              type="number"
              min={1}
              max={31}
              value={h.day}
              onChange={(e) => updateAt(i, { day: Number(e.target.value) })}
            />
          </label>
          <label>
            <span>Description</span>
            <input
              type="text"
              value={h.description}
              onChange={(e) => updateAt(i, { description: e.target.value })}
            />
          </label>
          <label>
            <span>Tags (comma separated)</span>
            <input
              type="text"
              value={h.tags.join(", ")}
              onChange={(e) =>
                updateAt(i, {
                  tags: e.target.value
                    .split(",")
                    .map((t) => t.trim())
                    .filter(Boolean),
                })
              }
            />
          </label>
          <button
            type="button"
            className="structured-remove"
            aria-label={`Remove holiday ${i + 1}`}
            onClick={() => removeAt(i)}
          >
            ×
          </button>
        </div>
      ))}
      <button type="button" className="structured-add" onClick={append}>
        + add holiday
      </button>
    </fieldset>
  );
}
