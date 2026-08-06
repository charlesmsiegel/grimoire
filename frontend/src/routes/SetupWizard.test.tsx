import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import SetupWizard from "./SetupWizard";

const navigate = vi.fn();
vi.mock("react-router-dom", async () => ({
  ...(await vi.importActual<any>("react-router-dom")),
  useNavigate: () => navigate,
}));

vi.mock("../api/client", () => ({
  ApiError: class ApiError extends Error {},
  api: {
    getDataDir: vi.fn(), putDataDir: vi.fn(), putConfig: vi.fn(), getConfig: vi.fn(),
    createConnection: vi.fn(), createWorld: vi.fn(), listWorlds: vi.fn(),
  },
}));
vi.mock("../api/models", () => ({ getModels: vi.fn(), priceLabel: () => "", contextLabel: () => "" }));

const setTheme = vi.fn();
vi.mock("../theme/ThemeProvider", () => ({ useTheme: () => ({ name: "codex", setTheme }) }));

import { api } from "../api/client";
import { getModels } from "../api/models";

const onDone = vi.fn();

beforeEach(() => {
  vi.clearAllMocks();
  (getModels as any).mockResolvedValue([]);
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
  (api.createConnection as any).mockResolvedValue({ id: "openrouter" });
  (api.createWorld as any).mockResolvedValue({ id: "saltmarch" });
});

function renderWizard() {
  render(<MemoryRouter><SetupWizard onDone={onDone} /></MemoryRouter>);
}

/** Walk to a step by clicking Next/Skip, which is the only way in. */
async function goToStep(n: number) {
  renderWizard();
  if (n >= 2) fireEvent.click(await screen.findByRole("button", { name: /next/i }));
  if (n >= 3) fireEvent.click(await screen.findByRole("button", { name: /^skip$/i }));
  if (n >= 4) fireEvent.click(await screen.findByRole("button", { name: /next/i }));
}

test("opens on the storage step, showing the current data dir", async () => {
  renderWizard();
  expect(await screen.findByRole("heading", { name: /welcome to grimoire/i })).toBeInTheDocument();
  expect(await screen.findByLabelText(/storage location/i)).toHaveValue("/home/u/.grimoire");
});

test("the storage step moves the data dir through the same API Config uses", async () => {
  renderWizard();
  const input = await screen.findByLabelText(/storage location/i);
  fireEvent.change(input, { target: { value: "/sync/grimoire" } });
  fireEvent.click(screen.getByRole("button", { name: /^move$/i }));
  await waitFor(() => expect(api.putDataDir).toHaveBeenCalledWith("/sync/grimoire"));
});

test("the connection step creates the connection and makes it active", async () => {
  await goToStep(2);
  fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "OpenRouter" } });
  fireEvent.change(screen.getByLabelText("API key"), { target: { value: "sk-or-test" } });
  fireEvent.click(screen.getByRole("button", { name: /save connection/i }));

  await waitFor(() => expect(api.createConnection).toHaveBeenCalledWith(
    expect.objectContaining({ kind: "openrouter", name: "OpenRouter", api_key: "sk-or-test" })));
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith({ active_connection_id: "openrouter" }));
  expect(await screen.findByText(/connected to openrouter/i)).toBeInTheDocument();
});

test("an OpenRouter connection cannot be saved without a key", async () => {
  // The backend reports a keyless OpenRouter connection as `ready: false`, so
  // accepting one here would print "Connected ✓" over something unusable.
  await goToStep(2);
  fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "OpenRouter" } });
  expect(screen.getByRole("button", { name: /save connection/i })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("API key"), { target: { value: "sk-or-test" } });
  expect(screen.getByRole("button", { name: /save connection/i })).toBeEnabled();
});

test("a custom endpoint cannot be saved without a base URL", async () => {
  await goToStep(2);
  fireEvent.change(await screen.findByLabelText("Kind"), { target: { value: "openai_compatible" } });
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Local" } });
  expect(screen.getByRole("button", { name: /save connection/i })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Base URL"), { target: { value: "http://localhost:1234/v1" } });
  expect(screen.getByRole("button", { name: /save connection/i })).toBeEnabled();
});

test("Claude needs neither a key nor a URL — a name is enough", async () => {
  await goToStep(2);
  fireEvent.change(await screen.findByLabelText("Kind"), { target: { value: "claude" } });
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Claude" } });
  expect(screen.getByRole("button", { name: /save connection/i })).toBeEnabled();
});

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
  await goToStep(3);
  fireEvent.click(await screen.findByText("ASTRAL"));

  // PUT /api/config is a read-modify-write of one file; letting the user reach
  // Finish here would race the setup_done write against this one.
  await waitFor(() => expect(screen.getByRole("button", { name: /saving/i })).toBeDisabled());
  expect(screen.getByRole("button", { name: /skip setup/i })).toBeDisabled();

  settle({});
  await waitFor(() => expect(screen.getByRole("button", { name: /next/i })).toBeEnabled());
});

