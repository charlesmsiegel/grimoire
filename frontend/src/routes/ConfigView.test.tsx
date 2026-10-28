import { cleanup, render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { Link, MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import ConfigView from "./ConfigView";
import { ThemeProvider } from "../theme/ThemeProvider";
import { EMBEDDINGS_COPY } from "../components/inference/copy";

import { api } from "../api/client";
import type { Config, PromptSnapshot, SendImagesReach } from "../api/types";
import { fixture } from "../testkit/fixture";

vi.mock("../api/client", async () => (await import("../testkit/configViewMocks")).configApiMock());
vi.mock("../components/ResponseTargetsPicker", async () => (await import("../testkit/configViewMocks")).responseTargetsMock());
vi.mock("../components/ImageStoreCard", async () => (await import("../testkit/configViewMocks")).imageStoreMock());
vi.mock("../components/RegexRulesEditor", async () => (await import("../testkit/configViewMocks")).regexRulesMock());
import { cfg, dataDir, installConfigMocks, renderView, open, save } from "../testkit/configViewHarness";

beforeEach(installConfigMocks);

test("the column leads with Inference's three pages, then every section in three groups", async () => {
  renderView();
  await screen.findByRole("button", { name: /^Storage/ });
  const groups = [...document.querySelectorAll(".column-section-head .section-label")];
  expect(groups.map((g) => g.textContent))
    .toEqual(["Inference", "The install", "What the model sees", "What you see"]);
  expect(screen.getByRole("link", { name: /^Providers/ })).toHaveAttribute("href", "/providers");
  expect(screen.getByRole("link", { name: /^Models/ })).toHaveAttribute("href", "/models");
  expect(screen.getByRole("link", { name: /^Presets/ })).toHaveAttribute("href", "/presets");
  for (const label of [
    /^Storage/, /^Backups/, /^Logging/, /^Timeouts & retries/, /^First-run setup/, /^Context/,
    /^Prompt layout/, /^Scene tracker/, /^Play decisions/, /^System prompt/, /^Response targets/,
    /^Transcript/, /^Output processing/, /^While playing/, /^Appearance/,
  ]) {
    expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
  }
  for (const gone of [/^Models/, /^Presets/, /^Token rates/]) {
    expect(screen.queryByRole("button", { name: gone })).toBeNull();
  }
});

test("the Output processing section mounts the global rule editor", async () => {
  renderView();
  await open(/^Output processing/);
  expect(screen.getByTestId("regex-rules")).toHaveAttribute("data-scope", '{"kind":"global"}');
});

test("no legacy connection or routing controls remain", async () => {
  renderView();
  await open(/^Timeouts & retries/);
  for (const label of [/LLM connection/i, /Fallback connection/i, /Embeddings connection/i,
                       /Embedding model/i]) {
    expect(screen.queryByLabelText(label)).toBeNull();
  }
  expect(screen.queryByRole("button", { name: /test connection/i })).toBeNull();
  expect(screen.queryByRole("button", { name: /^Connection/ })).toBeNull();
  expect(screen.queryByRole("button", { name: /^Model routing/ })).toBeNull();
  expect(screen.queryByRole("button", { name: /^Embeddings/ })).toBeNull();
  expect(screen.queryByRole("link", { name: /connections/i })).toBeNull();
  // Nothing this page sends names a legacy inference key: at format 2 the
  // server refuses every one of them.
  fireEvent.change(screen.getByLabelText(/^retries$/i), { target: { value: "0" } });
  await open(/^Context/);
  fireEvent.change(screen.getByLabelText(/recalled entries/i), { target: { value: "4" } });
  save();
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith(
    { llm_retries: "0", semantic_recall_depth: "4" }));
});

test("the image store card is part of Storage and nothing else", async () => {
  renderView();
  expect(await screen.findByTestId("image-store-card")).toBeInTheDocument();

  await open(/^Context/);
  expect(screen.queryByTestId("image-store-card")).toBeNull();
});

test("main shows one section at a time", async () => {
  renderView();
  // Storage is what it opens on; nothing else is mounted beside it.
  expect(await screen.findByLabelText(/storage location/i)).toBeInTheDocument();
  expect(screen.queryByLabelText(/context budget/i)).toBeNull();

  await open(/^Context/);
  expect(screen.getByLabelText(/context budget/i)).toBeInTheDocument();
  expect(screen.queryByLabelText(/storage location/i)).toBeNull();
});

test("the theme control is pinned under the column and persists on pick", async () => {
  // The look is no longer one of this page's draft fields. Three controls offer
  // it now -- here, the first-run wizard, and the header toggle -- and a
  // deferred draft let this page's stale copy overwrite a choice made from one
  // of the others on the next unrelated Save. So: applied at once, because a
  // look you cannot see until you commit it is a control you cannot use, and
  // written at once, because a look that lasts only the session reads as the
  // app forgetting.
  renderView();
  fireEvent.click(await screen.findByText("DARK"));
  expect(document.documentElement.dataset.themeMode).toBe("dark");
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith({ theme: "dark" }));
  // ...and it is not an unsaved change, because it is not unsaved.
  await open(/^Transcript/);
  expect(screen.getByText("No unsaved changes")).toBeInTheDocument();
});

