import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { parseCalendar, WorldCalendarForm } from "../WorldCalendarForm";

const SAKURA_CALENDAR = parseCalendar({
  epoch: "2025-04-08",
  days_per_week: 7,
  week_day_names: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
  months: [{ name: "January", days: 31 }],
  seasons: [],
  holidays: [],
});

describe("WorldCalendarForm — scalars", () => {
  it("renders epoch, days_per_week, week_day_names", () => {
    render(<WorldCalendarForm value={SAKURA_CALENDAR} onChange={vi.fn()} />);
    expect(screen.getByLabelText(/epoch/i)).toHaveValue("2025-04-08");
    expect(screen.getByLabelText(/days per week/i)).toHaveValue(7);
    expect(screen.getByDisplayValue("Mon")).toBeInTheDocument();
  });

  it("editing days_per_week fires onChange with updated calendar", () => {
    const onChange = vi.fn();
    render(<WorldCalendarForm value={SAKURA_CALENDAR} onChange={onChange} />);
    fireEvent.change(screen.getByLabelText(/days per week/i), { target: { value: "8" } });
    expect(onChange).toHaveBeenLastCalledWith({ ...SAKURA_CALENDAR, days_per_week: 8 });
  });
});
