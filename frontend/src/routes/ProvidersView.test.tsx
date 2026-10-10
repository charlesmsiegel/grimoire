import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { ApiError, api, type CapabilityNeed } from "../api/client";
import { forgetModelTests } from "../components/inference/TestCallDialog";
import ProvidersView, { validReturn } from "./ProvidersView";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, api: {
    listConnections: vi.fn(), readConnection: vi.fn(), createConnection: vi.fn(),
    updateConnection: vi.fn(), deleteConnection: vi.fn(), refreshConnectionModels: vi.fn(),
    checkConnection: vi.fn(), listProviderPresets: vi.fn(), getInferenceSettings: vi.fn(),
    readConnectionCapabilities: vi.fn(), readModelFacts: vi.fn(), putModelFacts: vi.fn(),
    previewModelTest: vi.fn(), runModelTest: vi.fn(),
  } };
});
// Its own suite covers it; here it only has to be where the page puts it.
vi.mock("../components/RegexRulesEditor", () => ({
  RegexRulesEditor: ({ scope, readOnly }: { scope: { id: string }; readOnly?: boolean }) =>
    <div>output rules for {scope.id}{readOnly ? " (read-only)" : ""}</div>,
}));

const preset = (over: Record<string, unknown>) => ({
  base_url: "", url_locked: false, billing: "metered", reports_price: false,
  always: [], possible: [], never: [], generating_check: false, ...over,
});

const PRESETS = [
  preset({ id: "openrouter", label: "OpenRouter", kind: "openrouter",
           base_url: "https://openrouter.ai/api/v1", url_locked: true, reports_price: true }),
  preset({ id: "claude", label: "Claude subscription", kind: "claude",
           billing: "subscription", generating_check: true }),
  preset({ id: "ollama", label: "Ollama", kind: "openai_compatible",
           base_url: "http://localhost:11434/v1" }),
];

const HEALTH = { state: "unknown", kind: "", detail: "", at: "" };

const provider = (over: Record<string, unknown>) => ({
  base_url: "", model: "", effective_model: "", post_process: "none", key_set: true,
  rev: "r1", health: HEALTH, billing: "metered", sampler_support: "", ...over,
});

const SALTMARCH = provider({
  id: "saltmarch", name: "Saltmarch Router", kind: "openrouter", preset: "openrouter",
  base_url: "https://openrouter.ai/api/v1",
});
const CLAUDE = provider({
  id: "realm-claude", name: "Realm Claude", kind: "claude", preset: "claude",
  billing: "subscription", key_set: false,
});

const DETAILS: Record<string, unknown> = {
  saltmarch: {
    ...SALTMARCH, models: [], fetched_at: "2026-10-01T10:00:00Z", sampling: null,
    used_by: [
      { kind: "role", key: "primary", scope: "global" },
      { kind: "route", key: "summary", scope: "global" },
    ],
  },
  "realm-claude": { ...CLAUDE, models: [], fetched_at: "", sampling: null, used_by: [] },
};

const CAPS = {
  generate: { value: "yes", source: "catalog" },
  vision: { value: "unknown", source: "unknown" },
  embed: { value: "no", source: "catalog" },
  decide_native: { value: "no", source: "adapter" },
  tools: { value: "unknown", source: "unknown" },
};

function answer(need: CapabilityNeed) {
  const row = { id: "vendor/m", name: "Vendor M", context: null, prompt: null, completion: null,
                reason: "the catalog says so", capabilities: CAPS };
  return {
    provider_preset: PRESETS[0], need, reason: null,
    groups: need === "generate" ? { fits: [row], unverified: [] } : { fits: [], unverified: [] },
    hidden: need === "generate" ? [] : [{ id: "vendor/m", reason: "it does not embed" }],
  };
}

function facts(over: Record<string, unknown> = {}) {
  return {
    provider: "saltmarch", model: "vendor/m", vision: "", prefill: false, post_process: "none",
    rates: null, verified: {}, overrides: {}, capabilities: CAPS,
    context_window: null, max_output: null,
    limits: { window: { value: null, source: "unknown", catalog: null },
              max_output: { value: null, source: "unknown", catalog: null } },
    ...over,
  };
}

function settings(over: Record<string, unknown> = {}) {
  return {
    format: "2", newer: false, migration: { state: "done", reason: "", skipped: [] },
    roles: {}, providers: [], presets: [], preset_clear: "",
    routes: [{ key: "summary", label: "Rolling summary" }],
    ...over,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  forgetModelTests();
  (api.listConnections as any).mockResolvedValue([SALTMARCH, CLAUDE]);
  (api.readConnection as any).mockImplementation((id: string) => Promise.resolve(DETAILS[id]));
  (api.listProviderPresets as any).mockResolvedValue(PRESETS);
  (api.getInferenceSettings as any).mockResolvedValue(settings());
  (api.readConnectionCapabilities as any).mockImplementation(
    (_id: string, need: CapabilityNeed) => Promise.resolve(answer(need)));
  (api.readModelFacts as any).mockResolvedValue(facts());
  (api.putModelFacts as any).mockImplementation(
    (_id: string, body: Record<string, unknown>) => Promise.resolve(facts(body)));
  (api.checkConnection as any).mockResolvedValue(
    { ok: true, kind: "", detail: "", checked_at: "2026-10-08T09:00:00Z" });
  (api.updateConnection as any).mockImplementation(
    (id: string) => Promise.resolve(DETAILS[id]));
  (api.previewModelTest as any).mockResolvedValue({
    provider: "Saltmarch Router", provider_id: "saltmarch", model: "vendor/m",
    sends: [], estimated_cost_usd: null });
});

/** Where the router is, so a chip's navigation can be read off the page. */
function Where() {
  const { pathname, search, hash } = useLocation();
  return <><div data-testid="where">{pathname + hash}</div><div data-testid="search">{search}</div></>;
}