test("a theme the store refuses is rolled back rather than left on screen", async () => {
  // A look left applied after a failed write looks chosen for the session and
  // is gone at the next reload -- the same trade this page's Save already made
  // for it, now made in the one place every control shares.
  vi.mocked(api.putConfig).mockRejectedValueOnce({ detail: "nope" });
  renderView();
  await screen.findByText("LIGHT");
  fireEvent.click(screen.getByText("DARK"));
  // Applied first, so the pick can be seen...
  expect(document.documentElement.dataset.themeMode).toBe("dark");
  // ...then put back to the stored legacy `codex` choice, which resolves to light.
  await waitFor(() => expect(document.documentElement.dataset.themeMode).toBe("light"));
});

test("the stored theme survives the collapse: codex is not an unsaved change", async () => {
  renderView();
  // The store still holds `codex`; the picker shows LIGHT. Nothing has been
  // edited, so the count must not read the mapping as a pending edit.
  await screen.findByText("LIGHT");
  await open(/^Transcript/);
  expect(screen.getByText("No unsaved changes")).toBeInTheDocument();
});

test("a section that saves on its own shows the page's save bar only for edits elsewhere", async () => {
  // Response targets writes its own record behind its own Save. The bar's
  // Revert/Save could never act on it, and a disabled Save beside the
  // picker's live one was two save models on one screen.
  renderView();
  await open(/^Response targets/);
  expect(await screen.findByTestId("response-preset-picker")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /^save$/i })).toBeNull();
  expect(screen.queryByText(/unsaved change/i)).toBeNull();

  // An edit held by another section still has its bar, wherever you are,
  // and says where the edit is.
  await open(/^Transcript/);
  fireEvent.change(screen.getByLabelText(/your label/i), { target: { value: "Kestrel" } });
  await open(/^Response targets/);
  expect(screen.getByText("1 unsaved change in other sections")).toBeInTheDocument();
  save();
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith({ user_label: "Kestrel" }));
});

test("the column says a zero context budget is no ceiling, not zero tokens", async () => {
  renderView();
  const row = await screen.findByRole("button", { name: /^Context/ });
  expect(row).toHaveTextContent("no ceiling");
  expect(row).not.toHaveTextContent("0 tok");
});

test("editing a field marks the draft dirty and writes nothing", async () => {
  renderView();
  await open(/^Timeouts/);
  fireEvent.change(screen.getByLabelText(/no-reply timeout/i), { target: { value: "45" } });
  expect(api.putConfig).not.toHaveBeenCalled();
  expect(screen.getByText("1 unsaved change")).toBeInTheDocument();
  // …and the column says which section is holding it.
  expect(screen.getByRole("button", { name: /^Timeouts & retries unsaved/ })).toBeInTheDocument();
});

test("Save commits every dirty field, across sections, in one call", async () => {
  renderView();
  await open(/^Timeouts/);
  fireEvent.change(screen.getByLabelText(/no-reply timeout/i), { target: { value: "45" } });
  fireEvent.change(screen.getByLabelText(/absorb budget/i), { target: { value: "300" } });
  await open(/^Context/);
  fireEvent.change(screen.getByLabelText(/context budget/i), { target: { value: "32000" } });
  await open(/^Transcript/);
  fireEvent.change(screen.getByLabelText(/your label/i), { target: { value: "Kestrel" } });
  fireEvent.click(screen.getByLabelText(/color quoted/i));
  expect(screen.getByText("5 unsaved changes")).toBeInTheDocument();

  save();
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledTimes(1));
  // Exactly the dirty fields — a whole-form PUT would carry the other fourteen.
  expect(api.putConfig).toHaveBeenCalledWith({
    llm_timeout: "45", absorb_budget: "300", context_budget: "32000",
    quote_color: "on", user_label: "Kestrel",
  });
});

test("an edit made while the write is in flight is not swallowed by it", async () => {
  // Save disables its own buttons, not the fields. Adopting the response
  // wholesale would revert whatever was typed in the gap.
  let land: (c: Config) => void = () => {};
  vi.mocked(api.putConfig).mockReturnValue(new Promise((r) => { land = r; }));
  renderView();
  await open(/^Transcript/);
  fireEvent.change(screen.getByLabelText(/your label/i), { target: { value: "Kestrel" } });
  save();
  fireEvent.change(screen.getByLabelText(/narrator label/i), { target: { value: "The Loom" } });
  land(fixture({ ...cfg, user_label: "Kestrel" }));

  await waitFor(() => expect(screen.getByText("1 unsaved change")).toBeInTheDocument());
  expect(screen.getByLabelText(/your label/i)).toHaveValue("Kestrel");   // committed
  expect(screen.getByLabelText(/narrator label/i)).toHaveValue("The Loom");  // still pending
});

