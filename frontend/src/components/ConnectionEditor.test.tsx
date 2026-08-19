import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ConnectionEditor } from "./ConnectionEditor";

// `ApiError` is the real class: the offline branch reads `kind` off the
// rejection, so a hand-rolled stand-in would prove nothing about what the
// client throws.
vi.mock("../api/client", async () => ({
  ...(await vi.importActual<typeof import("../api/client")>("../api/client")),
  api: {
    listConnections: vi.fn(), readConnection: vi.fn(), createConnection: vi.fn(),
    updateConnection: vi.fn(), deleteConnection: vi.fn(), refreshConnectionModels: vi.fn(),
    getConfig: vi.fn(), putConfig: vi.fn(),
  },
}));
vi.mock("../api/models", () => ({ getModels: vi.fn(), priceLabel: () => "", contextLabel: () => "" }));
import { api, ApiError } from "../api/client";
import { getModels } from "../api/models";

const OPENROUTER = { id: "openrouter", kind: "openrouter", name: "OpenRouter", base_url: "", model: "anthropic/claude-opus-4.1", post_process: "none", key_set: true, rev: "r1" };
const CUSTOM = { id: "zai-glm", kind: "openai_compatible", name: "z.ai GLM", base_url: "https://api.z.ai/v4", model: "glm-4.6", post_process: "strict", key_set: true, rev: "r2" };

beforeEach(() => {
  vi.clearAllMocks();
  (getModels as any).mockResolvedValue([]);
  (api.listConnections as any).mockResolvedValue([OPENROUTER, CUSTOM]);
  (api.getConfig as any).mockResolvedValue({ active_connection_id: "openrouter" });
  (api.readConnection as any).mockImplementation((id: string) => Promise.resolve(
    id === "openrouter"
      ? { ...OPENROUTER, models: [], fetched_at: "" }
      : { ...CUSTOM, models: [], fetched_at: "" }));
  (api.createConnection as any).mockResolvedValue({ id: "new-conn" });
  (api.updateConnection as any).mockResolvedValue({ ok: true });
  (api.deleteConnection as any).mockResolvedValue({ ok: true });
  (api.refreshConnectionModels as any).mockResolvedValue({ models: [], fetched_at: "2026-07-18", rev: "r2" });
  (api.putConfig as any).mockResolvedValue({ active_connection_id: "zai-glm" });
});

test("clicking a connection shows a read-only view", async () => {
  render(<ConnectionEditor />);
  const rail = await waitFor(() => screen.getByText("+ New connection").closest(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("z.ai GLM"));
  await waitFor(() => expect(api.readConnection).toHaveBeenCalledWith("zai-glm"));
  const detail = screen.getByRole("heading", { name: "z.ai GLM" }).closest(".detail-view") as HTMLElement;
  expect(within(detail).getByText(/openai_compatible/)).toBeInTheDocument();
  expect(screen.queryByLabelText("Base URL")).toBeNull();
});

test("Edit reveals the form with kind locked", async () => {
  render(<ConnectionEditor />);
  const rail = await waitFor(() => screen.getByText("+ New connection").closest(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("z.ai GLM"));
  await waitFor(() => expect(api.readConnection).toHaveBeenCalledWith("zai-glm"));
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
  expect(screen.getByLabelText("Base URL")).toBeInTheDocument();
  expect(screen.getByLabelText("Kind")).toBeDisabled();
});

test("+ New opens the form directly with an unlocked kind picker", async () => {
  render(<ConnectionEditor />);
  await screen.findByText("+ New connection");
  fireEvent.click(screen.getByText("+ New connection"));
  expect(screen.getByLabelText("Kind")).not.toBeDisabled();
  expect(screen.queryByLabelText("Base URL")).toBeNull(); // defaults to openrouter kind
  fireEvent.change(screen.getByLabelText("Kind"), { target: { value: "openai_compatible" } });
  expect(screen.getByLabelText("Base URL")).toBeInTheDocument();
});

test("creating a custom endpoint connection posts the right fields", async () => {
  render(<ConnectionEditor />);
  await screen.findByText("+ New connection");
  fireEvent.click(screen.getByText("+ New connection"));
  fireEvent.change(screen.getByLabelText("Kind"), { target: { value: "openai_compatible" } });
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "My Endpoint" } });
  fireEvent.change(screen.getByLabelText("Base URL"), { target: { value: "https://api.example.com/v1" } });
  fireEvent.change(screen.getByLabelText("API key"), { target: { value: "sk-new" } });
  fireEvent.click(screen.getByRole("button", { name: /create connection/i }));
  await waitFor(() => expect(api.createConnection).toHaveBeenCalledWith(
    expect.objectContaining({
      kind: "openai_compatible", name: "My Endpoint",
      base_url: "https://api.example.com/v1", api_key: "sk-new",
    })));
});

test("Set as active updates the active connection", async () => {
  render(<ConnectionEditor />);
  const rail = await waitFor(() => screen.getByText("+ New connection").closest(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("z.ai GLM"));
  await waitFor(() => expect(api.readConnection).toHaveBeenCalledWith("zai-glm"));
  fireEvent.click(screen.getByRole("button", { name: /set as active/i }));
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith({ active_connection_id: "zai-glm" }));
});

