import { act, render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import SetupWizard from "./SetupWizard";

const navigate = vi.fn();
vi.mock("react-router-dom", async () => ({
  ...(await vi.importActual<any>("react-router-dom")),
  useNavigate: () => navigate,
}));

vi.mock("../api/client", () => ({
  ApiError: class ApiError extends Error {},
  PRESET_CLEAR: "⁣none",
  api: {
    getDataDir: vi.fn(), putDataDir: vi.fn(), putConfig: vi.fn(), getConfig: vi.fn(),
    createConnection: vi.fn(), createWorld: vi.fn(), listWorlds: vi.fn(),
    checkConnection: vi.fn(), listProviderPresets: vi.fn(), refreshConnectionModels: vi.fn(),
    getInferenceSettings: vi.fn(), putInferenceSettings: vi.fn(),
    readConnectionCapabilities: vi.fn(), previewModelTest: vi.fn(), runModelTest: vi.fn(),
  },
}));

const setTheme = vi.fn();
vi.mock("../theme/ThemeProvider", () => ({
  useTheme: () => ({ mode: "system", name: "light", setTheme }),
}));

import { api } from "../api/client";

const onDone = vi.fn();

/** The provider presets the server lists, trimmed to the three shapes the
 *  wizard branches on: a hosted one with a fixed address and a free check, the
 *  Claude subscription (whose check generates), and an editable endpoint. */
const PRESETS = [
  { id: "openrouter", label: "OpenRouter", kind: "openrouter",
    base_url: "https://openrouter.ai/api/v1", url_locked: true, billing: "metered",
    reports_price: true, always: [], possible: [], never: [], generating_check: false },
  { id: "claude", label: "Claude subscription", kind: "claude", base_url: "",
    url_locked: false, billing: "subscription", reports_price: true,
    always: [], possible: [], never: [], generating_check: true },
  { id: "custom", label: "Custom (OpenAI-compatible)", kind: "openai_compatible",
    base_url: "", url_locked: false, billing: "metered", reports_price: false,
    always: [], possible: [], never: [], generating_check: false },
];

const BLANK = { provider: "", model: "", preset: "" };
const card = (stored = BLANK) => ({
  stored, fallback: BLANK, resolves: null, inherits: null, problem: null,
});

/** `GET /api/inference/settings` for a library at the current layout. */
function settings(over: Record<string, unknown> = {}) {
  return {
    format: "2", newer: false,
    migration: { state: "done", reason: "", skipped: [] },
    roles: {
      primary: card(), fast: card(), decision: card(),
      embedding: { stored: { provider: "", model: "" }, resolves: null, on: false },
    },
    routes: [],
    providers: [
      { id: "saltmarch", name: "Saltmarch Router", kind: "openrouter", preset: "openrouter",
        usable: true, problem: null },
      { id: "openrouter", name: "OpenRouter", kind: "openrouter", preset: "openrouter",
        usable: true, problem: null },
    ],
    presets: [],
    preset_clear: "⁣none",
    ...over,
  };
}

function capabilityRow(id: string) {
  return { id, name: id, context: null, prompt: null, completion: null,
           reason: "the catalog says so", capabilities: {} };
}

beforeEach(() => {
  vi.clearAllMocks();
  (api.checkConnection as any).mockResolvedValue({
    ok: true, kind: "", detail: "", checked_at: "2026-08-21T00:00:00Z",
  });
  (api.getDataDir as any).mockResolvedValue({
    data_dir: "/home/u/.grimoire", default: "/home/u/.grimoire",
    is_default: true, source: "default", exists: true,
  });
  (api.putDataDir as any).mockResolvedValue({
    data_dir: "/sync/grimoire", default: "/home/u/.grimoire",
    is_default: false, source: "custom", exists: true,
  });
  (api.putConfig as any).mockResolvedValue({});
  (api.getConfig as any).mockResolvedValue({ first_run: true });
  (api.listWorlds as any).mockResolvedValue([]);
  (api.listProviderPresets as any).mockResolvedValue(PRESETS);
  (api.createConnection as any).mockResolvedValue({ id: "openrouter" });
  (api.refreshConnectionModels as any).mockResolvedValue({ models: [], fetched_at: "", rev: "" });
  (api.getInferenceSettings as any).mockResolvedValue(settings());
  (api.putInferenceSettings as any).mockResolvedValue(settings());
  (api.readConnectionCapabilities as any).mockImplementation(
    (_id: string, need: string) => Promise.resolve({
      provider_preset: PRESETS[0], need, reason: null, hidden: [],
      groups: { fits: [capabilityRow(need === "embed" ? "realm-embed" : "mara-large")],
                unverified: [] },
    }));
  (api.createWorld as any).mockResolvedValue({ id: "saltmarch" });
});

function renderWizard() {
  return render(<MemoryRouter><SetupWizard onDone={onDone} /></MemoryRouter>);
}

/** A promise the test settles when it chooses, for holding a request in flight. */
function held<T>() {
  let settle: (v: T) => void = () => {};
  let fail: (e: unknown) => void = () => {};
  const promise = new Promise<T>((res, rej) => { settle = res; fail = rej; });
  return { promise, settle, fail };
}

/** The upgrade poll's clock, faked so a test that waits on it is instant.
 *  `shouldAdvanceTime` keeps the settle wrapper's own zero-delay ticks
 *  running; the poll is driven by `advance`. */
async function withFakeTimers(body: () => Promise<void>) {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    await body();
  } finally {
    vi.useRealTimers();
  }
}

async function advance(ms: number) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}

/** A library whose global switch has not landed yet. */
const UPGRADING = () => settings({
  format: "1", migration: { state: "running", reason: "", skipped: [] } });

/** `getInferenceSettings` answering whatever `reads.answer` says at the time
 *  it is asked, so a poll that fires early reads the same thing a late one
 *  would, and the test decides the order of answers rather than the clock. */
function scriptReads(first: () => Promise<unknown>) {
  const reads = { answer: first, count: 0 };
  (api.getInferenceSettings as any).mockImplementation(() => {
    reads.count += 1;
    return reads.answer();
  });
  return reads;
}

/** Walk to a step by clicking Next/Skip, which is the only way in. */
async function goToStep(n: number) {
  renderWizard();
  if (n >= 2) fireEvent.click(await screen.findByRole("button", { name: /next/i }));
  if (n >= 3) fireEvent.click(await screen.findByRole("button", { name: /^skip$/i }));
  if (n >= 4) fireEvent.click(await screen.findByRole("button", { name: /^skip$/i }));
  if (n >= 5) fireEvent.click(await screen.findByRole("button", { name: /next/i }));
  // Ending on an await is what lets the step it opened settle here: what that
  // step starts reading would otherwise land between this helper returning and
  // the caller's next query, outside act.
  await screen.findByRole("heading", { level: 3 });
}