test("Revert discards every edit; the theme is not one of them", async () => {
  renderView();
  await open(/^Transcript/);
  fireEvent.change(await screen.findByLabelText(/your label/i), { target: { value: "Kestrel" } });
  expect(screen.getByText("1 unsaved change")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: /^revert$/i }));
  expect(api.putConfig).not.toHaveBeenCalled();
  expect(screen.getByText("No unsaved changes")).toBeInTheDocument();
  expect(screen.getByLabelText(/your label/i)).toHaveValue("You");
  // Revert has nothing to say about the look: it was written when it was
  // picked, so there is no preview left over to put back -- and undoing a
  // stored setting as a side effect of discarding unrelated edits would be a
  // surprise, not a revert.
});

test("saves the retry count", async () => {
  renderView();
  await open(/^Timeouts & retries/);
  const retries = screen.getByLabelText(/^retries$/i);
  expect((retries as HTMLInputElement).value).toBe("2");
  fireEvent.change(retries, { target: { value: "0" } });
  save();
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith({ llm_retries: "0" }));
});

test("saves the system prompt", async () => {
  renderView();
  await open(/^System prompt/);
  fireEvent.change(screen.getByLabelText(/system prompt/i), { target: { value: "Never speak for the PC." } });
  save();
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith(
    { system_prompt: "Never speak for the PC." }));
});

test("mounts the response targets picker for the global scope", async () => {
  renderView();
  await open(/^Response targets/);
  expect(await screen.findByTestId("response-preset-picker")).toBeInTheDocument();
  expect(screen.queryByLabelText("Scene response mode")).toBeNull();
});

test("moving the storage location still saves immediately", async () => {
  // The one exception to the one-Save rule, and deliberately so: the move
  // relocates the file Save writes to.
  vi.mocked(api.putDataDir).mockResolvedValue({ ...dataDir, data_dir: "/sync/grimoire", is_default: false, source: "custom" });
  renderView();
  const input = await screen.findByLabelText(/storage location/i);
  fireEvent.change(input, { target: { value: "/sync/grimoire" } });
  fireEvent.click(screen.getByRole("button", { name: /^move$/i }));
  await waitFor(() => expect(api.putDataDir).toHaveBeenCalledWith("/sync/grimoire"));
});

test("shows the stored timeouts", async () => {
  renderView();
  await open(/^Timeouts/);
  expect(screen.getByLabelText(/no-reply timeout/i)).toHaveValue("120");
  expect(screen.getByLabelText(/absorb budget/i)).toHaveValue("600");
  expect(screen.getByLabelText(/one-shot call ceiling/i)).toHaveValue("300");
});

test("edits the context budget, recalled-scene cap and kept turn prompts", async () => {
  renderView();
  await open(/^Context/);
  expect(screen.getByLabelText(/context budget/i)).toHaveValue("0");   // unbounded by default
  expect(screen.getByLabelText(/recalled scenes/i)).toHaveValue("3");
  expect(screen.getByLabelText(/kept turn prompts/i)).toHaveValue("50");
  expect(screen.getByLabelText(/named off-scene characters/i)).toHaveValue("40");
  fireEvent.change(screen.getByLabelText(/recalled scenes/i), { target: { value: "5" } });
  fireEvent.change(screen.getByLabelText(/kept turn prompts/i), { target: { value: "0" } });
  fireEvent.change(screen.getByLabelText(/named off-scene characters/i), { target: { value: "12" } });
  save();
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith(
    { archive_depth: "5", prompt_log_depth: "0", offscene_known_limit: "12" }));
});

test("edits the context scan depth", async () => {
  renderView();
  await open(/^Context/);
  expect(screen.getByLabelText(/context scan depth/i)).toHaveValue("6");
  fireEvent.change(screen.getByLabelText(/context scan depth/i), { target: { value: "16" } });
  save();
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith({ context_scan_depth: "16" }));
});

test("edits the lore recursion depth beside the scan depth", async () => {
  renderView();
  await open(/^Context/);
  const field = screen.getByLabelText(/lore recursion depth/i);
  expect(field).toHaveValue("0");
  expect(screen.getByText("0 = off, max 3")).toBeInTheDocument();
  // Beside scan depth: the next field in the context section's grid.
  const fields = Array.from(document.querySelectorAll(".config-fields input")).map((el) => el.id);
  expect(fields.indexOf("cfg-lore-recursion-depth"))
    .toBe(fields.indexOf("cfg-context-scan-depth") + 1);
  fireEvent.change(field, { target: { value: "2" } });
  save();
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith({ lore_recursion_depth: "2" }));
});

// ---- recall, inside Context ----------------------------------------------

test("recall depth and threshold stay, and save together", async () => {
  renderView();
  await open(/^Context/);
  expect(screen.getByLabelText(/recalled entries/i)).toHaveValue("0");
  expect(screen.getByLabelText(/similarity threshold/i)).toHaveValue("0.4");
  fireEvent.change(screen.getByLabelText(/recalled entries/i), { target: { value: "4" } });
  fireEvent.change(screen.getByLabelText(/similarity threshold/i), { target: { value: "0.55" } });
  save();
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith({
    semantic_recall_depth: "4", semantic_recall_threshold: "0.55",
  }));
});

