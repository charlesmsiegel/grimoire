import { useEffect, useRef, useState } from "react";
import { api, splitNativeDate, type CalendarMonth, type CalendarScope } from "../api/client";

// "1492-Mirtul-05" -> ["1492", "Mirtul", "5"]; tolerates negative years.
function parseParts(dateOnly: string): [string, string, string] {
  const m = dateOnly.match(/^(-?\d+)-(.+)-(\d{1,2})$/);
  return m ? [m[1], m[2], String(parseInt(m[3], 10))] : ["", "", ""];
}

export function CalendarDatePicker({ scope, value, onChange, ariaLabel }: {
  scope: CalendarScope; value: string; onChange: (native: string) => void; ariaLabel: string;
}) {
  const [initYear, initMonth, initDay] = parseParts(splitNativeDate(value).date);
  const [year, setYear] = useState(initYear);
  const [month, setMonth] = useState(initMonth);
  const [day, setDay] = useState(initDay);
  const [months, setMonths] = useState<CalendarMonth[]>([]);

  // Every emit is recorded so the value-sync effect can tell our own echo
  // (parent state -> value prop) apart from a genuinely external change.
  const lastEmitted = useRef(value);
  function emitChange(x: string) {
    lastEmitted.current = x;
    onChange(x);
  }

  // An EXTERNAL value change (e.g. a suggested date arriving asynchronously)
  // re-syncs the fields; our own emits echoing back are ignored so an
  // in-progress edit is never clobbered.
  useEffect(() => {
    if (value === lastEmitted.current) return;
    lastEmitted.current = value;
    const [y, m, d] = parseParts(splitNativeDate(value).date);
    setYear(y); setMonth(m); setDay(d);
  }, [value]);

  useEffect(() => {
    const n = parseInt(year, 10);
    if (isNaN(n)) { setMonths([]); return; }
    let stale = false;
    api.getCalendarMonths(scope, n)
      .then((r) => { if (!stale) setMonths(r.months); })
      .catch(() => { if (!stale) setMonths([]); });
    return () => { stale = true; };
  }, [scope.kind, scope.id, year]);

  // A year change can invalidate the month (Shieldmeet, Adar I/II) or shrink
  // it under the selected day (Cheshvan/Kislev vary 29↔30 between years).
  useEffect(() => {
    if (!months.length) return;
    const entry = months.find((m) => m.key === month);
    if (month && !entry) {
      setMonth(""); setDay(""); emitChange("");
    } else if (entry && day && parseInt(day, 10) > entry.days) {
      setDay(""); emitChange("");
    }
  }, [months, month, day]);

  function emit(y: string, mKey: string, d: string) {
    const n = parseInt(y, 10);
    if (!isNaN(n) && mKey && d) emitChange(`${y}-${mKey}-${d.padStart(2, "0")}`);
    else emitChange("");
  }

  const entry = months.find((m) => m.key === month);
  const dayCount = entry?.days ?? 0;
  return (
    <span className="date-picker">
      <input type="number" aria-label={`${ariaLabel} year`} value={year}
             onChange={(e) => { setYear(e.target.value); emit(e.target.value, month, day); }} />
      <select aria-label={`${ariaLabel} month`} value={month} disabled={!months.length}
              onChange={(e) => { setMonth(e.target.value); setDay(""); emitChange(""); }}>
        <option value="">— month —</option>
        {months.map((m) => <option key={m.key} value={m.key}>{m.name}</option>)}
      </select>
      <select aria-label={`${ariaLabel} day`} value={day} disabled={!entry}
              onChange={(e) => { setDay(e.target.value); emit(year, month, e.target.value); }}>
        <option value="">—</option>
        {Array.from({ length: dayCount }, (_, i) => String(i + 1)).map((d) =>
          <option key={d} value={d}>{d}</option>)}
      </select>
    </span>
  );
}
