import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { createMemoryRouter, RouterProvider, Route, Routes } from "react-router-dom";
import { ArtNavigationProvider } from "../components/art/ArtNavigationProvider";
import { api, ApiError } from "../api/client";
import CampaignSettingsView from "./CampaignSettingsView";
import { fixture } from "../testkit/fixture";
import type { InferenceSettings, ResolvedSelection, RoleCard } from "../api/types";

// These are the real reused editors: mocked panels cannot prove that their
// dirty state reaches the page's route/unload protection (#73).
vi.mock("../api/client", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  return { ...actual, api: {
    getCampaignSettings: vi.fn(), getCalendarConfig: vi.fn(), getCalendarProviders: vi.fn(),
    setCalendarConfig: vi.fn(), getCampaignInference: vi.fn(), putCampaignInference: vi.fn(),
    readConnectionCapabilities: vi.fn(), previewControls: vi.fn(),
  } };
});
const NativeRequest = globalThis.Request;
class TestRequest extends NativeRequest {
  constructor(input: RequestInfo | URL, init?: RequestInit) {
    const { signal, ...rest } = init ?? {};
    super(input, rest);
    if (signal) Object.defineProperty(this, "signal", { value: signal });
  }
}
const EMPTY = { provider: "", model: "", preset: "" };
function inference(): InferenceSettings {
  const resolves: ResolvedSelection = { provider: "realm", provider_name: "Realm", model: "realm/small", preset: "",
    preset_name: "", via: "role", scope: "global" };
  const role: Partial<RoleCard> = { stored: EMPTY, fallback: EMPTY, resolves, inherits: resolves, problem: null,
    fallback_missing: [], fallback_problem: null, rate: null, limits: null };
  return fixture({
    format: "2", newer: false, migration: { state: "done", reason: "", skipped: [] },
    roles: { primary: { ...role, stored: { provider: "realm", model: "realm/small", preset: "" } },
      fast: role, decision: role },
    routes: [{ key: "scene", label: "Scene turns", hint: "Story turns.", tasks: ["chat"],
      operation: "generate", default_role: "primary", requires: [], campaign_scoped: true, use: "",
      pin: EMPTY, preset: "", problem: null, fallback_missing: [], fallback_problem: null, rate: null,
      role: "primary", resolves, inherits: resolves }],
    providers: [{ id: "realm", name: "Realm", kind: "openai_compatible", preset: "custom", usable: true, problem: null }],
    presets: [], preset_clear: "⁣none", retirement_notes: [],
  });
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => { resolve = r; });
  return { promise, resolve };
}
function setup(section: string) {
  const router = createMemoryRouter([{ path: "*", element: <ArtNavigationProvider><Routes>
    <Route path="/campaigns/:cid/settings" element={<CampaignSettingsView />} />
    <Route path="/campaigns/:cid" element={<p>Campaign hub destination</p>} />
  </Routes></ArtNavigationProvider> }], { initialEntries: [`/campaigns/saltmarch/settings?section=${section}`] });
  render(<RouterProvider router={router} />);
  return router;
}
function unloadBlocked() {
  const event = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(event);
  return event.defaultPrevented;
}
beforeEach(() => {
  vi.resetAllMocks();
  vi.stubGlobal("Request", TestRequest);
  const values = { context_scan_depth: "8", context_budget: "12000", recap_depth: "5", archive_depth: "3",
    rolling_summary_every: "10", scene_break_every: "6", system_prompt: "", user_label: "You", assistant_label: "Grimoire" };
  vi.mocked(api.getCampaignSettings).mockResolvedValue({ overrides: {}, inherited: values, effective: values });
  vi.mocked(api.getCalendarConfig).mockResolvedValue({ primary: { provider: "gregorian", region: "US", custom_holidays: [], anchor: null },
    secondary: null, confirmed: false, stale_after_days: 30, warn_days: 7 });
  vi.mocked(api.getCalendarProviders).mockResolvedValue({ providers: [{ id: "gregorian", name: "Gregorian" }] });
  vi.mocked(api.setCalendarConfig).mockResolvedValue({ ok: true });
  vi.mocked(api.getCampaignInference).mockResolvedValue(inference());
  vi.mocked(api.putCampaignInference).mockResolvedValue(inference());
  vi.mocked(api.readConnectionCapabilities).mockResolvedValue(fixture({ need: "generate", reason: null, hidden: [],
    groups: { fits: [{ id: "realm/small", name: "realm/small", capabilities: {} }], unverified: [] } }));
  vi.mocked(api.previewControls).mockResolvedValue(fixture({ controls: {} }));
});
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

