import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { api, PRESET_CLEAR, type CapabilityNeed } from "../api/client";
import { forgetModelTests } from "../components/inference/TestCallDialog";
import ModelsView from "./ModelsView";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, api: {
    getInferenceSettings: vi.fn(), putInferenceSettings: vi.fn(),
    readConnectionCapabilities: vi.fn(), previewControls: vi.fn(),
    previewModelTest: vi.fn(), runModelTest: vi.fn(),
  } };
});

const PRESET = {
  id: "openrouter", label: "OpenRouter", kind: "openrouter", base_url: "", url_locked: true,
  billing: "metered", reports_price: true, always: [], possible: [], never: [],
};

type Value = "yes" | "no" | "unknown";
const NAMES: Record<string, string> = {
  "vendor/m": "Vendor M", "vendor/embed": "Vendor Embed", "vendor/eye": "Vendor Eye",
};
/** What each model on the provider can do. A test changes a value to make a
 *  warning's case; `beforeEach` puts it back. */
let CAPS: Record<string, Record<string, Value>>;
const BASE_CAPS = (): Record<string, Record<string, Value>> => ({
  "vendor/m": { generate: "yes", vision: "yes", embed: "no", decide_native: "yes" },
  "vendor/embed": { generate: "no", vision: "no", embed: "yes", decide_native: "no" },
  "vendor/eye": { generate: "yes", vision: "unknown", embed: "no", decide_native: "no" },
});

/** The capabilities API, as the server groups it: yes fits, unknown is
 *  unverified, no is hidden. `decide` is a native decision or generation. */
function answer(need: CapabilityNeed, model?: string) {
  const out = {
    provider_preset: PRESET, need, reason: null as string | null,
    groups: { fits: [] as unknown[], unverified: [] as unknown[] },
    hidden: [] as { id: string; reason: string }[],
  };
  for (const id of model ? [model] : Object.keys(CAPS)) {
    const caps = CAPS[id] ?? {};
    const get = (c: string): Value => caps[c] ?? "unknown";
    const value: Value = need === "decide"
      ? (get("decide_native") === "yes" ? "yes" : get("generate"))
      : get(need);
    const row = {
      id, name: NAMES[id] ?? id, context: null, prompt: null, completion: null,
      reason: value === "yes" ? "the catalog says so" : "not known yet",
      capabilities: Object.fromEntries(Object.entries(caps)
        .map(([k, v]) => [k, { value: v, source: "catalog" }])),
    };
    if (value === "yes") out.groups.fits.push(row);
    else if (value === "unknown") out.groups.unverified.push(row);
    else out.hidden.push({ id, reason: `${id} cannot ${need}` });
  }
  return out;
}

const sel = (provider = "", model = "", preset = "") => ({ provider, model, preset });
const resolved = (over: Record<string, unknown> = {}) => ({
  provider: "saltmarch", provider_name: "Saltmarch Router", model: "vendor/m",
  preset: "", preset_name: "", via: "role", scope: "global", ...over,
});
const card = (over: Record<string, unknown> = {}) => ({
  stored: sel(), fallback: sel(), resolves: resolved(), inherits: resolved(), problem: null,
  fallback_missing: [], fallback_problem: null, decision_mode: "", decides_natively: false,
  ...over,
});
const route = (over: Record<string, unknown>) => ({
  hint: "", tasks: [], operation: "generate", default_role: "fast", requires: [],
  campaign_scoped: true, use: "", pin: sel(), preset: "", resolves: resolved(),
  inherits: resolved(), problem: null, fallback_missing: [], fallback_problem: null,
  decision_mode: "", decides_natively: false, role: "fast", uses: "fast", ...over,
});

const ROUTES = [
  route({ key: "scene", label: "Scene prose", default_role: "primary", role: "primary" }),
  route({ key: "summary", label: "Rolling summary", hint: "Keeps the running summary." }),
  route({ key: "image", label: "Image descriptions", requires: ["vision"] }),
];

function settings(over: Record<string, unknown> = {}) {
  return {
    format: "2", newer: false, migration: { state: "done", reason: "", skipped: [] },
    roles: {
      primary: card({
        stored: sel("saltmarch", "vendor/m", "balanced"),
        resolves: resolved({ preset: "balanced", preset_name: "Balanced" }), inherits: null }),
      fast: card(),
      decision: card(),
      embedding: { stored: { provider: "", model: "" }, resolves: null, on: false },
    },
    routes: ROUTES,
    providers: [
      { id: "saltmarch", name: "Saltmarch Router", kind: "openrouter", preset: "openrouter",
        usable: true },
      { id: "realm", name: "Realm Local", kind: "openai_compatible", preset: "custom",
        usable: true },
    ],
    presets: [{ id: "balanced", name: "Balanced" }, { id: "tight", name: "Tight" }],
    preset_clear: PRESET_CLEAR,
    ...over,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  forgetModelTests();
  CAPS = BASE_CAPS();
  (api.getInferenceSettings as any).mockResolvedValue(settings());
  (api.putInferenceSettings as any).mockResolvedValue(settings());
  (api.readConnectionCapabilities as any).mockImplementation(
    (_id: string, need: CapabilityNeed, model?: string) => Promise.resolve(answer(need, model)));
  (api.previewControls as any).mockResolvedValue({ requested: {}, effective: {}, controls: {} });
});