/** Fill the provider step for `preset` and save it. */
async function saveProvider(preset: string, fields: { key?: string; url?: string } = {}) {
  fireEvent.change(await screen.findByLabelText("Provider"), { target: { value: preset } });
  if (fields.key !== undefined) {
    fireEvent.change(screen.getByLabelText("API key"), { target: { value: fields.key } });
  }
  if (fields.url !== undefined) {
    fireEvent.change(screen.getByLabelText("Base URL"), { target: { value: fields.url } });
  }
  fireEvent.click(screen.getByRole("button", { name: /save provider/i }));
  await waitFor(() => expect(api.createConnection).toHaveBeenCalled());
}

const region = (name: string) => screen.getByRole("region", { name });

/** Choose `model` on `provider` in one role's picker. */
async function pick(role: string, provider: string, model: string) {
  const r = await screen.findByRole("region", { name: role });
  fireEvent.change(within(r).getByLabelText("Provider"), { target: { value: provider } });
  fireEvent.click(await within(r).findByRole("radio", { name: model }));
}

test("opens on the storage step, showing the current data dir", async () => {
  renderWizard();
  expect(await screen.findByRole("heading", { name: /^grimoire$/i })).toBeInTheDocument();
  // The promise the app makes, before it asks anything — and the answer to the
  // very question this step is about.
  expect(screen.getByText(/stays yours, as plain files/i)).toBeInTheDocument();
  expect(await screen.findByLabelText(/storage location/i)).toHaveValue("/home/u/.grimoire");
});

test("the storage step moves the data dir through the same API Config uses", async () => {
  renderWizard();
  const input = await screen.findByLabelText(/storage location/i);
  fireEvent.change(input, { target: { value: "/sync/grimoire" } });
  fireEvent.click(screen.getByRole("button", { name: /^move$/i }));
  await waitFor(() => expect(api.putDataDir).toHaveBeenCalledWith("/sync/grimoire"));
});

// ---- the provider step ----

test("the wizard creates a provider from a preset", async () => {
  await goToStep(2);
  await saveProvider("openrouter", { key: "sk-or-test" });

  await waitFor(() => expect(api.createConnection).toHaveBeenCalledTimes(1));
  const body = (api.createConnection as any).mock.calls[0][0];
  expect(body).toEqual(expect.objectContaining({
    kind: "openrouter", name: "OpenRouter", preset: "openrouter", billing: "metered",
    api_key: "sk-or-test",
  }));
  // The model is the Primary role's now; a provider carrying one is refused
  // at format 2 ("set this on the model, not the provider").
  expect(body).not.toHaveProperty("model");
  // Nothing to activate: which provider chat runs on is the Primary role.
  expect(api.putConfig).not.toHaveBeenCalledWith(
    expect.objectContaining({ active_connection_id: expect.anything() }));
  expect(await screen.findByText(/saved openrouter/i)).toBeInTheDocument();
  // The free check runs by itself, and the catalog is listed for the Models step.
  await waitFor(() => expect(api.checkConnection).toHaveBeenCalledWith("openrouter"));
  await waitFor(() => expect(api.refreshConnectionModels).toHaveBeenCalledWith("openrouter"));
});

test("the wizard does not probe a Claude provider unasked", async () => {
  (api.createConnection as any).mockResolvedValue({ id: "claude-subscription" });
  await goToStep(2);
  await saveProvider("claude");

  await waitFor(() => expect(api.createConnection).toHaveBeenCalledWith(expect.objectContaining({
    kind: "claude", preset: "claude", billing: "subscription" })));
  expect(await screen.findByText(/saved claude subscription/i)).toBeInTheDocument();
  // Its check is a real generation: it waits for the reader to ask for it.
  expect(api.checkConnection).not.toHaveBeenCalled();
  expect(api.refreshConnectionModels).not.toHaveBeenCalled();   // it has no catalog to list

  fireEvent.click(screen.getByRole("button", { name: /check \(sends one short message\)/i }));
  await waitFor(() => expect(api.checkConnection).toHaveBeenCalledWith(
    "claude-subscription", { confirm: true }));
  expect(api.checkConnection).toHaveBeenCalledTimes(1);
});

test("the saved provider is checked, and a refusal is said out loud", async () => {
  (api.checkConnection as any).mockResolvedValue({
    ok: false, kind: "auth", detail: "No auth credentials found",
    checked_at: "2026-08-21T09:00:00Z",
  });
  await goToStep(2);
  await saveProvider("openrouter", { key: "sk-or-dead" });

  expect(await screen.findByText(/No auth credentials found/)).toBeInTheDocument();
  // ...and it is a warning, not a gate: the provider IS saved, and a wizard
  // that refused to move on would trap someone whose provider is down.
  expect(screen.getByRole("button", { name: /next/i })).toBeEnabled();
});

test("a check that cannot be made does not undo a provider that saved", async () => {
  (api.checkConnection as any).mockRejectedValue(new Error("offline"));
  await goToStep(2);
  await saveProvider("openrouter", { key: "sk-or-test" });
  expect(await screen.findByText(/saved openrouter/i)).toBeInTheDocument();
});

test("an OpenRouter provider cannot be saved without a key", async () => {
  // The server reports a keyless OpenRouter provider as unable to send.
  await goToStep(2);
  fireEvent.change(await screen.findByLabelText("Provider"), { target: { value: "openrouter" } });
  expect(screen.getByRole("button", { name: /save provider/i })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("API key"), { target: { value: "sk-or-test" } });
  expect(screen.getByRole("button", { name: /save provider/i })).toBeEnabled();
});

test("a custom endpoint cannot be saved without a base URL", async () => {
  await goToStep(2);
  fireEvent.change(await screen.findByLabelText("Provider"), { target: { value: "custom" } });
  expect(screen.getByRole("button", { name: /save provider/i })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Base URL"), { target: { value: "http://localhost:1234/v1" } });
  expect(screen.getByRole("button", { name: /save provider/i })).toBeEnabled();
  fireEvent.change(screen.getByLabelText("Billing"), { target: { value: "subscription" } });
  fireEvent.click(screen.getByRole("button", { name: /save provider/i }));
  await waitFor(() => expect(api.createConnection).toHaveBeenCalledWith(expect.objectContaining({
    kind: "openai_compatible", preset: "custom", base_url: "http://localhost:1234/v1",
    billing: "subscription" })));
});

test("a Claude subscription needs neither a key nor a URL", async () => {
  await goToStep(2);
  fireEvent.change(await screen.findByLabelText("Provider"), { target: { value: "claude" } });
  expect(screen.queryByLabelText("API key")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Base URL")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: /save provider/i })).toBeEnabled();
});

test("a provider that fails to save reports why and stays on the step", async () => {
  (api.createConnection as any).mockRejectedValue({ detail: "name already taken" });
  await goToStep(2);
  await saveProvider("openrouter", { key: "sk-or-test" });
  expect(await screen.findByText(/name already taken/i)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /save provider/i })).toBeInTheDocument();
});

