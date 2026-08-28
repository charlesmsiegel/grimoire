import { act, render, screen, fireEvent, waitFor } from "@testing-library/react";
import { CalendarConfig } from "./CalendarConfig";

vi.mock("../api/client", () => ({
  api: { getCalendarConfig: vi.fn(), setCalendarConfig: vi.fn(), getCalendarProviders: vi.fn() },
}));
import { api } from "../api/client";

beforeEach(() => {
  vi.clearAllMocks();
  (api.getCalendarConfig as any).mockResolvedValue({
    primary: { provider: "gregorian", region: "US", custom_holidays: [], anchor: null },
    secondary: null, confirmed: false, stale_after_days: 30, warn_days: 7 });
  (api.setCalendarConfig as any).mockResolvedValue({ ok: true });
  (api.getCalendarProviders as any).mockResolvedValue({ providers: [
    { id: "gregorian", name: "Gregorian" }, { id: "hebrew", name: "Hebrew" },
    { id: "my-custom-calendar", name: "My Custom Calendar" },
  ] });
});

test("edits the region and saves", async () => {
  render(<CalendarConfig scope={{ kind: "campaign", id: "run" }} />);
  const sel = await screen.findByLabelText("Holidays region");
  fireEvent.change(sel, { target: { value: "GB" } });
  fireEvent.click(screen.getByRole("button", { name: /save/i }));
  await waitFor(() => expect(api.setCalendarConfig).toHaveBeenCalledWith({ kind: "campaign", id: "run" },
    expect.objectContaining({ primary: expect.objectContaining({ region: "GB" }) })));
});

test("selecting hebrew shows Observance and saves the Israel setting", async () => {
  render(<CalendarConfig scope={{ kind: "campaign", id: "run" }} />);
  const provider = await screen.findByLabelText("Calendar");
  fireEvent.change(provider, { target: { value: "hebrew" } });
  expect(screen.queryByLabelText("Holidays region")).toBeNull();
  const observance = screen.getByLabelText("Observance");
  fireEvent.change(observance, { target: { value: "IL" } });
  fireEvent.click(screen.getByRole("button", { name: /save/i }));
  await waitFor(() => expect(api.setCalendarConfig).toHaveBeenCalledWith({ kind: "campaign", id: "run" },
    expect.objectContaining({ primary: expect.objectContaining({ provider: "hebrew", region: "IL" }) })));
});

test("selecting a custom (user-authored) calendar hides both region and observance and saves", async () => {
  render(<CalendarConfig scope={{ kind: "campaign", id: "run" }} />);
  const provider = await screen.findByLabelText("Calendar");
  expect(screen.getByRole("option", { name: "My Custom Calendar" })).toBeInTheDocument();
  fireEvent.change(provider, { target: { value: "my-custom-calendar" } });
  expect(screen.queryByLabelText("Holidays region")).toBeNull();
  expect(screen.queryByLabelText("Observance")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /save/i }));
  await waitFor(() => expect(api.setCalendarConfig).toHaveBeenCalledWith({ kind: "campaign", id: "run" },
    expect.objectContaining({ primary: expect.objectContaining({ provider: "my-custom-calendar" }) })));
});

test("edits how long a record may go untouched before it is stale", async () => {
  // The campaign's one aging knob (#103), saved with the rest of its time
  // config — a client that dropped the field would reset it on every save.
  render(<CalendarConfig scope={{ kind: "campaign", id: "run" }} />);
  const days = await screen.findByLabelText("Stale after days");
  expect(days).toHaveValue(30);
  fireEvent.change(days, { target: { value: "7" } });
  fireEvent.click(screen.getByRole("button", { name: /save/i }));
  await waitFor(() => expect(api.setCalendarConfig).toHaveBeenCalledWith({ kind: "campaign", id: "run" },
    expect.objectContaining({ stale_after_days: 7 })));
});

// ---- world scope (#223) ----

test("a world's calendar is read and saved against the world, not a campaign", async () => {
  render(<CalendarConfig scope={{ kind: "world", id: "realm" }} />);
  await screen.findByLabelText("Calendar");
  expect(api.getCalendarConfig).toHaveBeenCalledWith({ kind: "world", id: "realm" });
  fireEvent.click(screen.getByRole("button", { name: /save/i }));
  await waitFor(() => expect(api.setCalendarConfig).toHaveBeenCalledWith(
    { kind: "world", id: "realm" }, expect.objectContaining({ confirmed: false })));
});

