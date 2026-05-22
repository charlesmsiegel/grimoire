import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import { CalendarsView } from "../CalendarsView";

vi.mock("../../../api/library", async () => {
  const actual = await vi.importActual<typeof import("../../../api/library")>(
    "../../../api/library",
  );
  return {
    ...actual,
    calendarsApi: {
      listCalendars: vi.fn().mockResolvedValue([
        {
          id: "gregorian",
          name: "Gregorian",
          description: "Civil calendar.",
          tags: ["solar"],
          system: "gregorian",
          builtin: true,
          custom: null,
          date_format: "",
          version: 0,
        },
        {
          id: "my-fantasy",
          name: "Fantasy Calendar",
          description: "",
          tags: [],
          system: "custom",
          builtin: false,
          custom: {
            months: [{ name: "Sunmoon", days: 30 }],
            days_per_week: 7,
            week_day_names: [],
            seasons: [],
            leap_rule: {
              kind: "none",
              cycle_short: 4,
              cycle_skip: 100,
              cycle_keep: 400,
              leap_days: 1,
              leap_day_month: 2,
              cycle_years: 0,
              leap_years_in_cycle: [],
              leap_month_name: "",
              leap_month_days: 30,
              leap_month_position: 1,
            },
            epoch_jdn: 1721426,
            era_name: "",
          },
          date_format: "",
          version: 1,
        },
      ]),
      getCalendar: vi.fn(),
      createCalendar: vi.fn(),
      updateCalendar: vi.fn(),
      deleteCalendar: vi.fn(),
      convertDate: vi.fn(),
      holidaysInYear: vi.fn(),
      listHolidaySets: vi.fn(),
      getHolidaySet: vi.fn(),
      createHolidaySet: vi.fn(),
      updateHolidaySet: vi.fn(),
      deleteHolidaySet: vi.fn(),
    },
  };
});

describe("CalendarsView", () => {
  it("lists built-in and custom calendars in separate groups", async () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <CalendarsView />
      </MemoryRouter>,
    );
    await waitFor(() => {
      expect(screen.getByText("Fantasy Calendar")).toBeInTheDocument();
    });
    expect(screen.getAllByText("Gregorian").length).toBeGreaterThan(0);
    expect(screen.getByText("Built-in")).toBeInTheDocument();
    expect(screen.getByText("Custom")).toBeInTheDocument();
  });
});
