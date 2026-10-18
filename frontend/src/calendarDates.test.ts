import { dateLabel, dateParts } from "./calendarDates";

const GREGORIAN_2026 = [
  { key: "01", name: "January", days: 31 }, { key: "02", name: "February", days: 28 },
];

test("a scene's date reads with its calendar's month name, its time of day left out", () => {
  expect(dateLabel(dateParts("2026-01-02T18:30"), GREGORIAN_2026)).toBe("January 2, 2026");
});

test("a calendar's own month keys are names the calendar gives them", () => {
  expect(dateLabel(dateParts("1492-Mirtul-05"), [{ key: "Mirtul", name: "Mirtul", days: 30 }]))
    .toBe("Mirtul 5, 1492");
});

test("a month nobody has named yet leaves the date as written, not half-named", () => {
  expect(dateLabel(dateParts("2026-02-14T09:00"), [])).toBe("2026-02-14");
  expect(dateLabel(dateParts("--01-02"), [])).toBe("--01-02");
});

test("a birthday's missing year or day leaves those parts out", () => {
  expect(dateParts("--01-02")).toEqual({ year: "", month: "01", day: "02" });
  expect(dateLabel(dateParts("--01-02"), GREGORIAN_2026)).toBe("January 2");
  expect(dateLabel(dateParts("2026-02"), GREGORIAN_2026)).toBe("February, 2026");
  expect(dateLabel(dateParts("2026"), GREGORIAN_2026)).toBe("2026");
});
