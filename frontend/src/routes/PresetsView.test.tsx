import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { api, PRESET_CLEAR } from "../api/client";
import { decodeStop, encodeStop } from "../components/presets/presetDraft";
import PresetsView from "./PresetsView";

vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, api: {
    listSamplerPresets: vi.fn(), createSamplerPreset: vi.fn(), updateSamplerPreset: vi.fn(),
    deleteSamplerPreset: vi.fn(), importSamplerPreset: vi.fn(), getInferenceSettings: vi.fn(),
    previewControls: vi.fn(), readConnectionCapabilities: vi.fn(),
    previewModelTest: vi.fn(), runModelTest: vi.fn(),
  } };
});

const TABLE = [
  { name: "temperature", label: "Temperature", kind: "float", min: 0, max: 5 },
  { name: "top_k", label: "Top-k", kind: "int", min: 0, max: 1000 },
  { name: "max_tokens", label: "Max tokens", kind: "int", min: 1, max: 200000 },
  { name: "stop", label: "Stop strings", kind: "stop", max_entries: 16, max_chars: 200 },
  { name: "reasoning_effort", label: "Reasoning effort", kind: "choice",
    choices: ["off", "low", "medium", "high"] },
];
const BALANCED = { id: "balanced", name: "Balanced", notes: "", source: "manual",
                   params: { temperature: 0.7 } };
const TIGHT = { id: "tight", name: "Tight", notes: "", source: "manual", params: {} };
const WARM = { id: "warm", name: "Warm", params: { temperature: 1.1, stop: ["\nYou:"] },
               notes: "For **prose**.", source: "" };
const PRESETS = [BALANCED, TIGHT, WARM];

const sel = (provider = "", model = "", preset = "") => ({ provider, model, preset });
const card = (over = {}) => ({ stored: sel(), fallback: sel(), resolves: null, inherits: null,
  problem: null, fallback_missing: [], fallback_problem: null, decision_mode: "",
  decides_natively: "unknown", rate: null, ...over });
const route = (key: string, label: string, over = {}) => ({ key, label, hint: "", tasks: [],
  operation: "generate", default_role: "fast", requires: [], campaign_scoped: true, use: "",
  pin: sel(), preset: "", resolves: null, inherits: null, problem: null, fallback_missing: [],
  fallback_problem: null, decision_mode: "", decides_natively: "unknown", role: "fast",
  uses: "fast", rate: null, ...over });
function settings(over = {}) {
  return { format: "2", newer: false, migration: { state: "done", reason: "", skipped: [] },
    roles: { primary: card({ stored: sel("saltmarch", "vendor/m", "balanced") }),
             fast: card({ fallback: sel("realm", "vendor/m", "balanced") }),
             decision: card() },
    routes: [route("summary", "Rolling summary", { preset: "balanced" }),
             route("scene break", "Scene break", { use: "model", pin: sel("realm", "vendor/m", "balanced") })],
    providers: [{ id: "saltmarch", name: "Saltmarch Router", kind: "openrouter",
                  preset: "openrouter", usable: true, problem: null }],
    presets: PRESETS.map(({ id, name }) => ({ id, name })),
    preset_clear: PRESET_CLEAR, retirement_notes: [], ...over } as never;
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.listSamplerPresets).mockResolvedValue({ presets: PRESETS, params: TABLE } as never);
  vi.mocked(api.getInferenceSettings).mockResolvedValue(settings());
  vi.mocked(api.previewControls).mockResolvedValue({ requested: {}, effective: {}, controls: {} });
  vi.mocked(api.updateSamplerPreset).mockImplementation(
    (async (id: string, b: object) => ({ ...b, id, source: "manual" })) as never);
  vi.mocked(api.createSamplerPreset).mockImplementation(
    (async (b: object) => ({ ...b, id: "new-one", source: "manual" })) as never);
  vi.mocked(api.deleteSamplerPreset).mockResolvedValue({} as never);
});

function Where() { const l = useLocation(); return <div data-testid="where">{l.pathname + l.hash}</div>; }
function open(at: string) {
  return render(
    <MemoryRouter initialEntries={[at]}>
      <Where />
      <Routes>
        <Route path="/presets" element={<PresetsView />} />
        <Route path="/presets/new" element={<PresetsView />} />
        <Route path="/presets/import" element={<PresetsView />} />
        <Route path="/presets/:id" element={<PresetsView />} />
      </Routes>
    </MemoryRouter>);
}
const column = () => within(screen.getByRole("complementary", { name: "Presets" }));
const main = () => within(screen.getByRole("main"));