test("the Confirmed checkbox is what sets the flag world-side", async () => {
  // World-side there is no scene inspector to answer, so the flag needs a
  // control of its own — and it is the whole point of the world's copy: it is
  // what `create_campaign` writes into every campaign started from this world.
  render(<CalendarConfig scope={{ kind: "world", id: "realm" }} />);
  fireEvent.click(await screen.findByLabelText(/confirmed/i));
  fireEvent.click(screen.getByRole("button", { name: /save/i }));
  await waitFor(() => expect(api.setCalendarConfig).toHaveBeenCalledWith(
    { kind: "world", id: "realm" }, expect.objectContaining({ confirmed: true })));
});

test("a campaign's calendar has no confirmed control — the scene inspector owns that", async () => {
  render(<CalendarConfig scope={{ kind: "campaign", id: "run" }} />);
  await screen.findByLabelText("Calendar");
  expect(screen.queryByLabelText(/confirmed/i)).toBeNull();
});

test("the config it loads and the one it saves are both reported to its owner", async () => {
  // The world Overview's checklist row is derived from this and nothing else,
  // so a load that reported nothing would leave the row permanently absent.
  const onConfig = vi.fn();
  render(<CalendarConfig scope={{ kind: "world", id: "realm" }} onConfig={onConfig} />);
  await waitFor(() => expect(onConfig).toHaveBeenCalledWith(
    expect.objectContaining({ confirmed: false })));
  fireEvent.click(await screen.findByLabelText(/confirmed/i));
  fireEvent.click(screen.getByRole("button", { name: /save/i }));
  await waitFor(() => expect(onConfig).toHaveBeenLastCalledWith(
    expect.objectContaining({ confirmed: true })));
});

test("a failed load says so, rather than loading forever", async () => {
  // It reports nothing either, so an owner deriving a checklist row from it
  // cannot mistake a failed read for an unconfirmed calendar.
  (api.getCalendarConfig as any).mockRejectedValue(new Error("nope"));
  const onConfig = vi.fn();
  render(<CalendarConfig scope={{ kind: "world", id: "realm" }} onConfig={onConfig} />);
  expect(await screen.findByText(/could not load this calendar/i)).toBeInTheDocument();
  expect(screen.queryByText(/loading calendar/i)).toBeNull();
  expect(onConfig).not.toHaveBeenCalled();
});

test("the confirmed control comes before the aging threshold it outranks", async () => {
  // It is the decision the world Overview's checklist points at; burying it
  // under a number about stale threads puts the answer below the footnote.
  render(<CalendarConfig scope={{ kind: "world", id: "realm" }} />);
  const confirmed = await screen.findByLabelText("Confirmed");
  const stale = screen.getByLabelText("Stale after days");
  expect(confirmed.compareDocumentPosition(stale))
    .toBe(Node.DOCUMENT_POSITION_FOLLOWING);
});

test("the world's save says what it saves — Mechanics has a Save beside it", async () => {
  render(<CalendarConfig scope={{ kind: "world", id: "realm" }} />);
  expect(await screen.findByRole("button", { name: "Save calendar" })).toBeInTheDocument();
});

test("a scope change hides the form until the new scope's calendar has landed", async () => {
  // Otherwise the panel shows one record's calendar under another record's
  // Save: pressing it in that window writes the world you left into the world
  // you are looking at.
  (api.getCalendarConfig as any).mockImplementation((scope: { id: string }) =>
    scope.id === "realm"
      ? Promise.resolve({ primary: { provider: "hebrew", region: "IL", custom_holidays: [], anchor: null },
                          secondary: null, confirmed: true, stale_after_days: 30 })
      : new Promise(() => {}));                    // never lands
  const { rerender } = render(<CalendarConfig scope={{ kind: "world", id: "realm" }} />);
  expect(await screen.findByLabelText("Calendar")).toHaveValue("hebrew");
  rerender(<CalendarConfig scope={{ kind: "world", id: "saltmarch" }} />);
  expect(await screen.findByText(/loading calendar/i)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /save/i })).toBeNull();
});