function open(at = "/providers") {
  return render(
    <MemoryRouter initialEntries={[at]}>
      <Where />
      <Routes>
        <Route path="/providers" element={<ProvidersView />} />
        <Route path="/providers/new" element={<ProvidersView />} />
        <Route path="/providers/:id" element={<ProvidersView />} />
        <Route path="/providers/:id/models/*" element={<ProvidersView />} />
        <Route path="/models" element={<div>the models page</div>} />
        <Route path="/models/*" element={<div>the models page</div>} />
        <Route path="/campaigns/:cid" element={<div>the campaign</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

const column = () => within(screen.getByRole("complementary", { name: "Providers" }));
const main = () => within(screen.getByRole("main"));

test("clicking a provider shows the read-only view with its sidebar", async () => {
  open();
  fireEvent.click(await column().findByRole("link", { name: /Saltmarch Router/ }));

  expect(await main().findByRole("heading", { name: "Saltmarch Router" })).toBeInTheDocument();
  expect(screen.getByTestId("where")).toHaveTextContent("/providers/saltmarch");
  // Read-only: nothing on the page is a field.
  expect(main().queryByRole("textbox")).not.toBeInTheDocument();
  const sidebar = within(main().getByRole("complementary", { name: "Saltmarch Router" }));
  expect(sidebar.getByRole("button", { name: "Edit" })).toBeEnabled();
  expect(sidebar.getByText("metered")).toBeInTheDocument();
  expect(sidebar.getByText(/Last fetched/)).toBeInTheDocument();
  // A free check runs on open, and says what it found.
  expect(api.checkConnection).toHaveBeenCalledWith("saltmarch");
  expect(sidebar.getByText(/Working/)).toBeInTheDocument();
  // The catalog, badged, and the provider's models listed in the column.
  expect(main().getByText("generate: yes")).toBeInTheDocument();
  expect(main().getByText("decide: no")).toBeInTheDocument();
  expect(column().getByRole("link", { name: "vendor/m" }))
    .toHaveAttribute("href", "/providers/saltmarch/models/vendor/m");
  expect(main().getByText("output rules for saltmarch")).toBeInTheDocument();
});

test("Edit reveals the form", async () => {
  open("/providers/saltmarch");
  fireEvent.click(await main().findByRole("button", { name: "Edit" }));

  const name = await main().findByRole("textbox", { name: "Name" });
  expect(name).toHaveValue("Saltmarch Router");
  expect(main().getByLabelText("API key")).toBeInTheDocument();
  // Nothing about a model is the provider's to say any more.
  expect(main().queryByLabelText(/^Model/)).not.toBeInTheDocument();

  fireEvent.change(name, { target: { value: "Saltmarch Main" } });
  fireEvent.click(main().getByRole("button", { name: "Save provider" }));
  expect(await main().findByRole("button", { name: "Edit" })).toBeInTheDocument();
  const [id, patch] = (api.updateConnection as any).mock.calls[0];
  expect(id).toBe("saltmarch");
  expect(patch).toMatchObject({ name: "Saltmarch Main" });
  // Billing was not touched, so the save says nothing about it.
  for (const field of ["model", "vision", "prefill", "post_process", "reasoning_effort",
                       "sampler_preset", "api_key", "base_url", "billing"]) {
    expect(patch).not.toHaveProperty(field);
  }
});

test("+ New provider asks for a preset first", async () => {
  (api.createConnection as any).mockResolvedValue({ id: "saltmarch-local" });
  open("/providers/saltmarch");
  fireEvent.click(await column().findByRole("button", { name: "+ New provider" }));

  const choose = await main().findByRole("group", { name: "Provider presets" });
  expect(main().queryByRole("textbox", { name: "Name" })).not.toBeInTheDocument();
  expect(api.createConnection).not.toHaveBeenCalled();

  fireEvent.click(within(choose).getByRole("button", { name: "Ollama" }));
  const address = await main().findByRole("textbox", { name: "Address" });
  expect(address).toHaveValue("http://localhost:11434/v1");
  expect(address).toBeEnabled();
  fireEvent.change(main().getByRole("textbox", { name: "Name" }),
                   { target: { value: "Saltmarch Local" } });

  const local = provider({ id: "saltmarch-local", name: "Saltmarch Local",
                           kind: "openai_compatible", preset: "ollama",
                           base_url: "http://localhost:11434/v1", key_set: false });
  DETAILS["saltmarch-local"] = { ...local, models: [], fetched_at: "", used_by: [] };
  (api.listConnections as any).mockResolvedValue([SALTMARCH, CLAUDE, local]);
  (api.checkConnection as any).mockClear();
  fireEvent.click(main().getByRole("button", { name: "Create provider" }));

  expect(await main().findByRole("heading", { name: "Saltmarch Local" })).toBeInTheDocument();
  expect(api.createConnection).toHaveBeenCalledWith({
    kind: "openai_compatible", preset: "ollama", name: "Saltmarch Local",
    base_url: "http://localhost:11434/v1", billing: "metered", sampler_support: "",
  });
  // After the save, the free check runs by itself.
  expect(api.checkConnection).toHaveBeenCalledWith("saltmarch-local");
});

test("a locked preset's address cannot be edited", async () => {
  open();
  fireEvent.click(await column().findByRole("button", { name: "+ New provider" }));
  fireEvent.click(within(await main().findByRole("group", { name: "Provider presets" }))
    .getByRole("button", { name: "OpenRouter" }));
  const address = await main().findByRole("textbox", { name: "Address" });
  expect(address).toHaveValue("https://openrouter.ai/api/v1");
  expect(address).toBeDisabled();
});

test("a failed presets read asks before any check rather than sending one", async () => {
  (api.listProviderPresets as any).mockRejectedValue(new Error("offline"));
  open("/providers/realm-claude");
  expect(await main().findByRole("button", { name: "Check (sends one short message)" }))
    .toBeInTheDocument();
  expect(api.checkConnection).not.toHaveBeenCalled();
});

test("a Claude provider's check asks before sending", async () => {
  open("/providers/realm-claude");
  const ask = await main().findByRole("button", { name: "Check (sends one short message)" });
  // Nothing was sent on open: this provider's check generates.
  expect(api.checkConnection).not.toHaveBeenCalled();

  fireEvent.click(ask);
  const send = await main().findByRole("button", { name: "Send it" });
  expect(api.checkConnection).not.toHaveBeenCalled();
  fireEvent.click(send);
  expect(await main().findByText(/Working/)).toBeInTheDocument();
  expect(api.checkConnection).toHaveBeenCalledWith("realm-claude", { confirm: true });
});

test("Used by chips link to the models page", async () => {
  open("/providers/saltmarch");
  const used = within(await main().findByRole("group", { name: "Used by" }));
  fireEvent.click(used.getByRole("button", { name: "Rolling summary" }));
  expect(await screen.findByText("the models page")).toBeInTheDocument();
  expect(screen.getByTestId("where")).toHaveTextContent("/models/edit#task-summary");
});

test("a Used by role chip opens that role", async () => {
  open("/providers/saltmarch");
  const used = within(await main().findByRole("group", { name: "Used by" }));
  fireEvent.click(used.getByRole("button", { name: "Primary" }));
  expect(await screen.findByText("the models page")).toBeInTheDocument();
  expect(screen.getByTestId("where")).toHaveTextContent(/^\/models$/);
});

test("a campaign's Used by chip opens that campaign, not the library's record", async () => {
  (api.readConnection as any).mockImplementation((id: string) => Promise.resolve(
    id === "saltmarch"
      ? { ...(DETAILS.saltmarch as object),
          used_by: [{ kind: "role", key: "fast", scope: "campaign", cid: "winifred-run" }] }
      : DETAILS[id]));
  open("/providers/saltmarch");
  const used = within(await main().findByRole("group", { name: "Used by" }));
  const chip = used.getByRole("button", { name: "Fast · winifred-run" });
  // Where it is changed: that campaign's own Models, in a scene's Inspector.
  expect(chip).toHaveAttribute("title", expect.stringMatching(/Inspector/));
  fireEvent.click(chip);
  expect(await screen.findByText("the campaign")).toBeInTheDocument();
  expect(screen.getByTestId("where")).toHaveTextContent("/campaigns/winifred-run");
});

test("a model's facts are read-only until Edit", async () => {
  open("/providers/saltmarch");
  fireEvent.click(await main().findByRole("link", { name: "vendor/m" }));

  expect(await main().findByRole("heading", { name: "vendor/m" })).toBeInTheDocument();
  expect(main().queryByRole("combobox")).not.toBeInTheDocument();
  expect(main().queryByRole("checkbox")).not.toBeInTheDocument();

  fireEvent.click(main().getByRole("button", { name: "Edit" }));
  fireEvent.click(await main().findByRole("checkbox", { name: /Continue replies by prefill/ }));
  fireEvent.change(main().getByRole("combobox", { name: "Vision: override" }),
                   { target: { value: "no" } });
  fireEvent.click(main().getByRole("button", { name: "Save facts" }));

  expect(await main().findByRole("button", { name: "Edit" })).toBeInTheDocument();
  // Only what changed: a field nobody touched is not made into an assertion.
  expect(api.putModelFacts).toHaveBeenCalledWith("saltmarch", {
    model: "vendor/m", prefill: true, overrides: { vision: "no" },
  });

  // Test… opens the confirmation, which sends nothing by itself.
  fireEvent.click(main().getByRole("button", { name: "Test…" }));
  expect(await screen.findByRole("dialog", { name: "Test a model" })).toBeInTheDocument();
  expect(api.runModelTest).not.toHaveBeenCalled();
});

test("a catalog's no is offered to a test; only the adapter's rules a probe out", async () => {
  // A passing test outranks the catalog (and the user's word), and the server
  // refuses only the provider preset's `never` -- so only that leaves a probe.
  (api.readModelFacts as any).mockResolvedValue(facts({ capabilities: {
    ...CAPS, generate: { value: "no", source: "adapter" },
    vision: { value: "no", source: "catalog" }, embed: { value: "no", source: "name" },
  } }));
  open("/providers/saltmarch/models/vendor/m");
  expect(await main().findByRole("heading", { name: "vendor/m" })).toBeInTheDocument();
  fireEvent.click(main().getByRole("button", { name: "Test…" }));
  expect(await screen.findByRole("dialog", { name: "Test a model" })).toBeInTheDocument();
  expect(api.previewModelTest).toHaveBeenCalledWith(
    "saltmarch", { model: "vendor/m", capabilities: ["vision", "embed", "tools"] });
});

test("Test… lists decide_native for a model whose native decisions are unknown", async () => {
  (api.readModelFacts as any).mockResolvedValue(facts({ capabilities: {
    ...CAPS, decide_native: { value: "unknown", source: "unknown" },
  } }));
  open("/providers/saltmarch/models/vendor/m");
  expect(await main().findByRole("heading", { name: "vendor/m" })).toBeInTheDocument();
  fireEvent.click(main().getByRole("button", { name: "Test…" }));
  expect(await screen.findByRole("dialog", { name: "Test a model" })).toBeInTheDocument();
  expect(api.previewModelTest).toHaveBeenCalledWith(
    "saltmarch", { model: "vendor/m",
                   capabilities: ["generate", "vision", "embed", "decide_native", "tools"] });
});

test("an adapter's no on tools leaves the tools probe out of Test…", async () => {
  (api.readModelFacts as any).mockResolvedValue(facts({ capabilities: {
    ...CAPS, tools: { value: "no", source: "adapter" } } }));
  open("/providers/saltmarch/models/vendor/m");
  expect(await main().findByRole("heading", { name: "vendor/m" })).toBeInTheDocument();
  fireEvent.click(main().getByRole("button", { name: "Test…" }));
  expect(await screen.findByRole("dialog", { name: "Test a model" })).toBeInTheDocument();
  expect(api.previewModelTest).toHaveBeenCalledWith(
    "saltmarch", { model: "vendor/m", capabilities: ["generate", "vision", "embed"] });
});

test("the facts form overrides tool calling", async () => {
  open("/providers/saltmarch/models/vendor/m");
  expect(await main().findByRole("heading", { name: "vendor/m" })).toBeInTheDocument();
  fireEvent.click(main().getByRole("button", { name: "Edit" }));
  fireEvent.change(await main().findByRole("combobox", { name: "Tool calling: override" }),
                   { target: { value: "no" } });
  fireEvent.click(main().getByRole("button", { name: "Save facts" }));
  expect(await main().findByRole("button", { name: "Edit" })).toBeInTheDocument();
  expect(api.putModelFacts).toHaveBeenCalledWith("saltmarch", {
    model: "vendor/m", overrides: { tools: "no" },
  });
});

test("a model the adapter rules out of every probe has nothing to test", async () => {
  const never = { value: "no", source: "adapter" };
  (api.readModelFacts as any).mockResolvedValue(facts({ capabilities: {
    ...CAPS, generate: never, vision: never, embed: never, tools: never } }));
  open("/providers/saltmarch/models/vendor/m");
  expect(await main().findByRole("heading", { name: "vendor/m" })).toBeInTheDocument();
  expect(main().getByRole("button", { name: "Test…" })).toBeDisabled();
});

const RATED = { prompt_usd_per_1k: 0.001, completion_usd_per_1k: 0.002 };
const sidebarOf = (model: string) => within(main().getByRole("complementary", { name: model }));

test("a model's rates show in its sidebar", async () => {
  (api.readModelFacts as any).mockResolvedValue(facts({
    rates: { ...RATED, cache_read_usd_per_1k: 0 } }));
  open("/providers/saltmarch/models/vendor/m");
  await main().findByRole("heading", { name: "vendor/m" });

  const sidebar = sidebarOf("vendor/m");
  expect(sidebar.getByRole("heading", { name: "Rates" })).toBeInTheDocument();
  expect(sidebar.getByText("Input $0.001 / 1K")).toHaveClass("chip", "on");
  expect(sidebar.getByText("Output $0.002 / 1K")).toBeInTheDocument();
  // A stated zero is a price, and an unstated rate draws nothing.
  expect(sidebar.getByText("Cache read $0 / 1K")).toBeInTheDocument();
  expect(sidebar.queryByText(/Cache write/)).not.toBeInTheDocument();
  // Plain attributes: nothing to click.
  expect(sidebar.queryByRole("button", { name: /Input/ })).not.toBeInTheDocument();
});

test("a model with no rates says the pricing table is used if it covers the model", async () => {
  open("/providers/saltmarch/models/vendor/m");
  await main().findByRole("heading", { name: "vendor/m" });

  expect(sidebarOf("vendor/m").getByText(
    "None stated — your pricing table is used if it covers this model.")).toBeInTheDocument();
});

test("rates are read-only until Edit, and Save sends them", async () => {
  open("/providers/saltmarch/models/vendor/m");
  await main().findByRole("heading", { name: "vendor/m" });
  expect(main().queryByRole("spinbutton")).not.toBeInTheDocument();

  fireEvent.click(main().getByRole("button", { name: "Edit" }));
  fireEvent.change(await main().findByLabelText("Input rate for vendor/m"),
                   { target: { value: "0.001" } });
  fireEvent.change(main().getByLabelText("Output rate for vendor/m"), { target: { value: "0.002" } });
  // Zero is a price, kept as one; the box nobody filled is left out, not sent as null.
  fireEvent.change(main().getByLabelText("Cache read rate for vendor/m"), { target: { value: "0" } });
  fireEvent.click(main().getByRole("button", { name: "Save facts" }));

  expect(await main().findByRole("button", { name: "Edit" })).toBeInTheDocument();
  expect(api.putModelFacts).toHaveBeenCalledWith("saltmarch", {
    model: "vendor/m", rates: { ...RATED, cache_read_usd_per_1k: 0 },
  });
  expect(sidebarOf("vendor/m").getByText("Cache read $0 / 1K")).toBeInTheDocument();
});

test.each([
  ["Input", "prompt_usd_per_1k"],
  ["Cache write", "cache_write_usd_per_1k"],
])("a negative %s rate is sent, and the store's refusal shown", async (label, field) => {
  // One policy for every box: a filled box is sent as typed and the store
  // names what is wrong with it. "Both needed" is only for a box left empty.
  (api.putModelFacts as any).mockRejectedValue(
    new ApiError(400, `${field} must be a non-negative number`));
  open("/providers/saltmarch/models/vendor/m");
  fireEvent.click(await main().findByRole("button", { name: "Edit" }));
  fireEvent.change(await main().findByLabelText("Input rate for vendor/m"),
                   { target: { value: "0.001" } });
  fireEvent.change(main().getByLabelText("Output rate for vendor/m"), { target: { value: "0.002" } });
  fireEvent.change(main().getByLabelText(`${label} rate for vendor/m`), { target: { value: "-1" } });

  expect(main().queryByText("Input and output are both needed.")).not.toBeInTheDocument();
  fireEvent.click(main().getByRole("button", { name: "Save facts" }));

  expect(await main().findByText(`${field} must be a non-negative number`)).toBeInTheDocument();
  expect(api.putModelFacts).toHaveBeenCalledWith("saltmarch", { model: "vendor/m",
    rates: { ...RATED, [field]: -1 } });
  expect(main().getByRole("button", { name: "Save facts" })).toBeInTheDocument();
});

test("clearing every rate sends an empty object", async () => {
  (api.readModelFacts as any).mockResolvedValue(facts({ rates: RATED }));
  open("/providers/saltmarch/models/vendor/m");
  fireEvent.click(await main().findByRole("button", { name: "Edit" }));
  fireEvent.change(await main().findByLabelText("Input rate for vendor/m"), { target: { value: "" } });
  fireEvent.change(main().getByLabelText("Output rate for vendor/m"), { target: { value: "" } });
  fireEvent.click(main().getByRole("button", { name: "Save facts" }));

  expect(await main().findByRole("button", { name: "Edit" })).toBeInTheDocument();
  expect(api.putModelFacts).toHaveBeenCalledWith("saltmarch", { model: "vendor/m", rates: {} });
});

test("a half-filled rate cannot be saved", async () => {
  open("/providers/saltmarch/models/vendor/m");
  fireEvent.click(await main().findByRole("button", { name: "Edit" }));
  fireEvent.change(await main().findByLabelText("Input rate for vendor/m"),
                   { target: { value: "0.001" } });

  expect(main().getByText("Input and output are both needed.")).toBeInTheDocument();
  expect(main().getByRole("button", { name: "Save facts" })).toBeDisabled();

  fireEvent.change(main().getByLabelText("Output rate for vendor/m"), { target: { value: "0" } });
  expect(main().queryByText("Input and output are both needed.")).not.toBeInTheDocument();
  expect(main().getByRole("button", { name: "Save facts" })).toBeEnabled();
});

test("cache rates alone cannot be saved", async () => {
  open("/providers/saltmarch/models/vendor/m");
  fireEvent.click(await main().findByRole("button", { name: "Edit" }));
  fireEvent.change(await main().findByLabelText("Cache read rate for vendor/m"),
                   { target: { value: "0.0001" } });

  expect(main().getByText("Input and output are both needed.")).toBeInTheDocument();
  expect(main().getByRole("button", { name: "Save facts" })).toBeDisabled();
  expect(api.putModelFacts).not.toHaveBeenCalled();
});

test("?edit=rates opens the form on the rates", async () => {
  (api.readModelFacts as any).mockResolvedValue(facts({ rates: RATED }));
  open("/providers/saltmarch/models/vendor/m?edit=rates");

  const input = await main().findByLabelText("Input rate for vendor/m");
  expect(input).toHaveValue(0.001);
  expect(input).toHaveFocus();
  expect(main().getByRole("button", { name: "Save facts" })).toBeInTheDocument();
});

test.each([
  ["a newer-format store", { newer: true, migration: { state: "newer", reason: "", skipped: [] } }],
  ["a store not yet switched", { format: "1",
                                 migration: { state: "pending", reason: "", skipped: [] } }],
])("?edit=rates on %s stays read-only", async (_what, over) => {
  (api.getInferenceSettings as any).mockResolvedValue(settings(over));
  open("/providers/saltmarch/models/vendor/m?edit=rates");

  expect(await main().findByRole("heading", { name: "vendor/m" })).toBeInTheDocument();
  expect(main().getByRole("button", { name: "Edit" })).toBeDisabled();
  expect(main().queryByRole("spinbutton")).not.toBeInTheDocument();
  expect(main().queryByRole("button", { name: "Save facts" })).not.toBeInTheDocument();
});

test("?edit=rates waits for the store's format before opening the form", async () => {
  let answer: (value: unknown) => void = () => {};
  (api.getInferenceSettings as any).mockReturnValue(new Promise((done) => { answer = done; }));
  open("/providers/saltmarch/models/vendor/m?edit=rates");

  // Not known yet is not writable: no form, and no Save to click early.
  expect(await main().findByRole("heading", { name: "vendor/m" })).toBeInTheDocument();
  expect(main().getByRole("button", { name: "Edit" })).toBeDisabled();
  expect(main().queryByRole("button", { name: "Save facts" })).not.toBeInTheDocument();

  await act(async () => { answer(settings()); });
  const input = await main().findByLabelText("Input rate for vendor/m");
  expect(input).toHaveFocus();
  expect(main().getByRole("button", { name: "Save facts" })).toBeEnabled();
});

test("?edit=rates is cleared on Save and on Cancel", async () => {
  const first = open("/providers/saltmarch/models/vendor/m?edit=rates");
  fireEvent.change(await main().findByLabelText("Input rate for vendor/m"),
                   { target: { value: "0.001" } });
  fireEvent.change(main().getByLabelText("Output rate for vendor/m"), { target: { value: "0.002" } });
  fireEvent.click(main().getByRole("button", { name: "Save facts" }));

  expect(await main().findByRole("button", { name: "Edit" })).toBeInTheDocument();
  expect(api.putModelFacts).toHaveBeenCalledWith("saltmarch", { model: "vendor/m", rates: RATED });
  expect(screen.getByTestId("where")).toHaveTextContent("/providers/saltmarch/models/vendor/m");
  expect(screen.getByTestId("search")).toBeEmptyDOMElement();
  first.unmount();

  open("/providers/saltmarch/models/vendor/m?edit=rates");
  await main().findByLabelText("Input rate for vendor/m");
  fireEvent.click(main().getByRole("button", { name: "Cancel" }));

  expect(await main().findByRole("button", { name: "Edit" })).toBeInTheDocument();
  expect(screen.getByTestId("search")).toBeEmptyDOMElement();
  // Cleared, not merely hidden: the view stays the view.
  expect(main().queryByRole("spinbutton")).not.toBeInTheDocument();
});

// ---- 01i: the model's size ----
const SIZED = {
  context_window: 32768, max_output: null,
  limits: { window: { value: 32768, source: "user", catalog: 131072 },
            max_output: { value: 16000, source: "catalog", catalog: 16000 } },
};

test("a model's size shows in its sidebar with its sources", async () => {
  (api.readModelFacts as any).mockResolvedValue(facts(SIZED));
  open("/providers/saltmarch/models/vendor/m");
  expect(await main().findByRole("heading", { name: "vendor/m" })).toBeInTheDocument();
  const side = sidebarOf("vendor/m");
  expect(side.getByText("Window 32,768 tokens (you)")).toBeInTheDocument();
  // The listing disagrees with the user's word: its figure shows beside it.
  expect(side.getByText(/The catalog says 131,072/)).toBeInTheDocument();
  expect(side.getByText("Max output 16,000 tokens (catalog)")).toBeInTheDocument();
  expect(main().queryByRole("spinbutton")).not.toBeInTheDocument();
});

test("an unknown size says so", async () => {
  open("/providers/saltmarch/models/vendor/m");
  expect(await main().findByRole("heading", { name: "vendor/m" })).toBeInTheDocument();
  const side = sidebarOf("vendor/m");
  expect(side.getByText("Window unknown")).toBeInTheDocument();
  expect(side.getByText("Max output unknown")).toBeInTheDocument();
});

test("Edit shows the two size fields with their hint", async () => {
  (api.readModelFacts as any).mockResolvedValue(facts(SIZED));
  open("/providers/saltmarch/models/vendor/m");
  fireEvent.click(await main().findByRole("button", { name: "Edit" }));
  expect(await main().findByLabelText("Context window")).toHaveValue(32768);
  expect(main().getByLabelText("Max output")).toHaveValue(null);
  expect(main().getByText(/set the context budget instead/)).toBeInTheDocument();
});

test("clearing a stated window sends 0, and an untouched size sends nothing", async () => {
  (api.readModelFacts as any).mockResolvedValue(facts(SIZED));
  open("/providers/saltmarch/models/vendor/m");
  fireEvent.click(await main().findByRole("button", { name: "Edit" }));
  fireEvent.change(await main().findByLabelText("Context window"), { target: { value: "" } });
  fireEvent.click(main().getByRole("button", { name: "Save facts" }));
  expect(await main().findByRole("button", { name: "Edit" })).toBeInTheDocument();
  expect(api.putModelFacts).toHaveBeenLastCalledWith("saltmarch",
                                                     { model: "vendor/m", context_window: 0 });

  fireEvent.click(main().getByRole("button", { name: "Edit" }));
  fireEvent.change(await main().findByLabelText("Max output"), { target: { value: "4096" } });
  fireEvent.click(main().getByRole("button", { name: "Save facts" }));
  expect(await main().findByRole("button", { name: "Edit" })).toBeInTheDocument();
  expect(api.putModelFacts).toHaveBeenLastCalledWith("saltmarch",
                                                     { model: "vendor/m", max_output: 4096 });
});

test("a size that is not a whole number is not sent", async () => {
  open("/providers/saltmarch/models/vendor/m");
  fireEvent.click(await main().findByRole("button", { name: "Edit" }));
  fireEvent.change(await main().findByLabelText("Context window"), { target: { value: "1.5" } });
  expect(main().getByText("Sizes are whole numbers of tokens.")).toBeInTheDocument();
  expect(main().getByRole("button", { name: "Save facts" })).toBeDisabled();
});

test("?edit=limits opens the form once, with the caret in the window box", async () => {
  (api.readModelFacts as any).mockResolvedValue(facts(SIZED));
  open("/providers/saltmarch/models/vendor/m?edit=limits");
  const box = await main().findByLabelText("Context window");
  expect(box).toHaveFocus();
  fireEvent.click(main().getByRole("button", { name: "Cancel" }));
  expect(await main().findByRole("button", { name: "Edit" })).toBeInTheDocument();
  expect(screen.getByTestId("search")).toBeEmptyDOMElement();
  expect(main().queryByRole("spinbutton")).not.toBeInTheDocument();
});

test("a model id with a slash opens its facts", async () => {
  open("/providers/saltmarch/models/vendor/m");
  expect(await main().findByRole("heading", { name: "vendor/m" })).toBeInTheDocument();
  expect(api.readModelFacts).toHaveBeenCalledWith("saltmarch", "vendor/m");
});

test("a newer store disables editing", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({
    newer: true, migration: { state: "newer", reason: "", skipped: [] } }));
  open("/providers/saltmarch");

  expect(await screen.findByText(/upgraded by a newer Grimoire/)).toBeInTheDocument();
  expect(main().getByRole("button", { name: "Edit" })).toBeDisabled();
  expect(column().getByRole("button", { name: "+ New provider" })).toBeDisabled();
});

/** Open saltmarch's edit form, as a record stating `billing` (or none). */
async function editSaltmarch(billing: string) {
  (api.readConnection as any).mockImplementation((id: string) =>
    Promise.resolve({ ...(DETAILS[id] as object), ...(id === "saltmarch" ? { billing } : {}) }));
  open("/providers/saltmarch");
  fireEvent.click(await main().findByRole("button", { name: "Edit" }));
  return await main().findByRole("textbox", { name: "Name" });
}

async function renameAndSave(name: HTMLElement) {
  fireEvent.change(name, { target: { value: "Saltmarch Main" } });
  fireEvent.click(main().getByRole("button", { name: "Save provider" }));
  expect(await main().findByRole("button", { name: "Edit" })).toBeInTheDocument();
  return (api.updateConnection as any).mock.calls[0][1];
}

test("a rename of a provider that states no billing sends no billing", async () => {
  const name = await editSaltmarch("");
  // The form starts at what the record says -- nothing -- not the preset's.
  expect(main().getByRole("combobox", { name: "Billing" })).toHaveValue("");
  const patch = await renameAndSave(name);
  expect(patch).toMatchObject({ name: "Saltmarch Main" });
  expect(patch).not.toHaveProperty("billing");
});

test("a rename when the presets read failed sends no billing", async () => {
  (api.listProviderPresets as any).mockRejectedValue(new Error("offline"));
  const name = await editSaltmarch("");
  // With no preset to ask, the form does not guess "metered" either.
  expect(main().getByRole("combobox", { name: "Billing" })).toHaveValue("");
  const patch = await renameAndSave(name);
  expect(patch).not.toHaveProperty("billing");
});

test("choosing a billing sends it", async () => {
  await editSaltmarch("");
  fireEvent.change(main().getByRole("combobox", { name: "Billing" }),
                   { target: { value: "subscription" } });
  fireEvent.click(main().getByRole("button", { name: "Save provider" }));
  expect(await main().findByRole("button", { name: "Edit" })).toBeInTheDocument();
  expect((api.updateConnection as any).mock.calls[0][1])
    .toMatchObject({ billing: "subscription" });
});

test("a saved edit checks the provider again", async () => {
  // Every read is a fresh record, as it is off the wire.
  (api.readConnection as any).mockImplementation(
    (id: string) => Promise.resolve({ ...(DETAILS[id] as object) }));
  open("/providers/saltmarch");
  fireEvent.click(await main().findByRole("button", { name: "Edit" }));
  expect(api.checkConnection).toHaveBeenCalledTimes(1);

  fireEvent.change(main().getByLabelText("API key"), { target: { value: "sk-new" } });
  fireEvent.click(main().getByRole("button", { name: "Save provider" }));
  expect(await main().findByRole("button", { name: "Edit" })).toBeInTheDocument();
  // A new key is exactly what a reader wants checked.
  expect(api.checkConnection).toHaveBeenCalledTimes(2);
  expect(api.checkConnection).toHaveBeenLastCalledWith("saltmarch");
});

test("a create whose list re-read fails opens the provider it made", async () => {
  (api.createConnection as any).mockResolvedValue({ id: "saltmarch-local" });
  const local = provider({ id: "saltmarch-local", name: "Saltmarch Local",
                           kind: "openai_compatible", preset: "ollama",
                           base_url: "http://localhost:11434/v1", key_set: false });
  DETAILS["saltmarch-local"] = { ...local, models: [], fetched_at: "", used_by: [] };
  open();
  fireEvent.click(await column().findByRole("button", { name: "+ New provider" }));
  fireEvent.click(within(await main().findByRole("group", { name: "Provider presets" }))
    .getByRole("button", { name: "Ollama" }));
  await main().findByRole("textbox", { name: "Name" });
  (api.listConnections as any).mockRejectedValue(new Error("the list is unreadable"));
  fireEvent.click(main().getByRole("button", { name: "Create provider" }));

  // The provider exists, so the page shows it -- not a Create that would make
  // a second one.
  expect(await main().findByRole("heading", { name: "Saltmarch Local" })).toBeInTheDocument();
  expect(screen.getByTestId("where")).toHaveTextContent("/providers/saltmarch-local");
  expect(main().queryByRole("button", { name: "Create provider" })).not.toBeInTheDocument();
  expect(api.createConnection).toHaveBeenCalledTimes(1);
  expect(main().getByText(/the list is unreadable/)).toBeInTheDocument();
});

test("a blocked store holds every write on the page", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({
    newer: true, migration: { state: "newer", reason: "", skipped: [] } }));
  open("/providers/saltmarch");

  expect(await screen.findByText(/upgraded by a newer Grimoire/)).toBeInTheDocument();
  expect(main().getByRole("button", { name: "Refresh" })).toBeDisabled();
  expect(main().getByText("output rules for saltmarch (read-only)")).toBeInTheDocument();

  fireEvent.click(main().getByRole("link", { name: "vendor/m" }));
  expect(await main().findByRole("heading", { name: "vendor/m" })).toBeInTheDocument();
  expect(main().getByRole("button", { name: "Edit" })).toBeDisabled();
  expect(main().getByRole("button", { name: "Test…" })).toBeDisabled();
});

