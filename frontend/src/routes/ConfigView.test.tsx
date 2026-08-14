import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import ConfigView from "./ConfigView";

vi.mock("../api/client", () => ({
  ApiError: class ApiError extends Error {},
  api: {
    getConfig: vi.fn(), putConfig: vi.fn(), getDataDir: vi.fn(), putDataDir: vi.fn(),
    listBackups: vi.fn(), createBackup: vi.fn(),
    listStyles: vi.fn(), listConnections: vi.fn(),
    listCampaigns: vi.fn(), listScenes: vi.fn(),
    listScenePrompts: vi.fn(), getScenePrompt: vi.fn(),
    getPromptLayout: vi.fn(), putPromptLayout: vi.fn(),
  },
}));
const setTheme = vi.fn();
vi.mock("../theme/ThemeProvider", () => ({
  useTheme: () => ({ mode: "system", name: "light", setTheme }),
}));
vi.mock("../components/ResponsePresetPicker", () => ({
  ResponsePresetPicker: () => <div data-testid="response-preset-picker" />,
}));
import { api } from "../api/client";

const cfg = {
  theme: "codex", system_prompt: "", quote_color: "off", user_label: "You", assistant_label: "Grimoire",
  active_connection_id: "openrouter",
  active_connection: { id: "openrouter", kind: "openrouter", name: "OpenRouter" }, ready: true,
  data_dir: "/home/u/.grimoire",
  llm_timeout: "120", absorb_budget: "600", llm_call_budget: "300",
  llm_retries: "2", fallback_connection_id: "",
  context_budget: "0", archive_depth: "3",
  prompt_log_depth: "50",
  turnstate_depth: "0", promote_streak: "3", rolling_summary_every: "10",
  embeddings_connection_id: "", embeddings_model: "", semantic_recall_depth: "0",
  semantic_recall_threshold: "0.4",
  prompt_layout_enabled: "off", speaker_turn_taking: "off",
  backup_enabled: "off", backup_interval_hours: "24", backup_keep: "7", backup_dir: "",
};
const dataDir = {
  data_dir: "/home/u/.grimoire", default: "/home/u/.grimoire",
  is_default: true, source: "default" as const, exists: true,
};
const connections = [
  { id: "openrouter", kind: "openrouter", name: "OpenRouter", base_url: "", model: "m", post_process: "none", key_set: true, rev: "r1" },
  { id: "claude", kind: "claude", name: "Claude", base_url: "", model: "opus", post_process: "none", key_set: false, rev: "r2" },
  { id: "local", kind: "openai_compatible", name: "Local vectors", base_url: "http://localhost:1234/v1", model: "", post_process: "none", key_set: false, rev: "r3" },
];
beforeEach(() => {
  vi.clearAllMocks();
  (api.getConfig as any).mockResolvedValue(cfg);
  (api.putConfig as any).mockResolvedValue(cfg);
  (api.getDataDir as any).mockResolvedValue(dataDir);
  (api.putDataDir as any).mockResolvedValue(dataDir);
  (api.listStyles as any).mockResolvedValue([
    { id: "gothic-horror", name: "Gothic Horror", description: "", tags: [], built_in: true },
    { id: "noir-detective", name: "Noir Detective", description: "", tags: [], built_in: true },
  ]);
  (api.listConnections as any).mockResolvedValue(connections);
  (api.listBackups as any).mockResolvedValue({ dir: "/home/u/.grimoire/backups", backups: [] });
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

test("the column indexes every section in three groups", async () => {
  renderView();
  await screen.findByRole("button", { name: /^Storage/ });
  // Queried by selector rather than by text: a group heading and the head that
  // wraps it have the same text content, so getByText matches both.
  const groups = [...document.querySelectorAll(".column-section-head .section-label")];
  expect(groups.map((g) => g.textContent))
    .toEqual(["The install", "What the model sees", "What you see"]);
  for (const label of [
    /^Storage/, /^Backups/, /^Connection/, /^Timeouts/, /^Context/, /^Prompt layout/,
    /^Transient state/,
    /^Semantic recall/, /^System prompt/, /^Response preset/, /^Transcript/,
    /^While playing/, /^Appearance/,
  ]) {
    expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
  }
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

test("the theme control is pinned under the column and previews without saving", async () => {
  renderView();
  fireEvent.click(await screen.findByText("DARK"));
  expect(setTheme).toHaveBeenCalledWith("dark");        // applied, so it can be seen
  expect(api.putConfig).not.toHaveBeenCalled();          // but not written
  expect(screen.getByText("1 unsaved change")).toBeInTheDocument();

  save();
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith({ theme: "dark" }));
});

test("the stored theme survives the collapse: codex is not an unsaved change", async () => {
  renderView();
  // The store still holds `codex`; the picker shows LIGHT. Nothing has been
  // edited, so the count must not read the mapping as a pending edit.
  await screen.findByText("LIGHT");
  expect(screen.getByText("No unsaved changes")).toBeInTheDocument();
  expect(screen.getByText("LIGHT")).toHaveAttribute("aria-pressed", "true");
});

test("editing a field marks the draft dirty and writes nothing", async () => {
  renderView();
  await open(/^Timeouts/);
  fireEvent.change(screen.getByLabelText(/no-reply timeout/i), { target: { value: "45" } });
  expect(api.putConfig).not.toHaveBeenCalled();
  expect(screen.getByText("1 unsaved change")).toBeInTheDocument();
  // …and the column says which section is holding it.
  expect(screen.getByRole("button", { name: /^Timeouts unsaved/ })).toBeInTheDocument();
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

test("Revert discards every edit, the theme preview included", async () => {
  renderView();
  await open(/^Transcript/);
  fireEvent.change(await screen.findByLabelText(/your label/i), { target: { value: "Kestrel" } });
  fireEvent.click(screen.getByText("DARK"));
  expect(screen.getByText("2 unsaved changes")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: /^revert$/i }));
  expect(api.putConfig).not.toHaveBeenCalled();
  expect(screen.getByText("No unsaved changes")).toBeInTheDocument();
  expect(screen.getByLabelText(/your label/i)).toHaveValue("You");
  // The preview goes back with everything else, or the screen keeps showing a
  // look nothing on disk agrees with. `codex` maps to light.
  expect(setTheme).toHaveBeenLastCalledWith("light");
});

test("switching the active connection waits for Save like everything else", async () => {
  renderView();
  await open(/^Connection/);
  const select = screen.getByLabelText("LLM connection");
  expect(Array.from(select.querySelectorAll("option")).map((o) => (o as HTMLOptionElement).value))
    .toEqual(["openrouter", "claude", "local"]);
  expect((select as HTMLSelectElement).value).toBe("openrouter");

  fireEvent.change(select, { target: { value: "claude" } });
  expect(api.putConfig).not.toHaveBeenCalled();
  save();
  await waitFor(() =>
    expect(api.putConfig).toHaveBeenCalledWith({ active_connection_id: "claude" }));
});

test("picks a fallback connection, excluding the active one", async () => {
  renderView();
  await open(/^Connection/);
  const select = screen.getByLabelText("Fallback connection");
  // "None" plus every connection that is not already the active one — offering
  // the active one would look like a working setting and is not one.
  expect(Array.from(select.querySelectorAll("option")).map((o) => (o as HTMLOptionElement).value))
    .toEqual(["", "claude", "local"]);
  expect((select as HTMLSelectElement).value).toBe("");

  fireEvent.change(select, { target: { value: "claude" } });
  save();
  await waitFor(() =>
    expect(api.putConfig).toHaveBeenCalledWith({ fallback_connection_id: "claude" }));
});

test("says when there is no fallback and when there is one", async () => {
  renderView();
  await open(/^Connection/);
  expect(screen.getByText(/no fallback/i)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Fallback connection"), { target: { value: "local" } });
  expect(screen.getByText(/tried once when the connection above is exhausted/i))
    .toBeInTheDocument();
});

test("a fallback that has become the active connection reads as None", async () => {
  // A <select> whose value matches no option renders blank — not the stale
  // name, not None, nothing. The setting is kept in the draft (so it comes
  // back if the active connection changes back) but shown as what it now
  // behaves as: no fallback.
  (api.getConfig as any).mockResolvedValue({ ...cfg, fallback_connection_id: "openrouter" });
  renderView();
  await open(/^Connection/);
  const select = screen.getByLabelText("Fallback connection") as HTMLSelectElement;
  expect(select.value).toBe("");
  expect(screen.getByText(/no fallback/i)).toBeInTheDocument();
  // and showing it as None did not make the page think it changed, so nothing
  // is rewritten on disk behind the user's back
  save();
  expect(api.putConfig).not.toHaveBeenCalled();
});

test("saves the retry count", async () => {
  renderView();
  await open(/^Connection/);
  const retries = screen.getByLabelText(/^retries$/i);
  expect((retries as HTMLInputElement).value).toBe("2");
  fireEvent.change(retries, { target: { value: "0" } });
  save();
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith({ llm_retries: "0" }));
});

test("links to the Connections page to manage keys/endpoints", async () => {
  renderView();
  await open(/^Connection/);
  expect(screen.getByRole("link", { name: /connections/i })).toHaveAttribute("href", "/connections");
});

test("saves the system prompt", async () => {
  renderView();
  await open(/^System prompt/);
  fireEvent.change(screen.getByLabelText(/system prompt/i), { target: { value: "Never speak for the PC." } });
  save();
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith(
    { system_prompt: "Never speak for the PC." }));
});

test("mounts the response preset picker for the global scope", async () => {
  renderView();
  await open(/^Response preset/);
  expect(await screen.findByTestId("response-preset-picker")).toBeInTheDocument();
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
  fireEvent.change(screen.getByLabelText(/recalled scenes/i), { target: { value: "5" } });
  fireEvent.change(screen.getByLabelText(/kept turn prompts/i), { target: { value: "0" } });
  save();
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith(
    { archive_depth: "5", prompt_log_depth: "0" }));
});

test("saves the transient-state settings", async () => {
  renderView();
  await open(/^Transient state/);
  expect(screen.getByLabelText(/tracked posts/i)).toHaveValue("0");
  fireEvent.change(screen.getByLabelText(/tracked posts/i), { target: { value: "6" } });
  fireEvent.change(screen.getByLabelText(/promote after/i), { target: { value: "2" } });
  save();
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith(
    { turnstate_depth: "6", promote_streak: "2" }));
});