function Where() {
  const { pathname } = useLocation();
  return <div data-testid="where">{pathname}</div>;
}

function open(at = "/models") {
  return render(
    <MemoryRouter initialEntries={[at]}>
      <Where />
      <Routes>
        <Route path="/models" element={<ModelsView />} />
        <Route path="/models/role/:role" element={<ModelsView />} />
        <Route path="/models/route/:key" element={<ModelsView />} />
        <Route path="/providers/*" element={<div>the providers page</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

const column = () => within(screen.getByRole("complementary", { name: "Models" }));
const main = () => within(screen.getByRole("main"));
const roleCard = (name: string) => within(main().getByRole("region", { name }));
/** The overview, once its cards are drawn. */
async function openCards() {
  open();
  await main().findByRole("region", { name: "Primary" });
}
const putBody = (n = 0) => (api.putInferenceSettings as any).mock.calls[n];

test("clicking a role shows the read-only view", async () => {
  open();
  fireEvent.click(await column().findByRole("link", { name: "Primary" }));

  expect(await main().findByRole("heading", { name: "Primary" })).toBeInTheDocument();
  expect(screen.getByTestId("where")).toHaveTextContent("/models/role/primary");
  expect(main().getByText("Saltmarch Router ▸ vendor/m ▸ Balanced")).toBeInTheDocument();
  // Read-only: nothing on the page is a field.
  expect(main().queryByRole("combobox")).not.toBeInTheDocument();
  expect(main().queryByRole("textbox")).not.toBeInTheDocument();
  const sidebar = within(main().getByRole("complementary", { name: "Primary" }));
  expect(sidebar.getByRole("button", { name: "Edit" })).toBeEnabled();
  expect(sidebar.getByText("Balanced")).toBeInTheDocument();
  // The provider is another record: its chip opens it.
  fireEvent.click(sidebar.getByRole("button", { name: "Saltmarch Router" }));
  expect(await screen.findByText("the providers page")).toBeInTheDocument();
  expect(screen.getByTestId("where")).toHaveTextContent("/providers/saltmarch");
});

test("with nothing selected, the four roles are read-only cards", async () => {
  open();
  expect(await main().findByRole("region", { name: "Primary" })).toBeInTheDocument();
  for (const name of ["Fast", "Decision", "Embedding"]) {
    expect(main().getByRole("region", { name })).toBeInTheDocument();
  }
  expect(roleCard("Primary").getByText("Saltmarch Router ▸ vendor/m ▸ Balanced"))
    .toBeInTheDocument();
  expect(roleCard("Primary").getByText("Every control is sent as written.")).toBeInTheDocument();
  expect(api.previewControls).toHaveBeenCalledWith(
    { preset_id: "balanced", provider: "saltmarch", model: "vendor/m" });
  expect(roleCard("Embedding").getByText(/Off/)).toBeInTheDocument();
  expect(main().queryByRole("combobox")).not.toBeInTheDocument();
  // No warning where every model can do what its role asks.
  expect(main().queryByRole("note")).not.toBeInTheDocument();
  // The set is fixed: nothing to add.
  expect(column().queryByRole("button", { name: /New/ })).not.toBeInTheDocument();
});

test("Edit reveals the role form", async () => {
  open("/models/role/primary");
  fireEvent.click(await main().findByRole("button", { name: "Edit" }));

  const role = within(await main().findByRole("group", { name: "Primary" }));
  expect(role.getByRole("combobox", { name: "Provider" })).toHaveValue("saltmarch");
  expect(await role.findByRole("radio", { name: "Vendor M" })).toBeChecked();
  expect(role.getByRole("combobox", { name: "Preset" })).toHaveValue("balanced");
  expect(main().getByRole("button", { name: "Fallback" })).toBeInTheDocument();
});

test("Save returns to view; Cancel discards", async () => {
  open("/models/role/primary");
  fireEvent.click(await main().findByRole("button", { name: "Edit" }));
  fireEvent.change(await main().findByRole("combobox", { name: "Preset" }),
                   { target: { value: "tight" } });
  fireEvent.click(main().getByRole("button", { name: "Cancel" }));

  expect(await main().findByRole("button", { name: "Edit" })).toBeInTheDocument();
  expect(api.putInferenceSettings).not.toHaveBeenCalled();
  fireEvent.click(main().getByRole("button", { name: "Edit" }));
  expect(await main().findByRole("combobox", { name: "Preset" })).toHaveValue("balanced");

  fireEvent.change(main().getByRole("combobox", { name: "Preset" }),
                   { target: { value: "tight" } });
  fireEvent.click(main().getByRole("button", { name: "Save" }));
  expect(await main().findByRole("button", { name: "Edit" })).toBeInTheDocument();
  expect(main().queryByRole("combobox")).not.toBeInTheDocument();
  expect(api.putInferenceSettings).toHaveBeenCalledTimes(1);
});

test("a role form picks provider, then a fitting model, then a preset", async () => {
  open("/models/role/fast");
  fireEvent.click(await main().findByRole("button", { name: "Edit" }));
  const role = within(await main().findByRole("group", { name: "Fast" }));
  fireEvent.change(role.getByRole("combobox", { name: "Provider" }),
                   { target: { value: "saltmarch" } });

  const fits = within(await role.findByRole("group", { name: "Fits" }));
  // A model that cannot generate is not offered for a generating role.
  expect(role.queryByRole("radio", { name: "Vendor Embed" })).not.toBeInTheDocument();
  fireEvent.click(fits.getByRole("radio", { name: "Vendor M" }));
  fireEvent.change(role.getByRole("combobox", { name: "Preset" }),
                   { target: { value: "tight" } });
  fireEvent.click(main().getByRole("button", { name: "Save" }));

  expect(await main().findByRole("button", { name: "Edit" })).toBeInTheDocument();
  expect(api.readConnectionCapabilities).toHaveBeenCalledWith("saltmarch", "generate");
  expect(putBody()).toEqual([
    { roles: { fast: { selection: { provider: "saltmarch", model: "vendor/m", preset: "tight" } } } },
  ]);
});

test("saving sends only that role", async () => {
  open("/models/role/primary");
  fireEvent.click(await main().findByRole("button", { name: "Edit" }));
  fireEvent.change(await main().findByRole("combobox", { name: "Preset" }),
                   { target: { value: "" } });
  fireEvent.click(main().getByRole("button", { name: "Save" }));
  await main().findByRole("button", { name: "Edit" });

  // One role, one selection: the fallback nobody touched is not resent, so a
  // fallback another tab set meanwhile survives this save.
  expect(putBody()).toEqual([
    { roles: { primary: { selection: { provider: "saltmarch", model: "vendor/m", preset: "" } } } },
  ]);

  // Touching the fallback sends it, still under that role alone.
  fireEvent.click(main().getByRole("button", { name: "Edit" }));
  fireEvent.click(await main().findByRole("button", { name: "Fallback" }));
  const fallback = within(await main().findByRole("group", { name: "Primary fallback" }));
  fireEvent.change(fallback.getByRole("combobox", { name: "Provider" }),
                   { target: { value: "saltmarch" } });
  fireEvent.click(await fallback.findByRole("radio", { name: "Vendor M" }));
  fireEvent.click(main().getByRole("button", { name: "Save" }));
  await main().findByRole("button", { name: "Edit" });
  const [body] = putBody(1);
  expect(Object.keys(body)).toEqual(["roles"]);
  expect(Object.keys(body.roles)).toEqual(["primary"]);
  expect(body.roles.primary.fallback).toEqual(
    { provider: "saltmarch", model: "vendor/m", preset: "" });
});

test("unset Fast reads Same as Primary", async () => {
  await openCards();
  expect(await roleCard("Fast").findByText(/Same as Primary/)).toBeInTheDocument();
  expect(roleCard("Fast").getByText(/Saltmarch Router ▸ vendor\/m/)).toBeInTheDocument();
  expect(roleCard("Decision").getByText(/Same as Fast/)).toBeInTheDocument();
  // A role with a choice of its own is not "same as" anything.
  expect(roleCard("Primary").queryByText(/Same as/)).not.toBeInTheDocument();
});

test("the embedding role has no preset or fallback", async () => {
  open("/models/role/embedding");
  fireEvent.click(await main().findByRole("button", { name: "Edit" }));
  const role = within(await main().findByRole("group", { name: "Embedding" }));
  expect(role.getByRole("combobox", { name: "Provider" })).toBeInTheDocument();
  expect(main().queryByRole("combobox", { name: /Preset/ })).not.toBeInTheDocument();
  expect(main().queryByRole("button", { name: "Fallback" })).not.toBeInTheDocument();
});

test("an embedding change asks before saving", async () => {
  open("/models/role/embedding");
  fireEvent.click(await main().findByRole("button", { name: "Edit" }));
  const role = within(await main().findByRole("group", { name: "Embedding" }));
  fireEvent.change(role.getByRole("combobox", { name: "Provider" }),
                   { target: { value: "saltmarch" } });
  fireEvent.click(await role.findByRole("radio", { name: "Vendor Embed" }));
  fireEvent.click(main().getByRole("button", { name: "Save" }));

  const ask = within(await main().findByRole("group", { name: "Confirm the re-embedding" }));
  expect(ask.getByText(/re-embeds your library through Saltmarch Router/)).toBeInTheDocument();
  expect(api.putInferenceSettings).not.toHaveBeenCalled();

  fireEvent.click(ask.getByRole("button", { name: "Re-embed and save" }));
  expect(await main().findByRole("button", { name: "Edit" })).toBeInTheDocument();
  expect(putBody()).toEqual([
    { roles: { embedding: { selection: { provider: "saltmarch", model: "vendor/embed" } } } },
    { confirmEmbedding: true },
  ]);
});

test("changing the embedding pick while the question is up takes the question away", async () => {
  // The question names one provider; "Re-embed and save" sends what is picked
  // when it is pressed. Once the pick moves, the yes on screen is no longer
  // a yes to it -- so it is gone, and Save asks again about the new one.
  open("/models/role/embedding");
  fireEvent.click(await main().findByRole("button", { name: "Edit" }));
  const role = within(await main().findByRole("group", { name: "Embedding" }));
  fireEvent.change(role.getByRole("combobox", { name: "Provider" }),
                   { target: { value: "saltmarch" } });
  fireEvent.click(await role.findByRole("radio", { name: "Vendor Embed" }));
  fireEvent.click(main().getByRole("button", { name: "Save" }));
  expect(await main().findByRole("group", { name: "Confirm the re-embedding" }))
    .toBeInTheDocument();

  fireEvent.change(role.getByRole("combobox", { name: "Provider" }),
                   { target: { value: "realm" } });
  expect(main().queryByRole("group", { name: "Confirm the re-embedding" }))
    .not.toBeInTheDocument();
  expect(main().queryByRole("button", { name: "Re-embed and save" })).not.toBeInTheDocument();
  expect(api.putInferenceSettings).not.toHaveBeenCalled();
});

test("an embedding save the server says needs confirming asks too", async () => {
  // Saved here as-is, so nothing looks changed -- but the server compares
  // against what is on disk now, and another tab moved it.
  (api.getInferenceSettings as any).mockResolvedValue(settings({ roles: {
    ...settings().roles,
    embedding: { stored: { provider: "saltmarch", model: "vendor/embed" }, on: true,
                 resolves: resolved({ model: "vendor/embed" }) },
  } }));
  (api.putInferenceSettings as any).mockRejectedValueOnce({
    detail: "Changing the embedding model re-embeds your library through Saltmarch Router, "
            + "which may cost money — confirm to change it.",
    kind: "confirm_embedding" });
  open("/models/role/embedding");
  fireEvent.click(await main().findByRole("button", { name: "Edit" }));
  fireEvent.click(await main().findByRole("button", { name: "Save" }));

  const ask = within(await main().findByRole("group", { name: "Confirm the re-embedding" }));
  expect(ask.getByText(/confirm to change it/)).toBeInTheDocument();
  fireEvent.click(ask.getByRole("button", { name: "Re-embed and save" }));
  expect(await main().findByRole("button", { name: "Edit" })).toBeInTheDocument();
  expect(putBody(0)).toHaveLength(1);
  expect(putBody(1)[1]).toEqual({ confirmEmbedding: true });
});

test("the routing rows sit under Advanced", async () => {
  open();
  const advanced = await column().findByRole("button", { name: "Advanced" });
  expect(column().queryByRole("link", { name: "Rolling summary" })).not.toBeInTheDocument();
  fireEvent.click(advanced);
  fireEvent.click(column().getByRole("link", { name: "Rolling summary" }));

  expect(await main().findByRole("heading", { name: "Rolling summary" })).toBeInTheDocument();
  expect(screen.getByTestId("where")).toHaveTextContent("/models/route/summary");
  expect(main().getByText("Keeps the running summary.")).toBeInTheDocument();
  expect(main().queryByRole("combobox")).not.toBeInTheDocument();
});

test("a route's inherit label says what it resolves to", async () => {
  open("/models/route/summary");
  // Opened by its address, the Advanced rows are already out.
  expect(await column().findByRole("link", { name: "Rolling summary" })).toBeInTheDocument();
  fireEvent.click(await main().findByRole("button", { name: "Edit" }));

  const use = await main().findByRole("combobox", { name: "Role (default: Fast)" });
  expect(within(use).getByRole("option",
    { name: "Inherit (resolves to Saltmarch Router ▸ vendor/m ▸ no preset)" })).toBeInTheDocument();
  expect(within(use).getByRole("option", { name: "Specific model…" })).toBeInTheDocument();
  const preset = main().getByRole("combobox", { name: "Preset override" });
  expect(within(preset).getByRole("option", { name: "Inherit (resolves to no preset)" }))
    .toBeInTheDocument();

  fireEvent.change(use, { target: { value: "decision" } });
  fireEvent.change(preset, { target: { value: PRESET_CLEAR } });
  fireEvent.click(main().getByRole("button", { name: "Save" }));
  expect(await main().findByRole("button", { name: "Edit" })).toBeInTheDocument();
  expect(putBody()).toEqual([
    { routes: { summary: { use: "decision", preset: PRESET_CLEAR } } }]);
});

test("a route can pin a specific model that fits its requires", async () => {
  open("/models/route/image");
  fireEvent.click(await main().findByRole("button", { name: "Edit" }));
  fireEvent.change(await main().findByRole("combobox", { name: "Role (default: Fast)" }),
                   { target: { value: "model" } });

  const pin = within(await main().findByRole("group", { name: "Specific model" }));
  fireEvent.change(pin.getByRole("combobox", { name: "Provider" }),
                   { target: { value: "saltmarch" } });
  const fits = within(await pin.findByRole("group", { name: "Fits" }));
  // The image route asks for generation AND vision.
  expect(api.readConnectionCapabilities).toHaveBeenCalledWith("saltmarch", "generate");
  expect(api.readConnectionCapabilities).toHaveBeenCalledWith("saltmarch", "vision");
  expect(fits.queryByRole("radio", { name: "Vendor Eye" })).not.toBeInTheDocument();
  expect(pin.getByRole("radio", { name: "Vendor Eye" })).toBeInTheDocument(); // unverified
  fireEvent.click(fits.getByRole("radio", { name: "Vendor M" }));
  fireEvent.click(main().getByRole("button", { name: "Save" }));

  expect(await main().findByRole("button", { name: "Edit" })).toBeInTheDocument();
  expect(putBody()).toEqual([{ routes: { image: {
    use: "model", pin: { provider: "saltmarch", model: "vendor/m", preset: "" }, preset: "",
  } } }]);
});

test("a provider changed without a model is never saved", async () => {
  // The picker clears the model when the provider changes: saved as it is,
  // the role would send a blank model id. Its selection, its fallback and a
  // route's pin each wait for a model.
  open("/models/role/primary");
  fireEvent.click(await main().findByRole("button", { name: "Edit" }));
  const role = within(await main().findByRole("group", { name: "Primary" }));
  fireEvent.change(role.getByRole("combobox", { name: "Provider" }),
                   { target: { value: "realm" } });
  expect(role.getByText("Choose a model for this provider to save.")).toBeInTheDocument();
  expect(main().getByRole("button", { name: "Save" })).toBeDisabled();
  fireEvent.change(role.getByRole("combobox", { name: "Provider" }),
                   { target: { value: "saltmarch" } });
  fireEvent.click(await role.findByRole("radio", { name: "Vendor M" }));
  expect(main().getByRole("button", { name: "Save" })).toBeEnabled();

  fireEvent.click(main().getByRole("button", { name: "Fallback" }));
  const fallback = within(await main().findByRole("group", { name: "Primary fallback" }));
  fireEvent.change(fallback.getByRole("combobox", { name: "Provider" }),
                   { target: { value: "saltmarch" } });
  expect(main().getByRole("button", { name: "Save" })).toBeDisabled();
  fireEvent.click(main().getByRole("button", { name: "Cancel" }));
  expect(api.putInferenceSettings).not.toHaveBeenCalled();

  cleanup();
  open("/models/route/image");
  fireEvent.click(await main().findByRole("button", { name: "Edit" }));
  fireEvent.change(await main().findByRole("combobox", { name: "Role (default: Fast)" }),
                   { target: { value: "model" } });
  const pin = within(await main().findByRole("group", { name: "Specific model" }));
  fireEvent.change(pin.getByRole("combobox", { name: "Provider" }),
                   { target: { value: "saltmarch" } });
  expect(main().getByRole("button", { name: "Save" })).toBeDisabled();
});

test("warns when the Primary model can't generate text", async () => {
  CAPS["vendor/m"].generate = "no";
  await openCards();
  expect(await roleCard("Primary").findByText(
    "This model can't generate text, so it can't be Primary.")).toBeInTheDocument();
});

test("warns when an image route's model is unverified for vision", async () => {
  CAPS["vendor/m"].vision = "unknown";
  open("/models/route/image");
  expect(await main().findByText(
    "This route sends images; the chosen model is unverified for vision.")).toBeInTheDocument();
});

test("a route that sends no images asks nothing about vision", async () => {
  CAPS["vendor/m"].vision = "unknown";
  open("/models/route/summary");
  expect(await main().findByRole("heading", { name: "Rolling summary" })).toBeInTheDocument();
  expect(main().queryByText(/unverified for vision/)).not.toBeInTheDocument();
  expect(api.readConnectionCapabilities).not.toHaveBeenCalledWith(
    "saltmarch", "vision", "vendor/m");
});

/** The settings view with the Decision card resolved `mode`, and `over` on it. */
function decisionAs(mode: string, over: Record<string, unknown> = {}) {
  return settings({ roles: { ...settings().roles,
                             decision: card({ decision_mode: mode, ...over }) } });
}

// I9: how a decision is answered is the resolution's `decision_mode`, never a
// rule the page works out from the model's capabilities.
test("a native Decision card says the provider's decisions endpoint answers", async () => {
  // The capabilities would say "no native API" -- the resolution outranks them.
  CAPS["vendor/m"].decide_native = "no";
  (api.getInferenceSettings as any).mockResolvedValue(decisionAs("native"));
  await openCards();
  expect(await roleCard("Decision").findByText(
    "Answered by the provider's decisions endpoint.")).toBeInTheDocument();
  expect(roleCard("Decision").queryByText(/structured generation/)).not.toBeInTheDocument();
});

test("a structured Decision card on a model that could decide natively claims no missing API",
     async () => {
  CAPS["vendor/m"].decide_native = "yes";
  (api.getInferenceSettings as any).mockResolvedValue(
    decisionAs("structured", { decides_natively: true }));
  await openCards();
  expect(await roleCard("Decision").findByText("Answered by structured generation."))
    .toBeInTheDocument();
  expect(roleCard("Decision").queryByText(/No native decision API/)).not.toBeInTheDocument();
});

test("warns when the Decision model has no native decision API", async () => {
  CAPS["vendor/m"].decide_native = "no";
  (api.getInferenceSettings as any).mockResolvedValue(
    decisionAs("structured", { decides_natively: false }));
  await openCards();
  expect(await roleCard("Decision").findByText(
    "No native decision API; structured generation will be used.")).toBeInTheDocument();
  expect(roleCard("Primary").queryByText(/native decision/)).not.toBeInTheDocument();
});

test("a structured card the picker hides on a guess still says how it is answered", async () => {
  // The name rule's `no` hides the model from the capabilities grouping, but
  // the resolver treats it as a guess and serves it structured. The words are
  // the server's, so nothing the picker hides can silence them.
  CAPS["vendor/m"] = { generate: "no", vision: "no", embed: "no", decide_native: "no" };
  (api.getInferenceSettings as any).mockResolvedValue(
    decisionAs("structured", { decides_natively: false }));
  await openCards();
  expect(await roleCard("Decision").findByText(
    "No native decision API; structured generation will be used.")).toBeInTheDocument();
  expect(api.readConnectionCapabilities).not.toHaveBeenCalledWith(
    "saltmarch", "decide", "vendor/m");
});

test("a refused Decision card shows only the refusal's own sentence", async () => {
  const incapable = "Saltmarch Router ▸ vendor/m cannot generate text or make native decisions.";
  CAPS["vendor/m"] = { generate: "no", vision: "no", embed: "no", decide_native: "no" };
  (api.getInferenceSettings as any).mockResolvedValue(decisionAs("", { problem: incapable }));
  await openCards();
  const decision = roleCard("Decision");
  expect(await decision.findByText(incapable)).toBeInTheDocument();
  expect(decision.queryByRole("note")).not.toBeInTheDocument();
  expect(decision.queryByText(/can't generate text/)).not.toBeInTheDocument();
  expect(decision.queryByText(/Answered by|No native decision API/)).not.toBeInTheDocument();
});

test("a Decision card on a native-only model says its sampling is not sent", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(decisionAs("native"));
  const na = { state: "n/a", wire: "", why: "a native decision takes no sampling",
               source: "adapter" };
  (api.previewControls as any).mockImplementation((body: { operation?: string }) =>
    Promise.resolve(body.operation === "decide"
      ? { requested: {}, effective: {}, controls: { temperature: na, reasoning_effort: na } }
      : { requested: {}, effective: {}, controls: {} }));
  await openCards();
  expect(await roleCard("Decision").findByText(
    "Not sent: a native decision takes no sampling.")).toBeInTheDocument();
  expect(api.previewControls).toHaveBeenCalledWith(expect.objectContaining({
    provider: "saltmarch", model: "vendor/m", operation: "decide" }));
  // Primary generates: it asks no decision question, and lists its controls as ever.
  expect(roleCard("Primary").queryByText(/native decision/)).not.toBeInTheDocument();
});

test("a decide route says how its decision is answered", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({ routes: [
    ...ROUTES,
    route({ key: "speaker", label: "Who speaks next", operation: "decide",
            default_role: "decision", role: "decision", uses: "decision",
            decision_mode: "native" }),
  ] }));
  open("/models/route/speaker");
  expect(await main().findByText("Answered by the provider's decisions endpoint."))
    .toBeInTheDocument();
  expect(api.previewControls).toHaveBeenCalledWith(expect.objectContaining({
    operation: "decide" }));
});

test("warns when the Embedding model can't create embeddings", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({ roles: {
    ...settings().roles,
    embedding: { stored: { provider: "saltmarch", model: "vendor/m" }, resolves: null, on: false },
  } }));
  open("/models/role/embedding");
  expect(await main().findByText("This model can't create embeddings.")).toBeInTheDocument();
});