test.each([["pending", ""], ["failed", "the safety backup failed: disk full"]])(
  "a store not yet switched (%s) edits its providers, not their model facts",
  async (state, reason) => {
  // The server takes provider writes at format 1 (it refuses only a newer
  // store): a key revoked while the upgrade keeps failing is fixed here, or
  // nowhere. A model's facts are the new layout's own and wait for it.
  (api.getInferenceSettings as any).mockResolvedValue(settings({
    format: "1", migration: { state, reason, skipped: [] } }));
  open("/providers/saltmarch");

  expect(await main().findByRole("heading", { name: "Saltmarch Router" })).toBeInTheDocument();
  expect(main().getByRole("button", { name: "Edit" })).toBeEnabled();
  expect(column().getByRole("button", { name: "+ New provider" })).toBeEnabled();
  expect(main().getByRole("button", { name: "Refresh" })).toBeEnabled();
  expect(main().getByText("output rules for saltmarch")).toBeInTheDocument();

  fireEvent.click(main().getByRole("link", { name: "vendor/m" }));
  expect(await main().findByRole("heading", { name: "vendor/m" })).toBeInTheDocument();
  expect(main().getByRole("button", { name: "Edit" })).toBeDisabled();
  expect(main().getByRole("button", { name: "Test…" })).toBeEnabled();
});

