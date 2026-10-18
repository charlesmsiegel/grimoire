import { useEffect, useState } from "react";
import { api, type CalendarMonth, type CalendarScope } from "../api/client";
import { dateLabel, dateParts, nativeDate } from "../calendarDates";

const NO_MONTHS: CalendarMonth[] = [];

/** A birthday has optional precision. The scene date picker still requires a day. */
function useBirthdateMonths(scope: CalendarScope, year: string): CalendarMonth[] {
  const monthKey = `${scope.kind}:${scope.id}:${year}`;
  const [loaded, setLoaded] = useState<{ key: string; months: CalendarMonth[] }>(
    { key: "", months: [] });
  const months = loaded.key === monthKey ? loaded.months : NO_MONTHS;

  useEffect(() => {
    let live = true;
    const target = { kind: scope.kind, id: scope.id };
    async function load() {
      try {
        let years: number[];
        if (year) {
          years = [Number(year)];
        } else {
          const cfg = await api.getCalendarConfig(target);
          const provider = cfg.primary.provider;
          const anchor = Number(cfg.primary.anchor?.native.match(/^-?\d+/)?.[0]);
          const reference = provider === "hebrew" ? 5784
            : provider === "gregorian" ? 2024 : (Number.isFinite(anchor) ? anchor : 2024);
          // Adjacent years offer both ordinary and leap-only months. Day
          // counts take the larger value so February 29 remains selectable.
          years = [reference, reference + 1];
        }
        const results = await Promise.allSettled(years.map((y) => api.getCalendarMonths(target, y)));
        const merged = new Map<string, CalendarMonth>();
        for (const result of results) {
          if (result.status !== "fulfilled") continue;
          for (const entry of result.value.months) {
            const prior = merged.get(entry.key);
            if (!prior || entry.days > prior.days) merged.set(entry.key, entry);
          }
        }
        if (live) setLoaded({ key: monthKey, months: [...merged.values()] });
      } catch {
        if (live) setLoaded({ key: monthKey, months: [] });
      }
    }
    void load();
    return () => { live = false; };
  }, [scope.kind, scope.id, year, monthKey]);

  return months;
}

export function BirthdateDisplay({ scope, value }: { scope: CalendarScope; value: string }) {
  const parts = dateParts(value);
  const months = useBirthdateMonths(scope, parts.year);
  return <span>{dateLabel(parts, months)}</span>;
}

export function BirthdatePicker({ scope, value, onChange, ariaLabel }: {
  scope: CalendarScope; value: string; onChange: (native: string) => void; ariaLabel: string;
}) {
  const { year, month, day } = dateParts(value);
  const months = useBirthdateMonths(scope, year);

  const selected = months.find((m) => m.key === month);
  const dayCount = selected?.days ?? 0;
  useEffect(() => {
    if (month && months.length && !selected) {
      onChange(nativeDate({ year, month: "", day: "" }));
    } else if (day && selected && Number(day) > selected.days) {
      onChange(nativeDate({ year, month, day: "" }));
    }
  }, [day, month, months, onChange, selected, year]);

  return <span className="date-picker">
    <input type="number" aria-label={`${ariaLabel} year`} value={year}
           onChange={(e) => onChange(nativeDate({ year: e.target.value, month, day }))} />
    <select aria-label={`${ariaLabel} month`} value={month} disabled={!months.length}
            onChange={(e) => onChange(nativeDate({ year, month: e.target.value, day: "" }))}>
      <option value="">— month —</option>
      {months.map((m) => <option key={m.key} value={m.key}>{m.name}</option>)}
    </select>
    <input type="number" aria-label={`${ariaLabel} day`} value={day ? String(Number(day)) : ""}
           min={1} max={dayCount} step={1} disabled={!selected}
           onChange={(e) => {
             const next = e.target.value;
             if (!next || (/^\d{1,2}$/.test(next) && Number(next) >= 1 && Number(next) <= dayCount)) {
               onChange(nativeDate({ year, month, day: next }));
             }
           }} />
  </span>;
}