test("the embeddings copy is a paragraph of its own", async () => {
  renderView();
  await open(/^Context/);
  // An exact, whole-string match: the sentence is its own paragraph.
  expect(screen.getByText(EMBEDDINGS_COPY).tagName).toBe("P");
});

test("the privacy copy points at the control that exists", async () => {
  // How to keep text on this machine, and how to stop embedding: both are
  // the Embedding role's, on Models, and nothing on this page is a
  // connection control or an endpoint field for the old words to mean.
  renderView();
  await open(/^Context/);
  expect(EMBEDDINGS_COPY)
    .toMatch(/Clear the Embedding role on the Models page to stop all embedding\.$/);
  const p = screen.getByText(/This sends text to the Embedding role's provider/).closest("p")!;
  expect(p.textContent)
    .toMatch(/Choose a local provider for the Embedding role to keep it on your machine\./);
  expect(within(p).getByRole("link", { name: "Embedding role" }))
    .toHaveAttribute("href", "/models/edit");
  const page = document.body.textContent;
  for (const gone of ["endpoint above", "Set the connection to Off", "LLM connection",
                      "local endpoint to keep"]) {
    expect(page).not.toContain(gone);
  }
});

test("the disclosure names every embedded payload", async () => {
  renderView();
  await open(/^Context/);
  const p = screen.getByText(/This sends text to the Embedding role's provider/).closest("p")!;
  for (const payload of [
    "recent scene text", "world info", "image descriptions", "search the library by meaning",
    "plot-thread and commitment summaries",
  ]) {
    expect(p.textContent).toContain(payload);
  }
  expect(p.textContent).not.toBe(EMBEDDINGS_COPY);
});

test("saves the rolling-summary cadence", async () => {
  renderView();
  await open(/^While playing/);
  expect(screen.getByLabelText(/summarize the scene every/i)).toHaveValue("10");
  fireEvent.change(screen.getByLabelText(/summarize the scene every/i), { target: { value: "4" } });
  save();
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith({ rolling_summary_every: "4" }));
});

test("saves the scene-break cadence, and 0 as the way to turn it off", async () => {
  renderView();
  await open(/^While playing/);
  expect(screen.getByLabelText(/scene-break check/i)).toHaveValue("20");
  fireEvent.change(screen.getByLabelText(/scene-break check/i), { target: { value: "0" } });
  save();
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith({ scene_break_every: "0" }));
});

test("saves the replay fork threshold (#80)", async () => {
  renderView();
  await open(/^While playing/);
  expect(screen.getByLabelText(/offer to fork before replaying/i)).toHaveValue("10");
  fireEvent.change(screen.getByLabelText(/offer to fork before replaying/i),
                   { target: { value: "4" } });
  save();
  await waitFor(() =>
    expect(api.putConfig).toHaveBeenCalledWith({ replay_fork_threshold: "4" }));
});

test("saves the checkpoint threshold the clock nudges at (#107)", async () => {
  renderView();
  await open(/^While playing/);
  expect(screen.getByLabelText(/offer a checkpoint before skipping/i)).toHaveValue("30");
  fireEvent.change(screen.getByLabelText(/offer a checkpoint before skipping/i),
                   { target: { value: "7" } });
  save();
  await waitFor(() =>
    expect(api.putConfig).toHaveBeenCalledWith({ advance_fork_threshold: "7" }));
});

// ---- the context budget bar ----

const snapshot = fixture<PromptSnapshot>({
  model: "m", total_tokens: 13_180, dropped_tokens: 0, budget_tokens: 32_000,
  sections: [
    { label: "Character descriptions", text: "", tokens: 2_700, tier: "lock-in", dropped: false, trimmed: 0 },
    { label: "Character state", text: "", tokens: 1_840, tier: "spotlight", dropped: false, trimmed: 0 },
    { label: "Conversation history", text: "", tokens: 3_900, tier: "history", dropped: false, trimmed: 0 },
    { label: "Earlier scenes", text: "", tokens: 1_040, tier: "archive", dropped: false, trimmed: 0 },
    // Rendered but left out by the packer: it was not sent, so it is not in
    // the stack — the verdict is where a drop is reported.
    { label: "Message examples", text: "", tokens: 900, tier: "background", dropped: true, trimmed: 0 },
  ],
});

function withLastPrompt() {
  vi.mocked(api.listCampaigns).mockResolvedValue(fixture([
    { id: "old-realm", name: "Realm", world: "w", created: "", updated: "2024-01-01", scenes: 2, last_scene: "", activity: "2024-01-01" },
    { id: "saltmarch", name: "Saltmarch", world: "w", created: "", updated: "2024-05-01", scenes: 11, last_scene: "", activity: "2024-06-02" },
  ]));
  vi.mocked(api.listScenes).mockResolvedValue([
    { id: "s11", title: "The long tide", model: "m", created: "", updated: "2024-06-02", date: "" },
  ]);
  vi.mocked(api.listScenePrompts).mockResolvedValue({
    entries: [{ id: "e9", scene: "s11", ts: "", model: "m", task: "chat", total_tokens: 13_180, dropped_tokens: 0, budget_tokens: 32_000 }],
  });
  vi.mocked(api.getScenePrompt).mockResolvedValue(snapshot);
}

