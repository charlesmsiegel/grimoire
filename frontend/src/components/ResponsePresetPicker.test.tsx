import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ResponsePresetPicker } from "./ResponsePresetPicker";
import { api, STYLE_CLEAR } from "../api/client";

vi.mock("../api/client");

const PRESETS = [{ id: "terse", name: "Terse", built_in: true },
                 { id: "cinematic", name: "Cinematic", built_in: true }];

beforeEach(() => {
  // The brief's test bodies (queries/assertions) are authoritative and left
  // verbatim; this reset is added because without it, call counts leak across
  // "it" blocks in file order (e.g. "not.toHaveBeenCalled()" in the global-scope
  // test would see calls left over from the earlier scene-scope tests).
  vi.clearAllMocks();
  (api.listResponsePresets as any).mockResolvedValue(PRESETS);
  (api.getSceneResponse as any).mockResolvedValue({
    response_preset: "terse", style_id: "", length_reply_words: "",
    length_blocks: "", length_paragraphs: "", length_speakers: "3",
    length_blocks_per_speaker: "",
    effective: { style_id: "", reply_words: 150, blocks: 3, paragraphs: 1,
                 speakers: 3, blocks_per_speaker: 1 },
    provenance: { reply_words: { scope: "scene" }, speakers: { scope: "scene" } },
  });
  (api.setSceneResponse as any).mockResolvedValue({ ok: true });
});

it("shows the resolved preset and effective values", async () => {
  render(<ResponsePresetPicker scope="scene" cid="run" sid="s1" />);
  expect(await screen.findByLabelText("Response preset")).toHaveValue("terse");
  expect(await screen.findByText(/150 words/)).toBeInTheDocument();
});

it("shows an inherited value as a placeholder, not a value", async () => {
  render(<ResponsePresetPicker scope="scene" cid="run" sid="s1" />);
  await userEvent.click(await screen.findByText("Overrides"));
  const words = screen.getByLabelText("Target words per reply");
  expect(words).toHaveValue(null);
  expect(words).toHaveAttribute("placeholder", "150");
});

it("saves a preset change", async () => {
  render(<ResponsePresetPicker scope="scene" cid="run" sid="s1" />);
  await userEvent.selectOptions(await screen.findByLabelText("Response preset"), "cinematic");
  await waitFor(() => expect(api.setSceneResponse).toHaveBeenCalledWith(
    "run", "s1", expect.objectContaining({ response_preset: "cinematic" })));
});

it("uses the global endpoints when scope is global", async () => {
  (api.getGlobalResponse as any).mockResolvedValue({
    response_preset: "brisk", style_id: "gothic-horror",
    length_reply_words: "", length_blocks: "", length_paragraphs: "",
    length_speakers: "", length_blocks_per_speaker: "",
    effective: { style_id: "gothic-horror", reply_words: 300, blocks: 4,
                 paragraphs: 2, speakers: 3, blocks_per_speaker: 1 },
    provenance: { reply_words: { scope: "global" } },
  });
  render(<ResponsePresetPicker scope="global" />);
  expect(await screen.findByLabelText("Response preset")).toHaveValue("brisk");
  expect(api.getSceneResponse).not.toHaveBeenCalled();
  await userEvent.selectOptions(screen.getByLabelText("Response preset"), "cinematic");
  await waitFor(() => expect(api.setGlobalResponse).toHaveBeenCalledWith(
    expect.objectContaining({ response_preset: "cinematic" })));
});

it("saves the currently-resolved values as a new preset and selects it at this scope", async () => {
  // The resolved style here is empty — "no style at all". Saving that as ""
  // would mean "no opinion", so applying the preset under a campaign that DOES
  // have a style would resurrect it instead of reproducing what was saved.
  const originalPrompt = window.prompt;
  window.prompt = () => "Slow Burn";
  (api.createResponsePreset as any).mockResolvedValue({ id: "slow-burn" });
  render(<ResponsePresetPicker scope="scene" cid="run" sid="s1" />);
  await userEvent.click(await screen.findByText("Overrides"));
  await userEvent.click(screen.getByRole("button", { name: "Save as preset…" }));
  await waitFor(() => expect(api.createResponsePreset).toHaveBeenCalledWith({
    name: "Slow Burn", style_id: STYLE_CLEAR,
    knobs: { reply_words: 150, blocks: 3, paragraphs: 1, speakers: 3, blocks_per_speaker: 1 },
  }));
  await waitFor(() => expect(api.setSceneResponse).toHaveBeenCalledWith(
    "run", "s1", expect.objectContaining({ response_preset: "slow-burn" })));
  window.prompt = originalPrompt;
});

