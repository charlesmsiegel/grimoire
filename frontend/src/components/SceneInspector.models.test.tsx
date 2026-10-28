import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { api, ApiError, PRESET_CLEAR, type CapabilityNeed } from "../api/client";
import type {
InferenceSettings, ModelCapabilities
} from "../api/types";
import { configChanged } from "../appEvents";
import { useHotkeys } from "../shortcuts/useHotkeys";
import { fixture, type DeepPartial } from "../testkit/fixture";
import { renderInspector, resetSceneInspectorMocks } from "../testkit/sceneInspectorHarness";
import { NO_FALLBACK_POLICY } from "./inference/selection";
vi.mock("../api/client", async () => (await import("../testkit/sceneInspectorMocks")).sceneInspectorApiMock());
vi.mock("../api/models", async () => (await import("../testkit/sceneInspectorMocks")).sceneInspectorModelsMock());
vi.mock("./ResponseTargetsPicker", async () => (await import("../testkit/sceneInspectorMocks")).responseTargetsMock());
vi.mock("./AuthorsNotesPanel", async () => (await import("../testkit/sceneInspectorMocks")).authorsNotesMock());

beforeEach(resetSceneInspectorMocks);

// ---- the Models section: this campaign's role and route overrides ----
const RESOLVED = (provider: string, provider_name: string, model: string,
                  scope: "campaign" | "global" = "global", preset = "", preset_name = "") =>
  ({ provider, provider_name, model, preset, preset_name, via: "role" as const, scope });
const NONE = { provider: "", model: "", preset: "" };

/** A role this campaign leaves to the library: it inherits Saltmarch's model. */
function inheritedRole(model: string) {
  return { stored: NONE, fallback: NONE, problem: null, fallback_missing: [],
           fallback_problem: null, rate: null,
           resolves: RESOLVED("saltmarch", "Saltmarch Router", model),
           inherits: RESOLVED("saltmarch", "Saltmarch Router", model) };
}

function campaignInference(over: DeepPartial<InferenceSettings> = {}): InferenceSettings {
  return fixture({
    format: "2", newer: false, migration: { state: "done", reason: "", skipped: [] },
    roles: { primary: inheritedRole("vendor/opus"), fast: inheritedRole("vendor/haiku"),
             decision: inheritedRole("vendor/haiku") },
    routes: [{ key: "scene", label: "Scene turns", hint: "Story turns.", tasks: ["chat"],
               operation: "generate", default_role: "primary", requires: [],
               campaign_scoped: true, use: "", pin: NONE, preset: "", problem: null,
               fallback_missing: [], fallback_problem: null, rate: null,
               role: "primary",
               resolves: RESOLVED("saltmarch", "Saltmarch Router", "vendor/opus"),
               inherits: RESOLVED("saltmarch", "Saltmarch Router", "vendor/opus") }],
    providers: [{ id: "saltmarch", name: "Saltmarch Router", kind: "openrouter",
                  preset: "openrouter", usable: true, problem: null },
                { id: "realm", name: "Realm Local", kind: "openai_compatible",
                  preset: "custom", usable: true, problem: null }],
    presets: [{ id: "terse", name: "Terse" }],
    preset_clear: "⁣none",
    ...over,
  });
}

function capabilities(need: CapabilityNeed, fits: string[]): ModelCapabilities {
  return fixture({
    provider_preset: { id: "custom", label: "Custom", kind: "openai_compatible", base_url: "",
                       url_locked: false, billing: "metered", reports_price: false,
                       always: [], possible: [], never: [] },
    need, reason: null, hidden: [],
    groups: { fits: fits.map((id) => ({ id, name: id, context: null, prompt: null,
                                        completion: null, reason: "listed",
                                        capabilities: {} })),
              unverified: [] },
  });
}

/** The inspector with its Models section opened, and that section to query. */
async function openModels() {
  renderInspector();
  const header = await screen.findByRole("button", { name: /^models$/i });
  fireEvent.click(header);
  const section = within(header.closest(".side-section") as HTMLElement);
  // The view has landed: its rows are drawn.
  await section.findByRole("button", { name: /^Primary/ });
  return section;
}