test("a warning follows the model being picked in the form", async () => {
  open("/models/role/primary");
  fireEvent.click(await main().findByRole("button", { name: "Edit" }));
  const role = within(await main().findByRole("group", { name: "Primary" }));
  fireEvent.change(role.getByRole("textbox", { name: "Model id" }),
                   { target: { value: "vendor/embed" } });
  fireEvent.click(role.getByRole("button", { name: "Use this id" }));
  expect(await role.findByText("This model can't generate text, so it can't be Primary."))
    .toBeInTheDocument();
});

test("a capability warning is asked again once a model test lands", async () => {
  // The test announces (`configChanged`); a warning the test disproved must
  // not outlive it on the same page.
  CAPS["vendor/m"].vision = "unknown";
  open("/models/route/image");
  expect(await main().findByText(
    "This route sends images; the chosen model is unverified for vision.")).toBeInTheDocument();

  CAPS["vendor/m"].vision = "yes";
  const { configChanged } = await import("../appEvents");
  act(() => { configChanged(); });
  await screen.findByRole("heading", { name: "Image descriptions" });
  expect(main().queryByText(/unverified for vision/)).not.toBeInTheDocument();
});

test("a role shows the seam's problem", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({ roles: {
    ...settings().roles, primary: card({ problem: "No API key is set for Saltmarch Router." }),
  } }));
  await openCards();
  expect(await roleCard("Primary").findByText("No API key is set for Saltmarch Router."))
    .toBeInTheDocument();
});