it("saves a resolved style as that style, not as the clear sentinel", async () => {
  const originalPrompt = window.prompt;
  window.prompt = () => "Slow Burn";
  (api.createResponsePreset as any).mockResolvedValue({ id: "slow-burn" });
  (api.getSceneResponse as any).mockResolvedValue({
    response_preset: "terse", style_id: "", length_reply_words: "",
    length_blocks: "", length_paragraphs: "", length_speakers: "",
    length_blocks_per_speaker: "",
    effective: { style_id: "gothic-horror", reply_words: 150, blocks: 3,
                 paragraphs: 1, speakers: 3, blocks_per_speaker: 1 },
    provenance: { style_id: { scope: "campaign" } },
  });
  render(<ResponsePresetPicker scope="scene" cid="run" sid="s1" />);
  await userEvent.click(await screen.findByText("Overrides"));
  await userEvent.click(screen.getByRole("button", { name: "Save as preset…" }));
  await waitFor(() => expect(api.createResponsePreset).toHaveBeenCalledWith(
    expect.objectContaining({ style_id: "gothic-horror" })));
  window.prompt = originalPrompt;
});

it("does nothing when the Save as preset… prompt is cancelled", async () => {
  const originalPrompt = window.prompt;
  window.prompt = () => null;
  render(<ResponsePresetPicker scope="scene" cid="run" sid="s1" />);
  await userEvent.click(await screen.findByText("Overrides"));
  await userEvent.click(screen.getByRole("button", { name: "Save as preset…" }));
  expect(api.createResponsePreset).not.toHaveBeenCalled();
  window.prompt = originalPrompt;
});

it("saves a single knob override without leaving the preset", async () => {
  render(<ResponsePresetPicker scope="scene" cid="run" sid="s1" />);
  await userEvent.click(await screen.findByText("Overrides"));
  await userEvent.type(screen.getByLabelText("Max speaking characters"), "2");
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.setSceneResponse).toHaveBeenCalledWith(
    "run", "s1", expect.objectContaining({ response_preset: "terse",
                                           length_speakers: "2" })));
});

it("offers an explicit style clear distinct from inherit", async () => {
  // The tri-state: "" = no opinion (inherit), STYLE_CLEAR = explicitly clear
  // the inherited style. Without an option for the sentinel it is unreachable.
  // A user style whose id is literally "none" is an ordinary option: the
  // sentinel is U+2063-prefixed precisely so the two cannot collide.
  (api.listStyles as any).mockResolvedValue([
    { id: "none", name: "None", description: "", tags: [], built_in: false },
    { id: "gothic-horror", name: "Gothic Horror", description: "", tags: [], built_in: true },
  ]);
  render(<ResponsePresetPicker scope="scene" cid="run" sid="s1" />);
  await userEvent.click(await screen.findByText("Overrides"));
  const style = screen.getByLabelText("Style");
  expect([...style.querySelectorAll("option")].map((o) => (o as HTMLOptionElement).value))
    .toEqual(["", STYLE_CLEAR, "none", "gothic-horror"]);
  await userEvent.selectOptions(style, STYLE_CLEAR);
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.setSceneResponse).toHaveBeenCalledWith(
    "run", "s1", expect.objectContaining({ style_id: STYLE_CLEAR })));
});

it("notifies the host after a successful write so resolved surfaces can refresh", async () => {
  // The composer chip renders the RESOLVED bundle, which this picker can
  // change; without the callback it keeps advertising the previous setting.
  const onChanged = vi.fn();
  render(<ResponsePresetPicker scope="scene" cid="run" sid="s1" onChanged={onChanged} />);
  await userEvent.selectOptions(await screen.findByLabelText("Response preset"), "cinematic");
  await waitFor(() => expect(onChanged).toHaveBeenCalled());
});