test("a failed activation retries the activation, not the creation", async () => {
  // create_connection uniquifies the slug, so re-submitting the form would
  // leave a `-2` connection behind on every retry.
  (api.putConfig as any).mockRejectedValueOnce({ detail: "could not write config" });
  await goToStep(2);
  fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "OpenRouter" } });
  fireEvent.change(screen.getByLabelText("API key"), { target: { value: "sk-or-test" } });
  fireEvent.click(screen.getByRole("button", { name: /save connection/i }));

  expect(await screen.findByText(/could not be made active/i)).toBeInTheDocument();
  expect(api.createConnection).toHaveBeenCalledTimes(1);

  fireEvent.click(screen.getByRole("button", { name: /retry activation/i }));
  await waitFor(() => expect(screen.getByText(/connected to openrouter/i)).toBeInTheDocument());
  expect(api.createConnection).toHaveBeenCalledTimes(1);   // still one connection on disk
});

test("theme cards are locked while a pick is saving, so two picks cannot race", async () => {
  let settle: (v: any) => void = () => {};
  (api.putConfig as any).mockReturnValue(new Promise((r) => { settle = r; }));
  await goToStep(3);
  fireEvent.click(await screen.findByText("ASTRAL"));

  await waitFor(() => expect(screen.getByText("MANUSCRIPT")).toBeDisabled());
  expect(screen.getByText("ASTRAL")).toBeDisabled();

  settle({});
  await waitFor(() => expect(screen.getByText("MANUSCRIPT")).toBeEnabled());
});

test("Finish later is locked while the world is being created", async () => {
  let settle: (v: any) => void = () => {};
  (api.createWorld as any).mockReturnValue(new Promise((r) => { settle = r; }));
  await goToStep(4);
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

test("an emptied store that merely dismissed setup is not treated as stocked", async () => {
  // `first_run: false` also describes an EMPTY store whose setup was skipped
  // before. Calling that stocked hides the create form and hands off into
  // CampaignWizard, which cannot get past step one with no world to pick — so
  // the verdict has to come from the worlds, not from first_run.
  (api.getConfig as any).mockResolvedValue({ first_run: false });
  await moveTo([]);
  fireEvent.click(await screen.findByRole("button", { name: /next/i }));
  fireEvent.click(await screen.findByRole("button", { name: /^skip$/i }));
  fireEvent.click(await screen.findByRole("button", { name: /next/i }));

  expect(await screen.findByLabelText(/world name/i)).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: /already stocked/i })).not.toBeInTheDocument();
});

test("a move forgets the connection and world recorded in the previous store", async () => {
  renderWizard();
  fireEvent.click(await screen.findByRole("button", { name: /next/i }));
  fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "OpenRouter" } });
  fireEvent.change(screen.getByLabelText("API key"), { target: { value: "sk-or-test" } });
  fireEvent.click(screen.getByRole("button", { name: /save connection/i }));
  expect(await screen.findByText(/connected to openrouter/i)).toBeInTheDocument();

  // back to step 1 and repoint: that connection lives in the store we just left
  fireEvent.click(screen.getByRole("button", { name: /^back$/i }));
  fireEvent.change(await screen.findByLabelText(/storage location/i), { target: { value: "/sync/grimoire" } });
  fireEvent.click(screen.getByRole("button", { name: /^move$/i }));
  await waitFor(() => expect(api.putDataDir).toHaveBeenCalled());

  fireEvent.click(await screen.findByRole("button", { name: /next/i }));
  expect(await screen.findByLabelText("Name")).toBeInTheDocument();   // the form, not "Connected ✓"
  expect(screen.queryByText(/connected to openrouter/i)).not.toBeInTheDocument();
});

test("Skip setup is locked while a connection is being activated", async () => {
  let settle: (v: any) => void = () => {};
  (api.putConfig as any).mockReturnValue(new Promise((r) => { settle = r; }));
  await goToStep(2);
  fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "OpenRouter" } });
  fireEvent.change(screen.getByLabelText("API key"), { target: { value: "sk-or-test" } });
  fireEvent.click(screen.getByRole("button", { name: /save connection/i }));

  // finish() writes setup_done; both are unlocked read-modify-writes of config.md
  await waitFor(() => expect(screen.getByRole("button", { name: /skip setup/i })).toBeDisabled());
  settle({});
  await waitFor(() => expect(screen.getByRole("button", { name: /skip setup/i })).toBeEnabled());
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
  fireEvent.click(await screen.findByRole("button", { name: /next/i }));
  fireEvent.click(await screen.findByRole("button", { name: /^skip$/i }));
  fireEvent.click(await screen.findByRole("button", { name: /next/i }));

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
  await goToStep(4);
  fireEvent.click(await screen.findByRole("button", { name: /finish later/i }));

  await waitFor(() => expect(screen.getByRole("button", { name: /finish later/i })).toBeDisabled());
  expect(screen.getByRole("button", { name: /^back$/i })).toBeDisabled();
  expect(navigate).not.toHaveBeenCalled();

  settle({});
  await waitFor(() => expect(navigate).toHaveBeenCalledWith("/", { replace: true }));
  expect(api.putConfig).toHaveBeenCalledTimes(1);   // not re-entered
});