test("the Decision card lists the routes using it", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({ routes: [
    ...ROUTES,
    route({ key: "speaker", label: "Who speaks next", operation: "decide",
            default_role: "decision", role: "decision", uses: "decision" }),
  ] }));
  await openCards();
  const routes = within(await roleCard("Decision").findByRole("list", { name: "Routes using Decision" }));
  expect(routes.getByRole("link", { name: "Who speaks next" }))
    .toHaveAttribute("href", "/models/route/speaker");
  expect(routes.queryByRole("link", { name: "Rolling summary" })).not.toBeInTheDocument();
  expect(routes.queryByText(/inherits/)).not.toBeInTheDocument();
});

test("the Decision card lists a route that uses it while Decision inherits", async () => {
  // CODE-M3: Decision unset, so Fast supplies the decide routes -- they still
  // use Decision, and setting it moves them. A pinned one uses no role.
  (api.getInferenceSettings as any).mockResolvedValue(settings({ routes: [
    ...ROUTES,
    route({ key: "speaker", label: "Who speaks next", operation: "decide",
            default_role: "decision", role: "fast", uses: "decision" }),
    route({ key: "scene_break", label: "Scene-break checks", operation: "decide",
            default_role: "decision", role: "primary", uses: "decision" }),
    route({ key: "voice_drift", label: "Voice drift checks", operation: "decide",
            default_role: "decision", role: null, uses: null, use: "model",
            resolves: resolved({ via: "route" }) }),
  ] }));
  await openCards();
  const card = roleCard("Decision");
  const routes = within(await card.findByRole("list", { name: "Routes using Decision" }));
  expect(routes.getByRole("link", { name: "Who speaks next" })).toBeInTheDocument();
  expect(routes.getByText(/inherits Fast/)).toBeInTheDocument();
  expect(routes.getByRole("link", { name: "Scene-break checks" })).toBeInTheDocument();
  expect(routes.getByText(/inherits Primary/)).toBeInTheDocument();
  expect(routes.queryByRole("link", { name: "Voice drift checks" })).not.toBeInTheDocument();
  expect(card.queryByText("No route uses Decision yet.")).not.toBeInTheDocument();
});

