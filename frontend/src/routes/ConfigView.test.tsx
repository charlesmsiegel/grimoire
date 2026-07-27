import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import ConfigView from "./ConfigView";

vi.mock("../api/client", () => ({
  ApiError: class ApiError extends Error {},
  api: {
    getConfig: vi.fn(), putConfig: vi.fn(), getDataDir: vi.fn(), putDataDir: vi.fn(),
    listStyles: vi.fn(), listConnections: vi.fn(),
  },
}));
vi.mock("../theme/ThemeProvider", () => ({ useTheme: () => ({ setTheme: vi.fn() }) }));
vi.mock("../components/ResponsePresetPicker", () => ({
  ResponsePresetPicker: () => <div data-testid="response-preset-picker" />,
}));
import { api } from "../api/client";

const cfg = {
  theme: "codex", system_prompt: "", quote_color: "off", user_label: "You", assistant_label: "Grimoire",
  active_connection_id: "openrouter",
  active_connection: { id: "openrouter", kind: "openrouter", name: "OpenRouter" }, ready: true,
};
const dataDir = {
  data_dir: "/home/u/.grimoire", default: "/home/u/.grimoire",
  is_default: true, source: "default" as const, exists: true,
};
const connections = [
  { id: "openrouter", kind: "openrouter", name: "OpenRouter", base_url: "", model: "m", post_process: "none", key_set: true, rev: "r1" },
  { id: "claude", kind: "claude", name: "Claude", base_url: "", model: "opus", post_process: "none", key_set: false, rev: "r2" },
];
beforeEach(() => {
  vi.clearAllMocks();
  (api.getConfig as any).mockResolvedValue(cfg);
  (api.putConfig as any).mockResolvedValue(cfg);
  (api.getDataDir as any).mockResolvedValue(dataDir);
  (api.putDataDir as any).mockResolvedValue(dataDir);
  (api.listStyles as any).mockResolvedValue([
    { id: "gothic-horror", name: "Gothic Horror", description: "", tags: [], built_in: true },
    { id: "noir-detective", name: "Noir Detective", description: "", tags: [], built_in: true },
  ]);
  (api.listConnections as any).mockResolvedValue(connections);
});

// ConfigView renders a <Link to="/connections">, which throws outside a
// Router context — wrap every render the same way CampaignsView.test.tsx does.
function renderView() {
  render(
    <MemoryRouter>
      <ConfigView />
    </MemoryRouter>,
  );
}

test("saves the system prompt", async () => {
  renderView();
  const ta = await screen.findByLabelText(/system prompt/i);
  fireEvent.change(ta, { target: { value: "Never speak for the PC." } });
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith(
    expect.objectContaining({ system_prompt: "Never speak for the PC." })));
});

test("mounts the response preset picker for the global scope", async () => {
  renderView();
  expect(await screen.findByTestId("response-preset-picker")).toBeInTheDocument();
});

test("toggling quote color saves immediately", async () => {
  renderView();
  const cb = await screen.findByLabelText(/color quoted/i);
  fireEvent.click(cb);
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith({ quote_color: "on" }));
});

test("moving the storage location saves the new path", async () => {
  (api.putDataDir as any).mockResolvedValue({ ...dataDir, data_dir: "/sync/grimoire", is_default: false, source: "custom" });
  renderView();
  const input = await screen.findByLabelText(/storage location/i);
  fireEvent.change(input, { target: { value: "/sync/grimoire" } });
  fireEvent.click(screen.getByRole("button", { name: /^move$/i }));
  await waitFor(() => expect(api.putDataDir).toHaveBeenCalledWith("/sync/grimoire"));
});

test("edits transcript labels and saves them", async () => {
  renderView();
  const user = await screen.findByLabelText(/your label/i);
  fireEvent.change(user, { target: { value: "Kestrel" } });
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
  await waitFor(() =>
    expect(api.putConfig).toHaveBeenCalledWith(expect.objectContaining({ user_label: "Kestrel" })));
});

test("shows the three theme cards", async () => {
  renderView();
  expect(await screen.findByText("CODEX")).toBeInTheDocument();
  expect(screen.getByText("MANUSCRIPT")).toBeInTheDocument();
  expect(screen.getByText("ASTRAL")).toBeInTheDocument();
});

test("shows every connection in the LLM connection dropdown", async () => {
  renderView();
  const select = await screen.findByLabelText("LLM connection");
  const values = Array.from(select.querySelectorAll("option")).map((o) => (o as HTMLOptionElement).value);
  expect(values).toEqual(["openrouter", "claude"]);
  expect((select as HTMLSelectElement).value).toBe("openrouter");
});

test("switching the active connection saves immediately", async () => {
  renderView();
  const select = await screen.findByLabelText("LLM connection");
  fireEvent.change(select, { target: { value: "claude" } });
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith({ active_connection_id: "claude" }));
});

test("links to the Connections page to manage keys/endpoints", async () => {
  renderView();
  await screen.findByLabelText("LLM connection");
  expect(screen.getByRole("link", { name: /connections/i })).toHaveAttribute("href", "/connections");
});