test("a connection this store already has is adopted, not asked for again", async () => {
  // Reloading /welcome after saving a connection but before making a world:
  // re-entering the form would create a uniquely-suffixed duplicate of the
  // connection that is already active.
  (api.getConfig as any).mockResolvedValue({
    first_run: true, ready: true,
    active_connection: { id: "my-openrouter", kind: "openrouter", name: "My OpenRouter" },
  });
  await goToStep(2);
  expect(await screen.findByText(/connected to my openrouter/i)).toBeInTheDocument();
  expect(screen.queryByLabelText("Name")).not.toBeInTheDocument();
});

test("a fresh store's unusable default connection is not mistaken for a real one", async () => {
  // A brand-new store ships with an OpenRouter connection selected and no key.
  (api.getConfig as any).mockResolvedValue({
    first_run: true, ready: false,
    active_connection: { id: "openrouter", kind: "openrouter", name: "OpenRouter" },
  });
  await goToStep(2);
  expect(await screen.findByLabelText("Name")).toBeInTheDocument();
  expect(screen.queryByText(/connected to/i)).not.toBeInTheDocument();
});

test("a theme that could not be saved does not stay applied", async () => {
  // Left applied, an unsaved theme looks chosen for the session and then
  // vanishes on reload, which reads as the app losing the setting.
  (api.putConfig as any).mockRejectedValue({ detail: "disk full" });
  await goToStep(3);
  fireEvent.click(await screen.findByText("ASTRAL"));
  await waitFor(() => expect(screen.getByText(/disk full/i)).toBeInTheDocument());
  expect(setTheme).toHaveBeenLastCalledWith("codex");   // reverted to the stored one
});

test("finishing reports which store the answer belongs to", async () => {
  (api.putConfig as any).mockResolvedValue({ data_dir: "/sync/grimoire" });
  renderWizard();
  fireEvent.click(await screen.findByRole("button", { name: /skip setup/i }));
  await waitFor(() => expect(onDone).toHaveBeenCalledWith("/sync/grimoire"));
});

test("a move to a library with a working connection adopts it too", async () => {
  // The mount path adopted an existing connection; the move path did not, so
  // step 2 asked again for one the new library already had — and saving that
  // form creates a duplicate of the active connection.
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
  expect(await screen.findByText(/connected to their openrouter/i)).toBeInTheDocument();
  expect(screen.queryByLabelText("Name")).not.toBeInTheDocument();
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

test("the connection step is skippable — playing by hand is allowed", async () => {
  await goToStep(2);
  fireEvent.click(await screen.findByRole("button", { name: /^skip$/i }));
  expect(await screen.findByRole("heading", { name: /pick a look/i })).toBeInTheDocument();
  expect(api.createConnection).not.toHaveBeenCalled();
});

test("the theme step applies the theme and saves it", async () => {
  await goToStep(3);
  fireEvent.click(await screen.findByText("ASTRAL"));
  expect(setTheme).toHaveBeenCalledWith("astral");
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith({ theme: "astral" }));
});

test("the world step creates the first world and hands off to the campaign wizard", async () => {
  await goToStep(4);
  fireEvent.change(await screen.findByLabelText(/world name/i), { target: { value: "Saltmarch" } });
  fireEvent.click(screen.getByRole("button", { name: /^create$/i }));
  await waitFor(() => expect(api.createWorld).toHaveBeenCalledWith("Saltmarch"));

  fireEvent.click(await screen.findByRole("button", { name: /start a campaign/i }));
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith({ setup_done: "on" }));
  await waitFor(() => expect(navigate).toHaveBeenCalledWith("/campaigns/new", { replace: true }));
  expect(onDone).toHaveBeenCalled();
});

test("the last step can be finished without starting a campaign", async () => {
  await goToStep(4);
  // before creating anything — the wizard is not a trap
  fireEvent.click(await screen.findByRole("button", { name: /finish later/i }));
  await waitFor(() => expect(navigate).toHaveBeenCalledWith("/", { replace: true }));
});

test("after creating a world, Finish goes to the campaigns list", async () => {
  await goToStep(4);
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

test("a connection that fails to save reports why and stays on the step", async () => {
  (api.createConnection as any).mockRejectedValue({ detail: "name already taken" });
  await goToStep(2);
  fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "OpenRouter" } });
  fireEvent.change(screen.getByLabelText("API key"), { target: { value: "sk-or-test" } });
  fireEvent.click(screen.getByRole("button", { name: /save connection/i }));
  expect(await screen.findByText(/name already taken/i)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /save connection/i })).toBeInTheDocument();
});

test("Back returns to the previous step", async () => {
  await goToStep(2);
  fireEvent.click(await screen.findByRole("button", { name: /^back$/i }));
  expect(await screen.findByLabelText(/storage location/i)).toBeInTheDocument();
});
