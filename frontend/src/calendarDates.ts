/** How a date written in a campaign's own calendar reads to a person.
 *
 *  A native date is `<year>-<month key>-<day>` -- `2026-01-02`, `1492-Mirtul-05`
 *  -- with an optional `Thh:mm`, and the month key is the calendar's spelling,
 *  not a name: only the calendar's months endpoint can say that `01` is
 *  January. So a reader holds that list and names the month off it, which is
 *  what keeps every calendar (a user's plugin included) reading in its own
 *  words without the client knowing any of them.
 *
 *  A leaf, with type imports only, for the reason `api/errors.ts` gives: the
 *  suites that render dates replace `api/client` wholesale. */
import { splitNativeDate, type CalendarMonth } from "./api/types";

export type DateParts = { year: string; month: string; day: string };

/** A native date's parts. A birthday may leave out the year (`--01-02`) or
 *  the day, so every part may be empty; a scene's time of day is dropped. */
export function dateParts(native: string): DateParts {
  const value = splitNativeDate(native).date;
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

/** The parts written back as a native date, the inverse of `dateParts`. */
export function nativeDate({ year, month, day }: DateParts): string {
  if (!month) return year;
  return `${year ? `${year}-` : "--"}${month}${day ? `-${day.padStart(2, "0")}` : ""}`;
}

/** "January 2, 2026": the month by the calendar's name for it, read off
 *  `months` (that year's list). A month the list does not name -- not loaded
 *  yet, or a calendar that could not be read -- leaves the date as it was
 *  written, which is still a date, rather than half-naming it. */
export function dateLabel(parts: DateParts, months: readonly CalendarMonth[]): string {
  const { year, month, day } = parts;
  const name = months.find((m) => m.key === month)?.name;
  if (month && name === undefined) return nativeDate(parts);
  const shown = day ? `${name} ${Number(day)}` : name ?? "";
  return year && shown ? `${shown}, ${year}` : shown || year;
}