describe("the Models section", () => {
  beforeEach(() => {
    vi.mocked(api.getCampaignInference).mockResolvedValue(campaignInference());
    vi.mocked(api.readConnectionCapabilities).mockImplementation(
      (_provider, need) => Promise.resolve(capabilities(need, ["realm/small"])));
    vi.mocked(api.previewControls).mockResolvedValue(fixture({ controls: {} }));
  });

  test("links to this campaign's full settings while preserving Models", async () => {
    const section = await openModels();
    expect(section.getByRole("link", { name: "Campaign settings" })).toHaveAttribute("href", "/campaigns/c/settings");
    expect(section.getByRole("button", { name: /^Primary/ })).toBeInTheDocument();
  });

  test("the inspector's Models section overrides a role for this campaign", async () => {
    const saved = campaignInference({ roles: {
      ...campaignInference().roles,
      fast: { stored: { provider: "realm", model: "realm/small", preset: "terse" },
              fallback: NONE, problem: null, fallback_missing: [], fallback_problem: null, rate: null,
              resolves: RESOLVED("realm", "Realm Local", "realm/small", "campaign", "terse",
                                 "Terse"),
              inherits: RESOLVED("saltmarch", "Saltmarch Router", "vendor/haiku") } } });
    vi.mocked(api.putCampaignInference).mockResolvedValue(fixture(saved));
    const section = await openModels();
    expect(api.getCampaignInference).toHaveBeenCalledWith("c");

    // The label is the server's `inherits`, never worked out here.
    const fast = await section.findByRole("button", { name: /^Fast/ });
    expect(fast).toHaveTextContent(
      "Inherit (resolves to Saltmarch Router ▸ vendor/haiku ▸ no preset)");
    fireEvent.click(fast);
    fireEvent.click(await section.findByRole("button", { name: "Edit" }));

    fireEvent.change(section.getByRole("combobox", { name: "Fast for this campaign" }),
                     { target: { value: "own" } });
    fireEvent.change(section.getByRole("combobox", { name: "Provider" }),
                     { target: { value: "realm" } });
    fireEvent.click(await section.findByRole("radio", { name: "realm/small" }));
    fireEvent.change(section.getByRole("combobox", { name: "Preset" }),
                     { target: { value: "terse" } });
    fireEvent.click(section.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(api.putCampaignInference).toHaveBeenCalledWith("c", {
      roles: { fast: { selection: { provider: "realm", model: "realm/small", preset: "terse" } } },
    }));
    // Back to the read-only view of what it now runs on.
    expect(await section.findByText("Runs on Realm Local ▸ realm/small ▸ Terse"))
      .toBeInTheDocument();
    expect(section.queryByRole("combobox", { name: "Provider" })).not.toBeInTheDocument();
  });

  test("a campaign role or fallback with a provider and no model is never saved", async () => {
    const section = await openModels();
    fireEvent.click(await section.findByRole("button", { name: /^Fast/ }));
    fireEvent.click(await section.findByRole("button", { name: "Edit" }));
    fireEvent.change(section.getByRole("combobox", { name: "Fast for this campaign" }),
                     { target: { value: "own" } });
    fireEvent.change(section.getByRole("combobox", { name: "Provider" }),
                     { target: { value: "realm" } });
    expect(section.getByText("Choose a model for this provider to save.")).toBeInTheDocument();
    expect(section.getByRole("button", { name: "Save" })).toBeDisabled();
    fireEvent.click(await section.findByRole("radio", { name: "realm/small" }));
    expect(section.getByRole("button", { name: "Save" })).toBeEnabled();

    fireEvent.click(section.getByRole("button", { name: "Fallback" }));
    const fallback = within(await section.findByRole("group", { name: "Fast fallback" }));
    fireEvent.change(fallback.getByRole("combobox", { name: "Provider" }),
                     { target: { value: "realm" } });
    expect(section.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(api.putCampaignInference).not.toHaveBeenCalled();
  });

  test("a fallback preset left without a provider says it is not used", async () => {
    // Saved, such a fallback is sent empty; the form must not go on showing
    // the preset as though it were kept (/models says the same of its draft).
    const section = await openModels();
    fireEvent.click(await section.findByRole("button", { name: /^Fast/ }));
    fireEvent.click(await section.findByRole("button", { name: "Edit" }));
    fireEvent.click(section.getByRole("button", { name: "Fallback" }));
    const fallback = within(await section.findByRole("group", { name: "Fast fallback" }));
    fireEvent.change(fallback.getByRole("combobox", { name: "Provider" }),
                     { target: { value: "realm" } });
    fireEvent.click(await fallback.findByRole("radio", { name: "realm/small" }));
    fireEvent.change(fallback.getByRole("combobox", { name: "Fallback preset" }),
                     { target: { value: "terse" } });
    expect(fallback.queryByText(/preset with no provider is not used/)).not.toBeInTheDocument();

    fireEvent.change(fallback.getByRole("combobox", { name: "Provider" }),
                     { target: { value: "" } });
    expect(fallback.getByText(/preset with no provider is not used/)).toBeInTheDocument();
  });

  test("an arrow on a model radio is the radio's, never the scene's variant swipe", async () => {
    // The play view binds bare ← and → to the last reply's variant swipe, and
    // the Inspector is a panel beside it, not an overlay: nothing holds those
    // bindings off while a Models form is open.
    const swipe = vi.fn();
    function Swipe() {
      useHotkeys([{ keys: "arrowleft", run: swipe }, { keys: "arrowright", run: swipe }]);
      return null;
    }
    render(<Swipe />);
    const section = await openModels();
    fireEvent.click(section.getByRole("button", { name: /^Primary/ }));
    fireEvent.click(await section.findByRole("button", { name: "Edit" }));
    fireEvent.change(section.getByRole("combobox", { name: "Primary for this campaign" }),
                     { target: { value: "own" } });
    fireEvent.change(section.getByRole("combobox", { name: "Provider" }),
                     { target: { value: "realm" } });
    const radio = await section.findByRole("radio", { name: "realm/small" });
    radio.focus();

    const left = fireEvent.keyDown(radio, { key: "ArrowLeft" });
    const right = fireEvent.keyDown(radio, { key: "ArrowRight" });

    expect(swipe).not.toHaveBeenCalled();
    // Stopped, never prevented: the browser still moves the selection.
    expect(left).toBe(true);
    expect(right).toBe(true);
    // Away from the radio, the swipe is still the scene's.
    fireEvent.keyDown(document.body, { key: "ArrowLeft" });
    expect(swipe).toHaveBeenCalledTimes(1);
  });

  test("it never offers the embedding role", async () => {
    const section = await openModels();
    await section.findByRole("button", { name: /^Primary/ });
    for (const role of ["Primary", "Fast", "Decision"]) {
      expect(section.getByRole("button", { name: new RegExp(`^${role}`) })).toBeInTheDocument();
    }
    expect(section.queryByText(/embedding/i)).not.toBeInTheDocument();
    // Nor among the routes.
    fireEvent.click(section.getByRole("button", { name: "Routes" }));
    expect(await section.findByRole("button", { name: /^Scene turns/ })).toBeInTheDocument();
    expect(section.queryByText(/embedding/i)).not.toBeInTheDocument();
  });

  test("rows are read-only until Edit", async () => {
    const section = await openModels();
    fireEvent.click(await section.findByRole("button", { name: "Routes" }));
    fireEvent.click(await section.findByRole("button", { name: /^Scene turns/ }));

    // The record opens in its view: what it runs on, and nothing to change it with.
    expect(await section.findByText(
      "Runs on Saltmarch Router ▸ vendor/opus ▸ no preset",
      { selector: "p" })).toBeInTheDocument();
    expect(section.queryByRole("combobox")).not.toBeInTheDocument();

    fireEvent.click(section.getByRole("button", { name: "Edit" }));
    expect(await section.findByRole("combobox", { name: "Role (default: Primary)" }))
      .toBeInTheDocument();
    expect(section.getByRole("combobox", { name: "Preset override" })).toBeInTheDocument();

    // Cancel goes back to the view, having sent nothing.
    fireEvent.click(section.getByRole("button", { name: "Cancel" }));
    expect(section.queryByRole("combobox")).not.toBeInTheDocument();
    expect(api.putCampaignInference).not.toHaveBeenCalled();
  });

  test("the section is shut until asked for, and asks nothing until then", async () => {
    renderInspector();
    const header = await screen.findByRole("button", { name: /^models$/i });
    expect(header).toHaveAttribute("aria-expanded", "false");
    expect(api.getCampaignInference).not.toHaveBeenCalled();
  });

  test("Edit waits for the new layout, and the upgrade banner says why", async () => {
    vi.mocked(api.getCampaignInference).mockResolvedValue(campaignInference({
      format: "1", migration: { state: "pending", reason: "", skipped: [] } }));
    const section = await openModels();
    expect(await section.findByText(/Upgrade pending/)).toBeInTheDocument();
    fireEvent.click(section.getByRole("button", { name: /^Primary/ }));
    expect(await section.findByRole("button", { name: "Edit" })).toBeDisabled();
  });

  test("Edit opens once the upgrade lands, with the section left open", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      vi.mocked(api.getCampaignInference).mockResolvedValue(campaignInference({
        format: "1", migration: { state: "running", reason: "", skipped: [] } }));
      const section = await openModels();
      fireEvent.click(section.getByRole("button", { name: /^Primary/ }));
      expect(await section.findByRole("button", { name: "Edit" })).toBeDisabled();

      vi.mocked(api.getCampaignInference).mockResolvedValue(campaignInference());
      await act(async () => { await vi.advanceTimersByTimeAsync(1000); });

      expect(await section.findByRole("button", { name: "Edit" })).toBeEnabled();
      expect(section.queryByText(/Upgrade pending/)).not.toBeInTheDocument();
      const reads = vi.mocked(api.getCampaignInference).mock.calls.length;
      await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
      expect(vi.mocked(api.getCampaignInference).mock.calls.length).toBe(reads);
    } finally {
      vi.useRealTimers();
    }
  });

  test("a campaign the move has not reached says so quietly at the new layout", async () => {
    vi.mocked(api.getCampaignInference).mockResolvedValue(campaignInference({
      migration: { state: "pending", reason: "",
                   skipped: ["campaign c: busy; finished on the next start"] } }));
    const section = await openModels();
    expect(await section.findByText(
      /still in the old layout \(busy; finished on the next start\)/)).toBeInTheDocument();
    // Not a block: the server moves it inside the write.
    expect(section.queryByText(/Upgrade pending/)).not.toBeInTheDocument();
    fireEvent.click(section.getByRole("button", { name: /^Primary/ }));
    expect(await section.findByRole("button", { name: "Edit" })).toBeEnabled();
  });

  test("another campaign left behind is the library's upgrade line, worded as Settings words it", async () => {
    const other = "campaign saltmarch: busy; finished on the next start";
    vi.mocked(api.getCampaignInference).mockResolvedValue(campaignInference({
      migration: { state: "pending", reason: "", skipped: [other] } }));
    const section = await openModels();
    expect(await section.findByText(`The upgrade left 1 thing for later (${other}).`))
      .toBeInTheDocument();
    expect(section.queryByText(/still in the old layout/)).not.toBeInTheDocument();
  });

  test("a fallback the server reports as dropped is said on its role and its route", async () => {
    // `fallback_missing` is the server's: the seam never sends a fallback
    // known unable to do what it is for, and refuses nothing over it.
    const base = campaignInference();
    vi.mocked(api.getCampaignInference).mockResolvedValue(campaignInference({
      roles: { ...base.roles,
               primary: { ...inheritedRole("vendor/opus"),
                          fallback: { provider: "realm", model: "realm/embed", preset: "" },
                          fallback_missing: ["generate"] },
               fast: { ...inheritedRole("vendor/haiku"), fallback_missing: ["generate"] } },
      routes: [{ ...base.routes[0], fallback_missing: ["generate"] }] }));
    const section = await openModels();

    fireEvent.click(section.getByRole("button", { name: /^Primary/ }));
    expect(await section.findByText(
      "The fallback, Realm Local ▸ realm/embed, is known not to fit Primary "
      + "(it cannot generate text), so it is never sent.")).toBeInTheDocument();

    // A fallback this campaign does not choose itself is still named as one.
    fireEvent.click(section.getByRole("button", { name: /^Fast/ }));
    expect(await section.findByText(
      "The fallback is known not to fit Fast (it cannot generate text), so it is never sent."))
      .toBeInTheDocument();

    fireEvent.click(section.getByRole("button", { name: "Routes" }));
    fireEvent.click(await section.findByRole("button", { name: /^Scene turns/ }));
    expect(await section.findByText(
      "The fallback is known not to fit Scene turns (it cannot generate text), "
      + "so it is never sent.")).toBeInTheDocument();
  });

  test("a fallback that cannot send says why on its role and its route", async () => {
    // `fallback_problem` is the server's: a fallback with no key is left out
    // of the chain as silently as one known unable to do the work.
    const base = campaignInference();
    vi.mocked(api.getCampaignInference).mockResolvedValue(campaignInference({
      roles: { ...base.roles,
               primary: { ...inheritedRole("vendor/opus"),
                          fallback: { provider: "realm", model: "realm/small", preset: "" },
                          fallback_problem: "Endpoint base URL not set" } },
      routes: [{ ...base.routes[0], fallback_problem: "Endpoint base URL not set" }] }));
    const section = await openModels();

    fireEvent.click(section.getByRole("button", { name: /^Primary/ }));
    expect(await section.findByText(
      "The fallback, Realm Local ▸ realm/small, cannot be sent (Endpoint base URL not set), "
      + "so it is never tried.")).toBeInTheDocument();

    fireEvent.click(section.getByRole("button", { name: "Routes" }));
    fireEvent.click(await section.findByRole("button", { name: /^Scene turns/ }));
    expect(await section.findByText(
      "The fallback cannot be sent (Endpoint base URL not set), so it is never tried."))
      .toBeInTheDocument();
  });

  test("a route whose task's policy sends no fallback says so, not that it cannot be sent", async () => {
    const base = campaignInference();
    vi.mocked(api.getCampaignInference).mockResolvedValue(campaignInference({
      routes: [{ ...base.routes[0], fallback_problem: NO_FALLBACK_POLICY }] }));
    const section = await openModels();

    fireEvent.click(section.getByRole("button", { name: "Routes" }));
    fireEvent.click(await section.findByRole("button", { name: /^Scene turns/ }));
    const line = await section.findByText(
      "The fallback is never sent: Scene turns runs without a fallback.");
    expect(line).toBeInTheDocument();
    // A code choice, not a fault: plain hint styling, not a problem.
    expect(line).toHaveClass("field-hint");
    expect(line).not.toHaveClass("problem");
    expect(section.queryByText(/cannot be sent/)).toBeNull();
  });

  test("a role whose fallback is sent says nothing of a dropped one", async () => {
    const section = await openModels();
    fireEvent.click(section.getByRole("button", { name: /^Primary/ }));
    await section.findByRole("button", { name: "Edit" });
    expect(section.queryByText(/so it is never sent/)).not.toBeInTheDocument();
  });

  test("a campaign a newer build marked refuses the save, and the section says so", async () => {
    vi.mocked(api.putCampaignInference).mockRejectedValue(new ApiError(409,
      "A newer version of grimoire has changed this campaign's model settings. "
      + "Update grimoire to change them here.", "newer_format"));
    const section = await openModels();
    fireEvent.click(await section.findByRole("button", { name: /^Primary/ }));
    fireEvent.click(await section.findByRole("button", { name: "Edit" }));
    fireEvent.click(section.getByRole("button", { name: "Save" }));

    expect(await section.findByText(/newer version of grimoire has changed this campaign/))
      .toBeInTheDocument();
    expect(section.getByRole("button", { name: "Edit" })).toBeDisabled();
  });

  test("a save refused as not migrated shows the upgrade's state", async () => {
    const failed: InferenceSettings["migration"] = { state: "failed", reason: "the safety backup failed: disk full", skipped: [] };
    vi.mocked(api.putCampaignInference).mockRejectedValue(new ApiError(409,
      "Model settings are being moved to the new layout. Try again once that has finished.",
      "not_migrated", { kind: "not_migrated", status: failed }));
    // Read again after the refusal, the store says the same: not moved yet.
    vi.mocked(api.getCampaignInference).mockResolvedValueOnce(campaignInference())
      .mockResolvedValue(campaignInference({ format: "1", migration: failed }));
    const section = await openModels();
    fireEvent.click(await section.findByRole("button", { name: /^Primary/ }));
    fireEvent.click(await section.findByRole("button", { name: "Edit" }));
    fireEvent.click(section.getByRole("button", { name: "Save" }));

    expect(await section.findByText(/Upgrade pending: the safety backup failed: disk full/))
      .toBeInTheDocument();
    fireEvent.click(section.getByRole("button", { name: "Cancel" }));
    expect(await section.findByRole("button", { name: "Edit" })).toBeDisabled();
  });

  test("a not-migrated refusal reads the view again, and a finished move frees Edit", async () => {
    vi.mocked(api.putCampaignInference).mockRejectedValue(new ApiError(409,
      "Model settings are being moved to the new layout. Try again once that has finished.",
      "not_migrated",
      { kind: "not_migrated", status: { state: "done", reason: "", skipped: [] } }));
    const section = await openModels();
    fireEvent.click(await section.findByRole("button", { name: /^Primary/ }));
    fireEvent.click(await section.findByRole("button", { name: "Edit" }));
    fireEvent.click(section.getByRole("button", { name: "Save" }));

    // The move finished between the read and the write: the view, read again,
    // is ready, so nothing is left blocked with no banner to say why.
    await waitFor(() => expect(api.getCampaignInference).toHaveBeenCalledTimes(2));
    fireEvent.click(await section.findByRole("button", { name: "Cancel" }));
    expect(await section.findByRole("button", { name: "Edit" })).toBeEnabled();
  });

  test("a route that inherits its role but overrides its preset shows what it runs on",
       async () => {
    const base = campaignInference();
    vi.mocked(api.getCampaignInference).mockResolvedValue(campaignInference({ routes: [{
      ...base.routes[0], use: "", preset: "terse",
      resolves: RESOLVED("saltmarch", "Saltmarch Router", "vendor/opus", "campaign", "terse",
                         "Terse"),
      // `inherits` silences every campaign key of the route, the preset too.
      inherits: RESOLVED("saltmarch", "Saltmarch Router", "vendor/opus") }] }));
    const section = await openModels();
    fireEvent.click(section.getByRole("button", { name: "Routes" }));

    const row = await section.findByRole("button", { name: /^Scene turns/ });
    expect(row).toHaveTextContent("Saltmarch Router ▸ vendor/opus ▸ Terse");
    expect(row).not.toHaveTextContent(/no preset/);
    fireEvent.click(row);
    const detail = within(await section.findByRole("region", { name: "Scene turns" }));
    expect(detail.getByText("Runs on Saltmarch Router ▸ vendor/opus ▸ Terse",
                            { selector: "p" })).toBeInTheDocument();
    // The inherit wording is what inheriting the ROLE gives, never the headline.
    expect(detail.getByText("Inherit (resolves to Saltmarch Router ▸ vendor/opus)"))
      .toBeInTheDocument();
    expect(detail.queryByText(/no preset/)).not.toBeInTheDocument();
    expect(detail.getByText("Terse", { selector: "span" })).toBeInTheDocument();
  });

  test("a model-settings change elsewhere re-reads the section's Inherit labels", async () => {
    const section = await openModels();
    const before = await section.findByRole("button", { name: /^Fast/ });
    expect(before).toHaveTextContent("vendor/haiku");

    vi.mocked(api.getCampaignInference).mockResolvedValue(campaignInference({ roles: {
      ...campaignInference().roles, fast: inheritedRole("vendor/sonnet") } }));
    act(() => { configChanged(); });

    expect(await section.findByText(
      "Inherit (resolves to Saltmarch Router ▸ vendor/sonnet ▸ no preset)")).toBeInTheDocument();
  });

  // ---- what a save sends: the named risks of a whole-selection replace ----
  /** A role this campaign chose for itself, with a fallback the library set. */
  function ownedFast(): InferenceSettings {
    return campaignInference({ roles: { ...campaignInference().roles,
      fast: { stored: { provider: "realm", model: "realm/small", preset: "terse" },
              fallback: { provider: "saltmarch", model: "vendor/haiku", preset: "" },
              problem: null, fallback_missing: [], rate: null,
              resolves: RESOLVED("realm", "Realm Local", "realm/small", "campaign", "terse",
                                 "Terse"),
              inherits: RESOLVED("saltmarch", "Saltmarch Router", "vendor/haiku") } } });
  }

  async function editRow(section: ReturnType<typeof within>, name: RegExp) {
    fireEvent.click(await section.findByRole("button", { name }));
    fireEvent.click(await section.findByRole("button", { name: "Edit" }));
    // Settled: a stored choice's picker and readout read on opening.
    await section.findByRole("button", { name: "Save" });
  }

  test("Inherit sends the empty selection", async () => {
    vi.mocked(api.getCampaignInference).mockResolvedValue(ownedFast());
    vi.mocked(api.putCampaignInference).mockResolvedValue(campaignInference());
    const section = await openModels();
    await editRow(section, /^Fast/);
    fireEvent.change(section.getByRole("combobox", { name: "Fast for this campaign" }),
                     { target: { value: "" } });
    fireEvent.click(section.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.putCampaignInference).toHaveBeenCalledWith("c", {
      roles: { fast: { selection: { provider: "", model: "", preset: "" } } },
    }));
  });

  test("a model edit keeps the stored preset and never names the fallback", async () => {
    vi.mocked(api.readConnectionCapabilities).mockImplementation(
      (_provider, need) =>
        Promise.resolve(capabilities(need, ["realm/small", "realm/large"])));
    vi.mocked(api.getCampaignInference).mockResolvedValue(ownedFast());
    vi.mocked(api.putCampaignInference).mockResolvedValue(ownedFast());
    const section = await openModels();
    await editRow(section, /^Fast/);
    // The role's own picker: the stored fallback opens with the form, and has one too.
    const choice = within(section.getByRole("group", { name: "Fast" }));
    fireEvent.click(await choice.findByRole("radio", { name: "realm/large" }));
    fireEvent.click(section.getByRole("button", { name: "Save" }));
    // Exactly this body: no `fallback` key, so the library's stays as it is.
    await waitFor(() => expect(api.putCampaignInference).toHaveBeenCalledWith("c", {
      roles: { fast: { selection: { provider: "realm", model: "realm/large", preset: "terse" } } },
    }));
  });

  test("a campaign sets its own fallback for a role, and only that is sent", async () => {
    vi.mocked(api.putCampaignInference).mockResolvedValue(campaignInference());
    const section = await openModels();
    await editRow(section, /^Primary/);
    // Left to inherit, the role can still have a fallback of this campaign's own.
    fireEvent.click(section.getByRole("button", { name: "Fallback" }));
    const fallback = within(await section.findByRole("group", { name: "Primary fallback" }));
    expect(fallback.getByRole("combobox", { name: "Fallback preset" })).toBeDisabled();
    fireEvent.change(fallback.getByRole("combobox", { name: "Provider" }),
                     { target: { value: "realm" } });
    fireEvent.click(await fallback.findByRole("radio", { name: "realm/small" }));
    fireEvent.click(section.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.putCampaignInference).toHaveBeenCalledWith("c", {
      roles: { primary: { selection: { provider: "", model: "", preset: "" },
                          fallback: { provider: "realm", model: "realm/small", preset: "" } } },
    }));
  });

  test("clearing a campaign's fallback sends it empty, back to the library's", async () => {
    vi.mocked(api.getCampaignInference).mockResolvedValue(ownedFast());
    vi.mocked(api.putCampaignInference).mockResolvedValue(campaignInference());
    const section = await openModels();
    await editRow(section, /^Fast/);
    // A stored fallback opens with the form.
    const fallback = within(await section.findByRole("group", { name: "Fast fallback" }));
    fireEvent.change(fallback.getByRole("combobox", { name: "Provider" }),
                     { target: { value: "" } });
    fireEvent.click(section.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.putCampaignInference).toHaveBeenCalledWith("c", {
      roles: { fast: { selection: { provider: "realm", model: "realm/small", preset: "terse" },
                       fallback: { provider: "", model: "", preset: "" } } },
    }));
  });

  /** The scene route, pinned to a model of this campaign's choosing. */
  function pinnedScene(): InferenceSettings {
    const base = campaignInference();
    return campaignInference({ routes: [{
      ...base.routes[0], use: "model",
      pin: { provider: "realm", model: "realm/small", preset: "" },
      resolves: RESOLVED("realm", "Realm Local", "realm/small", "campaign") }] });
  }

  async function editScene(section: ReturnType<typeof within>) {
    fireEvent.click(section.getByRole("button", { name: "Routes" }));
    await editRow(section, /^Scene turns/);
  }

  test("a route switched to a role leaves its pin out of the save", async () => {
    vi.mocked(api.getCampaignInference).mockResolvedValue(pinnedScene());
    vi.mocked(api.putCampaignInference).mockResolvedValue(campaignInference());
    const section = await openModels();
    await editScene(section);
    fireEvent.change(section.getByRole("combobox", { name: "Role (default: Primary)" }),
                     { target: { value: "fast" } });
    fireEvent.click(section.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.putCampaignInference).toHaveBeenCalledWith("c", {
      routes: { scene: { use: "fast", preset: "" } },
    }));
  });

  test("a route pinned to a specific model sends the pin", async () => {
    vi.mocked(api.putCampaignInference).mockResolvedValue(pinnedScene());
    const section = await openModels();
    await editScene(section);
    fireEvent.change(section.getByRole("combobox", { name: "Role (default: Primary)" }),
                     { target: { value: "model" } });
    fireEvent.change(section.getByRole("combobox", { name: "Provider" }),
                     { target: { value: "realm" } });
    fireEvent.click(await section.findByRole("radio", { name: "realm/small" }));
    fireEvent.click(section.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.putCampaignInference).toHaveBeenCalledWith("c", {
      routes: { scene: { use: "model",
                         pin: { provider: "realm", model: "realm/small", preset: "" },
                         preset: "" } },
    }));
  });

  test("a route's no-preset override is sent as the clear marker", async () => {
    vi.mocked(api.putCampaignInference).mockResolvedValue(campaignInference());
    const section = await openModels();
    await editScene(section);
    fireEvent.change(section.getByRole("combobox", { name: "Preset override" }),
                     { target: { value: PRESET_CLEAR } });
    fireEvent.click(section.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.putCampaignInference).toHaveBeenCalledWith("c", {
      routes: { scene: { use: "", preset: PRESET_CLEAR } },
    }));
  });
});