test("semantic recall is off by default and offers only openai-compatible connections", async () => {
  renderView();
  await open(/^Semantic recall/);
  const picker = screen.getByLabelText(/embeddings connection/i);
  expect(picker).toHaveValue("");                        // off until pointed somewhere
  expect(screen.getByLabelText(/recalled entries/i)).toHaveValue("0");
  expect(screen.getByLabelText(/similarity threshold/i)).toHaveValue("0.4");
  // OpenRouter and Claude serve no /embeddings route, so they are not offered.
  expect([...picker.querySelectorAll("option")].map((o) => o.textContent))
    .toEqual(["Off", "Local vectors"]);
});

test("turns semantic recall on and saves every knob together", async () => {
  renderView();
  await open(/^Semantic recall/);
  fireEvent.change(screen.getByLabelText(/embeddings connection/i), { target: { value: "local" } });
  fireEvent.change(screen.getByLabelText(/embedding model/i), { target: { value: "text-embedding-3-small" } });
  fireEvent.change(screen.getByLabelText(/recalled entries/i), { target: { value: "4" } });
  fireEvent.change(screen.getByLabelText(/similarity threshold/i), { target: { value: "0.55" } });
  save();
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith({
    embeddings_connection_id: "local", embeddings_model: "text-embedding-3-small",
    semantic_recall_depth: "4", semantic_recall_threshold: "0.55",
  }));
});

