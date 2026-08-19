import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { EventsPanel } from "./EventsPanel";
import { api } from "../api/client";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, api: {
    campaignEvents: vi.fn(),
    createCampaignEvent: vi.fn(),
    updateCampaignEvent: vi.fn(),
    unfireCampaignEvent: vi.fn(),
    deleteCampaignEvent: vi.fn(),
    // CalendarDatePicker (the date control on the form) fetches months.
    getCalendarMonths: vi.fn(),
  } };
});

const UPCOMING = {
  id: "coronation", name: "The coronation", date: "2026-12-26",
  friendly: "26 December 2026", note: "In the old hall.", fired: null, passed: false,
};
const FIRED = {
  id: "envoy", name: "The envoy arrives", date: "2026-12-01",
  friendly: "1 December 2026", note: "",
  fired: { at: "2026-12-01T10:00:00Z", moment: "2026-12-02" }, passed: false,
};

/** Fill the date control the way a reader does — year, then month, then day.
 *  The month and day selects only populate once the year's months arrive. */
async function pickDate(year = "2027", month = "01", day = "5") {
  fireEvent.change(screen.getByLabelText("Event date year"), { target: { value: year } });
  await waitFor(() => expect(screen.getByLabelText("Event date month")).toBeEnabled());
  fireEvent.change(screen.getByLabelText("Event date month"), { target: { value: month } });
  fireEvent.change(screen.getByLabelText("Event date day"), { target: { value: day } });
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.campaignEvents).mockResolvedValue(
    { events: [FIRED, UPCOMING], now: "2026-12-24", friendly: "24 December 2026" } as never);
  vi.mocked(api.createCampaignEvent).mockResolvedValue({ ok: true, id: "x" } as never);
  vi.mocked(api.updateCampaignEvent).mockResolvedValue({ ok: true } as never);
  vi.mocked(api.unfireCampaignEvent).mockResolvedValue({ ok: true } as never);
  vi.mocked(api.deleteCampaignEvent).mockResolvedValue({ ok: true } as never);
  // One real month, so the date control can actually compose a date: the
  // picker's day select stays disabled until the year's months are known.
  vi.mocked(api.getCalendarMonths).mockResolvedValue(
    { months: [{ key: "01", name: "January", days: 31 }] } as never);
});

test("lists what is scheduled, with the date in the campaign's own reckoning", async () => {
  render(<EventsPanel cid="c" />);
  expect(await screen.findByText(/The coronation — 26 December 2026/)).toBeInTheDocument();
  expect(screen.getByText("In the old hall.")).toBeInTheDocument();
});

test("says when the clock has already reached one", async () => {
  render(<EventsPanel cid="c" />);
  expect(await screen.findByText("fired")).toBeInTheDocument();
});

test("an empty campaign says so rather than showing nothing", async () => {
  vi.mocked(api.campaignEvents).mockResolvedValue({ events: [], now: "", friendly: "" } as never);
  render(<EventsPanel cid="c" />);
  expect(await screen.findByText("Nothing scheduled.")).toBeInTheDocument();
});

test("a new event needs both a name and a day", async () => {
  // An event with no day cannot fire, and one with no name is a row nobody can
  // read — so the button says so by being disabled rather than earning a 400.
  render(<EventsPanel cid="c" />);
  fireEvent.click(await screen.findByText("+ New event"));
  fireEvent.change(screen.getByLabelText("Event name"), { target: { value: "The eclipse" } });
  expect(screen.getByText("Save")).toBeDisabled();
});

test("saving a new event sends it and reloads the list", async () => {
  render(<EventsPanel cid="c" />);
  fireEvent.click(await screen.findByText("+ New event"));
  fireEvent.change(screen.getByLabelText("Event name"), { target: { value: "The eclipse" } });
  await pickDate();
  fireEvent.click(screen.getByText("Save"));
  await waitFor(() => expect(api.createCampaignEvent).toHaveBeenCalledWith(
    "c", { name: "The eclipse", date: "2027-01-05", note: "" }));
  expect(api.campaignEvents).toHaveBeenCalledTimes(2);
});

