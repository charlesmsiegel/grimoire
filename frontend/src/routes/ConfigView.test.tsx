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
  llm_timeout: "120", absorb_budget: "600", llm_call_budget: "300",
  context_budget: "0", archive_depth: "3",
  prompt_log_depth: "50",
  turnstate_depth: "0", promote_streak: "3",
  embeddings_connection_id: "", embeddings_model: "", semantic_recall_depth: "0",
  semantic_recall_threshold: "0.4",
};
const dataDir = {
  data_dir: "/home/u/.grimoire", default: "/home/u/.grimoire",
  is_default: true, source: "default" as const, exists: true,
};
const connections = [
  { id: "openrouter", kind: "openrouter", name: "OpenRouter", base_url: "", model: "m", post_process: "none", key_set: true, rev: "r1" },
  { id: "claude", kind: "claude", name: "Claude", base_url: "", model: "opus", post_process: "none", key_set: false, rev: "r2" },
  { id: "local", kind: "openai_compatible", name: "Local vectors", base_url: "http://localhost:1234/v1", model: "", post_process: "none", key_set: false, rev: "r3" },
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
  expect(values).toEqual(["openrouter", "claude", "local"]);
  expect((select as HTMLSelectElement).value).toBe("openrouter");
});

test("switching the active connection saves immediately", async () => {
  renderView();
  const select = await screen.findByLabelText("LLM connection");
  fireEvent.change(select, { target: { value: "claude" } });
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith({ active_connection_id: "claude" }));
});

test("shows the configured timeout and absorb budget", async () => {
  renderView();
  expect(await screen.findByLabelText(/no-reply timeout/i)).toHaveValue("120");
  expect(screen.getByLabelText(/absorb budget/i)).toHaveValue("600");
  expect(screen.getByLabelText(/one-shot call ceiling/i)).toHaveValue("300");
});

test("edits the timeouts and saves them", async () => {
  renderView();
  const timeout = await screen.findByLabelText(/no-reply timeout/i);
  fireEvent.change(timeout, { target: { value: "45" } });
  fireEvent.change(screen.getByLabelText(/absorb budget/i), { target: { value: "300" } });
  fireEvent.change(screen.getByLabelText(/one-shot call ceiling/i), { target: { value: "90" } });
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith(
    expect.objectContaining({ llm_timeout: "45", absorb_budget: "300",
                             llm_call_budget: "90" })));
});

test("edits the context budget and recalled-scene cap and saves them", async () => {
  renderView();
  const budget = await screen.findByLabelText(/context budget/i);
  expect(budget).toHaveValue("0");                       // unbounded by default
  expect(screen.getByLabelText(/recalled scenes/i)).toHaveValue("3");
  fireEvent.change(budget, { target: { value: "32000" } });
  fireEvent.change(screen.getByLabelText(/recalled scenes/i), { target: { value: "5" } });
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith(
    expect.objectContaining({ context_budget: "32000", archive_depth: "5" })));
});

test("edits how many turn prompts are kept and saves it", async () => {
  renderView();
  const kept = await screen.findByLabelText(/kept turn prompts/i);
  expect(kept).toHaveValue("50");
  fireEvent.change(kept, { target: { value: "0" } });    // 0 turns capture off
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith(
    expect.objectContaining({ prompt_log_depth: "0" })));
});

test("semantic recall is off by default and offers only openai-compatible connections", async () => {
  renderView();
  const picker = await screen.findByLabelText(/embeddings connection/i);
  expect(picker).toHaveValue("");                        // off until pointed somewhere
  expect(screen.getByLabelText(/recalled entries/i)).toHaveValue("0");
  expect(screen.getByLabelText(/similarity threshold/i)).toHaveValue("0.4");
  // OpenRouter and Claude serve no /embeddings route, so they are not offered.
  expect([...picker.querySelectorAll("option")].map((o) => o.textContent))
    .toEqual(["Off", "Local vectors"]);
});

test("turns semantic recall on and saves every knob together", async () => {
  renderView();
  fireEvent.change(await screen.findByLabelText(/embeddings connection/i), { target: { value: "local" } });
  fireEvent.change(screen.getByLabelText(/embedding model/i), { target: { value: "text-embedding-3-small" } });
  fireEvent.change(screen.getByLabelText(/recalled entries/i), { target: { value: "4" } });
  fireEvent.change(screen.getByLabelText(/similarity threshold/i), { target: { value: "0.55" } });
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith(
    expect.objectContaining({
      embeddings_connection_id: "local", embeddings_model: "text-embedding-3-small",
      semantic_recall_depth: "4", semantic_recall_threshold: "0.55",
    })));
});

test("links to the Connections page to manage keys/endpoints", async () => {
  renderView();
  await screen.findByLabelText("LLM connection");
  expect(screen.getByRole("link", { name: /connections/i })).toHaveAttribute("href", "/connections");
});


test("saves the transient-state settings", async () => {
  renderView();
  fireEvent.change(await screen.findByLabelText(/tracked posts/i), { target: { value: "6" } });
  fireEvent.change(screen.getByLabelText(/promote after/i), { target: { value: "2" } });
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
  await waitFor(() =>
    expect(api.putConfig).toHaveBeenCalledWith(
      expect.objectContaining({ turnstate_depth: "6", promote_streak: "2" }),
    ),
  );
});

test("shows the stored transient-state settings", async () => {
  (api.getConfig as any).mockResolvedValue({ ...cfg, turnstate_depth: "4", promote_streak: "5" });
  renderView();
  expect(await screen.findByLabelText(/tracked posts/i)).toHaveValue("4");
  expect(screen.getByLabelText(/promote after/i)).toHaveValue("5");
});
