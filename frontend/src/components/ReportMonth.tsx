import { useEffect } from "react";
import { useSearchParams } from "react-router-dom";

export function currentMonth(): string { return new Date().toISOString().slice(0, 7); }

export function shiftMonth(month: string, delta: number): string {
  const date = new Date(`${month}-01T00:00:00Z`);
  date.setUTCMonth(date.getUTCMonth() + delta);
  return date.toISOString().slice(0, 7);
}

export function useReportMonth(): [string, (month: string) => void] {
  const [params, setParams] = useSearchParams();
  const candidate = params.get("month") ?? "";
  const month = /^\d{4}-(0[1-9]|1[0-2])$/.test(candidate)
    && Number(candidate.slice(0, 4)) > 0 ? candidate : currentMonth();
  useEffect(() => {
    if (candidate !== month) setParams((previous) => {
      const changed = new URLSearchParams(previous);
      changed.set("month", month);
      return changed;
    }, { replace: true });
  }, [candidate, month, setParams]);
  const choose = (next: string) => setParams((previous) => {
    const changed = new URLSearchParams(previous);
    changed.set("month", next);
    return changed;
  });
  return [month, choose];
}

export default function ReportMonth({ month, available, onChange }: {
  month: string; available: string[]; onChange: (month: string) => void;
}) {
  const options = [...new Set([...available, month])].sort();
  return <nav aria-label="Cost month" className="stats-footer">
    <button type="button" onClick={() => onChange(shiftMonth(month, -1))}>Previous month</button>
    <select aria-label="Cost month" value={month} onChange={(e) => onChange(e.target.value)}>
      {options.map((value) => <option key={value} value={value}>{value}</option>)}
    </select>
    <button type="button" onClick={() => onChange(shiftMonth(month, 1))}>Next month</button>
  </nav>;
}
