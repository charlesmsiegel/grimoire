import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { createMemoryRouter, Link, MemoryRouter, Route, RouterProvider, Routes } from "react-router-dom";
import { ArtNavigationProvider } from "../components/art/ArtNavigationProvider";
import { api, type CampaignSettings } from "../api/client";
import { configChanged } from "../appEvents";
import CampaignSettingsView from "./CampaignSettingsView";

// Data-router navigation bridges jsdom's AbortSignal to Node's Request in
// the same way as ArtNavigationProvider's own navigation tests.
const NativeRequest = globalThis.Request;
class TestRequest extends NativeRequest {
  constructor(input: RequestInfo | URL, init?: RequestInit) {
    const { signal, ...rest } = init ?? {};
    super(input, rest);
    if (signal) Object.defineProperty(this, "signal", { value: signal });
  }
}
afterEach(() => vi.unstubAllGlobals());

vi.mock("../api/client", () => ({ api: { getCampaignSettings: vi.fn(), putCampaignSettings: vi.fn() } }));
vi.mock("../components/inference/CampaignModels", () => ({
  CampaignModels: ({ cid }: { cid: string }) => <p>Models for {cid}</p>,
}));
vi.mock("../components/CalendarConfig", () => ({
  CalendarConfig: ({ scope }: { scope: { kind: string; id: string } }) => <p>Calendar for {scope.kind}/{scope.id}</p>,
}));

const inherited = {
  context_scan_depth: "8", context_budget: "12000", recap_depth: "5", archive_depth: "3",
  rolling_summary_every: "10", scene_break_every: "6", system_prompt: "Mara waits.",
  user_label: "You", assistant_label: "Grimoire",
};
function view(overrides: CampaignSettings["overrides"] = {}): CampaignSettings {
  return { inherited, overrides, effective: { ...inherited, ...overrides } };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => { resolve = r; });
  return { promise, resolve };
}
function surface() {
  return <><Link to="/campaigns/realm/settings?section=context">Other campaign</Link>
    <Link to="/elsewhere">Leave settings</Link><Routes>
      <Route path="/campaigns/:cid/settings" element={<CampaignSettingsView />} />
      <Route path="/elsewhere" element={<p>Elsewhere</p>} />
    </Routes></>;
}
function open(section = "context") {
  return render(<MemoryRouter initialEntries={[`/campaigns/saltmarch/settings?section=${section}`]}>
    {surface()}</MemoryRouter>);
}
beforeEach(() => {
  vi.stubGlobal("Request", TestRequest);
  vi.resetAllMocks();
  vi.mocked(api.getCampaignSettings).mockResolvedValue(view());
  vi.mocked(api.putCampaignSettings).mockImplementation(async (_cid, patch) => {
    const overrides: CampaignSettings["overrides"] = {};
    for (const [key, value] of Object.entries(patch)) {
      if (value !== null) overrides[key as keyof typeof overrides] = value;
    }
    return view(overrides);
  });
});

test("opens read-only effective and inherited values, then explicitly edits", async () => {
  open();
  const field = await screen.findByRole("group", { name: "Context budget" });
  expect(within(field).getByText("12000")).toBeInTheDocument();
  expect(within(field).getByText(/Inherited/)).toBeInTheDocument();
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  expect(screen.getByRole("checkbox", { name: "Override Context budget" })).not.toBeChecked();
  expect(screen.getByRole("textbox", { name: "Context budget" })).toBeDisabled();
  expect(screen.getByRole("link", { name: /Storage/ })).toHaveAttribute("href", "/config?section=storage");
});

test("section selection starts main at the top so the new heading stays visible", async () => {
  open("advanced");
  await screen.findByRole("button", { name: "Edit" });
  const main = screen.getByRole("main");
  const scrollTo = vi.fn();
  main.scrollTo = scrollTo;
  fireEvent.click(screen.getByRole("button", { name: "Context & summaries" }));
  expect(screen.getByRole("main")).toBe(main);
  expect(scrollTo).toHaveBeenCalledWith(0, 0);
});

test("saves only changed overrides, including zero and explicit empty prompt, across sections", async () => {
  open();
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Override Context budget" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Context budget" }), { target: { value: "0" } });
  fireEvent.click(screen.getByRole("button", { name: "Advanced" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Override System prompt" }));
  fireEvent.change(screen.getByRole("textbox", { name: "System prompt" }), { target: { value: "" } });
  fireEvent.click(screen.getByRole("button", { name: "Context & summaries" }));
  expect(screen.getByRole("textbox", { name: "Context budget" })).toHaveValue("0");
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.putCampaignSettings).toHaveBeenCalledWith("saltmarch", {
    context_budget: "0", system_prompt: "",
  }));
  expect(await screen.findByRole("button", { name: "Edit" })).toBeInTheDocument();
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Advanced" }));
  expect(screen.getByText("Empty override — suppresses the global system prompt.")).toBeInTheDocument();
});

test("unchecking a stored override sends null; Cancel restores saved values", async () => {
  vi.mocked(api.getCampaignSettings).mockResolvedValue(view({ context_budget: "0" }));
  open();
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Context budget" }), { target: { value: "7" } });
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(api.putCampaignSettings).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  expect(screen.getByRole("textbox", { name: "Context budget" })).toHaveValue("0");
  fireEvent.click(screen.getByRole("checkbox", { name: "Override Context budget" }));
  expect(screen.getByRole("textbox", { name: "Context budget" })).toHaveValue("12000");
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.putCampaignSettings).toHaveBeenCalledWith("saltmarch", { context_budget: null }));
});

