import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { SamplerPresetEditor, decodeStop, encodeStop } from "./SamplerPresetEditor";
import { api } from "../api/client";

vi.mock("../api/client", async () => ({
  ...(await vi.importActual<typeof import("../api/client")>("../api/client")),
  api: {
    listSamplerPresets: vi.fn(), createSamplerPreset: vi.fn(), updateSamplerPreset: vi.fn(),
    deleteSamplerPreset: vi.fn(), importSamplerPreset: vi.fn(),
    getInferenceSettings: vi.fn(), previewControls: vi.fn(),
    readConnectionCapabilities: vi.fn(), previewModelTest: vi.fn(), runModelTest: vi.fn(),
  },
}));

const TABLE = [
  { name: "temperature", label: "Temperature", kind: "float", min: 0, max: 5 },
  { name: "top_k", label: "Top-k", kind: "int", min: 0, max: 1000 },
  { name: "max_tokens", label: "Max tokens", kind: "int", min: 1, max: 200000 },
  { name: "stop", label: "Stop strings", kind: "stop", max_entries: 16, max_chars: 200 },
  { name: "reasoning_effort", label: "Reasoning effort", kind: "choice",
    choices: ["off", "low", "medium", "high"] },
];
const WARM = { id: "warm", name: "Warm", params: { temperature: 1.1, stop: ["\nYou:"] },
               notes: "For **prose**.", source: "" };

/** The inference view, as far as this editor reads it: whether the store is
 *  newer than this build, and the providers a preview can be asked on. */
function settings(over: { newer?: boolean } = {}) {
  return {
    format: "2", newer: false, roles: {}, routes: [], presets: [], preset_clear: "",
    migration: { state: "done", reason: "", skipped: [] },
    providers: [{ id: "saltmarch", name: "Saltmarch Router", kind: "openrouter",
                  preset: "openrouter", usable: true, problem: null }],
    ...over,
  } as never;
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.listSamplerPresets).mockResolvedValue({ presets: [WARM], params: TABLE } as never);
  vi.mocked(api.createSamplerPreset).mockResolvedValue({ ...WARM, id: "new" });
  vi.mocked(api.updateSamplerPreset).mockResolvedValue(WARM);
  vi.mocked(api.getInferenceSettings).mockResolvedValue(settings());
});

function rail() {
  return screen.getByText("+ New preset").closest(".editor-list") as HTMLElement;
}

test("clicking a row shows the read-only view with its sidebar", async () => {
  render(<SamplerPresetEditor />);
  fireEvent.click(await within(await waitFor(rail)).findByText("Warm"));
  const view = screen.getByRole("heading", { name: "Warm" }).closest(".detail-view") as HTMLElement;
  expect(within(view).getByText("Temperature")).toBeInTheDocument();
  expect(within(view).getByText("1.1")).toBeInTheDocument();
  expect(within(view).getByText("prose").tagName).toBe("STRONG");   // notes are markdown
  expect(view.querySelector("textarea")).toBeNull();
  expect(within(view).getByRole("button", { name: "Edit" })).toBeInTheDocument();
  expect(within(view).getByText("Made here")).toBeInTheDocument();
});

test("Edit reveals the form, prefilled, and Save sends only what is set", async () => {
  render(<SamplerPresetEditor />);
  fireEvent.click(await within(await waitFor(rail)).findByText("Warm"));
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
  render(<SamplerPresetEditor />);
  fireEvent.click(await screen.findByText("+ New preset"));
  expect(screen.getByLabelText("Name")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Cold" } });
  fireEvent.change(screen.getByLabelText("Temperature"), { target: { value: "0.2" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.createSamplerPreset).toHaveBeenCalledWith({
    name: "Cold", notes: "", params: { temperature: 0.2 } }));
});

test("the max-tokens box warns what it caps", async () => {
  render(<SamplerPresetEditor />);
  fireEvent.click(await screen.findByText("+ New preset"));
  expect(screen.getByText(/absorb and dossiers included/)).toBeInTheDocument();
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
    .mockResolvedValueOnce({ presets: [WARM], params: TABLE } as never)
    .mockResolvedValue({ presets: [WARM, { id: "shared", name: "Shared",
      params: { temperature: 0.7 }, notes: "", source: "sillytavern" }], params: TABLE } as never);
  render(<SamplerPresetEditor />);
  fireEvent.click(await screen.findByText("Import from SillyTavern…"));
  const file = new File([JSON.stringify({ temp: 0.7 })], "Shared.json",
                        { type: "application/json" });
  fireEvent.change(screen.getByLabelText("Preset file"), { target: { files: [file] } });
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
    render(<SamplerPresetEditor />);
    fireEvent.click(await screen.findByText("Import from SillyTavern…"));
    const input = screen.getByLabelText("Preset file");
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
    render(<SamplerPresetEditor />);
    fireEvent.click(await screen.findByText("Import from SillyTavern…"));
    fireEvent.change(screen.getByLabelText("Preset file"),
                     { target: { files: [new File(["x"], "Abandoned.json")] } });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.click(screen.getByText("Import from SillyTavern…"));
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
  render(<SamplerPresetEditor />);
  // The view shows it, under the server's label.
  fireEvent.click(await within(await waitFor(rail)).findByText("Warm"));
  const view = screen.getByRole("heading", { name: "Warm" }).closest(".detail-view") as HTMLElement;
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
  fireEvent.click(await screen.findByText("+ New preset"));
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Cold" } });
  expect(screen.getByLabelText<HTMLSelectElement>("Reasoning effort").value).toBe("");
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.createSamplerPreset).toHaveBeenCalledWith({
    name: "Cold", notes: "", params: {} }));
});

test("the form offers max when the server lists it", async () => {
  const withMax = TABLE.map((row) => row.name === "reasoning_effort"
    ? { ...row, choices: ["off", "low", "medium", "high", "max"] } : row);
  vi.mocked(api.listSamplerPresets).mockResolvedValue({ presets: [WARM], params: withMax } as never);
  render(<SamplerPresetEditor />);
  fireEvent.click(await within(await waitFor(rail)).findByText("Warm"));
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
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
  render(<SamplerPresetEditor />);
  fireEvent.click(await within(await waitFor(rail)).findByText("Warm"));
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
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
  render(<SamplerPresetEditor />);
  fireEvent.click(await within(await waitFor(rail)).findByText("Warm"));
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
  render(<SamplerPresetEditor />);
  expect(await screen.findByText(/upgraded by a newer Grimoire/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "+ New preset" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Import from SillyTavern…" })).toBeDisabled();
  fireEvent.click(within(rail()).getByText("Warm"));
  expect(screen.getByRole("button", { name: "Edit" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Delete" })).toBeDisabled();
  // Reading and previewing are not writes.
  expect(screen.getByRole("region", { name: "Preview on…" })).toBeInTheDocument();
});