test("Refresh models calls the refresh endpoint and shows the result", async () => {
  (api.refreshConnectionModels as any).mockResolvedValue({
    models: [{ id: "glm-4.6", name: "GLM-4.6", context: 128000, prompt: null, completion: null }],
    fetched_at: "2026-07-18T12:00:00", rev: "r2",
  });
  render(<ConnectionEditor />);
  const rail = await waitFor(() => screen.getByText("+ New connection").closest(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("z.ai GLM"));
  await waitFor(() => expect(api.readConnection).toHaveBeenCalledWith("zai-glm"));
  fireEvent.click(screen.getByRole("button", { name: /refresh models/i }));
  await waitFor(() => expect(api.refreshConnectionModels).toHaveBeenCalledWith("zai-glm"));
  expect(await screen.findByText(/2026-07-18t12:00:00/i)).toBeInTheDocument();
});

test("a stale refresh response (rev no longer matches) is discarded", async () => {
  let resolveRefresh: (v: any) => void;
  (api.refreshConnectionModels as any).mockReturnValue(new Promise((res) => { resolveRefresh = res; }));
  render(<ConnectionEditor />);
  const rail = await waitFor(() => screen.getByText("+ New connection").closest(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("z.ai GLM"));
  await waitFor(() => expect(api.readConnection).toHaveBeenCalledWith("zai-glm"));
  fireEvent.click(screen.getByRole("button", { name: /refresh models/i }));
  // the connection changes underneath the open form (e.g. base_url saved) before the refresh resolves
  (api.readConnection as any).mockResolvedValueOnce({ ...CUSTOM, rev: "r3", models: [], fetched_at: "" });
  await select_again();
  resolveRefresh!({ models: [{ id: "stale", name: "stale", context: null, prompt: null, completion: null }], fetched_at: "STALE_TIMESTAMP", rev: "r2" });
  // The component is back in view mode after select_again(), where
  // detail.models never renders (only ModelCombobox in edit mode does) --
  // so "stale" would never appear here regardless of whether the rev guard
  // works. Assert instead on the "Cached models" sidebar's "Last fetched"
  // text, which IS visible in view mode: wait for the refresh to finish
  // (button label reverts from "Refreshing…"), then confirm the stale
  // response's distinctive fetched_at never reached the screen and the
  // pre-refresh "Never fetched" state survived untouched.
  await screen.findByRole("button", { name: /refresh models/i });
  expect(screen.queryByText(/STALE_TIMESTAMP/)).not.toBeInTheDocument();
  expect(screen.getByText("Never fetched")).toBeInTheDocument();

  async function select_again() {
    fireEvent.click(await within(rail).findByText("z.ai GLM"));
    await waitFor(() => expect(api.readConnection).toHaveBeenCalledTimes(2));
  }
});

test("deleting a connection removes it", async () => {
  const original = window.confirm;
  window.confirm = () => true;
  render(<ConnectionEditor />);
  const rail = await waitFor(() => screen.getByText("+ New connection").closest(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("z.ai GLM"));
  await waitFor(() => expect(api.readConnection).toHaveBeenCalledWith("zai-glm"));
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
  fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));
  await waitFor(() => expect(api.deleteConnection).toHaveBeenCalledWith("zai-glm"));
  window.confirm = original;
});

test("a model fetch that cannot reach the provider says so, without a link back here", async () => {
  // Offline, the catalog fetch is the first thing that fails on this page. The
  // note is the same one the scene view raises (#210) minus its Connections
  // link, which would point at the page the reader is already reading.
  (api.refreshConnectionModels as any).mockRejectedValue(
    new ApiError(502, "connection refused", "network"));
  render(<MemoryRouter initialEntries={["/connections"]}><ConnectionEditor /></MemoryRouter>);
  const rail = await waitFor(() => screen.getByText("+ New connection").closest(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("z.ai GLM"));
  fireEvent.click(await screen.findByRole("button", { name: /refresh models/i }));
  await screen.findByText(/Couldn.t reach the model provider/);
  expect(screen.getByText(/connection refused/)).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: /Connections/ })).toBeNull();
});

test("an unreachable model catalog degrades the picker instead of blocking config", async () => {
  // Offline, the OpenRouter catalog is fetched straight from the browser and
  // fails; a connection that already names a model must stay editable and
  // saveable anyway (#210).
  (getModels as any).mockRejectedValue(new Error("Failed to fetch"));
  render(<MemoryRouter initialEntries={["/connections"]}><ConnectionEditor /></MemoryRouter>);
  const rail = await waitFor(() => screen.getByText("+ New connection").closest(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("OpenRouter"));
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  expect(await screen.findByText(/couldn.t load model list — type a model id/)).toBeInTheDocument();
  const model = screen.getByDisplayValue("anthropic/claude-opus-4.1");
  fireEvent.change(model, { target: { value: "qwen3:8b" } });
  fireEvent.click(screen.getByRole("button", { name: /save connection/i }));
  await waitFor(() => expect(api.updateConnection).toHaveBeenCalledWith(
    "openrouter", expect.objectContaining({ model: "qwen3:8b" })));
});