test("the provider list is read once per mount, not again per scope", async () => {
  // It is a global: the built-in calendars plus whatever plugins the store
  // holds. Nothing about a scope changes it, so nothing about a scope should
  // re-read it.
  const { rerender } = render(<CalendarConfig scope={{ kind: "world", id: "realm" }} />);
  await screen.findByLabelText("Calendar");
  rerender(<CalendarConfig scope={{ kind: "world", id: "saltmarch" }} />);
  await screen.findByLabelText("Calendar");
  expect(api.getCalendarProviders).toHaveBeenCalledTimes(1);
  expect(api.getCalendarConfig).toHaveBeenCalledTimes(2);
});


test("the warn window saves, and 0 saves as 0 rather than as no opinion", async () => {
  // 0 is a real setting here — no warnings in this campaign — which is why the
  // field's no-opinion value is null and not 0 (#106).
  render(<CalendarConfig scope={{ kind: "campaign", id: "run" }} />);
  const input = await screen.findByLabelText("Warn ahead days");
  fireEvent.change(input, { target: { value: "0" } });
  fireEvent.click(screen.getByRole("button", { name: /save/i }));
  await waitFor(() => expect(api.setCalendarConfig).toHaveBeenCalledWith(
    { kind: "campaign", id: "run" }, expect.objectContaining({ warn_days: 0 })));
});

test("clearing the warn window sends the stored one, not the no-opinion sentinel", async () => {
  // `null` means "this request expressed no opinion", and the store answers it
  // by KEEPING the stored window (#106) — right for a client that predates the
  // field, wrong for this form, which would be left showing an empty box over a
  // server that kept 14. The form resolves blank before the request goes out,
  // and the control then shows what was actually saved.
  (api.getCalendarConfig as any).mockResolvedValue({
    primary: { provider: "gregorian", region: "US", custom_holidays: [], anchor: null },
    secondary: null, confirmed: false, stale_after_days: 30, warn_days: 14 });
  render(<CalendarConfig scope={{ kind: "campaign", id: "run" }} />);
  const input = await screen.findByLabelText("Warn ahead days");
  fireEvent.change(input, { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: /save/i }));
  await waitFor(() => expect(api.setCalendarConfig).toHaveBeenCalledWith(
    { kind: "campaign", id: "run" }, expect.objectContaining({ warn_days: 14 })));
  await waitFor(() => expect((input as HTMLInputElement).value).toBe("14"));
});

test("a warn window past the server's ceiling is clamped, not shown back as typed", async () => {
  // Otherwise the form displays 1000 and reports it onward while the server has
  // stored 365 — a control that lies about what was saved.
  render(<CalendarConfig scope={{ kind: "campaign", id: "run" }} />);
  const input = await screen.findByLabelText("Warn ahead days");
  fireEvent.change(input, { target: { value: "1000" } });
  expect((input as HTMLInputElement).value).toBe("365");
  fireEvent.click(screen.getByRole("button", { name: /save/i }));
  await waitFor(() => expect(api.setCalendarConfig).toHaveBeenCalledWith(
    { kind: "campaign", id: "run" }, expect.objectContaining({ warn_days: 365 })));
});

test("a save that settles after a scope change does not install itself", async () => {
  // This component is reused across worlds and campaigns. A save still in
  // flight when the scope changes would otherwise put ITS config on screen
  // under the new record's Save button, where the next edit writes the old
  // record's calendar over the new one.
  let landOld: (v: any) => void = () => {};
  (api.setCalendarConfig as any).mockReturnValueOnce(
    new Promise((r: any) => { landOld = r; }));
  const { rerender } = render(<CalendarConfig scope={{ kind: "campaign", id: "run" }} />);
  const input = await screen.findByLabelText("Warn ahead days");
  fireEvent.change(input, { target: { value: "21" } });
  fireEvent.click(screen.getByRole("button", { name: /save/i }));

  // The reader moves to a world, whose calendar loads with a different window.
  (api.getCalendarConfig as any).mockResolvedValue({
    primary: { provider: "gregorian", region: "GB", custom_holidays: [], anchor: null },
    secondary: null, confirmed: true, stale_after_days: 30, warn_days: 3 });
  rerender(<CalendarConfig scope={{ kind: "world", id: "realm" }} />);
  await waitFor(() => expect(
    screen.getByLabelText<HTMLInputElement>("Warn ahead days").value).toBe("3"));

  await act(async () => { landOld({ ok: true }); });
  // The campaign's 21 must not land on the world's form.
  expect(screen.getByLabelText<HTMLInputElement>("Warn ahead days").value).toBe("3");
});