test("a pending migration over a switched store leaves editing on", async () => {
  // The global switch landed (format 2); a campaign still unmarked keeps the
  // status pending, but the server takes global writes.
  (api.getInferenceSettings as any).mockResolvedValue(settings({
    format: "2", migration: { state: "pending", reason: "", skipped: [] } }));
  open("/providers/saltmarch");

  expect(await main().findByRole("heading", { name: "Saltmarch Router" })).toBeInTheDocument();
  expect(main().getByRole("button", { name: "Edit" })).toBeEnabled();
  expect(column().getByRole("button", { name: "+ New provider" })).toBeEnabled();
  expect(main().getByRole("button", { name: "Refresh" })).toBeEnabled();
  expect(main().getByText("output rules for saltmarch")).toBeInTheDocument();

  fireEvent.click(main().getByRole("link", { name: "vendor/m" }));
  expect(await main().findByRole("heading", { name: "vendor/m" })).toBeInTheDocument();
  expect(main().getByRole("button", { name: "Edit" })).toBeEnabled();
  expect(main().getByRole("button", { name: "Test…" })).toBeEnabled();
});

test("a pending migration over a switched store says so quietly, never as a lock", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({
    format: "2", migration: { state: "pending", reason: "", skipped: [] } }));
  open("/providers/saltmarch");

  expect(await main().findByRole("heading", { name: "Saltmarch Router" })).toBeInTheDocument();
  // The words every other model-settings surface uses for this status.
  expect(screen.queryByText(/Upgrade pending/)).not.toBeInTheDocument();
  expect(screen.getByText("Part of this library has not finished upgrading yet."))
    .toBeInTheDocument();
});

