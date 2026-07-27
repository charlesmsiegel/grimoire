import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ResponsePresetsView from "./ResponsePresetsView";
import { api } from "../api/client";

vi.mock("../api/client");

const PRESETS = [
  { id: "terse", name: "Terse", built_in: true },
  { id: "brisk", name: "Brisk", built_in: true },
  { id: "standard", name: "Standard", built_in: true },
  { id: "cinematic", name: "Cinematic", built_in: true },
  { id: "slow-burn", name: "Slow Burn", built_in: false },
  { id: "broken", name: "Broken", built_in: false },
  { id: "sloppy", name: "Sloppy", built_in: false },
];

const LENGTH_PRESETS = {
  terse: { reply_words: 150, blocks: 3, paragraphs: 1, speakers: 2, blocks_per_speaker: 1 },
  brisk: { reply_words: 300, blocks: 4, paragraphs: 2, speakers: 3, blocks_per_speaker: 1 },
  standard: { reply_words: 550, blocks: 5, paragraphs: 2, speakers: 4, blocks_per_speaker: 2 },
  cinematic: { reply_words: 900, blocks: 7, paragraphs: 3, speakers: 5, blocks_per_speaker: 2 },
};

beforeEach(() => {
  // The brief's test bodies (queries/assertions) are authoritative and left
  // verbatim; this reset + fixture setup is added so state doesn't leak
  // across "it" blocks and the rail/form have data to work with.
  vi.clearAllMocks();
  (api.listResponsePresets as any).mockResolvedValue(PRESETS);
  (api.listStyles as any).mockResolvedValue([
    { id: "gothic-horror", name: "Gothic Horror", description: "Dread.", tags: [], built_in: true },
  ]);
  (api.listLengthPresets as any).mockResolvedValue(LENGTH_PRESETS);
  (api.getResponsePreset as any).mockImplementation((pid: string) => Promise.resolve(
    pid === "terse"
      ? { meta: { id: "terse", name: "Terse", description: "", built_in: true,
                   style_id: "", length_preset: "terse" },
          validity: { valid: true, issues: [] } }
      : { meta: { id: "slow-burn", name: "Slow Burn", description: "A patient pace.", built_in: false,
                   style_id: "", length_preset: "",
                   reply_words: "700", blocks: "6", paragraphs: "2",
                   speakers: "3", blocks_per_speaker: "2" },
          validity: { valid: true, issues: [] } }));
  (api.createResponsePreset as any).mockResolvedValue({ id: "new-preset" });
  (api.updateResponsePreset as any).mockResolvedValue({ ok: true });
  (api.deleteResponsePreset as any).mockResolvedValue({ ok: true });
  (api.duplicateResponsePreset as any).mockResolvedValue({ id: "terse-copy" });
  (api.responsePresetUsage as any).mockResolvedValue({ affected: [] });
});

it("clicking a row shows the read-only view", async () => {
  render(<ResponsePresetsView />);
  await userEvent.click(await screen.findByRole("button", { name: "Slow Burn" }));
  expect(await screen.findByRole("heading", { name: "Slow Burn" })).toBeInTheDocument();
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
});

it("Edit reveals the form", async () => {
  render(<ResponsePresetsView />);
  await userEvent.click(await screen.findByRole("button", { name: "Slow Burn" }));
  await userEvent.click(screen.getByRole("button", { name: "Edit" }));
  expect(screen.getByLabelText("Name")).toBeInTheDocument();
});

it("+ New preset opens the form directly", async () => {
  render(<ResponsePresetsView />);
  await userEvent.click(await screen.findByRole("button", { name: "+ New preset" }));
  expect(screen.getByLabelText("Name")).toBeInTheDocument();
});

it("a built-in offers Duplicate instead of Edit", async () => {
  render(<ResponsePresetsView />);
  await userEvent.click(await screen.findByRole("button", { name: "Terse" }));
  expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Duplicate" })).toBeInTheDocument();
});

it("flags a broken preset in the detail view", async () => {
  (api.getResponsePreset as any).mockResolvedValue({
    meta: { id: "broken", name: "Broken", built_in: false },
    validity: { valid: false, issues: ["unknown length preset 'nonesuch' — this preset supplies nothing"] },
  });
  render(<ResponsePresetsView />);
  await userEvent.click(await screen.findByRole("button", { name: "Broken" }));
  expect(await screen.findByText(/supplies nothing/)).toBeInTheDocument();
});

it("flags an ignored malformed knob without calling the preset broken", async () => {
  (api.getResponsePreset as any).mockResolvedValue({
    meta: { id: "sloppy", name: "Sloppy", built_in: false },
    validity: { valid: true, issues: ["reply_words: 'lots' is not a positive whole number — ignored"] },
  });
  render(<ResponsePresetsView />);
  await userEvent.click(await screen.findByRole("button", { name: "Sloppy" }));
  expect(await screen.findByText(/ignored/)).toBeInTheDocument();
});

it("delete confirmation lists affected scopes and their post-deletion values", async () => {
  (api.responsePresetUsage as any).mockResolvedValue({ affected: [
    { scope: "campaign", id: "saltmarch", name: "Saltmarch",
      before: { reply_words: 900 }, after: { reply_words: 550 } }] });
  render(<ResponsePresetsView />);
  await userEvent.click(await screen.findByRole("button", { name: "Slow Burn" }));
  await userEvent.click(screen.getByRole("button", { name: "Edit" }));
  await userEvent.click(screen.getByRole("button", { name: "Delete" }));
  expect(await screen.findByText(/Saltmarch/)).toBeInTheDocument();
  expect(screen.getByText(/550/)).toBeInTheDocument();
});

it("warns that the impact is unknown when the usage lookup fails", async () => {
  // A failed lookup is NOT an empty impact: rendering it as "nothing else
  // changes" is a false reassurance immediately before an irreversible delete.
  (api.responsePresetUsage as any).mockRejectedValue({ detail: "boom" });
  render(<ResponsePresetsView />);
  await userEvent.click(await screen.findByRole("button", { name: "Slow Burn" }));
  await userEvent.click(screen.getByRole("button", { name: "Edit" }));
  await userEvent.click(screen.getByRole("button", { name: "Delete" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(/could not be checked/i);
  expect(screen.queryByText(/nothing else changes/i)).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: /confirm delete/i })).toBeInTheDocument();
});

it("offers the `none` sentinel as a style option distinct from inherit", async () => {
  render(<ResponsePresetsView />);
  await userEvent.click(await screen.findByRole("button", { name: "Slow Burn" }));
  await userEvent.click(screen.getByRole("button", { name: "Edit" }));
  const select = screen.getByRole("combobox", { name: "Style" });
  await userEvent.selectOptions(select, "none");
  await userEvent.click(screen.getByRole("button", { name: /save preset/i }));
  expect(api.updateResponsePreset).toHaveBeenCalledWith(
    "slow-burn", expect.objectContaining({ style_id: "none" }));
});

it("surfaces an error when a preset row cannot be read", async () => {
  (api.getResponsePreset as any).mockRejectedValue({ detail: "preset file could not be read" });
  render(<ResponsePresetsView />);
  await userEvent.click(await screen.findByRole("button", { name: "Slow Burn" }));
  expect(await screen.findByText(/could not be read/)).toBeInTheDocument();
});
