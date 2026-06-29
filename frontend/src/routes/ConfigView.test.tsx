import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import ConfigView from "./ConfigView";

vi.mock("../api/client", () => ({
  api: { getConfig: vi.fn(), putConfig: vi.fn() },
}));
vi.mock("../theme/ThemeProvider", () => ({ useTheme: () => ({ setTheme: vi.fn() }) }));
vi.mock("./ModelCombobox", () => ({ default: () => <div /> }));
import { api } from "../api/client";

const cfg = { model: "m", theme: "occult", key_set: false, system_prompt: "", quote_color: "off" };
beforeEach(() => {
  vi.clearAllMocks();
  (api.getConfig as any).mockResolvedValue(cfg);
  (api.putConfig as any).mockResolvedValue(cfg);
});

test("saves the system prompt", async () => {
  render(<ConfigView />);
  const ta = await screen.findByLabelText(/system prompt/i);
  fireEvent.change(ta, { target: { value: "Never speak for the PC." } });
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith(
    expect.objectContaining({ system_prompt: "Never speak for the PC." })));
});

test("toggling quote color saves immediately", async () => {
  render(<ConfigView />);
  const cb = await screen.findByLabelText(/color quoted/i);
  fireEvent.click(cb);
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith({ quote_color: "on" }));
});
