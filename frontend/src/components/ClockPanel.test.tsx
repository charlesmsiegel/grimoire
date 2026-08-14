import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ClockPanel } from "./ClockPanel";
import { api } from "../api/client";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, api: {
    getCampaignClock: vi.fn(),
    previewAdvance: vi.fn(),
    advanceTime: vi.fn(),
    // CalendarDatePicker (the "skip to a date" control) fetches months.
    getCalendarMonths: vi.fn(),
    // Read only to know whether a calendar has been chosen for this campaign.
    getCalendarConfig: vi.fn(),
  } };
});

const CLOCK = {
  now: "2026-12-24", friendly: "24 December 2026",
  log: [{ from: "", to: "2026-12-24", reason: "the caravan sets out", at: "2026-12-01T10:00:00Z" }],
};

const DIGEST = {
  from: "2026-12-24", to: "2026-12-27",
  from_friendly: "24 December 2026", to_friendly: "27 December 2026",
  elapsed_days: 3, backward: false, truncated: false,
  holidays: [{ name: "Christmas Day", native: "2026-12-25", friendly: "25 December 2026", in_days: 1 }],
  birthdays: [{ name: "Seraphine", age: 36, native: "2026-12-26", friendly: "26 December 2026" }],
  open_threads: [{ id: "debt", title: "The moneylender's debt", status: "open",
                   last_scene: "001--a", latest_beat: "Interest accrues." }],
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getCampaignClock).mockResolvedValue(CLOCK as never);
  vi.mocked(api.previewAdvance).mockResolvedValue({ digest: DIGEST } as never);
  vi.mocked(api.advanceTime).mockResolvedValue(
    { ok: true, moved: true, now: "2026-12-27", friendly: "27 December 2026", digest: DIGEST } as never);
  vi.mocked(api.getCalendarMonths).mockResolvedValue({ months: [] } as never);
  vi.mocked(api.getCalendarConfig).mockResolvedValue({
    primary: { provider: "gregorian", region: "US", custom_holidays: [], anchor: null },
    secondary: null, confirmed: true } as never);
});

test("shows where the campaign's present is", async () => {
  render(<ClockPanel cid="c" />);
  expect(await screen.findByText(/Now: 24 December 2026/)).toBeInTheDocument();
});

test("says so when no campaign date exists yet", async () => {
  vi.mocked(api.getCampaignClock).mockResolvedValue({ now: "", friendly: "", log: [] } as never);
  render(<ClockPanel cid="c" />);
  expect(await screen.findByText(/No campaign date yet/)).toBeInTheDocument();
});

test("refuses to move time until the campaign has a calendar", async () => {
  // The same nudge the neighbouring When section gives. Recording a moment in the
  // default calendar's notation before the reader picks a different one leaves the
  // whole campaign holding a date its own calendar cannot read.
  vi.mocked(api.getCalendarConfig).mockResolvedValue({
    primary: { provider: "gregorian", region: "US", custom_holidays: [], anchor: null },
    secondary: null, confirmed: false } as never);
  render(<ClockPanel cid="c" />);
  expect(await screen.findByText(/Select a calendar in the When section/)).toBeInTheDocument();
  expect(screen.queryByText("Advance time")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Days")).not.toBeInTheDocument();
});

test("previews the digest without advancing", async () => {
  render(<ClockPanel cid="c" />);
  await screen.findByText(/Now: 24 December 2026/);
  fireEvent.change(screen.getByLabelText("Days"), { target: { value: "3" } });
  fireEvent.click(screen.getByText("Preview"));
  expect(await screen.findByText(/Would advance/)).toBeInTheDocument();
  expect(screen.getByText(/Christmas Day/)).toBeInTheDocument();
  expect(screen.getByText(/Seraphine turns 36/)).toBeInTheDocument();
  expect(screen.getByText("The moneylender's debt")).toBeInTheDocument();
  expect(api.previewAdvance).toHaveBeenCalledWith("c", { days: 3 });
  expect(api.advanceTime).not.toHaveBeenCalled();
});

test("advancing needs a reason", async () => {
  render(<ClockPanel cid="c" />);
  await screen.findByText(/Now: 24 December 2026/);
  expect(screen.getByText("Advance time")).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "three days of rain" } });
  expect(screen.getByText("Advance time")).toBeEnabled();
});

test("advances by days with the reason, then reports what it crossed", async () => {
  render(<ClockPanel cid="c" />);
  await screen.findByText(/Now: 24 December 2026/);
  fireEvent.change(screen.getByLabelText("Days"), { target: { value: "3" } });
  fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "three days of rain" } });
  fireEvent.click(screen.getByText("Advance time"));
  expect(await screen.findByText(/^Advanced/)).toBeInTheDocument();
  expect(api.advanceTime).toHaveBeenCalledWith("c", { days: 3, reason: "three days of rain" });
  // The clock is re-read, so the header line follows the move.
  await waitFor(() => expect(api.getCampaignClock).toHaveBeenCalledTimes(2));
});