test("saves the rolling-summary cadence", async () => {
  renderView();
  await open(/^While playing/);
  expect(screen.getByLabelText(/summarize the scene every/i)).toHaveValue("10");
  fireEvent.change(screen.getByLabelText(/summarize the scene every/i), { target: { value: "4" } });
  save();
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith({ rolling_summary_every: "4" }));
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
  expect(screen.getByText("13,180 / 32,000 · 41%")).toBeInTheDocument();
  expect(screen.getByText(/CHARACTERS 2,700/)).toBeInTheDocument();
  expect(screen.getByText(/STANDING FRAME 1,840/)).toBeInTheDocument();
  expect(screen.getByText(/CONVERSATION 3,900/)).toBeInTheDocument();
  expect(screen.getByText(/RECALLED 1,040/)).toBeInTheDocument();
  expect(screen.getByText("NOTHING DROPPED")).toBeInTheDocument();
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
// ---- backups (#32) ---------------------------------------------------------

test("the backups row says off until the setting is on", async () => {
  renderView();
  expect(await screen.findByRole("button", { name: /^Backups off$/ })).toBeInTheDocument();

  await open(/^Backups/);
  await screen.findByText("No backups yet.");     // let the panel's read settle
  fireEvent.click(screen.getByLabelText(/back up automatically/i));

  // The row follows the DRAFT, so the label agrees with the checkbox you are
  // looking at rather than with the file it has not been written to yet.
  expect(screen.getByRole("button", { name: /^Backups unsaved$/ })).toBeInTheDocument();
});

test("the backup settings save as one patch of only what changed", async () => {
  renderView();
  await open(/^Backups/);
  await screen.findByText("No backups yet.");
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
  });
  (api.createBackup as any).mockResolvedValue({
    dir: "/home/u/.grimoire/backups", created: "grimoire-20260815T090000Z.zip", swept: [],
    backups: [{ name: "grimoire-20260815T090000Z.zip", size: 2_097_152,
                created: "2026-08-15T09:00:00Z" }],
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
