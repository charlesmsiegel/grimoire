import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { api, PRESET_CLEAR, type CapabilityNeed } from "../api/client";
import { forgetModelTests } from "../components/inference/TestCallDialog";
import ModelsView from "./ModelsView";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, api: {
    getInferenceSettings: vi.fn(), putInferenceSettings: vi.fn(),
    readConnectionCapabilities: vi.fn(), previewControls: vi.fn(),
    previewModelTest: vi.fn(), runModelTest: vi.fn(), dismissRetiredNote: vi.fn(),
    listConnections: vi.fn(), getPricing: vi.fn(), setPricing: vi.fn(),
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
const BOTH = { prompt_usd_per_1k: 0.003, completion_usd_per_1k: 0.015 };
const card = (over: Record<string, unknown> = {}) => ({
  stored: sel(), fallback: sel(), resolves: resolved(), inherits: resolved(), problem: null,
  fallback_missing: [], fallback_problem: null, decision_mode: "", decides_natively: "unknown",
  rate: { source: "provider", entry: BOTH }, ...over,
});
const route = (over: Record<string, unknown>) => ({
  hint: "", tasks: [], operation: "generate", default_role: "fast", requires: [],
  campaign_scoped: true, use: "", pin: sel(), preset: "", resolves: resolved(),
  inherits: resolved(), problem: null, fallback_missing: [], fallback_problem: null,
  decision_mode: "", decides_natively: "unknown", role: "fast", uses: "fast",
  rate: { source: "provider", entry: BOTH }, ...over,
});

const ROUTES = [
  route({ key: "scene", label: "Scene prose", default_role: "primary", role: "primary", uses: "primary" }),
  route({ key: "summary", label: "Rolling summary", hint: "Keeps the running summary." }),
  route({ key: "image", label: "Image descriptions", requires: ["vision"] }),
  route({ key: "scene break", label: "Scene break", operation: "decide", default_role: "decision",
          role: "fast", uses: "decision" }),
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
      embedding: { stored: { provider: "", model: "" }, resolves: null, on: false,
                   problem: "No provider chosen", rate: null },
    },
    routes: ROUTES,
    providers: [
      { id: "saltmarch", name: "Saltmarch Router", kind: "openrouter", preset: "openrouter",
        usable: true, problem: null },
      { id: "realm", name: "Realm Local", kind: "openai_compatible", preset: "custom",
        usable: true, problem: null },
    ],
    presets: [{ id: "balanced", name: "Balanced" }, { id: "tight", name: "Tight" }],
    preset_clear: PRESET_CLEAR,
    retirement_notes: [],
    ...over,
  };
}
const health = (state: string) => ({ state, kind: "", detail: "", at: "" });

beforeEach(() => {
  vi.clearAllMocks();
  forgetModelTests();
  CAPS = BASE_CAPS();
  (api.getInferenceSettings as any).mockResolvedValue(settings());
  (api.putInferenceSettings as any).mockResolvedValue(settings());
  (api.readConnectionCapabilities as any).mockImplementation(
    (_id: string, need: CapabilityNeed, model?: string) => Promise.resolve(answer(need, model)));
  (api.previewControls as any).mockResolvedValue({ requested: {}, effective: {}, controls: {} });
  (api.listConnections as any).mockResolvedValue([
    { id: "saltmarch", name: "Saltmarch Router", health: health("ok") },
    { id: "realm", name: "Realm Local", health: health("error") },
  ]);
  (api.getPricing as any).mockResolvedValue({ rates: { "vendor/m": BOTH } });
  (api.setPricing as any).mockImplementation(async (rates: unknown) => ({ rates }));
});

function Where() {
  const { pathname, search, hash } = useLocation();
  return <div data-testid="where">{pathname + search + hash}</div>;
}

function open(at = "/models") {
  return render(
    <MemoryRouter initialEntries={[at]}>
      <Where />
      <Routes>
        <Route path="/models" element={<ModelsView />} />
        <Route path="/models/edit" element={<ModelsView edit />} />
        <Route path="/providers/*" element={<div>the providers page</div>} />
        <Route path="/presets/*" element={<div>the presets page</div>} />
        <Route path="/config" element={<div>settings</div>} />
      </Routes>
    </MemoryRouter>,
  );
}

const main = () => within(screen.getByRole("main"));
const row = (name: string) => within(main().getByRole("group", { name }));
async function openSummary(at = "/models") {
  open(at);
  await main().findByRole("group", { name: "Primary" });
}
const putBody = (n = 0) => (api.putInferenceSettings as any).mock.calls[n];

afterEach(cleanup);


// ---- the summary ----
test("each role reads provider · model · preset, and nothing on it is an input", async () => {
  await openSummary();
  expect(row("Primary").getByRole("link", { name: "Saltmarch Router" }))
    .toHaveAttribute("href", "/providers/saltmarch");
  expect(row("Primary").getByText(/vendor\/m/)).toBeInTheDocument();
  expect(row("Primary").getByRole("link", { name: "Balanced" }))
    .toHaveAttribute("href", "/presets/balanced");
  expect(main().queryByRole("combobox")).toBeNull();
  expect(main().queryByRole("textbox")).toBeNull();
});

test("the provider's dot is its health from the connections list", async () => {
  await openSummary();
  await waitFor(() => expect(row("Primary").getByText("working")).toBeInTheDocument());
});

test("an unset Decision reads as the role it inherits", async () => {
  await openSummary();
  expect(row("Decision").getByText(/^Same as Fast — /)).toBeInTheDocument();
});

test("Decision lists every task it answers, inherited ones included, folded", async () => {
  await openSummary();
  const fold = row("Decision").getByText(/^Tasks answered by Decision/).closest("details")!;
  expect(fold).not.toHaveAttribute("open");
  // Opened as a reader would; jsdom toggles <details> on a summary click.
  // If this jsdom does not, set `fold.open = true` instead.
  fireEvent.click(row("Decision").getByText(/^Tasks answered by Decision/));
  await waitFor(() => expect(fold).toHaveAttribute("open"));
  const list = row("Decision").getByRole("list", { name: "Tasks answered by Decision" });
  const link = within(list).getByRole("link", { name: "Scene break" });
  expect(link).toHaveAttribute("href", "/models/edit#task-scene%20break");
});

