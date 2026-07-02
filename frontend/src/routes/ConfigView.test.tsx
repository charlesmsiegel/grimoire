import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import ConfigView from "./ConfigView";

vi.mock("../api/client", () => ({
  ApiError: class ApiError extends Error {},
  api: { getConfig: vi.fn(), putConfig: vi.fn(), getDataDir: vi.fn(), putDataDir: vi.fn() },
}));
vi.mock("../theme/ThemeProvider", () => ({ useTheme: () => ({ setTheme: vi.fn() }) }));
vi.mock("./ModelCombobox", () => ({ default: () => <div /> }));
import { api } from "../api/client";

const cfg = { model: "m", theme: "codex", key_set: false, system_prompt: "", quote_color: "off" };
const dataDir = {
  data_dir: "/home/u/.grimoire", default: "/home/u/.grimoire",
  is_default: true, source: "default" as const, exists: true,
};
beforeEach(() => {
  vi.clearAllMocks();
  (api.getConfig as any).mockResolvedValue(cfg);
  (api.putConfig as any).mockResolvedValue(cfg);
  (api.getDataDir as any).mockResolvedValue(dataDir);
  (api.putDataDir as any).mockResolvedValue(dataDir);
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

test("moving the storage location saves the new path", async () => {
  (api.putDataDir as any).mockResolvedValue({ ...dataDir, data_dir: "/sync/grimoire", is_default: false, source: "custom" });
  render(<ConfigView />);
  const input = await screen.findByLabelText(/storage location/i);
  fireEvent.change(input, { target: { value: "/sync/grimoire" } });
  fireEvent.click(screen.getByRole("button", { name: /^move$/i }));
  await waitFor(() => expect(api.putDataDir).toHaveBeenCalledWith("/sync/grimoire"));
});
