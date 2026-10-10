import { cleanup, render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { Link, MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import ConfigView from "./ConfigView";
import { EMBEDDINGS_COPY } from "../components/inference/copy";

vi.mock("../api/client", () => ({
  ApiError: class ApiError extends Error {},
  api: {
    getConfig: vi.fn(), putConfig: vi.fn(), getDataDir: vi.fn(), putDataDir: vi.fn(),
    listBackups: vi.fn(), createBackup: vi.fn(), createImageBackup: vi.fn(),
    getStoreConflicts: vi.fn(),
    listStyles: vi.fn(),
    listCampaigns: vi.fn(), listScenes: vi.fn(),
    listScenePrompts: vi.fn(), getScenePrompt: vi.fn(),
    getPromptLayout: vi.fn(), putPromptLayout: vi.fn(),
  },
}));
const setTheme = vi.fn();
vi.mock("../theme/ThemeProvider", () => ({
  useTheme: () => ({ mode: "system", name: "light", setTheme }),
}));
vi.mock("../components/ResponseTargetsPicker", () => ({
  ResponseTargetsPicker: () => <div data-testid="response-preset-picker" />,
}));
vi.mock("../components/ImageStoreCard", () => ({
  ImageStoreCard: () => <div data-testid="image-store-card" />,
}));
vi.mock("../components/RegexRulesEditor", () => ({
  RegexRulesEditor: ({ scope }: { scope: unknown }) =>
    <div data-testid="regex-rules" data-scope={JSON.stringify(scope)} />,
}));
import { api } from "../api/client";

const cfg = {
  theme: "codex", system_prompt: "", quote_color: "off", user_label: "You", assistant_label: "Grimoire",
  active_connection_id: "openrouter",
  active_connection: { id: "openrouter", kind: "openrouter", name: "OpenRouter" }, ready: true,
  data_dir: "/home/u/.grimoire",
  llm_timeout: "120", absorb_budget: "600", llm_call_budget: "300",
  llm_retries: "2", fallback_connection_id: "",
  context_budget: "0", context_scan_depth: "6", lore_recursion_depth: "0", archive_depth: "3",
  prompt_log_depth: "50", offscene_known_limit: "40",
  rolling_summary_every: "10",
  scene_break_every: "20", replay_fork_threshold: "10",
  advance_fork_threshold: "30",
  embeddings_connection_id: "", embeddings_model: "", semantic_recall_depth: "0",
  semantic_recall_threshold: "0.4",
  prompt_layout_enabled: "off", speaker_turn_taking: "off",
  backup_enabled: "off", backup_interval_hours: "24", backup_keep: "7", backup_dir: "",
  // The roles as `GET /config` names them (`settings.summary`). Decision
  // selects nothing of its own here, so the summary reports what it falls
  // through to -- Fast's choice -- and null only when nothing selects one.
  inference: {
    roles: {
      primary: { provider_name: "OpenRouter", model: "vendor/model-x", preset_name: "Warm" },
      fast: { provider_name: "Local vectors", model: "small-1", preset_name: "" },
      decision: { provider_name: "Local vectors", model: "small-1", preset_name: "" },
    },
    embedding_on: false,
  },
};
const dataDir = {
  data_dir: "/home/u/.grimoire", default: "/home/u/.grimoire",
  is_default: true, source: "default" as const, exists: true,
};
beforeEach(() => {
  vi.clearAllMocks();
  (api.getConfig as any).mockResolvedValue(cfg);
  (api.putConfig as any).mockResolvedValue(cfg);
  (api.getDataDir as any).mockResolvedValue(dataDir);
  (api.putDataDir as any).mockResolvedValue(dataDir);
  (api.getStoreConflicts as any).mockResolvedValue({ conflicts: [], truncated: false });
  (api.listStyles as any).mockResolvedValue([
    { id: "gothic-horror", name: "Gothic Horror", description: "", tags: [], built_in: true },
    { id: "noir-detective", name: "Noir Detective", description: "", tags: [], built_in: true },
  ]);
  (api.listBackups as any).mockResolvedValue({
    dir: "/home/u/.grimoire/backups", backups: [], image_backups: [],
  });
  // The context bar's source: no campaigns unless a test says otherwise, which
  // is also the "nothing to draw" case.
  (api.listCampaigns as any).mockResolvedValue([]);
  (api.listScenes as any).mockResolvedValue([]);
  (api.listScenePrompts as any).mockResolvedValue({ entries: [] });
  (api.getScenePrompt as any).mockResolvedValue(null);
});

// ConfigView renders a <Link to="/connections">, which throws outside a
// Router context — wrap every render the same way CampaignsView.test.tsx does.
function renderView() {
  render(
    <MemoryRouter>
      <ConfigView />
    </MemoryRouter>,
  );
}

/** Main shows one section at a time, so every field test opens its section
 *  first. The row's accessible name can carry a trailing state word ("unsaved",
 *  "off", "ready"), hence the anchored patterns. */
async function open(name: RegExp) {
  fireEvent.click(await screen.findByRole("button", { name }));
}

const save = () => fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

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
    /^Prompt layout/, /^Scene tracker/, /^System prompt/, /^Response targets/,
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
  expect(setTheme).toHaveBeenCalledWith("dark");
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith({ theme: "dark" }));
  // ...and it is not an unsaved change, because it is not unsaved.
  expect(screen.getByText("No unsaved changes")).toBeInTheDocument();
});

