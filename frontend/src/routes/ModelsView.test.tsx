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

void putBody; // the edit form tests (Task 6) read it
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