test("Skip setup is locked while a provider is being saved", async () => {
  let settle: (v: any) => void = () => {};
  (api.createConnection as any).mockReturnValue(new Promise((r) => { settle = r; }));
  await goToStep(2);
  await saveProvider("openrouter", { key: "sk-or-test" });

  await waitFor(() => expect(screen.getByRole("button", { name: /skip setup/i })).toBeDisabled());
  settle({ id: "openrouter" });
  await waitFor(() => expect(screen.getByRole("button", { name: /skip setup/i })).toBeEnabled());
});

test("the provider step is skippable — playing by hand is allowed", async () => {
  await goToStep(2);
  fireEvent.click(await screen.findByRole("button", { name: /^skip$/i }));
  expect(await screen.findByRole("heading", { name: /choose your models/i })).toBeInTheDocument();
  expect(api.createConnection).not.toHaveBeenCalled();
});

test("a library still upgrading shows Finishing the upgrade, and writes nothing until done", async () => {
  await withFakeTimers(async () => {
    const reads = scriptReads(() => Promise.resolve(UPGRADING()));
    await goToStep(2);
    expect(await screen.findByText(/finishing the upgrade/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /save provider/i })).not.toBeInTheDocument();

    // It asks again by itself, and the step opens once the upgrade is done.
    reads.answer = () => Promise.resolve(settings());
    await advance(1000);
    expect(await screen.findByRole("button", { name: /save provider/i })).toBeInTheDocument();
    expect(screen.queryByText(/finishing the upgrade/i)).not.toBeInTheDocument();
  });
});

test("an upgrade that never finishes stops promising, and is asked about less often", async () => {
  // `pending` forever is a real state (the switch turned off, a run elsewhere
  // that died): the step must not go on saying it opens "as soon as that is
  // done", nor ask every second for as long as the tab stays open.
  await withFakeTimers(async () => {
    const reads = scriptReads(() => Promise.resolve(UPGRADING()));
    await goToStep(2);
    expect(await screen.findByText(/finishing the upgrade/i)).toBeInTheDocument();
    for (let i = 0; i < 40; i += 1) await advance(1000);
    expect(await screen.findByText(/not finishing/i)).toBeInTheDocument();
    expect(screen.queryByText(/finishing the upgrade/i)).not.toBeInTheDocument();

    // Ten seconds asks twice at the slow cadence, not ten times.
    const before = reads.count;
    for (let i = 0; i < 10; i += 1) await advance(1000);
    expect(reads.count - before).toBeGreaterThanOrEqual(1);
    expect(reads.count - before).toBeLessThanOrEqual(2);
  });
});

test("a read that fails while upgrading does not stop the asking", async () => {
  // Keyed on the answer alone, the poll re-armed only when the answer moved:
  // a rejected read leaves it where it was, and the step said "opens as soon
  // as that is done" over a wizard that had stopped asking.
  await withFakeTimers(async () => {
    const reads = scriptReads(() => Promise.resolve(UPGRADING()));
    await goToStep(2);
    expect(await screen.findByText(/finishing the upgrade/i)).toBeInTheDocument();

    reads.answer = () => Promise.reject(new Error("the store is busy"));
    const before = reads.count;
    await advance(1000);
    await waitFor(() => expect(reads.count).toBeGreaterThan(before));
    expect(screen.getByText(/finishing the upgrade/i)).toBeInTheDocument();

    reads.answer = () => Promise.resolve(settings());
    await advance(1000);
    expect(await screen.findByRole("button", { name: /save provider/i })).toBeInTheDocument();
  });
});

test("the steps open once the format has switched, even with a campaign left to upgrade", async () => {
  // `pending` after the global switch: a campaign skipped as busy is finished
  // on the next start, and the server takes global writes meanwhile.
  (api.getInferenceSettings as any).mockResolvedValue(settings({
    format: "2",
    migration: { state: "pending", reason: "",
                 skipped: ["campaign realm: busy; finished on the next start"] },
  }));
  await withFakeTimers(async () => {
    await goToStep(2);
    expect(await screen.findByRole("button", { name: /save provider/i })).toBeInTheDocument();
    expect(screen.queryByText(/finishing the upgrade/i)).not.toBeInTheDocument();
    // One quiet line saying what is left, as the server names it.
    expect(screen.getByText(/left 1 thing for later \(campaign realm: busy; finished on the next start\)/))
      .toBeInTheDocument();
    // Nothing here waits on it, so nothing asks again.
    await advance(5000);
    expect(api.getInferenceSettings).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByRole("button", { name: /^skip$/i }));
    await pick("Primary", "saltmarch", "mara-large");
    fireEvent.click(screen.getByRole("button", { name: /save and continue/i }));
    await waitFor(() => expect(api.putInferenceSettings).toHaveBeenCalledTimes(1));
  });
});

test("the poll stops once the format switches, on a step change, and on unmount", async () => {
  await withFakeTimers(async () => {
    const reads = scriptReads(() => Promise.resolve(UPGRADING()));
    const view = renderWizard();
    fireEvent.click(await screen.findByRole("button", { name: /next/i }));
    expect(await screen.findByText(/finishing the upgrade/i)).toBeInTheDocument();
    await advance(1000);
    await waitFor(() => expect(reads.count).toBeGreaterThanOrEqual(2));

    // Leaving the model steps stops it.
    fireEvent.click(screen.getByRole("button", { name: /^back$/i }));
    await screen.findByLabelText(/storage location/i);
    let seen = reads.count;
    await advance(5000);
    expect(reads.count).toBe(seen);

    // The global switch landing stops it, though campaigns are still pending.
    fireEvent.click(screen.getByRole("button", { name: /next/i }));
    await screen.findByText(/finishing the upgrade/i);
    reads.answer = () => Promise.resolve(settings({
      format: "2", migration: { state: "pending", reason: "", skipped: [] } }));
    await advance(1000);
    expect(await screen.findByRole("button", { name: /save provider/i })).toBeInTheDocument();
    expect(screen.getByText(/has not finished upgrading yet/i)).toBeInTheDocument();
    seen = reads.count;
    await advance(5000);
    expect(reads.count).toBe(seen);

    // And unmounting stops it.
    reads.answer = () => Promise.resolve(UPGRADING());
    fireEvent.click(screen.getByRole("button", { name: /^skip$/i }));
    await screen.findByText(/finishing the upgrade/i);
    view.unmount();
    seen = reads.count;
    await advance(5000);
    expect(reads.count).toBe(seen);
  });
});

test("a failed upgrade says why and offers no form", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({
    format: "1", migration: { state: "failed", reason: "the safety backup failed: disk full", skipped: [] },
  }));
  await goToStep(2);
  expect(await screen.findByText(/the safety backup failed: disk full/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /save provider/i })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: /^skip$/i })).toBeEnabled();
});

// ---- the models step ----

test("Primary is required to continue", async () => {
  await goToStep(3);
  const save = await screen.findByRole("button", { name: /save and continue/i });
  expect(save).toBeDisabled();

  await pick("Primary", "saltmarch", "mara-large");
  expect(save).toBeEnabled();
  fireEvent.click(save);

  await waitFor(() => expect(api.putInferenceSettings).toHaveBeenCalledTimes(1));
  const [body, opts] = (api.putInferenceSettings as any).mock.calls[0];
  expect(body.roles.primary).toEqual({
    selection: { provider: "saltmarch", model: "mara-large", preset: "" } });
  expect(opts).toBeUndefined();
  expect(await screen.findByRole("heading", { name: /pick a look/i })).toBeInTheDocument();
});