test("Embedding off says so, with why, and draws no rate", async () => {
  await openSummary();
  expect(row("Embedding").getByText("Off — nothing is embedded, and semantic recall is not used."))
    .toBeInTheDocument();
  expect(row("Embedding").getByText("No provider chosen")).toBeInTheDocument();
  expect(row("Embedding").queryByText(/priced/)).toBeNull();
});

test("each row says what would price it, and an unpriced one offers Set rate", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({ roles: {
    ...settings().roles,
    fast: card({ rate: { source: "table", entry: BOTH } }),
    decision: card({ rate: { source: "none" }, resolves: resolved({ model: "vendor/odd#1" }) }),
  } }));
  await openSummary();
  expect(row("Primary").getByText(/would be priced at .* \(provider\)/)).toBeInTheDocument();
  expect(row("Fast").getByText(/would be priced at .* \(your rates\)/)).toBeInTheDocument();
  expect(row("Decision").getByText(/no rate: calls unpriced/)).toBeInTheDocument();
  expect(row("Decision").getByRole("link", { name: "Set rate" }))
    .toHaveAttribute("href", "/models?add=vendor%2Fodd%231#rates");
});

test("a native Decision says why it has no figure", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({ roles: {
    ...settings().roles,
    decision: card({ decision_mode: "native", rate: { source: "native" },
                     stored: sel("saltmarch", "vendor/decider") }),
  } }));
  await openSummary();
  expect(row("Decision").getByText(/native decisions: priced only if the provider reports a cost/))
    .toBeInTheDocument();
  expect(row("Decision").queryByRole("link", { name: "Set rate" })).toBeNull();
});

const limit = (value: number | null, source = value == null ? "unknown" : "catalog") =>
  ({ value, source });
const limits = (over: Record<string, unknown> = {}) => ({
  model: "vendor/m", window: limit(200000), max_output: limit(null),
  ceiling: { tokens: 195904, binding: ["saltmarch", "vendor/m"], reason: "" }, ...over,
});

test("a known window reads after the rate, and an unknown one offers Set", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({ roles: {
    ...settings().roles,
    primary: card({ stored: sel("saltmarch", "vendor/m"), inherits: null, limits: limits() }),
    fast: card({ limits: limits({ window: limit(null),
                                  ceiling: { tokens: null, binding: null, reason: "" } }) }),
  } }));
  await openSummary();
  expect(row("Primary").getByText("200k window")).toBeInTheDocument();
  expect(row("Primary").queryByRole("link", { name: "Set" })).toBeNull();
  expect(row("Fast").getByText(/window unknown/)).toBeInTheDocument();
  // The model's facts on its provider, the model's own slash kept a path.
  expect(row("Fast").getByRole("link", { name: "Set" }))
    .toHaveAttribute("href", "/providers/saltmarch/models/vendor/m?edit=limits");
});

test("the fallback line carries the riding fallback's window", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({ roles: {
    ...settings().roles,
    primary: card({ stored: sel("saltmarch", "vendor/m"), fallback: sel("realm", "realm/small"),
                    inherits: null,
                    limits: limits({ fallback_window: limit(8192),
                                     ceiling: { tokens: 6144, binding: ["realm", "realm/small"],
                                                reason: "" } }) }),
  } }));
  await openSummary();
  const fallback = main().getByText(/Fallback:/);
  expect(within(fallback).getByText(/8k window/)).toBeInTheDocument();
});

test("an inherited riding fallback's window is said on the window line", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({ roles: {
    ...settings().roles,
    // Fast stores no fallback of its own, yet one rides (inherited).
    fast: card({ limits: limits({ fallback_window: limit(8192) }) }),
  } }));
  await openSummary();
  expect(row("Fast").getByText(/200k window · fallback 8k window/)).toBeInTheDocument();
});

test("a ceiling the reply reserve fills says why, in the warning style", async () => {
  const reason = "The preset asks for 32,000 reply tokens; this model's window is 8,192, "
    + "which leaves no room for a prompt.";
  (api.getInferenceSettings as any).mockResolvedValue(settings({ roles: {
    ...settings().roles,
    fast: card({ limits: limits({ window: limit(8192),
                                  ceiling: { tokens: 0, binding: ["saltmarch", "vendor/m"],
                                             reason } }) }),
  } }));
  await openSummary();
  expect(row("Fast").getByText(reason)).toHaveClass("field-warning");
});

test("a card whose limits are null shows no window at all", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({ roles: {
    ...settings().roles,
    decision: card({ decision_mode: "native", rate: { source: "native" }, limits: null,
                     stored: sel("saltmarch", "vendor/decider") }),
  } }));
  await openSummary();
  expect(row("Decision").queryByText(/window/)).toBeNull();
  expect(row("Decision").queryByRole("link", { name: "Set" })).toBeNull();
});

test("a role naming a deleted provider keeps it on screen", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({ roles: {
    ...settings().roles,
    fast: card({ stored: sel("gone", "vendor/m"), resolves: null, rate: null,
                 problem: "The provider gone no longer exists." }),
  } }));
  await openSummary();
  expect(row("Fast").getByText(/gone \(missing provider\)/)).toBeInTheDocument();
  expect(row("Fast").getByText("The provider gone no longer exists.")).toBeInTheDocument();
});

test("what a role's preset sends is there, folded", async () => {
  await openSummary();
  const fold = row("Primary").getByText("What this sends").closest("details")!;
  expect(fold).not.toHaveAttribute("open");
});

test("task overrides are counted only when there are some", async () => {
  await openSummary();
  expect(main().queryByText(/task override/)).toBeNull();
  cleanup();
  (api.getInferenceSettings as any).mockResolvedValue(settings({
    routes: [ROUTES[0], { ...ROUTES[1], preset: "tight" }, ROUTES[2], ROUTES[3]] }));
  await openSummary();
  expect(main().getByRole("link", { name: "1 task override active" }))
    .toHaveAttribute("href", "/models/edit#advanced");
});

