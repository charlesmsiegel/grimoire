import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

// `ApiError` is re-exported real: the component narrows on it, and a mock that
// left it out would make every rejection look like a transport failure — which
// is exactly the case where the server's own words are the useful part.
vi.mock("../api/client", async () => ({
  ApiError: (await vi.importActual<typeof import("../api/client")>("../api/client")).ApiError,
  api: { listCalendarProviders: vi.fn(), getCalendarYear: vi.fn(),
         listWorlds: vi.fn().mockResolvedValue([]),
         listModules: vi.fn().mockResolvedValue([]),
         listStyles: vi.fn().mockResolvedValue([]),
         listResponsePresets: vi.fn().mockResolvedValue([]),
         listClimates: vi.fn().mockResolvedValue({ climates: [] }),
         listConnections: vi.fn().mockResolvedValue([]) },
}));

import { api } from "../api/client";
import CalendarsView from "./CalendarsView";

const GREG = {
  id: "gregorian", name: "Gregorian", year: 2026, region: "US",
  months: [{ key: "01", name: "January", days: 31 },
           { key: "02", name: "February", days: 28 }],
  holidays: [{ name: "New Year's Day", fixed: 739617, month_key: "01",
               month: 1, month_name: "January", day: 1, friendly: "1 January 2026" }],
};

const HEB = {
  id: "hebrew", name: "Hebrew", year: 5786, region: "",
  months: [{ key: "Tishrei", name: "Tishrei", days: 30 }],
  holidays: [{ name: "Rosh Hashana", fixed: 739500, month_key: "Tishrei",
               month: 1, month_name: "Tishrei", day: 1, friendly: "" }],
};

function renderCalendars() {
  return render(<MemoryRouter><CalendarsView /></MemoryRouter>);
}

beforeEach(() => {
  (api.listCalendarProviders as any).mockResolvedValue([
    { id: "gregorian", name: "Gregorian" },
    { id: "hebrew", name: "Hebrew" },
    { id: "harptos", name: "Calendar of Harptos" },
  ]);
  (api.getCalendarYear as any).mockResolvedValue(GREG);
});

test("every registered calendar is offered, plugins included", async () => {
  renderCalendars();
  expect(await screen.findByRole("button", { name: "Gregorian" })).toBeInTheDocument();
  // A homebrew calendar registered from the store's own directory is a
  // calendar like any other here.
  expect(screen.getByRole("button", { name: "Calendar of Harptos" })).toBeInTheDocument();
});

test("holidays are grouped under the month they land in", async () => {
  renderCalendars();
  await screen.findByText("January");
  const jan = screen.getByText("January").closest("li")!;
  expect(within(jan).getByText("New Year's Day")).toBeInTheDocument();
  // ...and not under a month it does not belong to.
  const feb = screen.getByText("February").closest("li")!;
  expect(within(feb).queryByText("New Year's Day")).not.toBeInTheDocument();
});

test("grouping uses the month key, not the month number", async () => {
  // The protocol's two halves disagree: `months()` yields a key ("01",
  // "Tishrei") and `describe()` a NUMBER (1, 12). Grouping by the number finds
  // no month and renders a year that observes nothing — which looks like an
  // answer, so nothing would report it.
  (api.getCalendarYear as any).mockResolvedValue(HEB);
  renderCalendars();
  await screen.findByText("Tishrei");
  const month = screen.getByText("Tishrei").closest("li")!;
  expect(within(month).getByText("Rosh Hashana")).toBeInTheDocument();
});

test("a calendar with no observances says so rather than showing an empty year", async () => {
  (api.getCalendarYear as any).mockResolvedValue({ ...GREG, region: "", holidays: [] });
  renderCalendars();
  expect(await screen.findByText(/no observances in this year/i)).toBeInTheDocument();
  // ...and where the calendar takes a region, it says that is why.
  expect(screen.getByText(/pick one above to see its holidays/i)).toBeInTheDocument();
});

test("the region control only appears for calendars that take one", async () => {
  renderCalendars();
  await screen.findByText("January");
  expect(screen.getByLabelText(/public holidays/i)).toBeInTheDocument();

  // Harptos is a homebrew calendar: its holidays are its own, and offering it
  // a country picker would be asking a question it has no answer to.
  (api.getCalendarYear as any).mockResolvedValue({
    id: "harptos", name: "Calendar of Harptos", year: 2026, region: "",
    months: [{ key: "01", name: "Hammer", days: 30 }], holidays: [],
  });
  fireEvent.click(screen.getByRole("button", { name: "Calendar of Harptos" }));
  await screen.findByText("Hammer");
  expect(screen.queryByLabelText(/public holidays/i)).not.toBeInTheDocument();
  expect(screen.queryByLabelText(/observance/i)).not.toBeInTheDocument();
});

test("the year is the calendar's own, and switching calendars does not carry it over", async () => {
  // 5786 means nothing to Harptos and 2026 means nothing to Hebrew, so the
  // year is dropped on a switch and the new calendar is asked for its own.
  renderCalendars();
  await screen.findByText("January");
  expect(screen.getByLabelText("Year")).toHaveValue(2026);

  (api.getCalendarYear as any).mockResolvedValue(HEB);
  fireEvent.click(screen.getByRole("button", { name: "Hebrew" }));
  await waitFor(() => expect(screen.getByLabelText("Year")).toHaveValue(5786));
  // The FIRST request for the new calendar named no year at all — that is what
  // makes the server resolve its own. (A second follows once the answer is
  // adopted, naming 5786; that one is the page echoing what it was told.)
  const hebrewCalls = (api.getCalendarYear as any).mock.calls
    .filter((c: unknown[]) => c[0] === "hebrew");
  expect(hebrewCalls[0][1]).toBeUndefined();
});

test("a calendar that cannot answer says so instead of showing an empty year", async () => {
  const { ApiError } = await vi.importActual<typeof import("../api/client")>("../api/client");
  (api.getCalendarYear as any).mockRejectedValue(
    new ApiError(400, "bad hebrew year: 1"));
  renderCalendars();
  expect(await screen.findByText(/bad hebrew year/i)).toBeInTheDocument();
  expect(screen.queryByText(/no observances/i)).not.toBeInTheDocument();
});
