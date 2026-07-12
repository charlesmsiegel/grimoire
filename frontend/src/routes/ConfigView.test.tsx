import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import ConfigView from "./ConfigView";

vi.mock("../api/client", () => ({
  ApiError: class ApiError extends Error {},
  api: { getConfig: vi.fn(), putConfig: vi.fn(), getDataDir: vi.fn(), putDataDir: vi.fn(), listStyles: vi.fn() },
}));
vi.mock("../theme/ThemeProvider", () => ({ useTheme: () => ({ setTheme: vi.fn() }) }));
vi.mock("./ModelCombobox", () => ({ default: () => <div /> }));
import { api } from "../api/client";

const cfg = { model: "m", theme: "codex", key_set: false, system_prompt: "", quote_color: "off", user_label: "You", assistant_label: "Grimoire", provider: "openrouter", claude_model: "opus", default_style_id: "" };
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
  (api.listStyles as any).mockResolvedValue([
    { id: "gothic-horror", name: "Gothic Horror", description: "", tags: [], built_in: true },
    { id: "noir-detective", name: "Noir Detective", description: "", tags: [], built_in: true },
  ]);
});

test("saves the system prompt", async () => {
  render(<ConfigView />);
  const ta = await screen.findByLabelText(/system prompt/i);
  fireEvent.change(ta, { target: { value: "Never speak for the PC." } });
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith(
    expect.objectContaining({ system_prompt: "Never speak for the PC." })));
});

test("saves the default prose style", async () => {
  render(<ConfigView />);
  const sel = await screen.findByLabelText(/default prose style/i);
  fireEvent.change(sel, { target: { value: "noir-detective" } });
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith(
    expect.objectContaining({ default_style_id: "noir-detective" })));
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

test("edits transcript labels and saves them", async () => {
  render(<ConfigView />);
  const user = await screen.findByLabelText(/your label/i);
  fireEvent.change(user, { target: { value: "Kestrel" } });
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
  await waitFor(() =>
    expect(api.putConfig).toHaveBeenCalledWith(expect.objectContaining({ user_label: "Kestrel" })));
});

test("shows the three theme cards", async () => {
  render(<ConfigView />);
  expect(await screen.findByText("CODEX")).toBeInTheDocument();
  expect(screen.getByText("MANUSCRIPT")).toBeInTheDocument();
  expect(screen.getByText("ASTRAL")).toBeInTheDocument();
});

test("switching provider to claude swaps key/model fields for a claude model input", async () => {
  render(<ConfigView />);
  const select = await screen.findByLabelText("LLM provider");
  expect(screen.getByLabelText("OpenRouter API key")).toBeInTheDocument();
  fireEvent.change(select, { target: { value: "claude" } });
  expect(screen.queryByLabelText("OpenRouter API key")).toBeNull();
  expect(screen.getByLabelText("Claude model")).toBeInTheDocument();
});

test("claude model is a select offering aliases and pinned versions", async () => {
  render(<ConfigView />);
  const provider = await screen.findByLabelText("LLM provider");
  fireEvent.change(provider, { target: { value: "claude" } });
  const model = screen.getByLabelText("Claude model");
  expect(model.tagName).toBe("SELECT");
  const values = Array.from(model.querySelectorAll("option")).map((o) => o.value);
  expect(values).toContain("fable");
  expect(values).toContain("opus");
  expect(values).toContain("sonnet");
  expect(values).toContain("haiku");
  expect(values).toContain("claude-fable-5");
  expect(values).toContain("claude-opus-4-8");
});

test("a saved claude model outside the list is kept as a custom option", async () => {
  (api.getConfig as any).mockResolvedValue({ ...cfg, provider: "claude", claude_model: "claude-opus-4-5" });
  render(<ConfigView />);
  const model = await screen.findByLabelText("Claude model");
  expect(model.tagName).toBe("SELECT");
  expect((model as HTMLSelectElement).value).toBe("claude-opus-4-5");
});

test("save sends provider and claude_model", async () => {
  render(<ConfigView />);
  const select = await screen.findByLabelText("LLM provider");
  fireEvent.change(select, { target: { value: "claude" } });
  fireEvent.change(screen.getByLabelText("Claude model"), { target: { value: "sonnet" } });
  fireEvent.click(screen.getByText("Save"));
  await waitFor(() =>
    expect(api.putConfig).toHaveBeenCalledWith(
      expect.objectContaining({ provider: "claude", claude_model: "sonnet" }),
    ),
  );
});
