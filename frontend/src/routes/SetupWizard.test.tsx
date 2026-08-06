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
    getDataDir: vi.fn(), putDataDir: vi.fn(), putConfig: vi.fn(),
    createConnection: vi.fn(), createWorld: vi.fn(),
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