test("the actions: Edit models, add a provider, provider status", async () => {
  await openSummary();
  expect(main().getByRole("link", { name: "+ Add provider" })).toHaveAttribute("href", "/providers/new");
  expect(main().getByRole("link", { name: "Provider status →" })).toHaveAttribute("href", "/providers");
  fireEvent.click(main().getByRole("button", { name: "Edit models" }));
  expect(screen.getByTestId("where")).toHaveTextContent("/models/edit");
});

test("Edit models waits while a newer build owns the settings", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({ newer: true }));
  await openSummary();
  expect(main().getByRole("button", { name: "Edit models" })).toBeDisabled();
});

test("the column is the Inference group, with Models current", async () => {
  await openSummary();
  const nav = within(screen.getByRole("complementary", { name: "Models" }));
  expect(nav.getByRole("link", { name: "Models" })).toHaveAttribute("aria-current", "page");
  expect(nav.getByRole("link", { name: "Providers" })).toHaveAttribute("href", "/providers");
  expect(nav.getByRole("link", { name: "Presets" })).toHaveAttribute("href", "/presets");
  expect(nav.getByRole("link", { name: "← All settings" })).toHaveAttribute("href", "/config");
});

test("Set rate lands in the rate editor with the model filled in, slashes and all", async () => {
  await openSummary("/models?add=vendor%2Fodd%231#rates");
  expect(await main().findByDisplayValue("vendor/odd#1")).toBeInTheDocument();
});

test("Cancel on a Set rate drops the address and keeps the table in view", async () => {
  await openSummary("/models?add=vendor%2Fodd%231#rates");
  await main().findByDisplayValue("vendor/odd#1");
  fireEvent.click(main().getByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/^\/models#rates$/));
  expect(await main().findByRole("button", { name: "Edit rates" })).toBeInTheDocument();
  expect(api.setPricing).not.toHaveBeenCalled();
});

test.each([
  ["pending", { state: "pending", reason: "", skipped: [] }, /not finished upgrading yet/],
  ["skipped", { state: "done", reason: "", skipped: ["campaign saltmarch-run: unreadable"] },
   /The upgrade left 1 thing for later/],
])("the upgrade's quiet line shows at format 2 (%s)", async (_name, migration, line) => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({ migration }));
  await openSummary();
  expect(main().getByText(line)).toHaveClass("field-hint");
});

test("+ Add provider comes back to the exact address it left", async () => {
  function Back() {
    const { state } = useLocation();
    return <div>back to {(state as { returnTo?: string } | null)?.returnTo}</div>;
  }
  render(
    <MemoryRouter initialEntries={["/models?add=vendor%2Fm#rates"]}>
      <Routes>
        <Route path="/models" element={<ModelsView />} />
        <Route path="/providers/new" element={<Back />} />
      </Routes>
    </MemoryRouter>,
  );
  fireEvent.click(await screen.findByRole("link", { name: "+ Add provider" }));
  expect(await screen.findByText("back to /models?add=vendor%2Fm#rates")).toBeInTheDocument();
});

test("what was not carried over sits under the heading until dismissed", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({ retirement_notes: [{
    id: "a1", scope: "global", scope_name: "", subject: "summary", provider_id: "glm",
    effort: "high", kind: "route_preset",
    text: "On the Summary route, the preset “Cold” sets no reasoning effort, so the GLM "
      + "provider “glm” no longer sends its reasoning effort (high) there — this was not "
      + "carried over.",
  }] }));
  (api.dismissRetiredNote as any).mockResolvedValue({ ok: true });
  await openSummary();
  const notice = await screen.findByRole("region", { name: "Not carried over" });
  const heading = screen.getByRole("heading", { level: 1, name: "Models" });
  // Under the page heading: after it in the document, before the role cards.
  expect(heading.compareDocumentPosition(notice) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(notice).toHaveTextContent("— this was not carried over.");
  fireEvent.click(within(notice).getByRole("button", { name: "Dismiss" }));
  await waitFor(() => expect(screen.queryByRole("region", { name: "Not carried over" }))
    .toBeNull());
  expect(api.dismissRetiredNote).toHaveBeenCalledWith("a1");
});

test("two dismisses in flight both stick", async () => {
  const note = (id: string) => ({
    id, scope: "global", scope_name: "", subject: id, provider_id: "glm", effort: "high",
    kind: "route_preset", text: `Note ${id} — this was not carried over.`,
  });
  (api.getInferenceSettings as any).mockResolvedValue(
    settings({ retirement_notes: [note("a1"), note("b2")] }));
  const finish: Record<string, () => void> = {};
  (api.dismissRetiredNote as any).mockImplementation((id: string) =>
    new Promise((resolve) => { finish[id] = () => resolve({ ok: true }); }));
  await openSummary();
  const notice = await screen.findByRole("region", { name: "Not carried over" });
  const [a, b] = within(notice).getAllByRole("button", { name: "Dismiss" });
  fireEvent.click(a);
  fireEvent.click(b);
  await waitFor(() => expect(Object.keys(finish).sort()).toEqual(["a1", "b2"]));
  act(() => finish.a1());
  await waitFor(() => expect(screen.queryByText(/Note a1/)).toBeNull());
  act(() => finish.b2());
  await waitFor(() => expect(screen.queryByRole("region", { name: "Not carried over" }))
    .toBeNull());
  expect(screen.queryByText(/Note a1/)).toBeNull();
});

test("with nothing lost, there is no notice", async () => {
  await openSummary();
  expect(screen.queryByRole("region", { name: "Not carried over" })).toBeNull();
});

// ---- ported from the card page: what the summary still owns ----
const decisionAs = (mode: string, over: Record<string, unknown> = {}) =>
  settings({ roles: { ...settings().roles, decision: card({ decision_mode: mode, ...over }) } });