test("the Decision card says when no route uses it", async () => {
  open("/models/role/decision");
  expect(await main().findByText("No route uses Decision yet.")).toBeInTheDocument();
});

test("a newer store disables editing", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({
    newer: true, migration: { state: "done", reason: "", skipped: [] } }));
  open("/models/role/primary");

  expect(await screen.findByText(/upgraded by a newer Grimoire/)).toBeInTheDocument();
  expect(main().getByRole("button", { name: "Edit" })).toBeDisabled();
});

test("a pending migration at format 2 still lets the library's settings be saved", async () => {
  // A campaign not reached yet keeps the migration pending, but the global
  // layout is current and the server takes a global write.
  (api.getInferenceSettings as any).mockResolvedValue(settings({
    migration: { state: "pending", reason: "",
                 skipped: ["campaign saltmarch-run: busy; finished on the next start"] } }));
  open("/models/role/primary");
  expect(await main().findByRole("button", { name: "Edit" })).toBeEnabled();
});

test("a store not yet at format 2 cannot be edited", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({
    format: "1", migration: { state: "pending", reason: "", skipped: [] } }));
  open("/models/role/primary");
  expect(await screen.findByText(/Upgrade pending/)).toBeInTheDocument();
  expect(main().getByRole("button", { name: "Edit" })).toBeDisabled();
});

