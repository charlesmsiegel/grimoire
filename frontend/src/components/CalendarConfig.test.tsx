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
