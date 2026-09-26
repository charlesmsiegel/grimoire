import { useEffect, useState } from "react";
import { api, type CalendarMonth, type CalendarScope } from "../api/client";

type Parts = { year: string; month: string; day: string };
const NO_MONTHS: CalendarMonth[] = [];

function parse(value: string): Parts {
  if (value.startsWith("--")) {
    const rest = value.slice(2);
    const match = rest.match(/^(.+)-(\d{1,2})$/);
    return { year: "", month: match ? match[1] : rest, day: match ? match[2] : "" };
  }
  const full = value.match(/^(-?\d+)-(.+)-(\d{1,2})$/);
  if (full) return { year: full[1], month: full[2], day: full[3] };
  const month = value.match(/^(-?\d+)-(.+)$/);
  if (month) return { year: month[1], month: month[2], day: "" };
  return { year: value, month: "", day: "" };
}

function format({ year, month, day }: Parts): string {
  if (!month) return year;
  return `${year ? `${year}-` : "--"}${month}${day ? `-${day.padStart(2, "0")}` : ""}`;
}

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
  const { year, month, day } = parse(value);
  const months = useBirthdateMonths(scope, year);
  const label = months.find((m) => m.key === month)?.name ?? month;
  const shown = day ? `${label} ${Number(day)}` : label;
  return <span>{year && shown ? `${shown}, ${year}` : shown || year}</span>;
}

export function BirthdatePicker({ scope, value, onChange, ariaLabel }: {
  scope: CalendarScope; value: string; onChange: (native: string) => void; ariaLabel: string;
}) {
  const { year, month, day } = parse(value);
  const months = useBirthdateMonths(scope, year);

  const selected = months.find((m) => m.key === month);
  const dayCount = selected?.days ?? 0;
  useEffect(() => {
    if (month && months.length && !selected) {
      onChange(format({ year, month: "", day: "" }));
    } else if (day && selected && Number(day) > selected.days) {
      onChange(format({ year, month, day: "" }));
    }
  }, [day, month, months, onChange, selected, year]);

  return <span className="date-picker">
    <input type="number" aria-label={`${ariaLabel} year`} value={year}
           onChange={(e) => onChange(format({ year: e.target.value, month, day }))} />
    <select aria-label={`${ariaLabel} month`} value={month} disabled={!months.length}
            onChange={(e) => onChange(format({ year, month: e.target.value, day: "" }))}>
      <option value="">— month —</option>
      {months.map((m) => <option key={m.key} value={m.key}>{m.name}</option>)}
    </select>
    <input type="number" aria-label={`${ariaLabel} day`} value={day ? String(Number(day)) : ""}
           min={1} max={dayCount} step={1} disabled={!selected}
           onChange={(e) => {
             const next = e.target.value;
             if (!next || (/^\d{1,2}$/.test(next) && Number(next) >= 1 && Number(next) <= dayCount)) {
               onChange(format({ year, month, day: next }));
             }
           }} />
  </span>;
}