test("a refused save says why and stays in the form", async () => {
  (api.putInferenceSettings as any).mockRejectedValueOnce(
    { detail: "no such preset: tight", kind: "" });
  open("/models/role/primary");
  fireEvent.click(await main().findByRole("button", { name: "Edit" }));
  fireEvent.click(await main().findByRole("button", { name: "Save" }));
  expect(await main().findByText(/no such preset: tight/)).toBeInTheDocument();
  expect(main().getByRole("button", { name: "Save" })).toBeInTheDocument();
});

test("the page unlocks once the upgrade lands, without leaving it", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    (api.getInferenceSettings as any).mockResolvedValue(settings({
      format: "1", migration: { state: "running", reason: "", skipped: [] } }));
    open("/models/role/primary");
    expect(await screen.findByText(/Upgrade pending/)).toBeInTheDocument();
    expect(main().getByRole("button", { name: "Edit" })).toBeDisabled();

    (api.getInferenceSettings as any).mockResolvedValue(settings());
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });

    expect(await main().findByRole("button", { name: "Edit" })).toBeEnabled();
    expect(screen.queryByText(/Upgrade pending/)).not.toBeInTheDocument();
    // Settled: nothing more is asked.
    const reads = (api.getInferenceSettings as any).mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect((api.getInferenceSettings as any).mock.calls.length).toBe(reads);
  } finally {
    vi.useRealTimers();
  }
});