/** The upgrade poll's clock, faked so a test that waits on it is instant;
 *  `shouldAdvanceTime` keeps the settle wrapper's own ticks running. */
async function withFakeTimers(body: () => Promise<void>) {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    await body();
  } finally {
    vi.useRealTimers();
  }
}

test("the page unlocks once the upgrade lands, without leaving it", async () => {
  await withFakeTimers(async () => {
    // A model's facts are what wait for the new layout (the provider record
    // does not: the server takes it at format 1).
    (api.getInferenceSettings as any).mockResolvedValue(settings({
      format: "1", migration: { state: "running", reason: "", skipped: [] } }));
    open("/providers/saltmarch/models/vendor/m");
    expect(await main().findByRole("heading", { name: "vendor/m" })).toBeInTheDocument();
    expect(main().getByRole("button", { name: "Edit" })).toBeDisabled();
    expect(screen.getByText(/Upgrade pending/)).toBeInTheDocument();

    (api.getInferenceSettings as any).mockResolvedValue(settings());
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });

    expect(await main().findByRole("button", { name: "Edit" })).toBeEnabled();
    expect(screen.queryByText(/Upgrade pending/)).not.toBeInTheDocument();
  });
});

test("a settled store is not asked again", async () => {
  await withFakeTimers(async () => {
    open("/providers/saltmarch");
    expect(await main().findByRole("heading", { name: "Saltmarch Router" })).toBeInTheDocument();
    const reads = (api.getInferenceSettings as any).mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect((api.getInferenceSettings as any).mock.calls.length).toBe(reads);
  });
});