test("a confirmed advance the clock was already at does not claim to have moved", async () => {
  vi.mocked(api.advanceTime).mockResolvedValue({
    ok: true, moved: false, now: "2026-12-24", friendly: "24 December 2026",
    digest: { ...DIGEST, from: "2026-12-24", to: "2026-12-24",
              to_friendly: "24 December 2026", elapsed_days: 0,
              holidays: [], birthdays: [] },
  } as never);
  render(<ClockPanel cid="c" />);
  await screen.findByText(/Now: 24 December 2026/);
  fireEvent.change(screen.getByLabelText("Days"), { target: { value: "0" } });
  fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "nowhere" } });
  fireEvent.click(screen.getByText("Advance time"));
  expect(await screen.findByText(/Already at/)).toBeInTheDocument();
  expect(screen.queryByText(/Advanced/)).not.toBeInTheDocument();
});

test("a landed advance disarms the button, so a stray second click cannot repeat it", async () => {
  render(<ClockPanel cid="c" />);
  await screen.findByText(/Now: 24 December 2026/);
  fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "three days of rain" } });
  fireEvent.click(screen.getByText("Advance time"));
  await screen.findByText(/^Advanced/);
  // The duration is deliberately left as typed; the cleared reason is what stops
  // the next click from skipping another three days.
  expect(screen.getByLabelText("Days")).toHaveValue(1);
  expect(screen.getByText("Advance time")).toBeDisabled();
  expect(api.advanceTime).toHaveBeenCalledTimes(1);
});

test("skips to a date instead of a duration when asked", async () => {
  render(<ClockPanel cid="c" />);
  await screen.findByText(/Now: 24 December 2026/);
  fireEvent.change(screen.getByLabelText("Advance by"), { target: { value: "date" } });
  expect(screen.queryByLabelText("Days")).not.toBeInTheDocument();
  expect(screen.getByLabelText("Skip to year")).toBeInTheDocument();
  // With no date picked there is nothing to preview or confirm.
  expect(screen.getByText("Preview")).toBeDisabled();
});

test("drops a previewed digest when the target changes", async () => {
  render(<ClockPanel cid="c" />);
  await screen.findByText(/Now: 24 December 2026/);
  fireEvent.click(screen.getByText("Preview"));
  await screen.findByText(/Would advance/);
  fireEvent.change(screen.getByLabelText("Days"), { target: { value: "9" } });
  // Confirming a skip the reader never previewed is the failure this prevents.
  expect(screen.queryByText(/Would advance/)).not.toBeInTheDocument();
});

test("an overlong span reports the span and says why it is not itemized", async () => {
  vi.mocked(api.previewAdvance).mockResolvedValue({ digest: {
    ...DIGEST, elapsed_days: 4000, truncated: true, holidays: [], birthdays: [],
  } } as never);
  render(<ClockPanel cid="c" />);
  await screen.findByText(/Now: 24 December 2026/);
  fireEvent.click(screen.getByText("Preview"));
  expect(await screen.findByText(/4000 days/)).toBeInTheDocument();
  expect(screen.getByText(/Too long a span to itemize/)).toBeInTheDocument();
});

test("a backward move is labelled as one", async () => {
  vi.mocked(api.previewAdvance).mockResolvedValue({ digest: {
    ...DIGEST, elapsed_days: -4, backward: true,
  } } as never);
  render(<ClockPanel cid="c" />);
  await screen.findByText(/Now: 24 December 2026/);
  fireEvent.click(screen.getByText("Preview"));
  expect(await screen.findByText(/4 days back/)).toBeInTheDocument();
});

test("surfaces a refused advance instead of pretending it landed", async () => {
  vi.mocked(api.advanceTime).mockRejectedValue({ detail: "no current date to advance from" });
  render(<ClockPanel cid="c" />);
  await screen.findByText(/Now: 24 December 2026/);
  fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "onwards" } });
  fireEvent.click(screen.getByText("Advance time"));
  expect(await screen.findByText(/no current date to advance from/)).toBeInTheDocument();
  expect(screen.queryByText(/Advanced/)).not.toBeInTheDocument();
});

test("lists the recent advances and why they happened", async () => {
  render(<ClockPanel cid="c" />);
  expect(await screen.findByText(/the caravan sets out/)).toBeInTheDocument();
});

test("a failed clock read degrades to the empty state", async () => {
  vi.mocked(api.getCampaignClock).mockRejectedValue(new Error("offline"));
  render(<ClockPanel cid="c" />);
  expect(await screen.findByText(/No campaign date yet/)).toBeInTheDocument();
});
