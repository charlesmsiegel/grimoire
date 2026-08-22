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
    getConfig: vi.fn(), putConfig: vi.fn(), previewModels: vi.fn(), checkConnection: vi.fn(),
  },
}));
vi.mock("../api/models", () => ({ priceLabel: () => "", contextLabel: () => "" }));
import { api, ApiError } from "../api/client";

const UNCHECKED = { state: "unknown", kind: "", detail: "", at: "" };
const OPENROUTER = { id: "openrouter", kind: "openrouter", name: "OpenRouter", base_url: "", model: "anthropic/claude-opus-4.1", post_process: "none", key_set: true, rev: "r1", health: UNCHECKED };
const CUSTOM = { id: "zai-glm", kind: "openai_compatible", name: "z.ai GLM", base_url: "https://api.z.ai/v4", model: "glm-4.6", post_process: "strict", key_set: true, rev: "r2", health: UNCHECKED };

beforeEach(() => {
  vi.clearAllMocks();
  (api.previewModels as any).mockResolvedValue({ models: [] });
  (api.checkConnection as any).mockResolvedValue({
    ok: true, kind: "", detail: "", checked_at: "2026-08-21T09:00:00Z",
  });
  (api.listConnections as any).mockResolvedValue([OPENROUTER, CUSTOM]);
  (api.getConfig as any).mockResolvedValue({ active_connection_id: "openrouter" });
  (api.readConnection as any).mockImplementation((id: string) => Promise.resolve(
    id === "openrouter"
      ? { ...OPENROUTER, models: [], fetched_at: "", health: UNCHECKED }
      : { ...CUSTOM, models: [], fetched_at: "", health: UNCHECKED }));
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
  (api.readConnection as any).mockResolvedValueOnce({
    ...CUSTOM, rev: "r3", models: [], fetched_at: "", health: UNCHECKED });
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
  // Offline, the catalog fetch fails; a connection that already names a model
  // must stay editable and saveable anyway (#210). Since #149 the fetch goes
  // through the backend to the connection's own provider, so this is the
  // refresh route failing rather than a browser fetch — and it fails on *open*,
  // which is a request the reader did not make, so it degrades the picker
  // rather than raising the banner.
  (api.refreshConnectionModels as any).mockRejectedValue(
    new ApiError(502, "connection refused", "network"));
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

// ---- the catalog comes from the connection being edited (#149) ----
test("opening an OpenRouter connection fills its picker from its own provider", async () => {
  (api.refreshConnectionModels as any).mockResolvedValue({
    models: [{ id: "anthropic/claude-opus-4.1", name: "Opus", context: 200000, prompt: "0", completion: "0" }],
    fetched_at: "2026-08-21", rev: "r1",
  });
  render(<ConnectionEditor />);
  const rail = await waitFor(() => screen.getByText("+ New connection").closest(".editor-list") as HTMLElement);

  fireEvent.click(await within(rail).findByText("OpenRouter"));

  await waitFor(() => expect(api.refreshConnectionModels).toHaveBeenCalledWith("openrouter"));
});

test("opening a custom endpoint does not go out to it uninvited", async () => {
  // A local server can be switched off, and a stall on merely *looking* at a
  // connection is worse than an empty picker with a Fetch button beside it.
  render(<ConnectionEditor />);
  const rail = await waitFor(() => screen.getByText("+ New connection").closest(".editor-list") as HTMLElement);

  fireEvent.click(await within(rail).findByText("z.ai GLM"));
  await waitFor(() => expect(api.readConnection).toHaveBeenCalledWith("zai-glm"));

  expect(api.refreshConnectionModels).not.toHaveBeenCalled();
});

test("a new connection's picker is filled without saving it first", async () => {
  (api.previewModels as any).mockResolvedValue({
    models: [{ id: "a/b", name: "B", context: 8192, prompt: "0", completion: "0" }],
  });
  render(<ConnectionEditor />);
  await waitFor(() => screen.getByText("+ New connection"));

  fireEvent.click(screen.getByText("+ New connection"));

  await waitFor(() => expect(api.previewModels).toHaveBeenCalledWith({ kind: "openrouter" }));
});

test("Fetch models on an unsaved custom endpoint uses what has been typed", async () => {
  (api.previewModels as any).mockResolvedValue({ models: [] });
  render(<ConnectionEditor />);
  await waitFor(() => screen.getByText("+ New connection"));
  fireEvent.click(screen.getByText("+ New connection"));
  fireEvent.change(screen.getByLabelText("Kind"), { target: { value: "openai_compatible" } });
  fireEvent.change(await screen.findByLabelText("Base URL"),
                   { target: { value: "http://127.0.0.1:8080/v1" } });
  fireEvent.change(screen.getByLabelText("API key"), { target: { value: "sk-typed" } });

  fireEvent.click(screen.getByRole("button", { name: /fetch models/i }));

  await waitFor(() => expect(api.previewModels).toHaveBeenCalledWith({
    kind: "openai_compatible", base_url: "http://127.0.0.1:8080/v1", api_key: "sk-typed" }));
});

// ---- Test connection (#146) ----
test("the sidebar says a connection has not been checked, rather than implying it works", async () => {
  render(<ConnectionEditor />);
  const rail = await waitFor(() => screen.getByText("+ New connection").closest(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("z.ai GLM"));

  expect(await screen.findByText("Not checked yet.")).toBeInTheDocument();
  // ...beside the credential chip, which is the claim it qualifies
  expect(screen.getByText("Key set")).toBeInTheDocument();
});

test("Test connection asks the provider and reports that it works", async () => {
  render(<ConnectionEditor />);
  const rail = await waitFor(() => screen.getByText("+ New connection").closest(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("z.ai GLM"));

  fireEvent.click(await screen.findByRole("button", { name: /test connection/i }));

  await waitFor(() => expect(api.checkConnection).toHaveBeenCalledWith("zai-glm"));
  expect(await screen.findByText(/^Working/)).toBeInTheDocument();
});

test("a refused connection reports the provider's reason, not an app error", async () => {
  (api.checkConnection as any).mockResolvedValue({
    ok: false, kind: "auth", detail: "No auth credentials found",
    checked_at: "2026-08-21T09:00:00Z",
  });
  render(<ConnectionEditor />);
  const rail = await waitFor(() => screen.getByText("+ New connection").closest(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("z.ai GLM"));

  fireEvent.click(await screen.findByRole("button", { name: /test connection/i }));

  expect(await screen.findByText(/No auth credentials found/)).toBeInTheDocument();
  expect(screen.getByText(/Reported as: auth/)).toBeInTheDocument();
  // The answer to the question asked — not the banner that means "this page
  // could not do the thing you clicked".
  expect(screen.queryByText(/Couldn.t reach the model provider/)).toBeNull();
});

test("the rail marks a failing connection, so the reason is findable from the list", async () => {
  (api.listConnections as any).mockResolvedValue([
    { ...OPENROUTER, health: { state: "error", kind: "auth", detail: "bad key", at: "2026-08-21T09:00:00Z" } },
    CUSTOM,
  ]);
  render(<ConnectionEditor />);
  const rail = await waitFor(() => screen.getByText("+ New connection").closest(".editor-list") as HTMLElement);

  expect(await within(rail).findByText("failing")).toBeInTheDocument();
  expect(within(rail).getAllByText("failing")).toHaveLength(1);   // only the one
});

test("a catalog that lands after the reader has moved on does not fill the wrong picker", async () => {
  // The fetch is slower than the click that starts it, so "the connection this
  // response is about" and "the connection on screen" are different questions.
  let landSlow: (v: any) => void;
  (api.refreshConnectionModels as any).mockImplementation((id: string) =>
    id === "openrouter"
      ? new Promise((res) => { landSlow = res; })
      : Promise.resolve({ models: [], fetched_at: "", rev: "r2" }));
  render(<ConnectionEditor />);
  const rail = await waitFor(() => screen.getByText("+ New connection").closest(".editor-list") as HTMLElement);

  fireEvent.click(await within(rail).findByText("OpenRouter"));   // starts the slow fetch
  await waitFor(() => expect(api.refreshConnectionModels).toHaveBeenCalledWith("openrouter"));
  fireEvent.click(await within(rail).findByText("z.ai GLM"));
  await waitFor(() => expect(api.readConnection).toHaveBeenCalledWith("zai-glm"));
  landSlow!({ models: [{ id: "openrouter-only/model", name: "OR", context: null, prompt: null, completion: null }],
              fetched_at: "2026-08-21", rev: "r1" });

  fireEvent.click(await screen.findByRole("button", { name: /^edit$/i }));
  const model = await screen.findByDisplayValue("glm-4.6");
  fireEvent.focus(model);
  expect(screen.queryByText("openrouter-only/model")).toBeNull();
});

test("a catalog for a revision the connection has moved past is discarded", async () => {
  // Not the same guard as the one above: this response IS about the connection
  // on screen, but about the settings it had before the reader saved. Its
  // models describe an endpoint this connection no longer points at, and
  // nothing would replace them.
  let landSlow: (v: any) => void;
  (api.refreshConnectionModels as any).mockReturnValue(
    new Promise((res) => { landSlow = res; }));
  render(<ConnectionEditor />);
  const rail = await waitFor(() => screen.getByText("+ New connection").closest(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("z.ai GLM"));
  fireEvent.click(await screen.findByRole("button", { name: /refresh models/i }));

  // the reader saves an edit; the reselect below picks up the new revision
  (api.readConnection as any).mockResolvedValue({
    ...CUSTOM, rev: "r3", base_url: "https://new/v4", models: [], fetched_at: "",
    health: UNCHECKED });
  fireEvent.click(await within(rail).findByText("z.ai GLM"));
  await waitFor(() => expect(api.readConnection).toHaveBeenCalledTimes(2));
  landSlow!({ models: [{ id: "old-endpoint/model", name: "Old", context: null, prompt: null, completion: null }],
              fetched_at: "2026-08-21", rev: "r2" });

  fireEvent.click(await screen.findByRole("button", { name: /^edit$/i }));
  fireEvent.focus(await screen.findByDisplayValue("glm-4.6"));
  expect(screen.queryByText("old-endpoint/model")).toBeNull();
});

test("a health verdict for a revision the reader saved past is discarded", async () => {
  // The server already refuses to hand that verdict back (it files them under
  // the revision they describe); this panel must not be where it survives.
  let landCheck: (v: any) => void;
  (api.checkConnection as any).mockReturnValue(new Promise((res) => { landCheck = res; }));
  render(<ConnectionEditor />);
  const rail = await waitFor(() => screen.getByText("+ New connection").closest(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("z.ai GLM"));
  fireEvent.click(await screen.findByRole("button", { name: /test connection/i }));

  (api.readConnection as any).mockResolvedValue({
    ...CUSTOM, rev: "r3", models: [], fetched_at: "", health: UNCHECKED });
  fireEvent.click(await within(rail).findByText("z.ai GLM"));
  await waitFor(() => expect(api.readConnection).toHaveBeenCalledTimes(2));
  landCheck!({ ok: false, kind: "auth", detail: "the old key was rejected",
               checked_at: "2026-08-21T09:00:00Z" });

  expect(await screen.findByText("Not checked yet.")).toBeInTheDocument();
  expect(screen.queryByText(/the old key was rejected/)).toBeNull();
});

test("a preview for a draft the reader has since edited is discarded", async () => {
  // An unsaved form has no revision, and its fields ARE its identity: the
  // catalog that lands describes the endpoint that was typed before, and the
  // reader could pick a model from it and save it against the new one.
  let landPreview: (v: any) => void;
  (api.previewModels as any).mockResolvedValueOnce({ models: [] })   // the mount fetch
    .mockReturnValueOnce(new Promise((res) => { landPreview = res; }));
  render(<ConnectionEditor />);
  await waitFor(() => screen.getByText("+ New connection"));
  fireEvent.click(screen.getByText("+ New connection"));
  fireEvent.change(screen.getByLabelText("Kind"), { target: { value: "openai_compatible" } });
  fireEvent.change(await screen.findByLabelText("Base URL"), { target: { value: "http://old/v1" } });
  fireEvent.click(screen.getByRole("button", { name: /fetch models/i }));

  fireEvent.change(screen.getByLabelText("Base URL"), { target: { value: "http://new/v1" } });
  landPreview!({ models: [{ id: "old-endpoint/model", name: "Old", context: null, prompt: null, completion: null }] });

  // Wait for the response to have been *processed* before asserting it was
  // dropped: the button reverting from "Fetching…" is that signal. Asserting
  // the absence first would pass on a page the answer had not reached yet,
  // which is a test that cannot fail.
  await screen.findByRole("button", { name: /fetch models/i });
  // The model field is the page's only combobox; its input carries no label of
  // its own, so it is reached through the wrapper the component gives it.
  fireEvent.focus(document.querySelector(".combobox input") as HTMLElement);
  expect(screen.queryByText("old-endpoint/model")).toBeNull();
});

test("an OpenRouter connection edited elsewhere fetches again when reopened", async () => {
  // The suppression is per revision, not per id: an edit from another tab
  // bumps the rev and clears the cached sidecar, and a connection whose picker
  // really is empty must not be skipped because an older revision was fetched.
  (api.refreshConnectionModels as any).mockResolvedValue({
    models: [], fetched_at: "2026-08-21", rev: "r1" });
  render(<ConnectionEditor />);
  const rail = await waitFor(() => screen.getByText("+ New connection").closest(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("OpenRouter"));
  await waitFor(() => expect(api.refreshConnectionModels).toHaveBeenCalledTimes(1));

  (api.readConnection as any).mockResolvedValue({
    ...OPENROUTER, rev: "r9", models: [], fetched_at: "", health: UNCHECKED });
  fireEvent.click(await within(rail).findByText("OpenRouter"));

  await waitFor(() => expect(api.refreshConnectionModels).toHaveBeenCalledTimes(2));
});

test("a stored failure is shown when the connection is opened, not only after a click", async () => {
  (api.readConnection as any).mockResolvedValue({
    ...CUSTOM, models: [], fetched_at: "",
    health: { state: "error", kind: "network", detail: "connection refused", at: "2026-08-21T09:00:00Z" },
  });
  render(<ConnectionEditor />);
  const rail = await waitFor(() => screen.getByText("+ New connection").closest(".editor-list") as HTMLElement);

  fireEvent.click(await within(rail).findByText("z.ai GLM"));

  expect(await screen.findByText(/connection refused/)).toBeInTheDocument();
});