test("draws the last prompt against the budget, and names whose it is", async () => {
  withLastPrompt();
  renderView();
  await open(/^Context/);

  // The most recently played campaign by `activity`, not by `updated`.
  expect(await screen.findByText("LAST TURN IN SALTMARCH, AGAINST THIS BUDGET")).toBeInTheDocument();
  expect(api.listScenePrompts).toHaveBeenCalledWith("saltmarch", "s11");
  expect(screen.getByText("≈ 13,180 / 32,000 · 41%")).toBeInTheDocument();
  expect(screen.getByText(/CHARACTERS 2,700/)).toBeInTheDocument();
  expect(screen.getByText(/STANDING FRAME 1,840/)).toBeInTheDocument();
  expect(screen.getByText(/CONVERSATION 3,900/)).toBeInTheDocument();
  expect(screen.getByText(/RECALLED 1,040/)).toBeInTheDocument();
  expect(screen.getByText("NOTHING DROPPED")).toBeInTheDocument();
});

test("the bar is the last TURN's, never a decision's", async () => {
  withLastPrompt();
  vi.mocked(api.listScenePrompts).mockResolvedValue({
    entries: [
      { id: "e10", scene: "s11", ts: "", model: "m", task: "scene-break", operation: "decide",
        total_tokens: 900, dropped_tokens: 0, budget_tokens: 32_000 },
      { id: "e9", scene: "s11", ts: "", model: "m", task: "chat", total_tokens: 13_180,
        dropped_tokens: 0, budget_tokens: 32_000 },
    ],
  });
  renderView();
  await open(/^Context/);
  expect(await screen.findByText("≈ 13,180 / 32,000 · 41%")).toBeInTheDocument();
  expect(api.getScenePrompt).toHaveBeenCalledWith("saltmarch", "s11", "e9");
  expect(api.getScenePrompt).not.toHaveBeenCalledWith("saltmarch", "s11", "e10");
});

test("the bar is only fetched by the section that shows it", async () => {
  withLastPrompt();
  renderView();
  await screen.findByLabelText(/storage location/i);
  expect(api.listCampaigns).not.toHaveBeenCalled();
  await open(/^Context/);
  await waitFor(() => expect(api.listCampaigns).toHaveBeenCalled());
});

test("reports what the packer actually dropped", async () => {
  withLastPrompt();
  vi.mocked(api.getScenePrompt).mockResolvedValue({ ...snapshot, dropped_tokens: 900 });
  renderView();
  await open(/^Context/);
  expect(await screen.findByText("900 TOKENS DROPPED")).toBeInTheDocument();
});

test("no stored prompt, no bar — the numbers are never invented", async () => {
  renderView();                                  // listCampaigns answers []
  await open(/^Context/);
  await waitFor(() => expect(api.listCampaigns).toHaveBeenCalled());
  expect(screen.queryByText(/AGAINST THIS BUDGET/)).toBeNull();
  expect(screen.queryByText(/NOTHING DROPPED/)).toBeNull();
});

test("the active-speaker toggle lives in Context and saves as on/off", async () => {
  vi.mocked(api.putConfig).mockResolvedValue({ ...cfg, speaker_turn_taking: "on" });
  renderView();
  await open(/^Context/);
  fireEvent.click(await screen.findByRole("checkbox", { name: /name an active speaker/i }));
  save();
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith(
    expect.objectContaining({ speaker_turn_taking: "on" })));
});

test("tracker settings toggle and save", async () => {
  vi.mocked(api.putConfig).mockResolvedValue({ ...cfg, tracker: "off" });
  renderView();
  await open(/^Scene tracker/);
  fireEvent.click(await screen.findByRole("checkbox",
    { name: "Track each character's state after every post" }));
  save();
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith(
    expect.objectContaining({ tracker: "off" })));
});

test("the play decisions save each switch, the speaker draw on by default", async () => {
  vi.mocked(api.getConfig).mockResolvedValue({
    ...cfg, decide_speaker_sampling: "on", decide_turn_plan: "off", decide_play_tool: "off" });
  vi.mocked(api.getPlayDecisionsInfo).mockResolvedValue({ pick_samples: false });
  vi.mocked(api.putConfig).mockResolvedValue(cfg);
  renderView();
  await open(/^Play decisions/);
  const draw = await screen.findByRole("checkbox", { name: /draw the next speaker/i });
  expect(draw).toBeChecked();
  expect(await screen.findByText(/always the most likely speaker/i)).toBeInTheDocument();
  fireEvent.click(draw);
  fireEvent.change(screen.getByLabelText("Turn decision before each reply"),
                   { target: { value: "plan" } });
  fireEvent.click(screen.getByRole("checkbox", { name: /ask one closed question/i }));
  save();
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith(expect.objectContaining({
    decide_speaker_sampling: "off", decide_turn_plan: "plan", decide_play_tool: "on" })));
});

