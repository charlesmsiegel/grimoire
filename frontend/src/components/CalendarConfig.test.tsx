import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { CalendarConfig } from "./CalendarConfig";

vi.mock("../api/client", () => ({
  api: { getCalendarConfig: vi.fn(), setCalendarConfig: vi.fn(), getCalendarProviders: vi.fn() },
}));
import { api } from "../api/client";

beforeEach(() => {
  vi.clearAllMocks();
  (api.getCalendarConfig as any).mockResolvedValue({
    primary: { provider: "gregorian", region: "US", custom_holidays: [], anchor: null }, secondary: null });
  (api.setCalendarConfig as any).mockResolvedValue({ ok: true });
  (api.getCalendarProviders as any).mockResolvedValue({ providers: [
    { id: "gregorian", name: "Gregorian" }, { id: "hebrew", name: "Hebrew" },
    { id: "my-custom-calendar", name: "My Custom Calendar" },
  ] });
});

test("edits the region and saves", async () => {
  render(<CalendarConfig cid="run" />);
  const sel = await screen.findByLabelText("Holidays region");
  fireEvent.change(sel, { target: { value: "GB" } });
  fireEvent.click(screen.getByRole("button", { name: /save/i }));
  await waitFor(() => expect(api.setCalendarConfig).toHaveBeenCalledWith("run",
    expect.objectContaining({ primary: expect.objectContaining({ region: "GB" }) })));
});

test("selecting hebrew shows Observance and saves the Israel setting", async () => {
  render(<CalendarConfig cid="run" />);
  const provider = await screen.findByLabelText("Calendar");
  fireEvent.change(provider, { target: { value: "hebrew" } });
  expect(screen.queryByLabelText("Holidays region")).toBeNull();
  const observance = screen.getByLabelText("Observance");
  fireEvent.change(observance, { target: { value: "IL" } });
  fireEvent.click(screen.getByRole("button", { name: /save/i }));
  await waitFor(() => expect(api.setCalendarConfig).toHaveBeenCalledWith("run",
    expect.objectContaining({ primary: expect.objectContaining({ provider: "hebrew", region: "IL" }) })));
});

test("selecting a custom (user-authored) calendar hides both region and observance and saves", async () => {
  render(<CalendarConfig cid="run" />);
  const provider = await screen.findByLabelText("Calendar");
  expect(screen.getByRole("option", { name: "My Custom Calendar" })).toBeInTheDocument();
  fireEvent.change(provider, { target: { value: "my-custom-calendar" } });
  expect(screen.queryByLabelText("Holidays region")).toBeNull();
  expect(screen.queryByLabelText("Observance")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /save/i }));
  await waitFor(() => expect(api.setCalendarConfig).toHaveBeenCalledWith("run",
    expect.objectContaining({ primary: expect.objectContaining({ provider: "my-custom-calendar" }) })));
});