test("Fast and Decision default to same as", async () => {
  await goToStep(3);
  expect(await within(await screen.findByRole("region", { name: "Fast" }))
    .findByRole("combobox", { name: "Fast" })).toHaveDisplayValue("Same as Primary");
  expect(within(region("Decision")).getByRole("combobox", { name: "Decision" }))
    .toHaveDisplayValue("Same as Fast");
  // "Same as" asks for no model of its own.
  expect(within(region("Fast")).queryByLabelText("Provider")).not.toBeInTheDocument();
  expect(within(region("Decision")).queryByLabelText("Provider")).not.toBeInTheDocument();

  await pick("Primary", "saltmarch", "mara-large");
  fireEvent.click(screen.getByRole("button", { name: /save and continue/i }));
  await waitFor(() => expect(api.putInferenceSettings).toHaveBeenCalled());
  const [body] = (api.putInferenceSettings as any).mock.calls[0];
  expect(body.roles.fast).toEqual({ selection: BLANK });
  expect(body.roles.decision).toEqual({ selection: BLANK });
});

test("Fast can be given a model of its own", async () => {
  await goToStep(3);
  fireEvent.change(within(await screen.findByRole("region", { name: "Fast" }))
    .getByRole("combobox", { name: "Fast" }), { target: { value: "own" } });
  await pick("Primary", "saltmarch", "mara-large");
  // An own model left unchosen is not a choice yet.
  expect(screen.getByRole("button", { name: /save and continue/i })).toBeDisabled();
  await pick("Fast", "openrouter", "mara-large");
  fireEvent.click(screen.getByRole("button", { name: /save and continue/i }));
  await waitFor(() => expect(api.putInferenceSettings).toHaveBeenCalled());
  const [body] = (api.putInferenceSettings as any).mock.calls[0];
  expect(body.roles.fast).toEqual({
    selection: { provider: "openrouter", model: "mara-large", preset: "" } });
});

test("embedding is optional and asks before saving", async () => {
  await goToStep(3);
  const embedding = await screen.findByRole("region", { name: "Embedding" });
  // One line on what it is for, and it starts off.
  expect(within(embedding).getByText(/by meaning/i)).toBeInTheDocument();
  expect(within(embedding).getByRole("combobox", { name: "Embedding" }))
    .toHaveDisplayValue("Not now");

  await pick("Primary", "saltmarch", "mara-large");
  fireEvent.change(within(embedding).getByRole("combobox", { name: "Embedding" }),
                   { target: { value: "on" } });
  await pick("Embedding", "saltmarch", "realm-embed");
  fireEvent.click(screen.getByRole("button", { name: /save and continue/i }));

  // Re-embedding a library may cost money: nothing is written until it is agreed.
  expect(await screen.findByText(/may cost money/i)).toBeInTheDocument();
  expect(api.putInferenceSettings).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole("button", { name: /embed and save/i }));
  await waitFor(() => expect(api.putInferenceSettings).toHaveBeenCalledTimes(1));
  const [body, opts] = (api.putInferenceSettings as any).mock.calls[0];
  expect(body.roles.embedding).toEqual({
    selection: { provider: "saltmarch", model: "realm-embed" } });
  expect(opts).toEqual({ confirmEmbedding: true });
});

test("leaving embedding off saves without asking", async () => {
  await goToStep(3);
  await pick("Primary", "saltmarch", "mara-large");
  fireEvent.click(screen.getByRole("button", { name: /save and continue/i }));
  await waitFor(() => expect(api.putInferenceSettings).toHaveBeenCalledTimes(1));
  const [body, opts] = (api.putInferenceSettings as any).mock.calls[0];
  expect(body.roles.embedding).toEqual({ selection: { provider: "", model: "" } });
  expect(opts).toBeUndefined();
  expect(screen.queryByText(/may cost money/i)).not.toBeInTheDocument();
});

test("the Primary picker starts on the provider just saved", async () => {
  await goToStep(2);
  await saveProvider("openrouter", { key: "sk-or-test" });
  fireEvent.click(await screen.findByRole("button", { name: /next/i }));
  const primary = await screen.findByRole("region", { name: "Primary" });
  expect(within(primary).getByLabelText("Provider")).toHaveValue("openrouter");
});

test("the provider just saved is not shown as missing while its step re-reads", async () => {
  // Step 3 renders at once with the providers step 2 read, which predate the
  // create; held here, its own re-read is what used to stand between them.
  (api.getInferenceSettings as any)
    .mockResolvedValueOnce(settings({ providers: [
      { id: "saltmarch", name: "Saltmarch Router", kind: "openrouter", preset: "openrouter",
        usable: true, problem: null }] }))
    .mockReturnValue(new Promise(() => {}));
  await goToStep(2);
  await saveProvider("openrouter", { key: "sk-or-test" });
  fireEvent.click(await screen.findByRole("button", { name: /next/i }));
  const primary = await screen.findByRole("region", { name: "Primary" });
  expect(within(primary).getByLabelText("Provider")).toHaveDisplayValue("OpenRouter");
  expect(screen.queryByText(/missing provider/i)).not.toBeInTheDocument();
});

test("a Claude subscription as Primary takes a typed model id", async () => {
  const claude = { id: "claude-subscription", name: "Claude subscription", kind: "claude",
                   preset: "claude", usable: true, problem: null };
  (api.createConnection as any).mockResolvedValue({ id: "claude-subscription" });
  (api.getInferenceSettings as any).mockResolvedValue(settings({
    providers: [...settings().providers, claude] }));
  (api.readConnectionCapabilities as any).mockImplementation(
    (id: string, need: string) => Promise.resolve({
      provider_preset: id === "claude-subscription" ? PRESETS[1] : PRESETS[0], need,
      reason: null, hidden: [],
      // It has no catalog: nothing is listed, whatever the need.
      groups: { fits: id === "claude-subscription" ? [] : [capabilityRow("mara-large")],
                unverified: [] },
    }));
  await goToStep(2);
  await saveProvider("claude");
  fireEvent.click(await screen.findByRole("button", { name: /next/i }));

  const primary = await screen.findByRole("region", { name: "Primary" });
  expect(within(primary).getByLabelText("Provider")).toHaveValue("claude-subscription");
  expect(api.refreshConnectionModels).not.toHaveBeenCalled();
  expect(within(primary).getByText(/type one, such as sonnet or opus/i)).toBeInTheDocument();
  expect(within(primary).queryByRole("radio")).not.toBeInTheDocument();

  fireEvent.change(within(primary).getByLabelText("Model id"), { target: { value: "sonnet" } });
  fireEvent.click(within(primary).getByRole("button", { name: /use this id/i }));
  const save = screen.getByRole("button", { name: /save and continue/i });
  await waitFor(() => expect(save).toBeEnabled());
  fireEvent.click(save);
  await waitFor(() => expect(api.putInferenceSettings).toHaveBeenCalledTimes(1));
  const [body] = (api.putInferenceSettings as any).mock.calls[0];
  expect(body.roles.primary).toEqual({
    selection: { provider: "claude-subscription", model: "sonnet", preset: "" } });
});