/** Opens Balanced, then clicks Warm in the column, as the old test clicked its rail row. */
async function openWarm() {
  open("/presets/balanced");
  fireEvent.click(await column().findByRole("link", { name: "Warm" }));
  return main().findByRole("heading", { name: "Warm" });
}

// ---- ported from SamplerPresetEditor.test.tsx ------------------------------

test("clicking a row shows the read-only view with its sidebar", async () => {
  const heading = await openWarm();
  const view = heading.closest(".detail-view") as HTMLElement;
  expect(within(view).getByText("Temperature")).toBeInTheDocument();
  expect(within(view).getByText("1.1")).toBeInTheDocument();
  expect(within(view).getByText("prose").tagName).toBe("STRONG");   // notes are markdown
  expect(view.querySelector("textarea")).toBeNull();
  expect(within(view).getByRole("button", { name: "Edit" })).toBeInTheDocument();
  expect(within(view).getByText("Made here")).toBeInTheDocument();
});

test("Edit reveals the form, prefilled, and Save sends only what is set", async () => {
  await openWarm();
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  expect(screen.getByLabelText<HTMLInputElement>("Temperature").value).toBe("1.1");
  expect(screen.getByLabelText<HTMLTextAreaElement>("Stop strings").value).toBe("\\nYou:");
  fireEvent.change(screen.getByLabelText("Top-k"), { target: { value: "40" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.updateSamplerPreset).toHaveBeenCalledWith("warm", {
    name: "Warm", notes: "For **prose**.",
    params: { temperature: 1.1, top_k: 40, stop: ["\nYou:"] } }));
});

test("+ New opens the form directly", async () => {
  open("/presets/balanced");
  fireEvent.click(await column().findByRole("button", { name: "+ New preset" }));
  expect(await screen.findByLabelText("Name")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Cold" } });
  fireEvent.change(screen.getByLabelText("Temperature"), { target: { value: "0.2" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.createSamplerPreset).toHaveBeenCalledWith({
    name: "Cold", notes: "", params: { temperature: 0.2 } }));
});

test("the max-tokens box warns what it caps", async () => {
  open("/presets/new");
  expect(await screen.findByText(/absorb and dossiers included/)).toBeInTheDocument();
  expect(screen.getByText(/count the model's thinking against this cap/)).toBeInTheDocument();
});

test("an import shows what mapped and what did not", async () => {
  vi.mocked(api.importSamplerPreset).mockResolvedValue({
    preset: { id: "shared", name: "Shared", params: { temperature: 0.7 }, notes: "",
              source: "sillytavern" },
    report: { mapped: [{ param: "temperature", from: "temp", value: 0.7 }],
              neutral: [{ param: "top_k", from: "top_k", value: 0 }],
              skipped: [{ param: "max_tokens", from: "genamt", value: 400, why: "caps every call" }],
              invalid: [], unmapped: ["mirostat_mode", "typical_p"], notes: [] },
  });
  vi.mocked(api.listSamplerPresets)
    .mockResolvedValueOnce({ presets: PRESETS, params: TABLE } as never)
    .mockResolvedValue({ presets: [...PRESETS, { id: "shared", name: "Shared",
      params: { temperature: 0.7 }, notes: "", source: "sillytavern" }], params: TABLE } as never);
  open("/presets/balanced");
  fireEvent.click(await column().findByRole("button", { name: "Import…" }));
  const file = new File([JSON.stringify({ temp: 0.7 })], "Shared.json",
                        { type: "application/json" });
  fireEvent.change(await screen.findByLabelText("Preset file"), { target: { files: [file] } });
  await waitFor(() =>
    expect(screen.getByLabelText<HTMLInputElement>("Save as").value).toBe("Shared"));
  fireEvent.click(screen.getByRole("button", { name: "Import" }));
  await waitFor(() => expect(api.importSamplerPreset).toHaveBeenCalledWith(
    { name: "Shared", data: { temp: 0.7 }, include_max_tokens: false }));
  const report = await screen.findByLabelText("Import report");
  expect(within(report).getByText(/temperature ← temp/)).toBeInTheDocument();
  expect(within(report).getByText(/mirostat_mode, typical_p/)).toBeInTheDocument();
  expect(within(report).getByText(/Skipped genamt/)).toBeInTheDocument();
  expect(screen.getByText("Imported from SillyTavern")).toBeInTheDocument();
  expect(screen.getByTestId("where")).toHaveTextContent("/presets/shared");
});

test("stop strings round-trip through their escaped form", () => {
  for (const s of ["\nYou:", "a\\nb", "\t#", "plain"]) expect(decodeStop(encodeStop(s))).toBe(s);
});

test("a slower read of an earlier file pick cannot replace the later one", async () => {
  // A FileReader whose loads the test finishes by hand, in any order.
  const readers: { result: string; onload: (() => void) | null; finish: () => void }[] = [];
  class HeldReader {
    result = "";
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    readAsText(f: File) {
      const me = { result: "", onload: null as (() => void) | null,
                   finish: () => { this.result = me.result; this.onload?.(); } };
      void f.name;
      me.result = f.name === "First.json" ? '{"temp": 0.1}' : '{"temp": 0.9}';
      readers.push(me);
    }
  }
  vi.stubGlobal("FileReader", HeldReader);
  try {
    open("/presets/import");
    const input = await screen.findByLabelText("Preset file");
    fireEvent.change(input, { target: { files: [new File(["x"], "First.json")] } });
    fireEvent.change(input, { target: { files: [new File(["y"], "Second.json")] } });
    readers[1].finish();   // the later pick lands first...
    readers[0].finish();   // ...and the earlier, slower one must be ignored
    await waitFor(() =>
      expect(screen.getByLabelText<HTMLInputElement>("Save as").value).toBe("Second"));
    fireEvent.click(screen.getByRole("button", { name: "Import" }));
    await waitFor(() => expect(api.importSamplerPreset).toHaveBeenCalledWith(
      expect.objectContaining({ data: { temp: 0.9 } })));
  } finally {
    vi.unstubAllGlobals();
  }
});

test("a read left over from a cancelled import cannot fill the next one", async () => {
  const readers: { finish: () => void }[] = [];
  class HeldReader {
    result = "";
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    readAsText(f: File) {
      readers.push({ finish: () => { this.result = '{"temp": 0.1}'; void f.name; this.onload?.(); } });
    }
  }
  vi.stubGlobal("FileReader", HeldReader);
  try {
    open("/presets/import");
    fireEvent.change(await screen.findByLabelText("Preset file"),
                     { target: { files: [new File(["x"], "Abandoned.json")] } });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.click(await column().findByRole("button", { name: "Import…" }));
    await screen.findByLabelText("Preset file");
    readers[0].finish();   // the abandoned read lands in the NEW session
    await waitFor(() => expect(screen.getByLabelText<HTMLInputElement>("Save as").value).toBe(""));
    expect(screen.getByRole("button", { name: "Import" })).toBeDisabled();
  } finally {
    vi.unstubAllGlobals();
  }
});

test("the form offers reasoning effort", async () => {
  vi.mocked(api.listSamplerPresets).mockResolvedValue({
    presets: [{ ...WARM, params: { ...WARM.params, reasoning_effort: "high" } }],
    params: TABLE } as never);
  open("/presets/warm");
  // The view shows it, under the server's label.
  const view = (await main().findByRole("heading", { name: "Warm" })).closest(".detail-view") as HTMLElement;
  expect(within(view).getByText("Reasoning effort")).toBeInTheDocument();
  expect(within(view).getByText("high")).toBeInTheDocument();
  // The form offers the server's choices, and blank is unset.
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  const box = screen.getByLabelText<HTMLSelectElement>("Reasoning effort");
  expect(box.tagName).toBe("SELECT");
  expect(box.value).toBe("high");
  expect([...box.options].map((o) => o.value)).toEqual(["", "off", "low", "medium", "high"]);
  fireEvent.change(box, { target: { value: "medium" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.updateSamplerPreset).toHaveBeenCalledWith("warm", {
    name: "Warm", notes: "For **prose**.",
    params: { temperature: 1.1, stop: ["\nYou:"], reasoning_effort: "medium" } }));
  // Blank sends nothing for it.
  fireEvent.click(await column().findByRole("button", { name: "+ New preset" }));
  fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "Cold" } });
  expect(screen.getByLabelText<HTMLSelectElement>("Reasoning effort").value).toBe("");
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.createSamplerPreset).toHaveBeenCalledWith({
    name: "Cold", notes: "", params: {} }));
});

test("the form offers max when the server lists it", async () => {
  const withMax = TABLE.map((row) => row.name === "reasoning_effort"
    ? { ...row, choices: ["off", "low", "medium", "high", "max"] } : row);
  vi.mocked(api.listSamplerPresets).mockResolvedValue({ presets: [WARM], params: withMax } as never);
  open("/presets/warm");
  fireEvent.click(await main().findByRole("button", { name: "Edit" }));
  const box = screen.getByLabelText<HTMLSelectElement>("Reasoning effort");
  expect([...box.options].map((o) => o.value)).toEqual(["", "off", "low", "medium", "high", "max"]);
  fireEvent.change(box, { target: { value: "max" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.updateSamplerPreset).toHaveBeenCalledWith("warm", {
    name: "Warm", notes: "For **prose**.",
    params: { temperature: 1.1, stop: ["\nYou:"], reasoning_effort: "max" } }));
});

test("a stored reasoning level the table does not offer is shown as itself", async () => {
  vi.mocked(api.listSamplerPresets).mockResolvedValue({
    presets: [{ ...WARM, params: { ...WARM.params, reasoning_effort: "max" } }],
    params: TABLE } as never);
  open("/presets/warm");
  fireEvent.click(await main().findByRole("button", { name: "Edit" }));
  const box = screen.getByLabelText<HTMLSelectElement>("Reasoning effort");
  expect(box.value).toBe("max");
  expect(screen.getByRole("option", { name: "max (not offered)" })).toBeDisabled();
});

test("Preview on… shows the server's controls for that model", async () => {
  vi.mocked(api.readConnectionCapabilities).mockResolvedValue({
    provider_preset: {}, need: "generate", hidden: [], reason: null,
    groups: { fits: [{ id: "vendor/mara", name: "Mara", reason: "", capabilities: {} }],
              unverified: [] },
  } as never);
  vi.mocked(api.previewControls).mockResolvedValue({
    requested: {}, effective: {},
    controls: {
      temperature: { state: "supported", wire: "temperature", why: "", source: "adapter" },
      reasoning_effort: { state: "translated", wire: "reasoning", why: "", source: "catalog" },
      min_p: { state: "unsupported", wire: null, why: "not on this endpoint", source: "adapter" },
    },
  } as never);
  await openWarm();
  const preview = await screen.findByRole("region", { name: "Preview on…" });
  fireEvent.change(within(preview).getByLabelText("Provider"), { target: { value: "saltmarch" } });
  fireEvent.click(await within(preview).findByRole("radio", { name: "Mara" }));
  // The picker asked what a turn needs, and the readout is the server's answer.
  expect(api.readConnectionCapabilities).toHaveBeenCalledWith("saltmarch", "generate");
  await waitFor(() => expect(api.previewControls).toHaveBeenCalledWith(
    { preset_id: "warm", provider: "saltmarch", model: "vendor/mara" }));
  const controls = await within(preview).findByRole("list", { name: "Controls" });
  expect(within(controls).getByText("Reasoning effort")).toBeInTheDocument();
  expect(within(controls).getByText("reasoning")).toBeInTheDocument();
  expect(within(controls).getByText(/not on this endpoint/)).toBeInTheDocument();
  expect(within(controls).queryByText("Temperature")).toBeNull();
});

test("a newer store disables editing", async () => {
  vi.mocked(api.getInferenceSettings).mockResolvedValue(settings({ newer: true }));
  open("/presets/balanced");
  expect(await screen.findByText(/upgraded by a newer Grimoire/)).toBeInTheDocument();
  // The write controls stay in view, disabled, as the old editor drew them.
  await waitFor(() => expect(column().getByRole("button", { name: "+ New preset" })).toBeDisabled());
  expect(column().getByRole("button", { name: "Import…" })).toBeDisabled();
  await waitFor(() => expect(main().getByRole("button", { name: "Edit" })).toBeDisabled());
  expect(main().getByRole("button", { name: "Delete" })).toBeDisabled();
  // Reading and previewing are not writes.
  expect(screen.getByRole("region", { name: "Preview on…" })).toBeInTheDocument();
});

// ---- new -------------------------------------------------------------------

test("a preset opens read-only, with everywhere it is used", async () => {
  open("/presets/balanced");
  expect(await main().findByRole("heading", { name: "Balanced" })).toBeInTheDocument();
  expect(main().queryByRole("spinbutton")).toBeNull();
  const used = within(await main().findByRole("list", { name: "Used by" }));
  expect(used.getByRole("link", { name: "Primary" })).toHaveAttribute("href", "/models/edit");
  expect(used.getByRole("link", { name: "Fast fallback" })).toHaveAttribute("href", "/models/edit");
  expect(used.getByRole("link", { name: "Rolling summary" }))
    .toHaveAttribute("href", "/models/edit#task-summary");
  expect(used.getByRole("link", { name: "Scene break (pinned model)" }))
    .toHaveAttribute("href", "/models/edit#task-scene%20break");
});

test("a preset nothing uses says so", async () => {
  open("/presets/tight");
  expect(await main().findByText("Nothing uses this preset yet.")).toBeInTheDocument();
});

test("Edit opens the form; Save returns to the view", async () => {
  open("/presets/balanced");
  fireEvent.click(await main().findByRole("button", { name: "Edit" }));
  fireEvent.click(main().getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.updateSamplerPreset).toHaveBeenCalledWith("balanced", expect.anything()));
  expect(await main().findByRole("button", { name: "Edit" })).toBeInTheDocument();
});

test("+ New preset goes straight to the form", async () => {
  open("/presets/balanced");
  fireEvent.click(await column().findByRole("button", { name: "+ New preset" }));
  // Still an address: the form is /presets/new, so a reload lands back on it.
  expect(screen.getByTestId("where")).toHaveTextContent(/^\/presets\/new$/);
  expect(await main().findByRole("textbox", { name: "Name" })).toHaveValue("");
});

test("Import… opens the import form", async () => {
  open("/presets/import");
  expect(await main().findByRole("heading", { name: "Import a SillyTavern preset" })).toBeInTheDocument();
});

test("/presets opens the first preset; an empty library says so", async () => {
  open("/presets");
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/presets/balanced"));
});

test("an empty library offers + New preset", async () => {
  vi.mocked(api.listSamplerPresets).mockResolvedValue({ presets: [], params: TABLE } as never);
  open("/presets");
  expect(await main().findByText("No presets yet.")).toBeInTheDocument();
  expect(column().getByRole("button", { name: "+ New preset" })).toBeEnabled();
});

test("Delete asks, deletes, and leaves for the list", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  open("/presets/tight");
  fireEvent.click(await main().findByRole("button", { name: "Delete" }));
  await waitFor(() => expect(api.deleteSamplerPreset).toHaveBeenCalledWith("tight"));
});