test("warns when the Primary model can't generate text", async () => {
  CAPS["vendor/m"].generate = "no";
  await openSummary();
  expect(await row("Primary").findByText(
    "This model can't generate text, so it can't be Primary.")).toBeInTheDocument();
});

test("warns when the Embedding model can't create embeddings", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({ roles: {
    ...settings().roles,
    embedding: { stored: { provider: "saltmarch", model: "vendor/m" }, resolves: null,
                 on: false, problem: null, rate: null },
  } }));
  await openSummary();
  expect(await row("Embedding").findByText("This model can't create embeddings."))
    .toBeInTheDocument();
});

test("no capability note when every model fits", async () => {
  await openSummary();
  await waitFor(() => expect(api.readConnectionCapabilities).toHaveBeenCalled());
  expect(main().queryByRole("note")).toBeNull();
});

test("a capability warning is asked again once a model test lands", async () => {
  CAPS["vendor/m"].generate = "no";
  await openSummary();
  expect(await row("Primary").findByText(/can't generate text/)).toBeInTheDocument();
  CAPS["vendor/m"].generate = "yes";
  const { configChanged } = await import("../appEvents");
  act(() => { configChanged(); });
  await waitFor(() => expect(row("Primary").queryByText(/can't generate text/)).toBeNull());
});

test("a native Decision says the provider's decisions endpoint answers", async () => {
  CAPS["vendor/m"].decide_native = "no";
  (api.getInferenceSettings as any).mockResolvedValue(decisionAs("native"));
  await openSummary();
  expect(await row("Decision").findByText("Answered by the provider's decisions endpoint."))
    .toBeInTheDocument();
  expect(row("Decision").queryByText(/structured generation/)).toBeNull();
});

test.each(["yes", "unknown"])("structured Decision with native API %s claims no missing API",
  async (known) => {
    (api.getInferenceSettings as any).mockResolvedValue(
      decisionAs("structured", { decides_natively: known }));
    await openSummary();
    expect(await row("Decision").findByText("Answered by structured generation."))
      .toBeInTheDocument();
    expect(row("Decision").queryByText(/No native decision API/)).toBeNull();
  });

test("a Decision model known to have no native API says so, even when the picker hides it",
  async () => {
    CAPS["vendor/m"] = { generate: "no", vision: "no", embed: "no", decide_native: "no" };
    (api.getInferenceSettings as any).mockResolvedValue(
      decisionAs("structured", { decides_natively: "no" }));
    await openSummary();
    expect(await row("Decision").findByText(
      "No native decision API; structured generation will be used.")).toBeInTheDocument();
    expect(row("Primary").queryByText(/native decision/)).toBeNull();
  });

test("a refused Decision shows only the refusal's own sentence", async () => {
  const incapable = "Saltmarch Router ▸ vendor/m cannot generate text or make native decisions.";
  CAPS["vendor/m"] = { generate: "no", vision: "no", embed: "no", decide_native: "no" };
  (api.getInferenceSettings as any).mockResolvedValue(decisionAs("", { problem: incapable }));
  await openSummary();
  expect(await row("Decision").findByText(incapable)).toBeInTheDocument();
  expect(row("Decision").queryByRole("note")).toBeNull();
  expect(row("Decision").queryByText(/Answered by|No native decision API/)).toBeNull();
});

test("a fallback known not to fit its role says it is never sent", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({ roles: {
    ...settings().roles,
    primary: card({ stored: sel("saltmarch", "vendor/m"), fallback: sel("saltmarch", "vendor/eye"),
                    inherits: null, fallback_missing: ["generate"] }),
  } }));
  await openSummary();
  expect(row("Primary").getByText(
    "The fallback, Saltmarch Router ▸ vendor/eye, is known not to fit Primary "
    + "(it cannot generate text), so it is never sent.")).toBeInTheDocument();
});

test("a fallback that cannot send says why", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({ roles: {
    ...settings().roles,
    primary: card({ stored: sel("saltmarch", "vendor/m"), inherits: null,
                    fallback: sel("realm", "realm/small"),
                    fallback_problem: "Endpoint base URL not set" }),
  } }));
  await openSummary();
  expect(row("Primary").getByText(
    "The fallback, Realm Local ▸ realm/small, cannot be sent (Endpoint base URL not set), "
    + "so it is never tried.")).toBeInTheDocument();
});

test("a fallback that fits says nothing", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({ roles: {
    ...settings().roles,
    primary: card({ stored: sel("saltmarch", "vendor/m"), fallback: sel("saltmarch", "vendor/eye"),
                    inherits: null }),
  } }));
  await openSummary();
  expect(row("Primary").getByText(/vendor\/eye/)).toBeInTheDocument();
  expect(row("Primary").queryByText(/is never (sent|tried)/)).toBeNull();
});

test("a native Decision's What this sends asks as a decision and says no sampling is sent",
  async () => {
    (api.getInferenceSettings as any).mockResolvedValue(decisionAs("native"));
    const na = { state: "n/a", wire: "", why: "a native decision takes no sampling",
                 source: "adapter" };
    (api.previewControls as any).mockImplementation((body: { operation?: string }) =>
      Promise.resolve(body.operation === "decide"
        ? { requested: {}, effective: {}, controls: { temperature: na, reasoning_effort: na } }
        : { requested: {}, effective: {}, controls: {} }));
    await openSummary();
    (row("Decision").getByText("What this sends").closest("details") as HTMLDetailsElement).open = true;
    expect(await row("Decision").findByText("Not sent: a native decision takes no sampling."))
      .toBeInTheDocument();
    expect(api.previewControls).toHaveBeenCalledWith(expect.objectContaining({
      provider: "saltmarch", model: "vendor/m", operation: "decide" }));
    expect(row("Primary").queryByText(/native decision/)).toBeNull();
  });