test("an Embedding role that is off says why, in the server's words", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({ roles: {
    ...settings().roles,
    embedding: { stored: { provider: "saltmarch", model: "vendor/embed" }, resolves: null,
                 on: false, problem: "Saltmarch Router has no key set" },
  } }));
  await openCards();
  expect(roleCard("Embedding").getByText(/Off/)).toBeInTheDocument();
  expect(roleCard("Embedding").getByText("Saltmarch Router has no key set")).toBeInTheDocument();
  // A stored choice that embeds nothing is a problem the column flags.
  const row = column().getByRole("link", { name: /Embedding/ });
  expect(within(row).getByText("problem")).toHaveAttribute(
    "title", "Saltmarch Router has no key set");
});

test("an Embedding role nobody chose is off, and no problem in the column", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({ roles: {
    ...settings().roles,
    embedding: { stored: { provider: "", model: "" }, resolves: null, on: false,
                 problem: "No provider chosen" },
  } }));
  await openCards();
  expect(roleCard("Embedding").getByText("No provider chosen")).toBeInTheDocument();
  expect(within(column().getByRole("link", { name: /Embedding/ })).queryByText("problem"))
    .not.toBeInTheDocument();
});

test("a role's preset waits for a provider", async () => {
  open("/models/role/fast");
  fireEvent.click(await main().findByRole("button", { name: "Edit" }));
  const role = within(await main().findByRole("group", { name: "Fast" }));
  // Unset Fast is the same as Primary, preset and all: a preset alone is not used.
  expect(role.getByRole("combobox", { name: "Preset" })).toBeDisabled();
  fireEvent.change(role.getByRole("combobox", { name: "Provider" }),
                   { target: { value: "saltmarch" } });
  expect(role.getByRole("combobox", { name: "Preset" })).toBeEnabled();
});