test("a Fast with a model of its own is shown as such and sent back unchanged", async () => {
  const own = { provider: "openrouter", model: "mara-large", preset: "winifred" };
  (api.getInferenceSettings as any).mockResolvedValue(settings({
    roles: {
      primary: card({ provider: "saltmarch", model: "mara-large", preset: "" }),
      fast: card(own), decision: card(),
      embedding: { stored: { provider: "", model: "" }, resolves: null, on: false },
    },
  }));
  await goToStep(3);
  const fast = await screen.findByRole("region", { name: "Fast" });
  expect(within(fast).getByRole("combobox", { name: "Fast" }))
    .toHaveDisplayValue("A model of its own");
  expect(within(fast).getByLabelText("Provider")).toHaveValue("openrouter");
  expect(await within(fast).findByRole("radio", { name: "mara-large" })).toBeChecked();

  fireEvent.click(screen.getByRole("button", { name: /save and continue/i }));
  await waitFor(() => expect(api.putInferenceSettings).toHaveBeenCalledTimes(1));
  const [body] = (api.putInferenceSettings as any).mock.calls[0];
  expect(body.roles.fast).toEqual({ selection: own });
});

test("Same as keeps a stored sampler preset", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({
    roles: {
      primary: card({ provider: "saltmarch", model: "mara-large", preset: "" }),
      // Already "Same as", with a preset of its own...
      fast: card({ provider: "", model: "", preset: "winifred" }),
      // ...and a model of its own that the reader turns into "Same as".
      decision: card({ provider: "openrouter", model: "mara-large", preset: "seraphine" }),
      embedding: { stored: { provider: "", model: "" }, resolves: null, on: false },
    },
  }));
  await goToStep(3);
  const fast = await screen.findByRole("region", { name: "Fast" });
  expect(within(fast).getByRole("combobox", { name: "Fast" })).toHaveDisplayValue("Same as Primary");
  fireEvent.change(within(region("Decision")).getByRole("combobox", { name: "Decision" }),
                   { target: { value: "same" } });

  fireEvent.click(screen.getByRole("button", { name: /save and continue/i }));
  await waitFor(() => expect(api.putInferenceSettings).toHaveBeenCalledTimes(1));
  const [body] = (api.putInferenceSettings as any).mock.calls[0];
  expect(body.roles.fast).toEqual({ selection: { provider: "", model: "", preset: "winifred" } });
  expect(body.roles.decision).toEqual({
    selection: { provider: "", model: "", preset: "seraphine" } });
});

test("a server that asks to confirm the embedding gets the confirmation", async () => {
  // The library's Embedding role changed elsewhere since it was read, so this
  // side thinks rewriting it re-embeds nothing; the server knows better.
  (api.getInferenceSettings as any).mockResolvedValue(settings({
    roles: {
      primary: card({ provider: "saltmarch", model: "mara-large", preset: "" }),
      fast: card(), decision: card(),
      embedding: { stored: { provider: "saltmarch", model: "realm-embed" }, resolves: null, on: true },
    },
  }));
  (api.putInferenceSettings as any)
    .mockRejectedValueOnce({ status: 400, kind: "confirm_embedding",
                             detail: "Changing the Embedding role re-embeds the library." })
    .mockResolvedValue(settings());
  await goToStep(3);
  await screen.findByRole("region", { name: "Embedding" });
  fireEvent.click(screen.getByRole("button", { name: /save and continue/i }));

  expect(await screen.findByText(/may cost money/i)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /embed and save/i }));
  await waitFor(() => expect(api.putInferenceSettings).toHaveBeenCalledTimes(2));
  const [, opts] = (api.putInferenceSettings as any).mock.calls[1];
  expect(opts).toEqual({ confirmEmbedding: true });
  expect(await screen.findByRole("heading", { name: /pick a look/i })).toBeInTheDocument();
});

test("roles this library already has are shown, not blanked", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({
    roles: {
      primary: card({ provider: "saltmarch", model: "mara-large", preset: "seraphine" }),
      fast: card(), decision: card(),
      embedding: { stored: { provider: "saltmarch", model: "realm-embed" }, resolves: null, on: true },
    },
  }));
  await goToStep(3);
  const primary = await screen.findByRole("region", { name: "Primary" });
  expect(await within(primary).findByRole("radio", { name: "mara-large" })).toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: /save and continue/i }));
  await waitFor(() => expect(api.putInferenceSettings).toHaveBeenCalled());
  const [body, opts] = (api.putInferenceSettings as any).mock.calls[0];
  // The preset rides along untouched, and rewriting the embedding it already
  // has re-embeds nothing, so there is nothing to confirm.
  expect(body.roles.primary.selection.preset).toBe("seraphine");
  expect(body.roles.embedding).toEqual({ selection: { provider: "saltmarch", model: "realm-embed" } });
  expect(opts).toBeUndefined();
});

test("a roles write that is refused says why and stays on the step", async () => {
  (api.putInferenceSettings as any).mockRejectedValue({ detail: "no such provider: saltmarch" });
  await goToStep(3);
  await pick("Primary", "saltmarch", "mara-large");
  fireEvent.click(screen.getByRole("button", { name: /save and continue/i }));
  expect(await screen.findByText(/no such provider: saltmarch/)).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: /choose your models/i })).toBeInTheDocument();
});

test("the models step is skippable", async () => {
  await goToStep(3);
  fireEvent.click(await screen.findByRole("button", { name: /^skip$/i }));
  expect(await screen.findByRole("heading", { name: /pick a look/i })).toBeInTheDocument();
  expect(api.putInferenceSettings).not.toHaveBeenCalled();
});

// ---- storage, look, world ----

test("the storage step will not advance while a move is in flight", async () => {
  let settle: (v: any) => void = () => {};
  (api.putDataDir as any).mockReturnValue(new Promise((r) => { settle = r; }));
  renderWizard();
  const input = await screen.findByLabelText(/storage location/i);
  fireEvent.change(input, { target: { value: "/sync/grimoire" } });
  fireEvent.click(screen.getByRole("button", { name: /^move$/i }));

  // Advancing here would unmount the only place the failure would be shown,
  // and let the next step write into whichever store the pointer still names.
  await waitFor(() => expect(screen.getByRole("button", { name: /next/i })).toBeDisabled());
  expect(screen.getByRole("button", { name: /skip setup/i })).toBeDisabled();
  expect(screen.getByRole("button", { name: /moving…/i })).toBeDisabled();  // the Move button itself

  settle({ data_dir: "/sync/grimoire", default: "/home/u/.grimoire", is_default: false, source: "custom", exists: true });
  await waitFor(() => expect(screen.getByRole("button", { name: /next/i })).toBeEnabled());
});