test("the Decision task list names inherited roles and leaves out pinned tasks", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({ routes: [
    ...ROUTES.slice(0, 3),
    route({ key: "speaker", label: "Who speaks next", operation: "decide",
            default_role: "decision", role: "fast", uses: "decision" }),
    route({ key: "scene_break", label: "Scene-break checks", operation: "decide",
            default_role: "decision", role: "primary", uses: "decision" }),
    route({ key: "voice_drift", label: "Voice drift checks", operation: "decide",
            default_role: "decision", role: null, uses: null, use: "model",
            resolves: resolved({ via: "route" }) }),
  ] }));
  await openSummary();
  const list = row("Decision").getByRole("list", { name: "Tasks answered by Decision" });
  const tasks = within(list);
  expect(tasks.getByRole("link", { name: "Who speaks next" })).toBeInTheDocument();
  expect(tasks.getByText(/inherits Fast/)).toBeInTheDocument();
  expect(tasks.getByRole("link", { name: "Scene-break checks" })).toBeInTheDocument();
  expect(tasks.getByText(/inherits Primary/)).toBeInTheDocument();
  expect(tasks.queryByRole("link", { name: "Voice drift checks" })).toBeNull();
});

test("the Decision row says when no task uses it", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({ routes: ROUTES.slice(0, 3) }));
  await openSummary();
  expect(row("Decision").getByText("No task uses Decision yet.")).toBeInTheDocument();
});

test("an Embedding that is on shows provider and model, no preset, and an input-only rate",
  async () => {
    (api.getInferenceSettings as any).mockResolvedValue(settings({ roles: {
      ...settings().roles,
      embedding: { stored: { provider: "saltmarch", model: "vendor/embed" }, on: true,
                   resolves: resolved({ model: "vendor/embed" }), problem: null,
                   rate: { source: "table", entry: { prompt_usd_per_1k: 0.002, completion_usd_per_1k: 0.002 } } },
    } }));
    await openSummary();
    const emb = row("Embedding");
    expect(emb.getByRole("link", { name: "Saltmarch Router" })).toBeInTheDocument();
    expect(emb.getByText(/vendor\/embed/)).toBeInTheDocument();
    expect(emb.queryByText(/no preset/)).toBeNull();
    expect(emb.getByText(/would be priced at .*\(your rates\)/)).toBeInTheDocument();
    expect(emb.queryByText(/ out/)).toBeNull();
  });

test("a newer store says so and disables Edit models", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({ newer: true }));
  await openSummary();
  expect(await screen.findByText(/upgraded by a newer Grimoire/)).toBeInTheDocument();
  expect(main().getByRole("button", { name: "Edit models" })).toBeDisabled();
});

test("a pending migration at format 2 still lets the settings be edited", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({
    migration: { state: "pending", reason: "",
                 skipped: ["campaign saltmarch-run: busy; finished on the next start"] } }));
  await openSummary();
  expect(main().getByRole("button", { name: "Edit models" })).toBeEnabled();
});

test("a store not yet at format 2 says the upgrade is pending and cannot be edited", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({
    format: "1", migration: { state: "pending", reason: "", skipped: [] } }));
  await openSummary();
  expect(await screen.findByText(/Upgrade pending/)).toBeInTheDocument();
  expect(main().getByRole("button", { name: "Edit models" })).toBeDisabled();
});

test("the page unlocks once the upgrade lands, without leaving it", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    (api.getInferenceSettings as any).mockResolvedValue(settings({
      format: "1", migration: { state: "running", reason: "", skipped: [] } }));
    await openSummary();
    expect(await screen.findByText(/Upgrade pending/)).toBeInTheDocument();
    expect(main().getByRole("button", { name: "Edit models" })).toBeDisabled();

    (api.getInferenceSettings as any).mockResolvedValue(settings());
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });

    expect(await main().findByRole("button", { name: "Edit models" })).toBeEnabled();
    expect(screen.queryByText(/Upgrade pending/)).toBeNull();
    const reads = (api.getInferenceSettings as any).mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect((api.getInferenceSettings as any).mock.calls.length).toBe(reads);
  } finally {
    vi.useRealTimers();
  }
});

// ---- the edit form ----
const form = () => within(main().getByRole("form", { name: "Edit models" }));
/** The form; `hash` opens part of it (`#advanced` unfolds the per-task rows,
 *  which a test must do before it can reach them -- folded is the default). */
async function openForm(hash = "") {
  open(`/models/edit${hash}`);
  await main().findByRole("form", { name: "Edit models" });
}
/** A role's own fieldset: its ModelSelect is a group of the same name too,
 *  nested inside, so the outer one is the first in document order. */
const roleBox = (name: string) => within(form().getAllByRole("group", { name })[0]);
const pick = (name: string, value: string) =>
  fireEvent.change(form().getByRole("combobox", { name }), { target: { value } });

test("every role has provider, model and preset on one form", async () => {
  await openForm();
  for (const role of ["Primary", "Fast", "Decision"]) {
    expect(form().getByRole("combobox", { name: `${role} provider` })).toBeInTheDocument();
  }
  expect(form().getByRole("combobox", { name: "Primary model" })).toHaveValue("vendor/m");
  expect(form().getByRole("combobox", { name: "Primary preset" })).toHaveValue("balanced");
  expect(form().getByRole("combobox", { name: "Embedding provider" })).toBeInTheDocument();
  expect(form().queryByRole("combobox", { name: "Embedding preset" })).toBeNull();
});

test("saving nothing sends nothing and goes back to the summary", async () => {
  await openForm();
  fireEvent.click(form().getByRole("button", { name: "Save" }));
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/^\/models$/));
  expect(api.putInferenceSettings).not.toHaveBeenCalled();
});

test("one changed role is all a save sends", async () => {
  await openForm();
  pick("Fast provider", "realm");
  await form().findAllByRole("option", { name: "Vendor M" });
  pick("Fast model", "vendor/m");
  fireEvent.click(form().getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.putInferenceSettings).toHaveBeenCalledTimes(1));
  expect(putBody()).toEqual([{ roles: { fast: { selection: sel("realm", "vendor/m", "") } } }]);
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/^\/models$/));
});