test("failed saves retain the draft and pending saves cannot be duplicated", async () => {
  const pending = deferred<CampaignSettings>();
  vi.mocked(api.putCampaignSettings).mockRejectedValueOnce(new Error("offline")).mockReturnValueOnce(pending.promise);
  open();
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Override Recap depth" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Recap depth" }), { target: { value: "0" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText(/offline/)).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "Recap depth" })).toHaveValue("0");
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  const saving = screen.getByRole("button", { name: /Saving/ });
  expect(saving).toBeDisabled();
  fireEvent.click(saving);
  expect(api.putCampaignSettings).toHaveBeenCalledTimes(2);
  await act(async () => { pending.resolve(view({ recap_depth: "0" })); });
  expect(await screen.findByRole("button", { name: "Edit" })).toBeInTheDocument();
});

test("a failed save stays visible through a config change that re-reads the page", async () => {
  vi.mocked(api.putCampaignSettings).mockRejectedValueOnce(new Error("offline"));
  open();
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Override Recap depth" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Recap depth" }), { target: { value: "0" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText(/offline/)).toBeInTheDocument();
  act(() => { configChanged(); });
  await waitFor(() => expect(api.getCampaignSettings).toHaveBeenCalledTimes(2));
  expect(screen.getByText(/offline/)).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "Recap depth" })).toHaveValue("0");
});

test("read failure is visible and can be retried without inventing inherited values", async () => {
  vi.mocked(api.getCampaignSettings).mockRejectedValueOnce(new Error("offline"));
  open();
  expect(await screen.findByText(/offline/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /Try again/ }));
  expect(await screen.findByRole("button", { name: "Edit" })).toBeInTheDocument();
});

test("global changes refresh inherited values without replacing edits or writing untouched overrides", async () => {
  open();
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Override Context budget" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Context budget" }), { target: { value: "7" } });
  const changed = view({ recap_depth: "42" });
  changed.inherited = { ...changed.inherited, context_budget: "24000" };
  vi.mocked(api.getCampaignSettings).mockResolvedValue(changed);
  act(() => { configChanged(); });
  expect(await screen.findByText("Global default: 24000")).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "Context budget" })).toHaveValue("7");
  expect(screen.getByRole("textbox", { name: "Recap depth" })).toHaveValue("42");
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.putCampaignSettings).toHaveBeenCalledWith("saltmarch", { context_budget: "7" }));
});

test("the overrides draft survives Models and Calendar without sharing their Save buttons", async () => {
  open();
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Override Recap depth" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Recap depth" }), { target: { value: "7" } });
  for (const name of ["Models", "Calendar"]) {
    fireEvent.click(screen.getByRole("button", { name }));
    expect(screen.queryByRole("button", { name: "Save" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument();
  }
  fireEvent.click(screen.getByRole("button", { name: "Context & summaries" }));
  expect(screen.getByRole("textbox", { name: "Recap depth" })).toHaveValue("7");
  expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
});

test("a pre-save refresh cannot replace the successful save's fresh view", async () => {
  const old = deferred<CampaignSettings>();
  open();
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Override Context budget" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Context budget" }), { target: { value: "7" } });
  vi.mocked(api.getCampaignSettings).mockReturnValueOnce(old.promise);
  act(() => { configChanged(); });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByRole("button", { name: "Edit" });
  await act(async () => { old.resolve(view()); });
  expect(within(screen.getByRole("group", { name: "Context budget" })).getByText("7")).toBeInTheDocument();
});

test.each(["models", "calendar"])("deep links reuse %s controls for this campaign", async (section) => {
  open(section);
  expect(await screen.findByText(section === "models" ? "Models for saltmarch" : "Calendar for campaign/saltmarch")).toBeInTheDocument();
});

test("late reads from another campaign cannot replace the current view", async () => {
  const old = deferred<CampaignSettings>();
  vi.mocked(api.getCampaignSettings).mockReturnValueOnce(old.promise).mockResolvedValueOnce(view({ context_budget: "42" }));
  open();
  fireEvent.click(screen.getByRole("link", { name: "Other campaign" }));
  const field = await screen.findByRole("group", { name: "Context budget" });
  expect(within(field).getByText("42")).toBeInTheDocument();
  await act(async () => { old.resolve(view({ context_budget: "7" })); });
  expect(within(field).queryByText("7")).not.toBeInTheDocument();
});

test("dirty navigation asks before discarding and late saves stay in their campaign", async () => {
  const pending = deferred<CampaignSettings>();
  vi.mocked(api.putCampaignSettings).mockReturnValueOnce(pending.promise);
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  const router = createMemoryRouter([{ path: "*", element: <ArtNavigationProvider>{surface()}</ArtNavigationProvider> }],
    { initialEntries: ["/campaigns/saltmarch/settings?section=context"] });
  render(<RouterProvider router={router} />);
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.click(screen.getByRole("checkbox", { name: "Override Context budget" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Context budget" }), { target: { value: "7" } });
  fireEvent.click(screen.getByRole("link", { name: "Leave settings" }));
  expect(confirm).toHaveBeenCalled();
  expect(screen.getByRole("textbox", { name: "Context budget" })).toHaveValue("7");
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  confirm.mockReturnValue(true);
  vi.mocked(api.getCampaignSettings).mockResolvedValue(view({ context_budget: "42" }));
  fireEvent.click(screen.getByRole("link", { name: "Other campaign" }));
  const field = await screen.findByRole("group", { name: "Context budget" });
  expect(within(field).getByText("42")).toBeInTheDocument();
  await act(async () => { pending.resolve(view({ context_budget: "7" })); });
  expect(within(field).queryByText("7")).not.toBeInTheDocument();
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  confirm.mockRestore();
});