test("the perception rider toggle saves as off, never blank", async () => {
  vi.mocked(api.getConfig).mockResolvedValue({ ...cfg, perception_rider: "on" });
  vi.mocked(api.putConfig).mockResolvedValue({ ...cfg, perception_rider: "off" });
  renderView();
  await open(/^Scene tracker/);
  fireEvent.click(await screen.findByRole("checkbox",
    { name: /ask each character to note what it heard or saw/i }));
  save();
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith(
    expect.objectContaining({ perception_rider: "off" })));
});

test("an unset tracker reads as checked, since the default is on", async () => {
  renderView();                                  // `cfg` carries neither key
  await open(/^Scene tracker/);
  expect(await screen.findByRole("checkbox",
    { name: "Track each character's state after every post" })).toBeChecked();
  expect(screen.getByRole("checkbox",
    { name: /ask each character to note what it heard or saw/i })).toBeChecked();
});

// ---- backups (#32) ---------------------------------------------------------

test("the backups row says off until the setting is on", async () => {
  renderView();
  expect(await screen.findByRole("button", { name: /^Backups off$/ })).toBeInTheDocument();

  await open(/^Backups/);
  await screen.findByText("No full backups yet.");     // let the panel's read settle
  fireEvent.click(screen.getByLabelText(/back up automatically/i));

  // The row follows the DRAFT, so the label agrees with the checkbox you are
  // looking at rather than with the file it has not been written to yet.
  expect(screen.getByRole("button", { name: /^Backups unsaved$/ })).toBeInTheDocument();
});

test("the backup settings save as one patch of only what changed", async () => {
  renderView();
  await open(/^Backups/);
  await screen.findByText("No full backups yet.");
  fireEvent.click(screen.getByLabelText(/back up automatically/i));
  fireEvent.change(screen.getByLabelText(/^every$/i), { target: { value: "6" } });
  fireEvent.change(screen.getByLabelText(/^keep$/i), { target: { value: "3" } });
  fireEvent.change(screen.getByLabelText(/^backup folder$/i), { target: { value: "/mnt/usb" } });

  expect(screen.getByText("4 unsaved changes")).toBeInTheDocument();
  save();

  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith({
    backup_enabled: "on", backup_interval_hours: "6",
    backup_keep: "3", backup_dir: "/mnt/usb",
  }));
});

test("the archives are listed, and Back up now writes one", async () => {
  vi.mocked(api.listBackups).mockResolvedValue({
    dir: "/home/u/.grimoire/backups",
    backups: [{ name: "grimoire-20260814T210000Z.zip", size: 2_097_152,
                created: "2026-08-14T21:00:00Z" }],
    image_backups: [],
  });
  vi.mocked(api.createBackup).mockResolvedValue({
    dir: "/home/u/.grimoire/backups", created: "grimoire-20260815T090000Z.zip",
    swept: [], retention_error: null,
    backups: [{ name: "grimoire-20260815T090000Z.zip", size: 2_097_152,
                created: "2026-08-15T09:00:00Z" }],
    image_backups: [],
  });
  renderView();
  await open(/^Backups/);

  expect(await screen.findByText("grimoire-20260814T210000Z.zip")).toBeInTheDocument();
  expect(screen.getByText(/2\.0 MB/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: /back up now/i }));

  expect(await screen.findByText(/Backed up to grimoire-20260815T090000Z\.zip/))
    .toBeInTheDocument();
  // The response IS the refreshed listing: no second read, and the row it
  // replaced is gone from the panel.
  expect(screen.getByText("grimoire-20260815T090000Z.zip")).toBeInTheDocument();
  expect(screen.queryByText("grimoire-20260814T210000Z.zip")).toBeNull();
  expect(api.listBackups).toHaveBeenCalledTimes(1);
});

test("the backups list is only read by the section that shows it", async () => {
  renderView();
  await screen.findByLabelText(/storage location/i);
  expect(api.listBackups).not.toHaveBeenCalled();
  await open(/^Backups/);
  await waitFor(() => expect(api.listBackups).toHaveBeenCalled());
});

test("the Storage section reports sync-conflict files in the library (#35)", async () => {
  vi.mocked(api.getStoreConflicts).mockResolvedValue({
    conflicts: [{ path: "worlds/realm/lore/pact.sync-conflict-1.md",
                  name: "pact.sync-conflict-1.md", tool: "syncthing",
                  kind: "file", size: 40, modified: "2026-01-01T00:00:00Z" }],
    truncated: false,
  });
  renderView();
  // Storage is the section this page opens on.
  expect(await screen.findByText("worlds/realm/lore/pact.sync-conflict-1.md")).toBeInTheDocument();
});