test("Same as Fast clears provider, model and preset alike", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({ roles: {
    ...settings().roles, decision: card({ stored: sel("saltmarch", "vendor/m", "tight") }) } }));
  await openForm();
  pick("Decision provider", "");
  expect(form().queryByRole("combobox", { name: "Decision preset" })).toBeNull();
  fireEvent.click(form().getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.putInferenceSettings).toHaveBeenCalled());
  expect(putBody()[0]).toEqual({ roles: { decision: { selection: sel("", "", "") } } });
});

test("a provider with no model holds Save and says why", async () => {
  await openForm();
  pick("Fast provider", "realm");
  expect(form().getAllByText("Choose a model for this provider to save.").length).toBeGreaterThan(0);
  expect(form().getByRole("button", { name: "Save" })).toBeDisabled();
});

test("a fallback is added, sent, and removed as an empty one", async () => {
  await openForm();
  fireEvent.click(form().getByRole("button", { name: "+ Fallback for Fast" }));
  pick("Fast fallback provider", "realm");
  await form().findAllByRole("option", { name: "Vendor M" });
  pick("Fast fallback model", "vendor/m");
  fireEvent.click(form().getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.putInferenceSettings).toHaveBeenCalledTimes(1));
  expect(putBody()[0]).toEqual({ roles: { fast: { fallback: sel("realm", "vendor/m", "") } } });
});

test("removing a stored fallback writes an empty one", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({ roles: {
    ...settings().roles, fast: card({ fallback: sel("realm", "vendor/m", "") }) } }));
  await openForm();
  fireEvent.click(form().getByRole("button", { name: "Remove Fast fallback" }));
  fireEvent.click(form().getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.putInferenceSettings).toHaveBeenCalledTimes(1));
  expect(putBody()[0]).toEqual({ roles: { fast: { fallback: sel("", "", "") } } });
});

test("an Embedding that would re-embed asks first, then sends the yes", async () => {
  await openForm();
  pick("Embedding provider", "saltmarch");
  await form().findAllByRole("option", { name: "Vendor Embed" });
  pick("Embedding model", "vendor/embed");
  fireEvent.click(form().getByRole("button", { name: "Save" }));
  const ask = await form().findByRole("group", { name: "Confirm the re-embedding" });
  expect(api.putInferenceSettings).not.toHaveBeenCalled();
  fireEvent.click(within(ask).getByRole("button", { name: "Re-embed and save" }));
  await waitFor(() => expect(api.putInferenceSettings).toHaveBeenCalledTimes(1));
  expect(putBody()).toEqual([{ roles: { embedding: { selection: { provider: "saltmarch", model: "vendor/embed" } } } },
                             { confirmEmbedding: true }]);
});

test("turning Embedding off asks nothing up front", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({ roles: {
    ...settings().roles,
    embedding: { stored: { provider: "saltmarch", model: "vendor/embed" }, on: true,
                 resolves: resolved({ model: "vendor/embed" }), problem: null, rate: null } } }));
  await openForm();
  pick("Embedding provider", "");
  fireEvent.click(form().getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.putInferenceSettings).toHaveBeenCalledTimes(1));
  expect(putBody()).toEqual([{ roles: { embedding: { selection: { provider: "", model: "" } } } }]);
});

test("the server's re-embedding question is asked in its words, and the resend carries the yes", async () => {
  (api.putInferenceSettings as any).mockRejectedValueOnce({
    detail: "Changing the embedding model re-embeds your library through Saltmarch Router, "
            + "which may cost money — confirm to change it.",
    kind: "confirm_embedding" });
  (api.getInferenceSettings as any).mockResolvedValue(settings({ roles: {
    ...settings().roles,
    embedding: { stored: { provider: "saltmarch", model: "vendor/embed" }, on: true,
                 resolves: resolved({ model: "vendor/embed" }), problem: null, rate: null } } }));
  await openForm();
  pick("Embedding provider", "");
  fireEvent.click(form().getByRole("button", { name: "Save" }));
  const ask = await form().findByRole("group", { name: "Confirm the re-embedding" });
  expect(ask).toHaveTextContent(/re-embeds your library through Saltmarch Router/);
  fireEvent.click(within(ask).getByRole("button", { name: "Re-embed and save" }));
  await waitFor(() => expect(api.putInferenceSettings).toHaveBeenCalledTimes(2));
  expect(putBody(1)[1]).toEqual({ confirmEmbedding: true });
});

test("any other refusal is shown above the form in the server's words, the form kept", async () => {
  (api.putInferenceSettings as any).mockRejectedValueOnce({ detail: "no such preset: tight", kind: "" });
  await openForm();
  pick("Primary preset", "tight");
  fireEvent.click(form().getByRole("button", { name: "Save" }));
  expect(await main().findByText(/no such preset: tight/)).toBeInTheDocument();
  expect(form().getByRole("combobox", { name: "Primary preset" })).toHaveValue("tight");
  expect(screen.getByTestId("where")).toHaveTextContent("/models/edit");
});

test("Cancel discards the draft and lands on the summary, even from a direct load", async () => {
  await openForm();
  pick("Primary preset", "tight");
  fireEvent.click(form().getByRole("button", { name: "Cancel" }));
  expect(screen.getByTestId("where")).toHaveTextContent(/^\/models$/);
  expect(api.putInferenceSettings).not.toHaveBeenCalled();
});

test("an edit row carries the role's problem, and a fallback row why it is dropped", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({ roles: {
    ...settings().roles,
    primary: card({ stored: sel("saltmarch", "vendor/embed"), problem: "vendor/embed cannot generate text." }),
    fast: card({ fallback: sel("realm", "vendor/embed"), fallback_missing: ["generate"] }),
    decision: card({ fallback: sel("realm", "vendor/m"), fallback_problem: "Realm Local has no key set" }),
  } }));
  await openForm();
  expect(roleBox("Primary")
    .getByText("vendor/embed cannot generate text.")).toBeInTheDocument();
  expect(roleBox("Fast fallback")
    .getByText(/is known not to fit Fast \(it cannot generate text\)/)).toBeInTheDocument();
  expect(roleBox("Decision fallback")
    .getByText(/cannot be sent \(Realm Local has no key set\)/)).toBeInTheDocument();
});