test("a theme the store refuses is rolled back rather than left on screen", async () => {
  // A look left applied after a failed write looks chosen for the session and
  // is gone at the next reload -- the same trade this page's Save already made
  // for it, now made in the one place every control shares.
  (api.putConfig as any).mockRejectedValueOnce({ detail: "nope" });
  renderView();
  await screen.findByText("LIGHT");
  fireEvent.click(screen.getByText("DARK"));
  // Applied first, so the pick can be seen...
  expect(setTheme).toHaveBeenCalledWith("dark");
  // ...then put back to what it was when the write is refused. The mocked
  // provider holds `system`, which is what "what it was" means here.
  await waitFor(() => expect(setTheme).toHaveBeenLastCalledWith("system"));
});

test("the stored theme survives the collapse: codex is not an unsaved change", async () => {
  renderView();
  // The store still holds `codex`; the picker shows LIGHT. Nothing has been
  // edited, so the count must not read the mapping as a pending edit.
  await screen.findByText("LIGHT");
  expect(screen.getByText("No unsaved changes")).toBeInTheDocument();
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
  let land: (c: unknown) => void = () => {};
  (api.putConfig as any).mockReturnValue(new Promise((r) => { land = r; }));
  renderView();
  await open(/^Transcript/);
  fireEvent.change(screen.getByLabelText(/your label/i), { target: { value: "Kestrel" } });
  save();
  fireEvent.change(screen.getByLabelText(/narrator label/i), { target: { value: "The Loom" } });
  land({ ...cfg, user_label: "Kestrel" });

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
  (api.putDataDir as any).mockResolvedValue({ ...dataDir, data_dir: "/sync/grimoire", is_default: false, source: "custom" });
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

const snapshot = {
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
};

function withLastPrompt() {
  (api.listCampaigns as any).mockResolvedValue([
    { id: "old-realm", name: "Realm", world: "w", created: "", updated: "2024-01-01", scenes: 2, last_scene: "", activity: "2024-01-01" },
    { id: "saltmarch", name: "Saltmarch", world: "w", created: "", updated: "2024-05-01", scenes: 11, last_scene: "", activity: "2024-06-02" },
  ]);
  (api.listScenes as any).mockResolvedValue([
    { id: "s11", title: "The long tide", model: "m", created: "", updated: "2024-06-02", date: "" },
  ]);
  (api.listScenePrompts as any).mockResolvedValue({
    entries: [{ id: "e9", scene: "s11", ts: "", model: "m", task: "chat", total_tokens: 13_180, dropped_tokens: 0, budget_tokens: 32_000 }],
  });
  (api.getScenePrompt as any).mockResolvedValue(snapshot);
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
  (api.listScenePrompts as any).mockResolvedValue({
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
  (api.getScenePrompt as any).mockResolvedValue({ ...snapshot, dropped_tokens: 900 });
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

test("the Prompt layout section carries the toggle and the editor", async () => {
  (api.getPromptLayout as any).mockResolvedValue({
    enabled: false,
    sections: [{ id: "world_info", label: "", default_label: "World info",
                 tier: "spotlight", enabled: true }],
  });
  renderView();
  await open(/^Prompt layout/);
  expect(await screen.findByRole("checkbox", { name: /use my section order/i }))
    .not.toBeChecked();
  expect(await screen.findByLabelText("Label for World info")).toBeInTheDocument();
});

test("the section order toggle saves as on/off", async () => {
  (api.getPromptLayout as any).mockResolvedValue({ enabled: false, sections: [] });
  (api.putConfig as any).mockResolvedValue({ ...cfg, prompt_layout_enabled: "on" });
  renderView();
  await open(/^Prompt layout/);
  fireEvent.click(await screen.findByRole("checkbox", { name: /use my section order/i }));
  save();
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith(
    expect.objectContaining({ prompt_layout_enabled: "on" })));
});

test("the active-speaker toggle lives in Context and saves as on/off", async () => {
  (api.putConfig as any).mockResolvedValue({ ...cfg, speaker_turn_taking: "on" });
  renderView();
  await open(/^Context/);
  fireEvent.click(await screen.findByRole("checkbox", { name: /name an active speaker/i }));
  save();
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith(
    expect.objectContaining({ speaker_turn_taking: "on" })));
});

test("tracker settings toggle and save", async () => {
  (api.putConfig as any).mockResolvedValue({ ...cfg, tracker: "off" });
  renderView();
  await open(/^Scene tracker/);
  fireEvent.click(await screen.findByRole("checkbox",
    { name: "Track each character's state after every post" }));
  save();
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith(
    expect.objectContaining({ tracker: "off" })));
});

test("the perception rider toggle saves as off, never blank", async () => {
  (api.getConfig as any).mockResolvedValue({ ...cfg, perception_rider: "on" });
  (api.putConfig as any).mockResolvedValue({ ...cfg, perception_rider: "off" });
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

const twoRows = () => ({
  enabled: false,
  sections: [
    { id: "world_info", label: "", default_label: "World info",
      tier: "spotlight", enabled: true },
    { id: "weather", label: "", default_label: "Weather",
      tier: "spotlight", enabled: true },
  ],
});

test("reordering a section counts as an unsaved change on the page", async () => {
  /** The trap this replaced: the panel held its own draft, so a reordered
   *  section left the footer reading "No unsaved changes" while the page's
   *  Save wrote everything except the reordering. */
  (api.getPromptLayout as any).mockResolvedValue(twoRows());
  renderView();
  await open(/^Prompt layout/);
  await screen.findByLabelText("Label for World info");
  expect(screen.getByText(/no unsaved changes/i)).toBeInTheDocument();

  fireEvent.click(screen.getAllByRole("button", { name: /^move .+ down$/i })[0]);
  expect(screen.getByText(/1 unsaved change$/i)).toBeInTheDocument();
});

test("the page's Save writes the layout, and writes no config when only it moved", async () => {
  (api.getPromptLayout as any).mockResolvedValue(twoRows());
  (api.putPromptLayout as any).mockImplementation((sections: any[]) =>
    Promise.resolve({ enabled: false, sections: sections.map((s) => ({
      ...s, default_label: s.id === "weather" ? "Weather" : "World info",
      tier: "spotlight" })) }));
  renderView();
  await open(/^Prompt layout/);
  await screen.findByLabelText("Label for World info");
  fireEvent.click(screen.getAllByRole("button", { name: /^move .+ down$/i })[0]);
  save();

  await waitFor(() => expect(api.putPromptLayout).toHaveBeenCalled());
  expect((api.putPromptLayout as any).mock.calls[0][0].map((s: any) => s.id))
    .toEqual(["weather", "world_info"]);
  // config.md is untouched: an empty patch is a read-modify-write storing nothing
  expect(api.putConfig).not.toHaveBeenCalled();
  await waitFor(() => expect(screen.getByText(/no unsaved changes/i)).toBeInTheDocument());
});

test("Revert puts a reordered layout back", async () => {
  (api.getPromptLayout as any).mockResolvedValue(twoRows());
  renderView();
  await open(/^Prompt layout/);
  await screen.findByLabelText("Label for World info");
  fireEvent.click(screen.getAllByRole("button", { name: /^move .+ down$/i })[0]);
  expect(screen.getAllByTestId("layout-row").map((r) => r.getAttribute("data-id")))
    .toEqual(["weather", "world_info"]);

  fireEvent.click(screen.getByRole("button", { name: /^revert$/i }));
  expect(screen.getAllByTestId("layout-row").map((r) => r.getAttribute("data-id")))
    .toEqual(["world_info", "weather"]);
  expect(screen.getByText(/no unsaved changes/i)).toBeInTheDocument();
});

test("Reset writes immediately and clears the pending reorder", async () => {
  (api.getPromptLayout as any).mockResolvedValue(twoRows());
  (api.putPromptLayout as any).mockResolvedValue(twoRows());
  renderView();
  await open(/^Prompt layout/);
  await screen.findByLabelText("Label for World info");
  fireEvent.click(screen.getAllByRole("button", { name: /^move .+ down$/i })[0]);

  fireEvent.click(screen.getByRole("button", { name: /reset to default order/i }));
  await waitFor(() => expect(api.putPromptLayout).toHaveBeenCalledWith([]));
  await waitFor(() => expect(screen.getByText(/no unsaved changes/i)).toBeInTheDocument());
});

test("the layout is fetched only when its section is opened", async () => {
  (api.getPromptLayout as any).mockResolvedValue(twoRows());
  renderView();
  await screen.findByRole("button", { name: /^Storage/ });
  expect(api.getPromptLayout).not.toHaveBeenCalled();
  await open(/^Prompt layout/);
  await waitFor(() => expect(api.getPromptLayout).toHaveBeenCalledTimes(1));
});

test("a failed layout write reports itself and leaves the settings unwritten", async () => {
  /** One Save, one outcome: the settings must not land while the layout the
   *  same click was meant to store did not. */
  (api.getPromptLayout as any).mockResolvedValue(twoRows());
  (api.putPromptLayout as any).mockRejectedValue({ detail: "disk full" });
  renderView();
  await open(/^Prompt layout/);
  await screen.findByLabelText("Label for World info");
  fireEvent.click(screen.getAllByRole("button", { name: /^move .+ down$/i })[0]);
  fireEvent.click(await screen.findByRole("checkbox", { name: /use my section order/i }));
  save();

  expect(await screen.findByText(/disk full/i)).toBeInTheDocument();
  expect(api.putConfig).not.toHaveBeenCalled();
  // ...and the reorder is still on screen to retry, not silently discarded
  expect(screen.getAllByTestId("layout-row").map((r) => r.getAttribute("data-id")))
    .toEqual(["weather", "world_info"]);
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
  (api.listBackups as any).mockResolvedValue({
    dir: "/home/u/.grimoire/backups",
    backups: [{ name: "grimoire-20260814T210000Z.zip", size: 2_097_152,
                created: "2026-08-14T21:00:00Z" }],
    image_backups: [],
  });
  (api.createBackup as any).mockResolvedValue({
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
  (api.getStoreConflicts as any).mockResolvedValue({
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
  const images = (reach: string, send = "on") =>
    ({ ...cfg, send_images: send, send_images_limit: "3", send_images_reach: reach });

  test("the switch saves on and unlocks the limit", async () => {
    (api.getConfig as any).mockResolvedValue(images("off", "off"));
    (api.putConfig as any).mockResolvedValue(images("unknown"));
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
    (api.getConfig as any).mockResolvedValue(images("unknown"));
    renderView();
    await open(/^Context/);
    expect(await screen.findByText(/not known to read images/i)).toBeInTheDocument();
  });

  test("a text-only model says so", async () => {
    (api.getConfig as any).mockResolvedValue(images("no"));
    renderView();
    await open(/^Context/);
    expect(await screen.findByText(/cannot read images/i)).toBeInTheDocument();
  });

  test("no connection yet says that, not that the model cannot read images", async () => {
    (api.getConfig as any).mockResolvedValue(images("none"));
    renderView();
    await open(/^Context/);
    expect(await screen.findByText(/no connection is set up/i)).toBeInTheDocument();
    expect(screen.queryByText(/cannot read images/i)).toBeNull();
  });

  test("a model that reads images needs no hint", async () => {
    (api.getConfig as any).mockResolvedValue(images("yes"));
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
      <Routes>
        <Route path="/config" element={<><ConfigView /><Link to="/config?section=timeouts">to timeouts</Link></>} />
        <Route path="/models" element={<div>the models page</div>} />
        <Route path="/models/edit" element={<div>the models form</div>} />
        <Route path="/presets" element={<div>the presets page</div>} />
      </Routes>
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