test("the theme step will not advance while its save is in flight", async () => {
  let settle: (v: any) => void = () => {};
  (api.putConfig as any).mockReturnValue(new Promise((r) => { settle = r; }));
  await goToStep(4);
  fireEvent.click(await screen.findByText("DARK"));

  // PUT /api/config is a read-modify-write of one file; letting the user reach
  // Finish here would race the setup_done write against this one.
  await waitFor(() => expect(screen.getByRole("button", { name: /saving/i })).toBeDisabled());
  expect(screen.getByRole("button", { name: /skip setup/i })).toBeDisabled();

  settle({});
  await waitFor(() => expect(screen.getByRole("button", { name: /next/i })).toBeEnabled());
});

test("the appearance segments are locked while a pick is saving, so two picks cannot race", async () => {
  let settle: (v: any) => void = () => {};
  (api.putConfig as any).mockReturnValue(new Promise((r) => { settle = r; }));
  await goToStep(4);
  fireEvent.click(await screen.findByText("DARK"));

  await waitFor(() => expect(screen.getByText("LIGHT")).toBeDisabled());
  expect(screen.getByText("DARK")).toBeDisabled();

  settle({});
  await waitFor(() => expect(screen.getByText("LIGHT")).toBeEnabled());
});

test("Finish later is locked while the world is being created", async () => {
  let settle: (v: any) => void = () => {};
  (api.createWorld as any).mockReturnValue(new Promise((r) => { settle = r; }));
  await goToStep(5);
  fireEvent.change(await screen.findByLabelText(/world name/i), { target: { value: "Saltmarch" } });
  fireEvent.click(screen.getByRole("button", { name: /^create$/i }));

  // Leaving now would dismiss setup for good and unmount the only place the
  // creation result can be reported.
  await waitFor(() => expect(screen.getByRole("button", { name: /finish later/i })).toBeDisabled());
  settle({ id: "saltmarch" });
  await waitFor(() => expect(screen.getByText(/created saltmarch/i)).toBeInTheDocument());
});

async function moveTo(worlds: any[]) {
  (api.listWorlds as any).mockResolvedValue(worlds);
  renderWizard();
  fireEvent.change(await screen.findByLabelText(/storage location/i), { target: { value: "/sync/grimoire" } });
  fireEvent.click(screen.getByRole("button", { name: /^move$/i }));
  await waitFor(() => expect(api.putDataDir).toHaveBeenCalled());
}

/** From the storage step, past Provider and Models and Look to World. */
async function skipToWorld() {
  fireEvent.click(await screen.findByRole("button", { name: /next/i }));
  fireEvent.click(await screen.findByRole("button", { name: /^skip$/i }));
  fireEvent.click(await screen.findByRole("button", { name: /^skip$/i }));
  fireEvent.click(await screen.findByRole("button", { name: /next/i }));
  await screen.findByRole("heading", { level: 3 });
}

test("an emptied store that merely dismissed setup is not treated as stocked", async () => {
  // `first_run: false` also describes an EMPTY store whose setup was skipped
  // before. Calling that stocked hides the create form and hands off into
  // CampaignWizard, which cannot get past step one with no world to pick — so
  // the verdict has to come from the worlds, not from first_run.
  (api.getConfig as any).mockResolvedValue({ first_run: false });
  await moveTo([]);
  await skipToWorld();

  expect(await screen.findByLabelText(/world name/i)).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: /already stocked/i })).not.toBeInTheDocument();
});

test("a move forgets the provider and verdict recorded in the previous store", async () => {
  // A check is about one provider in one store (#146); carrying either across
  // a move would describe records the new store does not have.
  (api.checkConnection as any).mockResolvedValue({
    ok: false, kind: "auth", detail: "No auth credentials found",
    checked_at: "2026-08-21T09:00:00Z",
  });
  renderWizard();
  fireEvent.click(await screen.findByRole("button", { name: /next/i }));
  await saveProvider("openrouter", { key: "sk-or-dead" });
  expect(await screen.findByText(/No auth credentials found/)).toBeInTheDocument();

  // back to step 1 and repoint: that provider lives in the store we just left
  fireEvent.click(screen.getByRole("button", { name: /^back$/i }));
  fireEvent.change(await screen.findByLabelText(/storage location/i), { target: { value: "/sync/grimoire" } });
  fireEvent.click(screen.getByRole("button", { name: /^move$/i }));
  await waitFor(() => expect(api.putDataDir).toHaveBeenCalled());

  fireEvent.click(await screen.findByRole("button", { name: /next/i }));
  expect(await screen.findByRole("button", { name: /save provider/i })).toBeInTheDocument();
  expect(screen.queryByText(/saved openrouter/i)).not.toBeInTheDocument();
  expect(screen.queryByText(/No auth credentials found/)).not.toBeInTheDocument();
});

/** From the provider step, back to storage and repoint, then forward again. */
async function moveFromProviderStep() {
  fireEvent.click(screen.getByRole("button", { name: /^back$/i }));
  fireEvent.change(await screen.findByLabelText(/storage location/i), { target: { value: "/sync/grimoire" } });
  fireEvent.click(screen.getByRole("button", { name: /^move$/i }));
  await waitFor(() => expect(api.putDataDir).toHaveBeenCalled());
  fireEvent.click(await screen.findByRole("button", { name: /next/i }));
  // Ending on an await, as `goToStep` does: the step's read would otherwise
  // land while the caller's `await` of this helper unwinds, outside act.
  await screen.findByRole("heading", { name: /add a provider/i });
}

test("a check in flight across a move does not leave the next one stuck", async () => {
  // Its answer is dropped as belonging to the old store, so nothing else
  // would clear the flag it set: the new provider's Check read "Checking…".
  (api.createConnection as any).mockResolvedValue({ id: "claude-subscription" });
  (api.checkConnection as any).mockReturnValueOnce(new Promise(() => {}));
  renderWizard();
  fireEvent.click(await screen.findByRole("button", { name: /next/i }));
  await saveProvider("claude");
  fireEvent.click(await screen.findByRole("button", { name: /check \(sends one short message\)/i }));
  expect(await screen.findByRole("button", { name: /checking…/i })).toBeDisabled();

  await moveFromProviderStep();
  (api.createConnection as any).mockClear();
  await saveProvider("claude");
  expect(await screen.findByRole("button", { name: /check \(sends one short message\)/i }))
    .toBeEnabled();
});