test("a failed presets read says so on + New provider, and can be tried again", async () => {
  (api.listProviderPresets as any).mockRejectedValueOnce(new Error("the presets are unreadable"));
  open();
  fireEvent.click(await column().findByRole("button", { name: "+ New provider" }));
  expect(await main().findByText(/the presets are unreadable/)).toBeInTheDocument();
  expect(main().queryByRole("group", { name: "Provider presets" })).not.toBeInTheDocument();

  fireEvent.click(main().getByRole("button", { name: "Try again" }));
  const choose = await main().findByRole("group", { name: "Provider presets" });
  expect(within(choose).getByRole("button", { name: "Ollama" })).toBeInTheDocument();
  expect(main().queryByText(/the presets are unreadable/)).not.toBeInTheDocument();
});

test("a saved edit reads the catalog again, for the endpoint it now names", async () => {
  open("/providers/saltmarch");
  fireEvent.click(await main().findByRole("button", { name: "Edit" }));
  const before = (api.readConnectionCapabilities as any).mock.calls.length;
  fireEvent.change(main().getByLabelText("API key"), { target: { value: "sk-new" } });
  fireEvent.click(main().getByRole("button", { name: "Save provider" }));
  expect(await main().findByRole("button", { name: "Edit" })).toBeInTheDocument();
  expect((api.readConnectionCapabilities as any).mock.calls.length).toBeGreaterThan(before);
});

// ---- an edit that moves the Embedding role's vector space asks first ----
/** The library's Embedding role, resolving to `providerId`. */
function embeddingOn(providerId: string) {
  return settings({ roles: { embedding: {
    stored: { provider: providerId, model: "vendor/embed" }, on: true, problem: null,
    resolves: { provider: providerId, provider_name: "Saltmarch Router", model: "vendor/embed",
                preset: "", preset_name: "", via: "role", scope: "global" } } } });
}