describe("sending post images (#377)", () => {
  const images = (reach: SendImagesReach, send = "on"): Config =>
    fixture({ ...cfg, send_images: send, send_images_limit: "3", send_images_reach: reach });

  test("the switch saves on and unlocks the limit", async () => {
    vi.mocked(api.getConfig).mockResolvedValue(images("off", "off"));
    vi.mocked(api.putConfig).mockResolvedValue(images("unknown"));
    renderView();
    await open(/^Context/);
    const limit = await screen.findByLabelText("Images sent");
    expect(limit).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox",
      { name: "Send post images to models that can read them" }));
    expect(limit).toBeEnabled();
    save();
    await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith(
      expect.objectContaining({ send_images: "on" })));
  });

  test("an unknown model says images are not being sent", async () => {
    vi.mocked(api.getConfig).mockResolvedValue(images("unknown"));
    renderView();
    await open(/^Context/);
    expect(await screen.findByText(/not known to read images/i)).toBeInTheDocument();
  });

  test("a text-only model says so", async () => {
    vi.mocked(api.getConfig).mockResolvedValue(images("no"));
    renderView();
    await open(/^Context/);
    expect(await screen.findByText(/cannot read images/i)).toBeInTheDocument();
  });

  test("no connection yet says that, not that the model cannot read images", async () => {
    vi.mocked(api.getConfig).mockResolvedValue(images("none"));
    renderView();
    await open(/^Context/);
    expect(await screen.findByText(/no connection is set up/i)).toBeInTheDocument();
    expect(screen.queryByText(/cannot read images/i)).toBeNull();
  });

  test("a model that reads images needs no hint", async () => {
    vi.mocked(api.getConfig).mockResolvedValue(images("yes"));
    renderView();
    await open(/^Context/);
    await screen.findByLabelText("Images sent");
    expect(screen.queryByText(/not known to read images/i)).toBeNull();
    expect(screen.queryByText(/cannot read images/i)).toBeNull();
  });
});

// ---- ?section= (a link from elsewhere can open one section) ----------------
//
// Three panes became pages of their own, and `/config?section=semantic` is the
// address older links carry. MOVED sends each old id to the page that answers
// for it.

function Where() {
  const l = useLocation();
  return <div data-testid="where">{l.pathname + l.search + l.hash}</div>;
}
function renderAt(entry: string) {
  render(
    <MemoryRouter initialEntries={[entry]}>
      <Where />
      <ThemeProvider initial={cfg.theme}><Routes>
        <Route path="/config" element={<><ConfigView /><Link to="/config?section=timeouts">to timeouts</Link></>} />
        <Route path="/models" element={<div>the models page</div>} />
        <Route path="/models/edit" element={<div>the models form</div>} />
        <Route path="/presets" element={<div>the presets page</div>} />
      </Routes></ThemeProvider>
    </MemoryRouter>,
  );
}

test("an old pane's address goes to the page that replaced it", async () => {
  const moved: [string, string][] = [
    ["models", "/models"], ["semantic", "/models"], ["connection", "/models"],
    ["routing", "/models/edit"], ["samplers", "/presets"], ["pricing", "/models#rates"],
  ];
  for (const [id, to] of moved) {
    renderAt(`/config?section=${id}`);
    await waitFor(() => expect(screen.getByTestId("where").textContent).toBe(to));
    cleanup();
  }
});

test("a section named like an object's own member falls back to Storage", async () => {
  // The moved ids are a lookup table, and a plain object answers
  // `toString` with a function -- which once left a blank page.
  for (const id of ["toString", "constructor", "__proto__", "hasOwnProperty"]) {
    renderAt(`/config?section=${id}`);
    expect(await screen.findByRole("heading", { level: 1, name: "Storage" })).toBeInTheDocument();
    cleanup();
  }
});

test("an unknown section falls back to Storage", async () => {
  renderAt("/config?section=nonsense");
  expect(await screen.findByRole("heading", { level: 1, name: "Storage" })).toBeInTheDocument();
  expect(await screen.findByLabelText(/storage location/i)).toBeInTheDocument();
});

