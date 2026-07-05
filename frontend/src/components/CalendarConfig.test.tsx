import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { CalendarConfig } from "./CalendarConfig";

vi.mock("../api/client", () => ({
  api: { getCalendarConfig: vi.fn(), setCalendarConfig: vi.fn() },
}));
import { api } from "../api/client";

beforeEach(() => {
  vi.clearAllMocks();
  (api.getCalendarConfig as any).mockResolvedValue({
    primary: { provider: "gregorian", region: "US", custom_holidays: [], anchor: null }, secondary: null });
  (api.setCalendarConfig as any).mockResolvedValue({ ok: true });
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

test("selecting harptos hides both region and observance and saves", async () => {
  render(<CalendarConfig cid="run" />);
  const provider = await screen.findByLabelText("Calendar");
  fireEvent.change(provider, { target: { value: "harptos" } });
  expect(screen.queryByLabelText("Holidays region")).toBeNull();
  expect(screen.queryByLabelText("Observance")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /save/i }));
  await waitFor(() => expect(api.setCalendarConfig).toHaveBeenCalledWith("run",
    expect.objectContaining({ primary: expect.objectContaining({ provider: "harptos" }) })));
});