test("the page says what a preset is and what is dropped", async () => {
  open("/presets/balanced");
  expect(await main().findByText(/A preset sets only what it\s+names/)).toBeInTheDocument();
  expect(main().getByText(/what cannot be sent is dropped/)).toBeInTheDocument();
});

test("the column is the Inference group, Presets current", async () => {
  open("/presets/balanced");
  const nav = within(await screen.findByRole("complementary", { name: "Presets" }));
  expect(nav.getByRole("link", { name: "Presets" })).toHaveAttribute("aria-current", "page");
});

test("Used by makes no claim while the settings are still being read", async () => {
  vi.mocked(api.getInferenceSettings).mockReturnValue(new Promise(() => {}));
  open("/presets/tight");
  expect(await main().findByRole("heading", { name: "Tight" })).toBeInTheDocument();
  expect(main().queryByText("Nothing uses this preset yet.")).toBeNull();
  expect(main().getByText("Reading…")).toBeInTheDocument();
});

test("Used by makes no claim when the settings could not be read", async () => {
  vi.mocked(api.getInferenceSettings).mockRejectedValue(new Error("offline"));
  open("/presets/tight");
  expect(await main().findByRole("heading", { name: "Tight" })).toBeInTheDocument();
  expect(main().queryByText("Nothing uses this preset yet.")).toBeNull();
  expect(main().queryByText("Reading…")).toBeNull();
});

test("a read from an import left through the column cannot fail onto a preset", async () => {
  const readers: { fail: () => void }[] = [];
  class HeldReader {
    result = "";
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    readAsText(f: File) { void f.name; readers.push({ fail: () => { this.onerror?.(); } }); }
  }
  vi.stubGlobal("FileReader", HeldReader);
  try {
    open("/presets/import");
    fireEvent.change(await screen.findByLabelText("Preset file"),
                     { target: { files: [new File(["x"], "Abandoned.json")] } });
    fireEvent.click(column().getByRole("link", { name: "Balanced" }));
    await main().findByRole("heading", { name: "Balanced" });
    readers[0].fail();   // the abandoned read fails while Balanced is open
    await waitFor(() => expect(main().getByRole("heading", { name: "Balanced" })).toBeInTheDocument());
    expect(screen.queryByText(/Could not read Abandoned.json/)).toBeNull();
  } finally {
    vi.unstubAllGlobals();
  }
});