test("a rejected save keeps the form up and says why", async () => {
  vi.mocked(api.createCampaignEvent).mockRejectedValue({ detail: "not a date in this calendar" });
  render(<EventsPanel cid="c" />);
  fireEvent.click(await screen.findByText("+ New event"));
  fireEvent.change(screen.getByLabelText("Event name"), { target: { value: "Nonsense" } });
  await pickDate();
  fireEvent.click(screen.getByText("Save"));
  expect(await screen.findByText("not a date in this calendar")).toBeInTheDocument();
  expect(screen.getByLabelText("Event name")).toHaveValue("Nonsense");
});

test("unfire is offered only for an event the clock has reached", async () => {
  render(<EventsPanel cid="c" />);
  await screen.findByText(/The envoy arrives/);
  expect(screen.getAllByText("Unfire")).toHaveLength(1);
  fireEvent.click(screen.getByText("Unfire"));
  await waitFor(() => expect(api.unfireCampaignEvent).toHaveBeenCalledWith("c", "envoy"));
});

test("deleting removes it and reloads", async () => {
  render(<EventsPanel cid="c" />);
  await screen.findByText(/The coronation/);
  fireEvent.click(screen.getAllByText("Delete")[1]);
  await waitFor(() => expect(api.deleteCampaignEvent).toHaveBeenCalledWith("c", "coronation"));
});

test("a date the campaign's calendar cannot read still shows its raw value", async () => {
  // This row is the only place a reader can see, and fix, the value that broke.
  vi.mocked(api.campaignEvents).mockResolvedValue(
    { events: [{ ...UPCOMING, friendly: "" }], now: "", friendly: "" } as never);
  render(<EventsPanel cid="c" />);
  expect(await screen.findByText(/The coronation — 2026-12-26/)).toBeInTheDocument();
});

test("an event the campaign is already past says so, and says what to do", async () => {
  // No advance can fire it — a span starting at "now" cannot contain a day
  // behind it — so the label and the Edit button beside it are the whole remedy.
  vi.mocked(api.campaignEvents).mockResolvedValue(
    { events: [{ ...UPCOMING, passed: true }], now: "2026-12-31",
      friendly: "31 December 2026" } as never);
  render(<EventsPanel cid="c" />);
  expect(await screen.findByText("missed")).toBeInTheDocument();
  expect(screen.getByText(/already past this day/)).toBeInTheDocument();
});

test("an event can be re-dated in place", async () => {
  render(<EventsPanel cid="c" />);
  await screen.findByText(/The coronation/);
  fireEvent.click(screen.getAllByText("Edit")[1]);
  await pickDate("2027", "01", "5");
  fireEvent.click(screen.getByText("Save"));
  await waitFor(() => expect(api.updateCampaignEvent).toHaveBeenCalledWith(
    "c", "coronation", { name: "The coronation", date: "2027-01-05",
                         note: "In the old hall." }));
});

test("an edit form opens on the stored values", async () => {
  render(<EventsPanel cid="c" />);
  await screen.findByText(/The coronation/);
  fireEvent.click(screen.getAllByText("Edit")[1]);
  expect(screen.getByLabelText("Event name")).toHaveValue("The coronation");
  expect(screen.getByLabelText("Event note")).toHaveValue("In the old hall.");
});

test("a rejected edit keeps the form open with what was typed", async () => {
  vi.mocked(api.updateCampaignEvent).mockRejectedValue({ detail: "not a date in this calendar" });
  render(<EventsPanel cid="c" />);
  await screen.findByText(/The coronation/);
  fireEvent.click(screen.getAllByText("Edit")[1]);
  fireEvent.change(screen.getByLabelText("Event name"), { target: { value: "Renamed" } });
  fireEvent.click(screen.getByText("Save"));
  expect(await screen.findByText("not a date in this calendar")).toBeInTheDocument();
  expect(screen.getByLabelText("Event name")).toHaveValue("Renamed");
});