test("an Embedding model known not to embed is warned of on the form", async () => {
  await openForm();
  pick("Embedding provider", "saltmarch");
  fireEvent.change(form().getByRole("combobox", { name: "Embedding model" }),
                   { target: { value: "\u0000other" } });
  fireEvent.change(form().getByRole("textbox", { name: "Embedding model id" }),
                   { target: { value: "vendor/m" } });
  fireEvent.click(form().getByRole("button", { name: "Use this id" }));
  expect(await roleBox("Embedding")
    .findByText("This model can't create embeddings.")).toBeInTheDocument();
});

test("a newer build's store holds the whole form", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({ newer: true }));
  await openForm();
  expect(form().getByRole("combobox", { name: "Primary provider" })).toBeDisabled();
  expect(form().getByRole("button", { name: "Save" })).toBeDisabled();
});

// ---- Advanced: per-task overrides ----
const advanced = () => main().getByText(/^Advanced: per-task overrides/).closest("details")!;
const task = (name: string) => within(within(advanced()).getByRole("group", { name }));

test("Advanced is folded until asked for", async () => {
  await openForm();
  expect(advanced()).not.toHaveAttribute("open");
});

test("an address opens it: the section, or one task, however its key is spelt", async () => {
  open("/models/edit#advanced");
  await main().findByRole("form", { name: "Edit models" });
  expect(advanced()).toHaveAttribute("open");
  cleanup();
  open("/models/edit#task-scene%20break");
  await main().findByRole("form", { name: "Edit models" });
  expect(advanced()).toHaveAttribute("open");
  expect(task("Scene break").getByRole("combobox", { name: "Scene break use" })).toBeInTheDocument();
});

test("a malformed task address opens the page with the section folded", async () => {
  open("/models/edit#task-%ZZ");
  await main().findByRole("form", { name: "Edit models" });
  expect(advanced()).not.toHaveAttribute("open");
});

test("choosing a role for a task marks it as overriding and sends only use", async () => {
  await openForm("#advanced");
  fireEvent.change(task("Rolling summary").getByRole("combobox", { name: "Rolling summary use" }),
                   { target: { value: "primary" } });
  expect(task("Rolling summary").getByText("overrides")).toBeInTheDocument();
  fireEvent.click(form().getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.putInferenceSettings).toHaveBeenCalledTimes(1));
  expect(putBody()[0]).toEqual({ routes: { summary: { use: "primary" } } });
});

test("switching a pinned task to a role keeps the pin to come back to", async () => {
  const pinned = { ...ROUTES[1], use: "model", pin: sel("realm", "vendor/m", "tight") };
  (api.getInferenceSettings as any).mockResolvedValue(settings({
    routes: [ROUTES[0], pinned, ROUTES[2], ROUTES[3]] }));
  await openForm("#advanced");
  const use = task("Rolling summary").getByRole("combobox", { name: "Rolling summary use" });
  fireEvent.change(use, { target: { value: "fast" } });
  fireEvent.change(use, { target: { value: "model" } });
  expect(task("Rolling summary").getByRole("combobox", { name: "Rolling summary pinned provider" }))
    .toHaveValue("realm");
  expect(task("Rolling summary").getByRole("combobox", { name: "Rolling summary pin preset" }))
    .toHaveValue("tight");
  fireEvent.change(use, { target: { value: "fast" } });
  fireEvent.click(form().getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.putInferenceSettings).toHaveBeenCalledTimes(1));
  expect(putBody()[0]).toEqual({ routes: { summary: { use: "fast" } } });
});

test("a pin preset and a preset override are two values, and stop-inheriting round-trips", async () => {
  const pinned = { ...ROUTES[1], use: "model", pin: sel("saltmarch", "vendor/m", "balanced"),
                   preset: PRESET_CLEAR };
  (api.getInferenceSettings as any).mockResolvedValue(settings({
    routes: [ROUTES[0], pinned, ROUTES[2], ROUTES[3]] }));
  await openForm("#advanced");
  expect(task("Rolling summary").getByRole("combobox", { name: "Rolling summary pin preset" }))
    .toHaveValue("balanced");
  const override = task("Rolling summary").getByRole("combobox", { name: "Rolling summary preset override" });
  expect(override).toHaveValue(PRESET_CLEAR);
  fireEvent.change(override, { target: { value: "tight" } });
  fireEvent.click(form().getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.putInferenceSettings).toHaveBeenCalledTimes(1));
  expect(putBody()[0]).toEqual({ routes: { summary: { preset: "tight" } } });
});

test("stop inheriting is sent as the sentinel, never as an empty preset", async () => {
  await openForm("#advanced");
  fireEvent.change(task("Rolling summary").getByRole("combobox", { name: "Rolling summary preset override" }),
                   { target: { value: PRESET_CLEAR } });
  fireEvent.click(form().getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.putInferenceSettings).toHaveBeenCalledTimes(1));
  expect(putBody()[0]).toEqual({ routes: { summary: { preset: PRESET_CLEAR } } });
});

test("a pin with no model holds Save", async () => {
  await openForm("#advanced");
  fireEvent.change(task("Rolling summary").getByRole("combobox", { name: "Rolling summary use" }),
                   { target: { value: "model" } });
  expect(form().getByRole("button", { name: "Save" })).toBeDisabled();
  fireEvent.change(task("Rolling summary").getByRole("combobox", { name: "Rolling summary pinned provider" }),
                   { target: { value: "realm" } });
  expect(form().getByRole("button", { name: "Save" })).toBeDisabled();
});