test("a changed query follows", async () => {
  renderAt("/config");
  expect(await screen.findByRole("heading", { level: 1, name: "Storage" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("link", { name: "to timeouts" }));
  expect(await screen.findByRole("heading", { level: 1, name: "Timeouts & retries" }))
    .toBeInTheDocument();
});

test("Retries lives with the timeouts, with what a retry is", async () => {
  renderView();
  await open(/^Timeouts & retries/);
  expect(screen.getByLabelText(/^retries$/i)).toHaveValue("2");
  expect(screen.getByText(/Only ever\s+the reply starts arriving/)).toBeInTheDocument();
});

test("recall lives with the context, privacy sentence and all", async () => {
  renderView();
  await open(/^Context/);
  expect(screen.getByLabelText(/^recalled entries$/i)).toBeInTheDocument();
  expect(screen.getByLabelText(/^similarity threshold$/i)).toBeInTheDocument();
  expect(screen.getByText("This sends text to the Embedding role's provider.")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Embedding role" })).toHaveAttribute("href", "/models/edit");
  expect(screen.getByText(EMBEDDINGS_COPY)).toBeInTheDocument();
});


test("Context exposes and saves all four history controls with cost disclosure", async () => {
  renderView();
  await open(/^Context/);
  fireEvent.change(screen.getByLabelText("Historical scene depth"), { target: { value: "2" } });
  fireEvent.change(screen.getByLabelText("History recall budget"), { target: { value: "900" } });
  fireEvent.change(screen.getByLabelText("History minimum cosine"), { target: { value: "0.5" } });
  fireEvent.click(screen.getByLabelText("Rerank history"));
  expect(screen.getByText(/Embeddings are optional/i)).toBeInTheDocument();
  expect(screen.getByText(/reranking adds a decision call/i)).toBeInTheDocument();
  save();
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith({
    history_recall_depth: "2", history_recall_budget: "900",
    history_recall_threshold: "0.5", history_recall_rerank: "on",
  }));
});

test("Context saves history planning, its repair inert until planning is on", async () => {
  renderView();
  await open(/^Context/);
  const repair = screen.getByLabelText("Repair a thin plan once");
  expect(repair).toBeChecked();
  expect(repair).toBeDisabled();
  expect(screen.getByText(/History planning spends one short call/i)).toBeInTheDocument();
  fireEvent.click(screen.getByLabelText("Plan history recall"));
  expect(screen.getByLabelText("Repair a thin plan once")).toBeEnabled();
  fireEvent.click(screen.getByLabelText("Repair a thin plan once"));
  save();
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith({
    history_plan: "auto", history_plan_repair: "off",
  }));
});

// Failure controls keep read ownership and the ordinary save retry visible.
// Losing the initial read catch used to leave Loading forever and reject globally.
test.each([
  [{ detail: "settings unavailable" }, "settings unavailable"],
  [new Error("offline"), "Could not load these settings"],
])("an initial settings rejection settles with a readable error (%#)", async (failure, message) => {
  vi.mocked(api.getConfig).mockRejectedValueOnce(failure);
  renderView();
  expect(await screen.findByText(message)).toBeInTheDocument();
  expect(screen.queryByText("Loading…")).toBeNull();
  expect(api.putConfig).not.toHaveBeenCalled();
  expect(api.putPromptLayout).not.toHaveBeenCalled();
});

test("an optional prompt read failure leaves Context usable without a fabricated bar", async () => {
  vi.mocked(api.listCampaigns).mockRejectedValueOnce({ detail: "offline" });
  renderView();
  await open(/^Context/);
  expect(screen.getByLabelText("Context budget")).toBeEnabled();
  expect(screen.queryByText(/AGAINST THIS BUDGET/)).toBeNull();
  expect(screen.queryByText("offline")).toBeNull();
});

test("a failed settings save keeps the draft and permits a successful retry", async () => {
  vi.mocked(api.putConfig).mockRejectedValueOnce({ detail: "disk full" })
    .mockResolvedValueOnce({ ...cfg, llm_retries: "4" });
  renderView();
  await open(/^Timeouts/);
  fireEvent.change(screen.getByLabelText(/^retries$/i), { target: { value: "4" } });
  save();
  expect(await screen.findByText("disk full")).toBeInTheDocument();
  expect(screen.getByLabelText(/^retries$/i)).toHaveValue("4");
  expect(screen.getByText("1 unsaved change")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /^save$/i })).toBeEnabled();
  save();
  expect(await screen.findByText("No unsaved changes")).toBeInTheDocument();
  expect(api.putConfig).toHaveBeenNthCalledWith(2, { llm_retries: "4" });
});

test("moving the library refreshes its conflict notice while retaining other edits", async () => {
  vi.mocked(api.putDataDir).mockResolvedValue({
    ...dataDir, data_dir: "/sync/grimoire", is_default: false, source: "custom",
  });
  renderView();
  await open(/^Timeouts/);
  fireEvent.change(screen.getByLabelText(/^retries$/i), { target: { value: "4" } });
  await open(/^Storage/);
  await screen.findByLabelText(/storage location/i);
  vi.mocked(api.getStoreConflicts).mockResolvedValue({
    conflicts: [{ path: "worlds/realm/lore/pact.sync-conflict-1.md",
      name: "pact.sync-conflict-1.md", tool: "syncthing", kind: "file", size: 40,
      modified: "2026-01-01T00:00:00Z" }], truncated: false,
  });
  fireEvent.change(screen.getByLabelText(/storage location/i), { target: { value: "/sync/grimoire" } });
  fireEvent.click(screen.getByRole("button", { name: /^move$/i }));
  expect(await screen.findByText("worlds/realm/lore/pact.sync-conflict-1.md")).toBeInTheDocument();
  expect(screen.getByText("1 unsaved change in other sections")).toBeInTheDocument();
  await open(/^Timeouts/);
  expect(screen.getByLabelText(/^retries$/i)).toHaveValue("4");
  expect(api.putConfig).not.toHaveBeenCalled();
});
