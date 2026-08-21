import { render, screen, fireEvent, waitFor } from "@testing-library/react";

vi.mock("../api/client", async () => ({
  ...(await vi.importActual<typeof import("../api/client")>("../api/client")),
  api: { listConnections: vi.fn(), readConnection: vi.fn() },
}));
vi.mock("../api/models", async () => ({
  ...(await vi.importActual<typeof import("../api/models")>("../api/models")),
  getModels: vi.fn(),
}));

import { api } from "../api/client";
import { getModels } from "../api/models";
import RerollRoutePicker, { NO_REROLL_ROUTE } from "./RerollRoute";

const OPENROUTER = {
  id: "openrouter", kind: "openrouter", name: "OpenRouter", base_url: "",
  model: "vendor/campaign", post_process: "none", key_set: true, rev: "r1",
};
const LOCAL = {
  id: "local", kind: "openai_compatible", name: "Local", base_url: "http://localhost:11434/v1",
  model: "llama3", post_process: "none", key_set: false, rev: "r2",
};
const CLAUDE = {
  id: "claude", kind: "claude", name: "Claude", base_url: "",
  model: "opus", post_process: "none", key_set: true, rev: "r3",
};
const ACTIVE = { id: "openrouter", kind: "openrouter" as const, name: "OpenRouter", model: "vendor/campaign" };

beforeEach(() => {
  vi.clearAllMocks();
  (api.listConnections as any).mockResolvedValue([OPENROUTER, LOCAL, CLAUDE]);
  (api.readConnection as any).mockResolvedValue({ ...LOCAL, models: [], fetched_at: "" });
  (getModels as any).mockResolvedValue([]);
});

test("the default option is offered, with the active connection on hover", async () => {
  render(<RerollRoutePicker value={NO_REROLL_ROUTE} onChange={() => {}} active={ACTIVE} />);
  const select = await screen.findByLabelText<HTMLSelectElement>("Reroll connection");
  expect(select.value).toBe("");
  await screen.findByRole("option", { name: "Default" });
  // Every OTHER connection is offered: reaching another provider is the case a
  // bare model id cannot express.
  expect(screen.getByRole("option", { name: "Local" })).toBeInTheDocument();
  expect(screen.getByRole("option", { name: "Claude" })).toBeInTheDocument();
  // and the active one is not offered twice — "Default" already is it.
  expect(screen.queryByRole("option", { name: "OpenRouter" })).toBeNull();
  expect(screen.getAllByRole("option")).toHaveLength(3);
  // The name is not lost, only moved off a control it does not fit.
  expect(screen.getByRole("option", { name: "Default" }))
    .toHaveAttribute("title", "Default: OpenRouter");
});

test("the model box shows what leaving it blank would run", async () => {
  render(<RerollRoutePicker value={NO_REROLL_ROUTE} onChange={() => {}} active={ACTIVE} />);
  expect(await screen.findByPlaceholderText("vendor/campaign")).toBeInTheDocument();
});

test("choosing a connection clears the model chosen for the previous one", async () => {
  const onChange = vi.fn();
  render(<RerollRoutePicker value={{ connection_id: "", model: "vendor/bigger" }}
                            onChange={onChange} active={ACTIVE} />);
  await screen.findByRole("option", { name: "Local" });

  fireEvent.change(screen.getByLabelText("Reroll connection"), { target: { value: "local" } });

  // Not carried across: an OpenRouter id means nothing to a local endpoint.
  expect(onChange).toHaveBeenCalledWith({ connection_id: "local", model: "" });
});

test("a custom endpoint offers the models its own refresh cached", async () => {
  (api.readConnection as any).mockResolvedValue({
    ...LOCAL, fetched_at: "t",
    models: [{ id: "qwen3", name: "Qwen 3", context: 32768, prompt: null, completion: null }],
  });
  render(<RerollRoutePicker value={{ connection_id: "local", model: "" }}
                            onChange={() => {}} active={ACTIVE} />);

  await waitFor(() => expect(api.readConnection).toHaveBeenCalledWith("local"));
  fireEvent.focus(await screen.findByLabelText("Reroll model"));
  expect(await screen.findByText("Qwen 3")).toBeInTheDocument();
  // and the catalog it has no business fetching stays unfetched
  expect(getModels).not.toHaveBeenCalled();
});

test("a claude connection offers the model ids the connection form knows", async () => {
  render(<RerollRoutePicker value={{ connection_id: "claude", model: "" }}
                            onChange={() => {}} active={ACTIVE} />);
  await screen.findByRole("option", { name: "Claude" });

  fireEvent.focus(screen.getByLabelText("Reroll model"));

  expect(await screen.findByText("Opus (latest)")).toBeInTheDocument();
  expect(getModels).not.toHaveBeenCalled();
  expect(api.readConnection).not.toHaveBeenCalled();
});

test("an openrouter reroll offers the catalog", async () => {
  (getModels as any).mockResolvedValue(
    [{ id: "vendor/bigger", name: "Bigger", context: 200000, prompt: "0.000001", completion: "0.000002" }]);
  render(<RerollRoutePicker value={NO_REROLL_ROUTE} onChange={() => {}} active={ACTIVE} />);
  await waitFor(() => expect(getModels).toHaveBeenCalled());

  fireEvent.focus(screen.getByLabelText("Reroll model"));

  expect(await screen.findByText("Bigger")).toBeInTheDocument();
});

test("a catalog that will not load leaves the box typeable and says so", async () => {
  (getModels as any).mockRejectedValue(new Error("offline"));
  render(<RerollRoutePicker value={NO_REROLL_ROUTE} onChange={() => {}} active={ACTIVE} />);

  expect(await screen.findByText(/couldn’t load model list/)).toBeInTheDocument();
  expect(screen.getByLabelText("Reroll model")).not.toBeDisabled();
});

test("a connection list that cannot be read still offers the default", async () => {
  (api.listConnections as any).mockRejectedValue(new Error("offline"));
  render(<RerollRoutePicker value={NO_REROLL_ROUTE} onChange={() => {}} active={ACTIVE} />);

  expect(await screen.findByRole("option", { name: "Default" })).toBeInTheDocument();
});