test("a new key for the Embedding role's provider asks before re-embedding", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(embeddingOn("saltmarch"));
  open("/providers/saltmarch");
  fireEvent.click(await main().findByRole("button", { name: "Edit" }));
  fireEvent.change(main().getByLabelText("API key"), { target: { value: "sk-new" } });
  fireEvent.click(main().getByRole("button", { name: "Save provider" }));

  const ask = await main().findByRole("group", { name: "Confirm the re-embedding" });
  expect(ask).toHaveTextContent(
    "Changing this provider's key or address re-embeds your library through "
    + "Saltmarch Router, which may cost money.");
  expect(api.updateConnection).not.toHaveBeenCalled();

  fireEvent.click(within(ask).getByRole("button", { name: "Re-embed and save" }));
  await waitFor(() => expect(api.updateConnection).toHaveBeenCalledWith(
    "saltmarch", expect.objectContaining({ api_key: "sk-new", confirm_embedding: true })));
  expect(await main().findByRole("button", { name: "Edit" })).toBeInTheDocument();
});

test("Not now sends nothing and leaves the form as it was", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(embeddingOn("saltmarch"));
  open("/providers/saltmarch");
  fireEvent.click(await main().findByRole("button", { name: "Edit" }));
  fireEvent.change(main().getByLabelText("API key"), { target: { value: "sk-new" } });
  fireEvent.click(main().getByRole("button", { name: "Save provider" }));
  const ask = await main().findByRole("group", { name: "Confirm the re-embedding" });
  fireEvent.click(within(ask).getByRole("button", { name: "Not now" }));

  expect(main().queryByRole("group", { name: "Confirm the re-embedding" })).not.toBeInTheDocument();
  expect(main().getByLabelText("API key")).toHaveValue("sk-new");
  expect(api.updateConnection).not.toHaveBeenCalled();
});

test("a rename of the Embedding role's provider asks nothing", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(embeddingOn("saltmarch"));
  const name = await editSaltmarch("");
  const patch = await renameAndSave(name);
  expect(patch).not.toHaveProperty("confirm_embedding");
});

test("a new key for a provider the Embedding role does not use asks nothing", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(embeddingOn("realm-claude"));
  open("/providers/saltmarch");
  fireEvent.click(await main().findByRole("button", { name: "Edit" }));
  fireEvent.change(main().getByLabelText("API key"), { target: { value: "sk-new" } });
  fireEvent.click(main().getByRole("button", { name: "Save provider" }));
  expect(await main().findByRole("button", { name: "Edit" })).toBeInTheDocument();
  expect((api.updateConnection as any).mock.calls[0][1]).not.toHaveProperty("confirm_embedding");
});

test("an unreadable facts file is said, and Save stays off", async () => {
  // Nothing stated is what the server could read, not what the user said:
  // a save would replace their word, so the panel never offers one.
  (api.readModelFacts as any).mockResolvedValue(facts({ unreadable: true }));
  open("/providers/saltmarch");
  fireEvent.click(await main().findByRole("link", { name: "vendor/m" }));

  expect(await main().findByText(/facts file could not be read/)).toBeInTheDocument();
  fireEvent.click(main().getByRole("button", { name: "Edit" }));
  expect(await main().findByText(/facts file could not be read/)).toBeInTheDocument();
  expect(main().getByRole("button", { name: "Save facts" })).toBeDisabled();
  expect(api.putModelFacts).not.toHaveBeenCalled();
});

test("a mangled facts file says it needs fixing by hand, and Save stays off", async () => {
  // Not the transient case: waiting for a sync will not help, and a save
  // would replace every other model's facts with this one entry.
  (api.readModelFacts as any).mockResolvedValue(
    facts({ unreadable: true, unreadable_reason: "mangled" }));
  open("/providers/saltmarch");
  fireEvent.click(await main().findByRole("link", { name: "vendor/m" }));

  expect(await main().findByText(/not valid JSON/)).toBeInTheDocument();
  expect(main().queryByText(/until it has synced/)).not.toBeInTheDocument();
  fireEvent.click(main().getByRole("button", { name: "Edit" }));
  expect(await main().findByText(/fixed or removed/)).toBeInTheDocument();
  expect(main().getByRole("button", { name: "Save facts" })).toBeDisabled();
  expect(api.putModelFacts).not.toHaveBeenCalled();
});

test("a facts save that turns embedding on is asked, then resent with the yes", async () => {
  (api.putModelFacts as any)
    .mockRejectedValueOnce(new ApiError(400,
      "This turns embedding on through Saltmarch Router: your library will be embedded.",
      "confirm_embedding"))
    .mockImplementation(
      (_id: string, body: Record<string, unknown>) => Promise.resolve(facts(body)));
  open("/providers/saltmarch");
  fireEvent.click(await main().findByRole("link", { name: "vendor/m" }));
  fireEvent.click(await main().findByRole("button", { name: "Edit" }));
  fireEvent.change(await main().findByRole("combobox", { name: "Embed: override" }),
                   { target: { value: "yes" } });
  fireEvent.click(main().getByRole("button", { name: "Save facts" }));

  const ask = await main().findByRole("group", { name: "Confirm the embedding" });
  expect(ask).toHaveTextContent(/library will be embedded/);
  expect(api.putModelFacts).toHaveBeenCalledTimes(1);
  fireEvent.click(within(ask).getByRole("button", { name: "Embed and save" }));
  await waitFor(() => expect(api.putModelFacts).toHaveBeenLastCalledWith("saltmarch", {
    model: "vendor/m", overrides: { embed: "yes" }, confirm_embedding: true }));
  expect(await main().findByRole("button", { name: "Edit" })).toBeInTheDocument();
});

test("the server's own confirm_embedding refusal is asked, then resent with the yes", async () => {
  // The page's view says nothing embeds through it; another tab moved that.
  (api.updateConnection as any)
    .mockRejectedValueOnce(new ApiError(400,
      "This edit changes what the Embedding role embeds with, which re-embeds the library.",
      "confirm_embedding"))
    .mockImplementation((id: string) => Promise.resolve(DETAILS[id]));
  open("/providers/saltmarch");
  fireEvent.click(await main().findByRole("button", { name: "Edit" }));
  fireEvent.change(main().getByLabelText("API key"), { target: { value: "sk-new" } });
  fireEvent.click(main().getByRole("button", { name: "Save provider" }));

  const ask = await main().findByRole("group", { name: "Confirm the re-embedding" });
  expect(ask).toHaveTextContent(/re-embeds the library/);
  fireEvent.click(within(ask).getByRole("button", { name: "Re-embed and save" }));
  await waitFor(() => expect(api.updateConnection).toHaveBeenLastCalledWith(
    "saltmarch", expect.objectContaining({ api_key: "sk-new", confirm_embedding: true })));
  expect(await main().findByRole("button", { name: "Edit" })).toBeInTheDocument();
});

test("+ New provider is an address of its own, and a direct load shows the form", async () => {
  open("/providers/new");
  expect(await main().findByRole("group", { name: "Provider presets" })).toBeInTheDocument();
  expect(column().getByRole("button", { name: "+ New provider" })).toBeDisabled();
});

test("+ New provider goes to /providers/new", async () => {
  open("/providers/saltmarch");
  fireEvent.click(await column().findByRole("button", { name: "+ New provider" }));
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/^\/providers\/new$/));
});