test("real Calendar edits guard route/unload navigation, retain across sections, and clear on revert/cancel", async () => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  setup("calendar");
  const warn = await screen.findByRole("spinbutton", { name: "Warn ahead days" });
  expect(unloadBlocked()).toBe(false);
  fireEvent.change(warn, { target: { value: "14" } });
  expect(unloadBlocked()).toBe(true);
  fireEvent.click(screen.getByRole("link", { name: "Campaign hub" }));
  expect(confirm).toHaveBeenCalledTimes(1);
  expect(warn).toHaveValue(14);
  fireEvent.click(screen.getByRole("button", { name: "Advanced" }));
  fireEvent.click(screen.getByRole("button", { name: "Calendar" }));
  expect(warn).toHaveValue(14);
  fireEvent.change(warn, { target: { value: "7" } });
  expect(unloadBlocked()).toBe(false);
  fireEvent.change(warn, { target: { value: "21" } });
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(warn).toHaveValue(7);
  expect(unloadBlocked()).toBe(false);
  fireEvent.click(screen.getByRole("link", { name: "Campaign hub" }));
  expect(await screen.findByText("Campaign hub destination")).toBeInTheDocument();
  expect(confirm).toHaveBeenCalledTimes(1);
});

test("Calendar failures retain protection; pending saves cannot duplicate and later edits stay dirty", async () => {
  const pending = deferred<{ ok: boolean }>();
  vi.mocked(api.setCalendarConfig).mockRejectedValueOnce(new Error("offline")).mockReturnValueOnce(pending.promise);
  setup("calendar");
  const warn = await screen.findByRole("spinbutton", { name: "Warn ahead days" });
  fireEvent.change(warn, { target: { value: "14" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByText(/offline/);
  expect(unloadBlocked()).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  const saving = screen.getByRole("button", { name: /Saving/ });
  expect(saving).toBeDisabled();
  fireEvent.click(saving);
  expect(api.setCalendarConfig).toHaveBeenCalledTimes(2);
  fireEvent.change(warn, { target: { value: "21" } });
  await act(async () => { pending.resolve({ ok: true }); });
  expect(warn).toHaveValue(21);
  expect(unloadBlocked()).toBe(true);
  fireEvent.change(warn, { target: { value: "14" } });
  expect(unloadBlocked()).toBe(false);
  fireEvent.change(warn, { target: { value: "28" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByText("Saved.");
  expect(unloadBlocked()).toBe(false);
});

test("Calendar submits a pending save once even when Save is clicked twice", async () => {
  const pending = deferred<{ ok: boolean }>();
  vi.mocked(api.setCalendarConfig).mockReturnValue(pending.promise);
  setup("calendar");
  fireEvent.change(await screen.findByRole("spinbutton", { name: "Warn ahead days" }), { target: { value: "14" } });
  const save = screen.getByRole("button", { name: "Save" });
  fireEvent.click(save);
  fireEvent.click(save);
  expect(api.setCalendarConfig).toHaveBeenCalledTimes(1);
  await act(async () => { pending.resolve({ ok: true }); });
});

async function editModel(kind: "role" | "route") {
  setup("models");
  if (kind === "route") {
    fireEvent.click(await screen.findByRole("button", { name: "Routes" }));
    fireEvent.click(screen.getByRole("button", { name: /^Scene turns/ }));
  } else fireEvent.click(await screen.findByRole("button", { name: /^Primary/ }));
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  const input = kind === "role" ? screen.getByRole("combobox", { name: "Primary for this campaign" })
    : screen.getByRole("combobox", { name: /Role \(default/ });
  return { input, original: kind === "role" ? "own" : "", changed: kind === "role" ? "" : "fast" };
}

test.each(["role", "route"] as const)("real Models %s edits protect route/unload and internal row changes", async (kind) => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  const { input, original, changed } = await editModel(kind);
  expect(unloadBlocked()).toBe(false);
  fireEvent.change(input, { target: { value: changed } });
  expect(unloadBlocked()).toBe(true);
  fireEvent.click(screen.getByRole("link", { name: "Campaign hub" }));
  expect(confirm).toHaveBeenCalledTimes(1);
  fireEvent.click(within(screen.getByRole("group", { name: "Roles" })).getByRole("button", { name: /^Fast/ }));
  expect(confirm).toHaveBeenCalledTimes(2);
  expect(input).toHaveValue(changed);
  fireEvent.click(screen.getByRole("button", { name: "Advanced" }));
  fireEvent.click(screen.getByRole("button", { name: "Models" }));
  expect(input).toHaveValue(changed);
  fireEvent.change(input, { target: { value: original } });
  expect(unloadBlocked()).toBe(false);
  fireEvent.change(input, { target: { value: changed } });
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(unloadBlocked()).toBe(false);
});

test.each(["role", "route"] as const)("Models %s keeps failed edits and clears protection on successful save", async (kind) => {
  const pending = deferred<ReturnType<typeof inference>>();
  vi.mocked(api.putCampaignInference).mockRejectedValueOnce(new Error("offline")).mockReturnValueOnce(pending.promise);
  const { input, changed } = await editModel(kind);
  fireEvent.change(input, { target: { value: changed } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByText(/offline/);
  expect(unloadBlocked()).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  const save = screen.getByRole("button", { name: "Save" });
  expect(save).toBeDisabled();
  fireEvent.click(save);
  expect(api.putCampaignInference).toHaveBeenCalledTimes(2);
  expect(input).toBeDisabled();
  await act(async () => { pending.resolve(inference()); });
  await screen.findByRole("button", { name: "Edit" });
  expect(unloadBlocked()).toBe(false);
});

test("each retained editor contributes independently to the page guard", async () => {
  setup("calendar");
  fireEvent.change(await screen.findByRole("spinbutton", { name: "Warn ahead days" }), { target: { value: "14" } });
  fireEvent.click(screen.getByRole("button", { name: "Models" }));
  fireEvent.click(await screen.findByRole("button", { name: "Routes" }));
  fireEvent.click(screen.getByRole("button", { name: /^Scene turns/ }));
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByRole("combobox", { name: /Role \(default/ }), { target: { value: "fast" } });
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(unloadBlocked()).toBe(true); // the hidden Calendar draft still exists
  fireEvent.click(screen.getByRole("button", { name: "Calendar" }));
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(unloadBlocked()).toBe(false);
});

test("a confirmed Models row switch retires a pending save without closing a newer form", async () => {
  const pending = deferred<ReturnType<typeof inference>>();
  vi.mocked(api.putCampaignInference).mockReturnValueOnce(pending.promise);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  const { input, changed } = await editModel("role");
  fireEvent.change(input, { target: { value: changed } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  fireEvent.click(within(screen.getByRole("group", { name: "Roles" })).getByRole("button", { name: /^Fast/ }));
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  const fast = screen.getByRole("combobox", { name: "Fast for this campaign" });
  fireEvent.change(fast, { target: { value: "own" } });
  await act(async () => { pending.resolve(inference()); });
  expect(fast).toHaveValue("own");
  expect(unloadBlocked()).toBe(true);
});

test("an unapplied typed model id is protected and must be chosen before Save", async () => {
  await editModel("role");
  const typed = await screen.findByRole("textbox", { name: "Model id" });
  fireEvent.change(typed, { target: { value: "realm/other" } });
  expect(unloadBlocked()).toBe(true);
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(unloadBlocked()).toBe(false);
});

test("an untouched legacy fallback preset is clean when its model form opens", async () => {
  const settings = inference();
  settings.roles.primary.fallback = { ...EMPTY, preset: "terse" };
  vi.mocked(api.getCampaignInference).mockResolvedValue(settings);
  await editModel("role");
  expect(unloadBlocked()).toBe(false);
});

test.each(["role", "route"] as const)("a newer build refusal retains the %s draft until explicit Cancel", async (kind) => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  vi.mocked(api.putCampaignInference).mockRejectedValueOnce(
    new ApiError(409, "A newer build wrote this campaign.", "newer_format"));
  const { input, changed } = await editModel(kind);
  fireEvent.change(input, { target: { value: changed } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByRole("status")).toHaveTextContent("A newer build wrote this campaign.");
  expect(input).toBeInTheDocument();
  expect(input).toHaveValue(changed);
  expect(input).toBeDisabled();
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  expect(unloadBlocked()).toBe(true);
  fireEvent.click(screen.getByRole("link", { name: "Campaign hub" }));
  expect(confirm).toHaveBeenCalledTimes(1);
  expect(input).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(unloadBlocked()).toBe(false);
  expect(input).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Edit" })).toBeDisabled();
});