test("a stored preset with no provider says it is not used, and can be cleared", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({ roles: {
    ...settings().roles, fast: card({ stored: sel("", "", "tight") }) } }));
  open("/models/role/fast");
  fireEvent.click(await main().findByRole("button", { name: "Edit" }));
  const role = within(await main().findByRole("group", { name: "Fast" }));
  expect(role.getByRole("combobox", { name: "Preset" })).toBeEnabled();
  expect(role.getByText(/A preset with no provider is not used/)).toBeInTheDocument();
});

test("a fallback known not to fit its role says it is never sent", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({ roles: {
    ...settings().roles,
    primary: card({ stored: sel("saltmarch", "vendor/m"), fallback: sel("saltmarch", "vendor/eye"),
                    inherits: null, fallback_missing: ["generate"] }),
  } }));
  open("/models/role/primary");
  expect(await main().findByText(
    "The fallback, Saltmarch Router ▸ vendor/eye, is known not to fit Primary "
    + "(it cannot generate text), so it is never sent.")).toBeInTheDocument();
});

test("a fallback that cannot send says why, on its role and its route", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({
    roles: { ...settings().roles,
             primary: card({ stored: sel("saltmarch", "vendor/m"), inherits: null,
                             fallback: sel("realm", "realm/small"),
                             fallback_problem: "Endpoint base URL not set" }) },
    routes: [route({ key: "scene", label: "Scene prose", default_role: "primary",
                     role: "primary", fallback_problem: "Endpoint base URL not set" }),
             ...ROUTES.slice(1)],
  }));
  open("/models/role/primary");
  expect(await main().findByText(
    "The fallback, Realm Local ▸ realm/small, cannot be sent (Endpoint base URL not set), "
    + "so it is never tried.")).toBeInTheDocument();
  cleanup();
  open("/models/route/scene");
  expect(await main().findByText(
    "The fallback cannot be sent (Endpoint base URL not set), so it is never tried."))
    .toBeInTheDocument();
});

test("a fallback the picker hides on a guess is sent, so it says nothing", async () => {
  // The capabilities API hides vendor/embed for generate (the name rule), but
  // the resolver sends a guessed `no`: only `fallback_missing` is the verdict.
  (api.getInferenceSettings as any).mockResolvedValue(settings({ roles: {
    ...settings().roles,
    primary: card({ stored: sel("saltmarch", "vendor/m"), fallback: sel("saltmarch", "vendor/embed"),
                    inherits: null, fallback_missing: [] }),
  } }));
  open("/models/role/primary");
  await main().findByRole("heading", { name: "Primary" });
  expect(main().queryByText(/is never sent/)).not.toBeInTheDocument();
});

test("a route's dropped fallback says it is never sent", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({ routes: [
    ...ROUTES.slice(0, 2),
    route({ key: "image", label: "Image descriptions", requires: ["vision"],
            fallback_missing: ["vision"] }),
  ] }));
  open("/models/route/image");
  expect(await main().findByText(
    "The fallback is known not to fit Image descriptions (it cannot read images), "
    + "so it is never sent.")).toBeInTheDocument();
});

test("a fallback that fits says nothing", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({ roles: {
    ...settings().roles,
    primary: card({ stored: sel("saltmarch", "vendor/m"), fallback: sel("saltmarch", "vendor/eye"),
                    inherits: null }),
  } }));
  open("/models/role/primary");
  await main().findByRole("heading", { name: "Primary" });
  expect(main().queryByText(/is never sent/)).not.toBeInTheDocument();
});
