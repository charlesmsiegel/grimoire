import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { SamplerPresetEditor, decodeStop, encodeStop } from "./SamplerPresetEditor";
import { api } from "../api/client";

vi.mock("../api/client", async () => ({
  ...(await vi.importActual<typeof import("../api/client")>("../api/client")),
  api: {
    listSamplerPresets: vi.fn(), createSamplerPreset: vi.fn(), updateSamplerPreset: vi.fn(),
    deleteSamplerPreset: vi.fn(), importSamplerPreset: vi.fn(),
  },
}));

const TABLE = [
  { name: "temperature", label: "Temperature", kind: "float", min: 0, max: 5 },
  { name: "top_k", label: "Top-k", kind: "int", min: 0, max: 1000 },
  { name: "max_tokens", label: "Max tokens", kind: "int", min: 1, max: 200000 },
  { name: "stop", label: "Stop strings", kind: "stop", max_entries: 16, max_chars: 200 },
];
const WARM = { id: "warm", name: "Warm", params: { temperature: 1.1, stop: ["\nYou:"] },
               notes: "For **prose**.", source: "" };

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.listSamplerPresets).mockResolvedValue({ presets: [WARM], params: TABLE } as never);
  vi.mocked(api.createSamplerPreset).mockResolvedValue({ ...WARM, id: "new" });
  vi.mocked(api.updateSamplerPreset).mockResolvedValue(WARM);
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
