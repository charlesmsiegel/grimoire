import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { ConnectionEditor } from "./ConnectionEditor";

vi.mock("../api/client", () => ({
  api: {
    listConnections: vi.fn(), readConnection: vi.fn(), createConnection: vi.fn(),
    updateConnection: vi.fn(), deleteConnection: vi.fn(), refreshConnectionModels: vi.fn(),
    getConfig: vi.fn(), putConfig: vi.fn(),
  },
}));
vi.mock("../api/models", () => ({ getModels: vi.fn(), priceLabel: () => "", contextLabel: () => "" }));
import { api } from "../api/client";
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
  expect(screen.getByText(/openai_compatible/)).toBeInTheDocument();
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
  resolveRefresh!({ models: [{ id: "stale", name: "stale", context: null, prompt: null, completion: null }], fetched_at: "old", rev: "r2" });
  await waitFor(() => expect(screen.queryByText("stale")).not.toBeInTheDocument());

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