test("a check or catalog answer that lands after a move is dropped", async () => {
  const check = held<any>();
  const refresh = held<any>();
  (api.checkConnection as any).mockReturnValueOnce(check.promise);
  (api.refreshConnectionModels as any)
    .mockReturnValueOnce(refresh.promise)
    .mockReturnValue(new Promise(() => {}));   // the new store's listing, still going
  renderWizard();
  fireEvent.click(await screen.findByRole("button", { name: /next/i }));
  await saveProvider("openrouter", { key: "sk-or-dead" });

  await moveFromProviderStep();
  (api.createConnection as any).mockClear();
  await saveProvider("openrouter", { key: "sk-or-test" });
  expect(await screen.findByText(/saved openrouter/i)).toBeInTheDocument();

  // Both answers are about the store the wizard left.
  check.settle({ ok: false, kind: "auth", detail: "No auth credentials found",
                 checked_at: "2026-08-21T09:00:00Z" });
  refresh.settle({ models: [], fetched_at: "", rev: "" });
  fireEvent.click(await screen.findByRole("button", { name: /next/i }));
  expect(await screen.findByText(/listing the new provider's models/i)).toBeInTheDocument();
  expect(screen.queryByText(/No auth credentials found/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /^back$/i }));
  expect(await screen.findByText(/saved openrouter/i)).toBeInTheDocument();
  expect(screen.queryByText(/No auth credentials found/)).not.toBeInTheDocument();
});

test("the move stays pending until the new store has been classified", async () => {
  // Clearing "moving" before the recheck lands lets the user reach the World
  // step while the wizard still believes it is looking at the old store.
  let settle: (v: any) => void = () => {};
  (api.listWorlds as any).mockReturnValue(new Promise((r) => { settle = r; }));
  renderWizard();
  fireEvent.change(await screen.findByLabelText(/storage location/i), { target: { value: "/sync/grimoire" } });
  fireEvent.click(screen.getByRole("button", { name: /^move$/i }));

  await waitFor(() => expect(api.putDataDir).toHaveBeenCalled());
  expect(screen.getByRole("button", { name: /next/i })).toBeDisabled();

  settle([{ id: "saltmarch", name: "Saltmarch" }]);
  await waitFor(() => expect(screen.getByRole("button", { name: /next/i })).toBeEnabled());
});

test("Reset to default is locked while a move is already running", async () => {
  // Two pointer updates in flight: whichever returns last decides the store,
  // and the first to return clears the single pending flag.
  (api.getDataDir as any).mockResolvedValue({
    data_dir: "/sync/grimoire", default: "/home/u/.grimoire",
    is_default: false, source: "custom", exists: true,
  });
  (api.putDataDir as any).mockReturnValue(new Promise(() => {}));   // never settles
  renderWizard();
  fireEvent.change(await screen.findByLabelText(/storage location/i), { target: { value: "/other/grimoire" } });
  fireEvent.click(screen.getByRole("button", { name: /^move$/i }));

  await waitFor(() => expect(screen.getByRole("button", { name: /reset to default/i })).toBeDisabled());
  expect(api.putDataDir).toHaveBeenCalledTimes(1);
});

test("moving onto a library that already has worlds drops the create-a-world step", async () => {
  await moveTo([{ id: "saltmarch", name: "Saltmarch" }]);
  await skipToWorld();

  expect(await screen.findByRole("heading", { name: /already stocked/i })).toBeInTheDocument();
  expect(screen.queryByLabelText(/world name/i)).not.toBeInTheDocument();
  // a world already exists, so the campaign handoff is live without creating one
  expect(screen.getByRole("button", { name: /start a campaign/i })).toBeInTheDocument();
});

test("a move adopts the new library's theme", async () => {
  // The theme lives in the store's own config.md, so after a move the Theme
  // step must mark the new library's card active — otherwise clicking the one
  // that looks active overwrites that library's preference.
  (api.getConfig as any).mockResolvedValue({ first_run: false, theme: "manuscript" });
  await moveTo([{ id: "saltmarch", name: "Saltmarch" }]);
  await waitFor(() => expect(setTheme).toHaveBeenCalledWith("manuscript"));
});

test("finishing locks the wizard until the write settles", async () => {
  // finish() is a config write like the theme's; Back-then-pick-a-theme during
  // a slow one is a second write, and clicking both destinations would make
  // the landing page depend on response order.
  let settle: (v: any) => void = () => {};
  (api.putConfig as any).mockReturnValue(new Promise((r) => { settle = r; }));
  await goToStep(5);
  fireEvent.click(await screen.findByRole("button", { name: /finish later/i }));

  await waitFor(() => expect(screen.getByRole("button", { name: /finish later/i })).toBeDisabled());
  expect(screen.getByRole("button", { name: /^back$/i })).toBeDisabled();
  expect(navigate).not.toHaveBeenCalled();

  settle({});
  await waitFor(() => expect(navigate).toHaveBeenCalledWith("/", { replace: true }));
  expect(api.putConfig).toHaveBeenCalledTimes(1);   // not re-entered
});

test("a provider this library already writes through is adopted, not asked for again", async () => {
  // Reloading /welcome after setting a provider up but before making a world:
  // re-entering the form would create a uniquely-suffixed duplicate of it.
  (api.getConfig as any).mockResolvedValue({
    first_run: true, ready: true,
    active_connection: { id: "my-openrouter", kind: "openrouter", name: "My OpenRouter" },
  });
  await goToStep(2);
  expect(await screen.findByText(/already writes through my openrouter/i)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /save provider/i })).not.toBeInTheDocument();
});

test("a library that cannot generate yet is not mistaken for one that can", async () => {
  (api.getConfig as any).mockResolvedValue({
    first_run: true, ready: false,
    active_connection: { id: "openrouter", kind: "openrouter", name: "OpenRouter" },
  });
  await goToStep(2);
  expect(await screen.findByRole("button", { name: /save provider/i })).toBeInTheDocument();
  expect(screen.queryByText(/already writes through/i)).not.toBeInTheDocument();
});

test("a theme that could not be saved does not stay applied", async () => {
  // Left applied, an unsaved theme looks chosen for the session and then
  // vanishes on reload, which reads as the app losing the setting.
  (api.putConfig as any).mockRejectedValue({ detail: "disk full" });
  await goToStep(4);
  fireEvent.click(await screen.findByText("DARK"));
  await waitFor(() => expect(screen.getByText(/disk full/i)).toBeInTheDocument());
  expect(setTheme).toHaveBeenLastCalledWith("system");   // reverted to the stored one
});

test("finishing reports which store the answer belongs to", async () => {
  (api.putConfig as any).mockResolvedValue({ data_dir: "/sync/grimoire" });
  renderWizard();
  fireEvent.click(await screen.findByRole("button", { name: /skip setup/i }));
  await waitFor(() => expect(onDone).toHaveBeenCalledWith("/sync/grimoire"));
});

test("a move to a library with a working provider adopts it too", async () => {
  // The mount path adopted an existing provider; the move path did not, so
  // step 2 asked again for one the new library already had.
  (api.listWorlds as any).mockResolvedValue([{ id: "saltmarch", name: "Saltmarch" }]);
  (api.getConfig as any).mockResolvedValue({
    first_run: false, theme: "codex", data_dir: "/sync/grimoire", ready: true,
    active_connection: { id: "theirs", kind: "openrouter", name: "Their OpenRouter" },
  });
  renderWizard();
  fireEvent.change(await screen.findByLabelText(/storage location/i), { target: { value: "/sync/grimoire" } });
  fireEvent.click(screen.getByRole("button", { name: /^move$/i }));
  await waitFor(() => expect(api.putDataDir).toHaveBeenCalled());

  fireEvent.click(await screen.findByRole("button", { name: /next/i }));
  expect(await screen.findByText(/already writes through their openrouter/i)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /save provider/i })).not.toBeInTheDocument();
});