test("Cancel returns to the provider the form was opened from", async () => {
  open("/providers/saltmarch");
  fireEvent.click(await column().findByRole("button", { name: "+ New provider" }));
  fireEvent.click(await main().findByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/^\/providers\/saltmarch$/));
});

test("Cancel from a direct load lands on the provider list", async () => {
  open("/providers/new");
  fireEvent.click(await main().findByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/^\/providers$/));
});

test("Cancel returns to another page that opened it, such as Models", async () => {
  render(
    <MemoryRouter initialEntries={[{ pathname: "/providers/new", state: { returnTo: "/models" } }]}>
      <Where />
      <Routes>
        <Route path="/providers/new" element={<ProvidersView />} />
        <Route path="/models" element={<div>the models page</div>} />
      </Routes>
    </MemoryRouter>);
  fireEvent.click(await main().findByRole("button", { name: "Cancel" }));
  expect(await screen.findByText("the models page")).toBeInTheDocument();
});

test("only an internal returnTo is honoured, and never the form itself", () => {
  expect(validReturn({ returnTo: "/models#rates" })).toBe("/models#rates");
  expect(validReturn({ returnTo: "//evil.example" })).toBeNull();
  expect(validReturn({ returnTo: "https://evil.example" })).toBeNull();
  expect(validReturn({ returnTo: "/providers/new" })).toBeNull();
  expect(validReturn({ returnTo: "/providers/new?x=1" })).toBeNull();
  expect(validReturn(null)).toBeNull();
  expect(validReturn({ returnTo: 7 })).toBeNull();
});

test("the column opens with the Inference group, Providers current", async () => {
  open("/providers");
  const nav = within(await screen.findByRole("complementary", { name: "Providers" }));
  expect(nav.getByRole("link", { name: "Providers" })).toHaveAttribute("aria-current", "page");
  expect(nav.getByRole("link", { name: "Models" })).toHaveAttribute("href", "/models");
});

test("a refreshed /providers/new still returns to where it came from, search and hash kept", async () => {
  // A reload keeps the history entry, state included: the same entry, mounted again.
  const entry = { pathname: "/providers/new", state: { returnTo: "/models?add=x#rates" } };
  const page = () => (
    <MemoryRouter initialEntries={[entry]}>
      <Where />
      <Routes>
        <Route path="/providers/new" element={<ProvidersView />} />
        <Route path="/models" element={<div>the models page</div>} />
      </Routes>
    </MemoryRouter>);
  const first = render(page());
  await main().findByRole("button", { name: "Cancel" });
  first.unmount();
  render(page());
  fireEvent.click(await main().findByRole("button", { name: "Cancel" }));
  expect(await screen.findByText("the models page")).toBeInTheDocument();
  expect(screen.getByTestId("where")).toHaveTextContent(/^\/models#rates$/);
  expect(screen.getByTestId("search")).toHaveTextContent(/^\?add=x$/);
});

// ---- embedding options (01h-S4) ----

const EMBEDS = { ...CAPS, embed: { value: "unknown", source: "unknown" } };
const NOMIC_MODEL = "vendor/nomic-embed-text-v1.5";

test("the embedding options are only for a model that may embed", async () => {
  open("/providers/saltmarch/models/vendor/m");
  fireEvent.click(await main().findByRole("button", { name: "Edit" }));
  await main().findByRole("button", { name: "Save facts" });
  expect(main().queryByRole("heading", { name: "Embedding options" })).toBeNull();
  expect(main().queryByRole("combobox", { name: "Input type" })).toBeNull();
});

test("Suggest fills the options and saves nothing; Save asks, and the yes is resent", async () => {
  (api.readModelFacts as any).mockResolvedValue(facts({ model: NOMIC_MODEL, capabilities: EMBEDS }));
  (api.putModelFacts as any).mockImplementation((_id: string, body: Record<string, unknown>) =>
    Promise.resolve(facts({ ...body, capabilities: EMBEDS })));
  (api.putModelFacts as any).mockRejectedValueOnce(new ApiError(
    400, "This moves the Embedding role to a new vector space.", "confirm_embedding"));
  open(`/providers/saltmarch/models/${NOMIC_MODEL}`);
  await main().findByRole("heading", { name: NOMIC_MODEL });
  expect(await sidebarOf(NOMIC_MODEL).findByText("None — texts are sent as they are."))
    .toBeInTheDocument();
  fireEvent.click(main().getByRole("button", { name: "Edit" }));
  fireEvent.click(await main().findByRole("button", { name: "Suggest" }));

  expect(main().getByLabelText("Input type")).toHaveValue("prefix");
  expect(main().getByLabelText("Query prefix")).toHaveValue("search_query: ");
  expect(main().getByLabelText("Document prefix")).toHaveValue("search_document: ");
  expect(api.putModelFacts).not.toHaveBeenCalled();

  fireEvent.change(main().getByLabelText("Dimensions"), { target: { value: "512" } });
  fireEvent.click(main().getByRole("button", { name: "Save facts" }));
  const ask = await main().findByRole("group", { name: "Confirm the embedding" });
  const block = { input: "prefix", query_prefix: "search_query: ",
                  document_prefix: "search_document: ", dimensions: 512 };
  expect(api.putModelFacts).toHaveBeenLastCalledWith("saltmarch",
    { model: NOMIC_MODEL, embedding: block });
  fireEvent.click(within(ask).getByRole("button", { name: "Embed and save" }));

  expect(await main().findByRole("button", { name: "Edit" })).toBeInTheDocument();
  expect(api.putModelFacts).toHaveBeenLastCalledWith("saltmarch",
    { model: NOMIC_MODEL, embedding: block, confirm_embedding: true });
  expect(sidebarOf(NOMIC_MODEL).getByText("query/document prefixes, 512 dimensions"))
    .toBeInTheDocument();
});

test("a save that leaves the options alone does not send them", async () => {
  (api.readModelFacts as any).mockResolvedValue(facts({
    capabilities: EMBEDS, embedding: { input: "param", param_field: "task",
                                       query_value: "q", document_value: "d" } }));
  open("/providers/saltmarch/models/vendor/m");
  await main().findByRole("heading", { name: "vendor/m" });
  expect(await sidebarOf("vendor/m").findByText("query/document in `task`, two requests per recall"))
    .toBeInTheDocument();
  fireEvent.click(main().getByRole("button", { name: "Edit" }));
  fireEvent.click(await main().findByRole("checkbox", { name: /Continue replies by prefill/ }));
  expect(main().getByRole("button", { name: "Suggest" })).toBeDisabled();
  fireEvent.click(main().getByRole("button", { name: "Save facts" }));
  expect(await main().findByRole("button", { name: "Edit" })).toBeInTheDocument();
  expect(api.putModelFacts).toHaveBeenCalledWith("saltmarch", { model: "vendor/m", prefill: true });
});

test("an invalid block and a width the endpoint ignored are both said", async () => {
  (api.readModelFacts as any).mockResolvedValue(facts({
    capabilities: EMBEDS, embedding: { input: "param" }, embedding_invalid: true,
    embedding_invalid_reason: "the request-field input type needs a field name",
    verified: { embed: { ok: false, error: "ignored", requested_dims: 512,
                         returned_dims: 1536 } } }));
  open("/providers/saltmarch/models/vendor/m");
  await main().findByRole("heading", { name: "vendor/m" });
  expect(await sidebarOf("vendor/m").findByText(/Invalid — embedding is off with this model/))
    .toBeInTheDocument();
  expect(main().getByText(
    "embed: failed — requested 512, test returned 1536: this endpoint ignores `dimensions`"))
    .toBeInTheDocument();
});