test("each task keeps what its detail page said: vision, a dropped fallback, the decide note", async () => {
  CAPS["vendor/eye"].vision = "unknown";
  (api.getInferenceSettings as any).mockResolvedValue(settings({ routes: [
    ROUTES[0],
    { ...ROUTES[1], fallback_problem: "Realm Local has no key set" },
    { ...ROUTES[2], resolves: resolved({ model: "vendor/eye" }), inherits: resolved({ model: "vendor/eye" }) },
    { ...ROUTES[3], decision_mode: "structured", decides_natively: "no" },
  ] }));
  open("/models/edit#advanced");
  await main().findByRole("form", { name: "Edit models" });
  expect(await task("Image descriptions").findByText(
    "This route sends images; the chosen model is unverified for vision.")).toBeInTheDocument();
  expect(task("Rolling summary").getByText(/cannot be sent \(Realm Local has no key set\)/))
    .toBeInTheDocument();
  expect(task("Scene break").getByText("No native decision API; structured generation will be used."))
    .toBeInTheDocument();
  expect(task("Image descriptions").getByText(/Also needs: vision/)).toBeInTheDocument();
});

test("a pinned model is warned of when it is unverified for the task's images", async () => {
  const pinned = { ...ROUTES[2], use: "model", pin: sel("saltmarch", "vendor/eye", "") };
  (api.getInferenceSettings as any).mockResolvedValue(settings({
    routes: [ROUTES[0], ROUTES[1], pinned, ROUTES[3]] }));
  open("/models/edit#advanced");
  await main().findByRole("form", { name: "Edit models" });
  expect(await task("Image descriptions").findByText(
    "This route sends images; the chosen model is unverified for vision.")).toBeInTheDocument();
});

test("the section counts what this form would override", async () => {
  await openForm("#advanced");
  expect(main().getByText("Advanced: per-task overrides (0 active)")).toBeInTheDocument();
  fireEvent.change(task("Rolling summary").getByRole("combobox", { name: "Rolling summary use" }),
                   { target: { value: "primary" } });
  expect(main().getByText("Advanced: per-task overrides (1 active)")).toBeInTheDocument();
});

test("a settings refresh while the form is open does not make an untouched role look moved", async () => {
  await openForm("#advanced");
  expect(api.getInferenceSettings).toHaveBeenCalledTimes(1);
  // Another tab (or the migration poll) changed what Primary stores.
  const refreshed = settings();
  refreshed.roles.primary = { ...refreshed.roles.primary, stored: sel("realm", "vendor/other", "tight") };
  (api.getInferenceSettings as any).mockResolvedValue(refreshed);
  const { configChanged } = await import("../appEvents");
  act(() => { configChanged(); });
  await waitFor(() => expect(api.getInferenceSettings).toHaveBeenCalledTimes(2));
  fireEvent.change(task("Rolling summary").getByRole("combobox", { name: "Rolling summary use" }),
                   { target: { value: "fast" } });
  fireEvent.click(form().getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.putInferenceSettings).toHaveBeenCalledTimes(1));
  expect(putBody()[0]).toEqual({ routes: { summary: { use: "fast" } } });
});

test("a folded What this sends asks the server nothing until it is opened", async () => {
  await openSummary();
  await waitFor(() => expect(row("Primary").getByText("working")).toBeInTheDocument());
  expect(api.previewControls).not.toHaveBeenCalled();
  fireEvent.click(row("Primary").getByText("What this sends"));
  await waitFor(() => expect(api.previewControls).toHaveBeenCalledTimes(1));
  cleanup();
  vi.mocked(api.previewControls).mockClear();
  await openForm("#advanced");
  expect(api.previewControls).not.toHaveBeenCalled();
  fireEvent.click(task("Rolling summary").getByText("What this sends"));
  await waitFor(() => expect(api.previewControls).toHaveBeenCalledTimes(1));
});

test("a task whose draft moved says it is checked on save, not what the saved one said", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({ routes: [
    ROUTES[0],
    { ...ROUTES[1], problem: "The summary model is gone.", fallback_problem: "Realm Local has no key set" },
    ROUTES[2], ROUTES[3],
  ] }));
  await openForm("#advanced");
  expect(task("Rolling summary").getByText("The summary model is gone.")).toBeInTheDocument();
  expect(task("Rolling summary").queryByText("Checked when you save.")).toBeNull();
  fireEvent.change(task("Rolling summary").getByRole("combobox", { name: "Rolling summary use" }),
                   { target: { value: "primary" } });
  expect(task("Rolling summary").queryByText("The summary model is gone.")).toBeNull();
  expect(task("Rolling summary").queryByText(/Realm Local has no key set/)).toBeNull();
  expect(task("Rolling summary").queryByText("What this sends")).toBeNull();
  expect(task("Rolling summary").getByText("Checked when you save.")).toBeInTheDocument();
});

test("a role whose draft moved says it is checked on save, not what the saved one said", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({ roles: {
    ...settings().roles,
    fast: card({ stored: sel("saltmarch", "vendor/embed"), problem: "vendor/embed cannot generate text.",
                 fallback: sel("realm", "vendor/m"), fallback_problem: "Realm Local has no key set" }),
  } }));
  await openForm();
  expect(roleBox("Fast").getByText("vendor/embed cannot generate text.")).toBeInTheDocument();
  pick("Fast provider", "realm");
  expect(roleBox("Fast").queryByText("vendor/embed cannot generate text.")).toBeNull();
  expect(roleBox("Fast").getByText("Checked when you save.")).toBeInTheDocument();
  // The fallback's words were about the saved primary too.
  expect(roleBox("Fast").queryByText(/Realm Local has no key set/)).toBeNull();
});

test("a pin on a route that sends images asks the provider for generate and vision", async () => {
  await openForm("#advanced");
  fireEvent.change(task("Image descriptions").getByRole("combobox", { name: "Image descriptions use" }),
                   { target: { value: "model" } });
  vi.mocked(api.readConnectionCapabilities).mockClear();
  fireEvent.change(task("Image descriptions").getByRole("combobox",
                   { name: "Image descriptions pinned provider" }), { target: { value: "realm" } });
  await waitFor(() => {
    expect(api.readConnectionCapabilities).toHaveBeenCalledWith("realm", "generate");
    expect(api.readConnectionCapabilities).toHaveBeenCalledWith("realm", "vision");
  });
});