test("a failed setup_done write still names the store the wizard moved to", async () => {
  // Falling back to the caller's own idea of the store would key its latch on
  // the pre-move path, and the next config read would redirect straight back
  // into the wizard — the trap the latch exists to prevent.
  (api.getConfig as any).mockResolvedValue({
    first_run: true, theme: "codex", data_dir: "/sync/grimoire", ready: false, active_connection: null,
  });
  renderWizard();
  fireEvent.change(await screen.findByLabelText(/storage location/i), { target: { value: "/sync/grimoire" } });
  fireEvent.click(screen.getByRole("button", { name: /^move$/i }));
  await waitFor(() => expect(api.putDataDir).toHaveBeenCalled());

  (api.putConfig as any).mockRejectedValue(new Error("disk full"));
  fireEvent.click(screen.getByRole("button", { name: /skip setup/i }));
  await waitFor(() => expect(onDone).toHaveBeenCalledWith("/sync/grimoire"));
});

test("the theme step applies the theme and saves it", async () => {
  await goToStep(4);
  fireEvent.click(await screen.findByText("DARK"));
  expect(setTheme).toHaveBeenCalledWith("dark");
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith({ theme: "dark" }));
});

test("the world step creates the first world and hands off to the campaign wizard", async () => {
  await goToStep(5);
  fireEvent.change(await screen.findByLabelText(/world name/i), { target: { value: "Saltmarch" } });
  fireEvent.click(screen.getByRole("button", { name: /^create$/i }));
  await waitFor(() => expect(api.createWorld).toHaveBeenCalledWith("Saltmarch"));

  fireEvent.click(await screen.findByRole("button", { name: /start a campaign/i }));
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith({ setup_done: "on" }));
  await waitFor(() => expect(navigate).toHaveBeenCalledWith("/campaigns/new", { replace: true }));
  expect(onDone).toHaveBeenCalled();
});

test("the last step can be finished without starting a campaign", async () => {
  await goToStep(5);
  // before creating anything — the wizard is not a trap
  fireEvent.click(await screen.findByRole("button", { name: /finish later/i }));
  await waitFor(() => expect(navigate).toHaveBeenCalledWith("/", { replace: true }));
});

test("after creating a world, Finish goes to the campaigns list", async () => {
  await goToStep(5);
  fireEvent.change(await screen.findByLabelText(/world name/i), { target: { value: "Saltmarch" } });
  fireEvent.click(screen.getByRole("button", { name: /^create$/i }));
  fireEvent.click(await screen.findByRole("button", { name: /^finish$/i }));
  await waitFor(() => expect(navigate).toHaveBeenCalledWith("/", { replace: true }));
});

test("skipping setup records the answer and leaves for the campaigns list", async () => {
  renderWizard();
  fireEvent.click(await screen.findByRole("button", { name: /skip setup/i }));
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith({ setup_done: "on" }));
  await waitFor(() => expect(navigate).toHaveBeenCalledWith("/", { replace: true }));
  expect(onDone).toHaveBeenCalled();
});

test("a failed setup_done write still lets the user out", async () => {
  (api.putConfig as any).mockRejectedValue(new Error("disk full"));
  renderWizard();
  fireEvent.click(await screen.findByRole("button", { name: /skip setup/i }));
  await waitFor(() => expect(navigate).toHaveBeenCalledWith("/", { replace: true }));
  expect(onDone).toHaveBeenCalled();
});

test("Back returns to the previous step", async () => {
  await goToStep(2);
  fireEvent.click(await screen.findByRole("button", { name: /^back$/i }));
  expect(await screen.findByLabelText(/storage location/i)).toBeInTheDocument();
});

test("all five steps are named, not only the one you are on", async () => {
  // Five questions is short enough to show whole, and seeing the whole of it
  // is what makes it read as short.
  renderWizard();
  await screen.findByRole("heading", { name: /^grimoire$/i });
  for (const label of ["Storage", "Provider", "Models", "Look", "World"]) {
    expect(screen.getByText(label)).toBeInTheDocument();
  }
});

test("Skip setup is offered beside the step's own Next, not only at the end", async () => {
  // Leaving is a real answer to the wizard's questions; it should not take
  // reading a paragraph to find.
  renderWizard();
  await screen.findByRole("heading", { name: /^grimoire$/i });
  expect(screen.getByRole("button", { name: /skip setup/i })).toBeEnabled();
  expect(screen.getByRole("button", { name: /next/i })).toBeEnabled();
});

test("a Primary stored on a provider that cannot send starts on the one just saved", async () => {
  // The library's Primary names a keyless provider -- the state this step is
  // for. The provider made in step 2 is the one that works.
  (api.getInferenceSettings as any).mockResolvedValue(settings({
    roles: { ...settings().roles,
             primary: card({ provider: "saltmarch", model: "mara-large", preset: "winifred" }) },
    providers: [
      { id: "saltmarch", name: "Saltmarch Router", kind: "openrouter", preset: "openrouter",
        usable: false, problem: "No API key set" },
      { id: "openrouter", name: "OpenRouter", kind: "openrouter", preset: "openrouter",
        usable: true, problem: null },
    ],
  }));
  await goToStep(2);
  await saveProvider("openrouter", { key: "sk-or-test" });
  fireEvent.click(await screen.findByRole("button", { name: /next/i }));
  const primary = await screen.findByRole("region", { name: "Primary" });
  expect(within(primary).getByLabelText("Provider")).toHaveValue("openrouter");
  // The old provider's model is not carried onto a provider that may not serve it.
  expect(within(primary).queryByRole("radio", { checked: true })).not.toBeInTheDocument();
});

test("a Primary stored on a provider that can send is kept", async () => {
  (api.getInferenceSettings as any).mockResolvedValue(settings({
    roles: { ...settings().roles,
             primary: card({ provider: "saltmarch", model: "mara-large", preset: "" }) },
  }));
  await goToStep(2);
  await saveProvider("openrouter", { key: "sk-or-test" });
  fireEvent.click(await screen.findByRole("button", { name: /next/i }));
  const primary = await screen.findByRole("region", { name: "Primary" });
  expect(within(primary).getByLabelText("Provider")).toHaveValue("saltmarch");
});

test("a failed presets read can be tried again", async () => {
  (api.listProviderPresets as any).mockRejectedValueOnce(
    Object.assign(new Error("offline"), { detail: "the presets are unreadable" }));
  await goToStep(2);
  expect(await screen.findByText(/Couldn't list the kinds of provider: the presets are unreadable/))
    .toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(await screen.findByLabelText("Provider")).toBeInTheDocument();
  expect(screen.queryByText(/Couldn't list the kinds of provider/)).not.toBeInTheDocument();
});
