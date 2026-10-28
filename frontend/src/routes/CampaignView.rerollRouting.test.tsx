import { screen, fireEvent, waitFor } from "@testing-library/react";
import { api } from "../api/client";
import type { InferenceProvider, InferenceSettings } from "../api/client";
import { installCampaignMocks, ONE_SCENE, openScene, renderCampaign } from "../testkit/campaignHarness";
import { fixture } from "../testkit/fixture";
import { ALT, TWO_SCENES } from "../testkit/campaignFixtures";

// The mocks and the per-test defaults live in `src/testkit`, which the review
// suite shares (#378). A `vi.mock` factory is hoisted above every import
// and can close over nothing, so it reaches the harness through a dynamic
// import rather than a module-scope binding.
vi.mock("../components/CastPanel", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.CastPanel());
vi.mock("../components/NewSceneChooser", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.NewSceneChooser());
vi.mock("../components/CalendarConfig", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.CalendarConfig());
vi.mock("../components/ReplayPanel", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.ReplayPanel());
vi.mock("../components/ResponseTargetsPicker", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.ResponseTargetsPicker());
vi.mock("../api/client", async () => (await import("../testkit/campaignMocks")).campaignApiMock());
vi.mock("../components/PostImagePicker", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.PostImagePicker());
vi.mock("../api/models", () => ({ getModels: vi.fn() }));

beforeEach(installCampaignMocks);



test("Reroll on the last assistant post replaces it with a fresh reply", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene)
    .mockResolvedValueOnce(fixture({ meta: {}, messages: [
      { role: "user", content: "hi" }, { role: "assistant", content: "old reply" }] }))
    .mockResolvedValue(fixture({ meta: {}, messages: [
      { role: "user", content: "hi" }, { role: "assistant", content: "fresh reply" }] }));
  vi.mocked(api.regenerate).mockImplementation(async (_c, _s, onEvent) => {
    onEvent({ delta: "fresh reply" });
  });
  renderCampaign();
  await screen.findByText("old reply");
  fireEvent.click(await screen.findByTitle("Reroll"));
  // clicking Reroll opens the popover instead of firing immediately
  expect(api.regenerate).not.toHaveBeenCalled();
  expect(screen.getByTitle("Reroll")).toBeInTheDocument(); // hovertext present
  fireEvent.click(screen.getByRole("button", { name: /reroll ▸/i })); // empty = plain reroll
  await waitFor(() => expect(api.regenerate).toHaveBeenCalledWith(
    "run", "s1", expect.any(Function), { guidance: "" },
    expect.any(AbortSignal), expect.any(String), expect.any(Function)));
  await screen.findByText("fresh reply");
  expect(screen.queryByText("old reply")).toBeNull();
});

test("typed guidance is passed to regenerate", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "old reply" }] }));
  renderCampaign();
  await screen.findByText("old reply");
  fireEvent.click(await screen.findByTitle("Reroll"));
  const input = screen.getByPlaceholderText(/guide the reroll/i);
  fireEvent.change(input, { target: { value: "make her angrier" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(api.regenerate).toHaveBeenCalledWith(
    "run", "s1", expect.any(Function),
    { guidance: "make her angrier" },
    expect.any(AbortSignal), expect.any(String), expect.any(Function)));
});

test.each(["Reroll guidance", "Provider", "Reroll preset"])(
  "Escape closes the reroll popover from %s, without firing", async (control) => {
  // Every control, not just the autofocused one: the route row (#77) added
  // more, and a popover only one of its controls can be backed out of is worse
  // than one that offers no escape at all.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "old reply" }] }));
  vi.mocked(api.getCampaignInference).mockResolvedValue(rerollInference());
  renderCampaign();
  await screen.findByText("old reply");
  fireEvent.click(await screen.findByTitle("Reroll"));

  fireEvent.keyDown(await screen.findByLabelText(control), { key: "Escape" });

  expect(screen.queryByPlaceholderText(/guide the reroll/i)).toBeNull();
  expect(api.regenerate).not.toHaveBeenCalled();
});

// ---- the per-reroll route override (#77) ----
/** The campaign's inference view with a second provider to send the reroll
 *  to, and two presets: what the picker offers is read from here. */
function rerollInference(): InferenceSettings {
  const provider = (id: string, name: string, kind: InferenceProvider["kind"]) =>
    ({ id, name, kind, preset: kind, usable: true, problem: null });
  return fixture({
    format: "2", newer: false, migration: { state: "done", reason: "", skipped: [] },
    roles: {}, preset_clear: "\u2063none",
    routes: [{ key: "scene", label: "Scene turns", hint: "", tasks: ["chat", "regenerate"],
               operation: "generate", default_role: "primary", requires: [], campaign_scoped: true,
               use: "", pin: { provider: "", model: "", preset: "" }, preset: "", problem: null,
               role: "primary", inherits: null,
               resolves: { provider: "openrouter", provider_name: "OpenRouter", model: "campaign/model",
                           preset: "", preset_name: "", via: "role", scope: "global" } }],
    providers: [provider("openrouter", "OpenRouter", "openrouter"),
                provider("local", "Local", "openai_compatible")],
    presets: [{ id: "warm", name: "Warm" }, { id: "cold", name: "Cold" }],
  });
}

/** One model on the local provider, in the group `group`. */
function localModels(group: "fits" | "unverified" = "fits") {
  vi.mocked(api.readConnectionCapabilities).mockImplementation(
    (provider, need) => {
      const rows = provider === "local"
        ? [{ id: "llama3", name: "Llama 3", context: null, prompt: null, completion: null,
             reason: group === "fits" ? "the catalog says so" : "nothing says it can",
             capabilities: {} }]
        : [];
      return Promise.resolve(fixture({
        provider_preset: { id: "custom", label: "Custom", kind: "openai_compatible", base_url: "",
                           url_locked: false, billing: "metered", reports_price: false,
                           always: [], possible: [], never: [] },
        need, reason: null, hidden: [],
        groups: { fits: group === "fits" ? rows : [], unverified: group === "unverified" ? rows : [] },
      }));
    });
}

function rerollableScene(problem: string | null = null) {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "old reply" }] }));
  vi.mocked(api.getCampaignInference).mockResolvedValue({ ...rerollInference(),
    routes: rerollInference().routes.map((route) => ({ ...route, problem })) });
  localModels();
}

/** Open the reroll popover and choose the local provider's model. */
async function rerollOnLocal() {
  await screen.findByText("old reply");
  fireEvent.click(await screen.findByTitle("Reroll"));
  fireEvent.change(await screen.findByLabelText("Provider"), { target: { value: "local" } });
  fireEvent.click(await screen.findByRole("radio", { name: "Llama 3" }));
}

test.each(["legacy", "response", "continuation"])(
  "an explicit provider rescues an unready default for a %s reroll", async (kind) => {
    rerollableScene("The standing provider has no key");
    if (kind !== "legacy") {
      vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
        { role: "user", content: "hi" },
        { role: "assistant", content: "old reply", response_id: "r1",
          response_status: "complete", response_can_reroll: true },
      ] });
      vi.mocked(api.getResponseSwipe).mockResolvedValue({
        active: 0, variants: [{ id: "v1", status: "complete" }], settings: null,
        resume_settings: null, can_reroll: true, editable: true, round_open: false, edited: false,
      });
    }
    renderCampaign();
    await screen.findByText(/No model is ready to generate/);
    await screen.findByText("old reply");
    if (kind === "legacy") fireEvent.click(await screen.findByTitle("Reroll"));
    else {
      fireEvent.click(screen.getByText("Response actions"));
      fireEvent.click(screen.getByText("Model for this reroll"));
    }
    const name = kind === "legacy" ? /reroll ▸/i
      : kind === "response" ? "Reroll response" : /Keep writing/;
    expect(screen.getByRole("button", { name })).toBeDisabled();
    if (kind === "legacy") {
      fireEvent.change(await screen.findByLabelText("Reroll preset"), { target: { value: "cold" } });
      expect(screen.getByRole("button", { name })).toBeDisabled();
      fireEvent.click(screen.getByRole("button", { name: "Default" }));
    }
    fireEvent.change(await screen.findByLabelText("Provider"), { target: { value: "local" } });
    fireEvent.click(await screen.findByRole("radio", { name: "Llama 3" }));
    expect(screen.getByRole("button", { name })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name }));
    await waitFor(() => {
      if (kind === "legacy") expect(api.regenerate).toHaveBeenCalledWith(
        "run", "s1", expect.any(Function), { guidance: "", provider: "local", model: "llama3" },
        expect.any(AbortSignal), expect.any(String), expect.any(Function));
      else expect(kind === "response" ? api.regenerateResponse : api.extendResponse)
        .toHaveBeenCalledWith("run", "s1", "r1", expect.any(Function),
          { guidance: "", provider: "local", model: "llama3" },
          expect.any(AbortSignal), expect.any(String), expect.any(Function));
    });
    expect(screen.getByRole("button", { name: /^Continue/ })).toBeDisabled();
  });

test("a transient failure on an explicit provider can retry around the unready default", async () => {
  rerollableScene("The standing provider has no key");
  vi.mocked(api.regenerate).mockImplementationOnce(async (_cid, _sid, onEvent) => {
    onEvent({ error: { kind: "network", detail: "The selected provider disconnected" } });
  });
  renderCampaign();
  await screen.findByText(/No model is ready to generate/);
  await rerollOnLocal();
  fireEvent.click(screen.getByRole("button", { name: /reroll ▸/i }));
  const retry = await screen.findByRole("button", { name: "Retry" });
  expect(retry).toBeEnabled();
  fireEvent.click(retry);
  await waitFor(() => expect(api.regenerate).toHaveBeenCalledTimes(2));
  expect(vi.mocked(api.regenerate).mock.calls[1]?.[3]).toEqual({
    guidance: "", provider: "local", model: "llama3",
  });
});

test("a reroll sent to another provider names it, its model and its preset on the wire", async () => {
  rerollableScene();
  renderCampaign();
  await rerollOnLocal();

  expect(api.getCampaignInference).toHaveBeenCalledWith("run");
  fireEvent.change(screen.getByLabelText("Reroll preset"), { target: { value: "cold" } });
  fireEvent.click(screen.getByRole("button", { name: /reroll ▸/i }));

  await waitFor(() => expect(api.regenerate).toHaveBeenCalledWith(
    "run", "s1", expect.any(Function),
    { guidance: "", provider: "local", model: "llama3", preset: "cold" },
    expect.any(AbortSignal), expect.any(String), expect.any(Function)));
});

test("a reroll may name a preset alone and keep the campaign's route", async () => {
  rerollableScene();
  renderCampaign();
  await screen.findByText("old reply");
  fireEvent.click(await screen.findByTitle("Reroll"));

  fireEvent.change(await screen.findByLabelText("Reroll preset"), { target: { value: "warm" } });
  fireEvent.click(screen.getByRole("button", { name: /reroll ▸/i }));

  await waitFor(() => expect(api.regenerate).toHaveBeenCalledWith(
    "run", "s1", expect.any(Function), { guidance: "", preset: "warm" },
    expect.any(AbortSignal), expect.any(String), expect.any(Function)));
});

test("Default after a choice sends the reroll with no override", async () => {
  rerollableScene();
  renderCampaign();
  await rerollOnLocal();

  fireEvent.click(screen.getByRole("button", { name: "Default" }));
  fireEvent.click(screen.getByRole("button", { name: /reroll ▸/i }));

  await waitFor(() => expect(api.regenerate).toHaveBeenCalledWith(
    "run", "s1", expect.any(Function), { guidance: "" },
    expect.any(AbortSignal), expect.any(String), expect.any(Function)));
});

test("the route rides the guidance, and neither steers the next reroll", async () => {
  rerollableScene();
  renderCampaign();
  await rerollOnLocal();
  fireEvent.change(screen.getByPlaceholderText(/guide the reroll/i),
                   { target: { value: "warmer" } });
  fireEvent.click(screen.getByRole("button", { name: /reroll ▸/i }));
  await waitFor(() => expect(api.regenerate).toHaveBeenCalledTimes(1));
  expect(vi.mocked(api.regenerate).mock.calls[0][3]).toEqual(
    { guidance: "warmer", provider: "local", model: "llama3" });

  // A second reroll, popover reopened and left alone: one-shot means the local
  // endpoint does not quietly keep serving this scene.
  fireEvent.click(await screen.findByTitle("Reroll"));
  fireEvent.click(screen.getByRole("button", { name: /reroll ▸/i }));

  await waitFor(() => expect(api.regenerate).toHaveBeenCalledTimes(2));
  expect(vi.mocked(api.regenerate).mock.calls[1][3]).toEqual({ guidance: "" });
});

test("Retry after a failed reroll repeats its route, not just its guidance", async () => {
  rerollableScene();
  vi.mocked(api.regenerate).mockImplementation(
    async (_c, _s, onEvent) => {
      onEvent({ error: { detail: "the endpoint refused", kind: "network" } });
    });
  renderCampaign();
  await rerollOnLocal();
  fireEvent.click(screen.getByRole("button", { name: /reroll ▸/i }));
  await screen.findByText(/the endpoint refused/);

  fireEvent.click(screen.getByRole("button", { name: /^retry$/i }));

  await waitFor(() => expect(api.regenerate).toHaveBeenCalledTimes(2));
  // "Try that again" means the same reroll. A Retry that dropped the endpoint
  // the player chose would answer from the campaign's model while looking like
  // a repeat.
  expect(vi.mocked(api.regenerate).mock.calls[1][3]).toMatchObject(
    { provider: "local", model: "llama3" });
});

test("switching scenes closes the reroll popover and drops its route", async () => {
  // Codex review: the popover is anchored to "the post a reroll would replace",
  // which is a different post in every scene. Left open across a switch it
  // reappears over B's trailing reply still holding the provider chosen for
  // A's, and the next click sends B's reply to a model picked for another
  // scene.
  rerollableScene();
  vi.mocked(api.listScenes).mockResolvedValue(TWO_SCENES);
  vi.mocked(api.getScene).mockImplementation(async (_c, sid) => fixture({
    meta: { id: sid }, messages: [{ role: "user", content: `hi from ${sid}` },
                                  { role: "assistant", content: `a reply in ${sid}` }],
  }));
  renderCampaign();
  await screen.findByText("a reply in s1");
  fireEvent.click(await screen.findByTitle("Reroll"));
  fireEvent.change(await screen.findByLabelText("Provider"), { target: { value: "local" } });
  fireEvent.click(await screen.findByRole("radio", { name: "Llama 3" }));

  await openScene(/The Saltmarch Gate/);
  await screen.findByText("a reply in s2");

  // gone, not merely re-rendered over the new scene's trailing post
  expect(screen.queryByPlaceholderText(/guide the reroll/i)).toBeNull();
  // and reopening starts from the campaign's own route, not the one chosen in s1
  fireEvent.click(await screen.findByTitle("Reroll"));
  fireEvent.click(screen.getByRole("button", { name: /reroll ▸/i }));
  await waitFor(() => expect(api.regenerate).toHaveBeenCalledWith(
    "run", "s2", expect.any(Function), { guidance: "" },
    expect.any(AbortSignal), expect.any(String), expect.any(Function)));
});

test("Enter on a chosen model row commits the reroll; Enter on the provider does not", async () => {
  // A select's Enter is its menu's: letting it commit sent the reroll through
  // the route the reader was still choosing. A model row has none of its own.
  rerollableScene();
  renderCampaign();
  await rerollOnLocal();

  fireEvent.keyDown(screen.getByLabelText("Provider"), { key: "Enter" });
  expect(api.regenerate).not.toHaveBeenCalled();
  fireEvent.keyDown(screen.getByRole("radio", { name: "Llama 3" }), { key: "Enter" });

  await waitFor(() => expect(api.regenerate).toHaveBeenCalledWith(
    "run", "s1", expect.any(Function), { guidance: "", provider: "local", model: "llama3" },
    expect.any(AbortSignal), expect.any(String), expect.any(Function)));
});

test("Enter in the reroll box's typed-id box uses the id and does not send the reroll", async () => {
  rerollableScene();
  renderCampaign();
  await screen.findByText("old reply");
  fireEvent.click(await screen.findByTitle("Reroll"));
  fireEvent.change(await screen.findByLabelText("Provider"), { target: { value: "local" } });
  const box = await screen.findByLabelText("Model id");

  fireEvent.change(box, { target: { value: "vendor/typed" } });
  fireEvent.keyDown(box, { key: "Enter" });

  expect(await screen.findByRole("radio", { name: "vendor/typed" })).toBeChecked();
  expect(api.regenerate).not.toHaveBeenCalled();
  // and the next commit carries it
  fireEvent.click(screen.getByRole("button", { name: /reroll ▸/i }));
  await waitFor(() => expect(api.regenerate).toHaveBeenCalledWith(
    "run", "s1", expect.any(Function), { guidance: "", provider: "local", model: "vendor/typed" },
    expect.any(AbortSignal), expect.any(String), expect.any(Function)));
});

test("Escape in a model's test dialog closes the dialog, not the reroll popover", async () => {
  // The dialog is modal and closes on its own Escape; the popover around it
  // hearing the same key shut both, losing the route being chosen.
  rerollableScene();
  localModels("unverified");
  vi.mocked(api.previewModelTest).mockResolvedValue(fixture({
    provider: "Local", provider_id: "local", model: "llama3", sends: [], estimated_cost_usd: null }));
  renderCampaign();
  await rerollOnLocal();
  fireEvent.click(screen.getByRole("button", { name: "Test Llama 3" }));
  const dialog = await screen.findByRole("dialog", { name: "Test a model" });
  // Portalled to the body, so it paints above the page's chrome; React still
  // bubbles its keys through the popover, whose Enter must not commit from it.
  expect(dialog.closest(".reroll-pop")).toBeNull();
  fireEvent.keyDown(dialog, { key: "Enter" });
  expect(api.regenerate).not.toHaveBeenCalled();

  fireEvent.keyDown(dialog, { key: "Escape" });

  await waitFor(() => expect(screen.queryByRole("dialog", { name: "Test a model" })).toBeNull());
  expect(screen.getByPlaceholderText(/guide the reroll/i)).toBeInTheDocument();
  expect(screen.getByRole("radio", { name: "Llama 3" })).toBeChecked();
  expect(api.regenerate).not.toHaveBeenCalled();
});

test("a variant generated elsewhere says so on the swipe control", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "second" }] }));
  vi.mocked(api.getAlternates).mockResolvedValue(
    { active: 1, alternates: [ALT("first"), ALT("second", "local/llama3")] });
  renderCampaign();
  await screen.findByText("second");

  // The counter is the one place a take's route is visible at all: two
  // alternates read identically, and nothing else says which model wrote them.
  await waitFor(() => expect(screen.getByTitle(/Model: local\/llama3/)).toBeInTheDocument());
});

test("a variant with no recorded route adds no line to the swipe control", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "second" }] }));
  vi.mocked(api.getAlternates).mockResolvedValue(
    { active: 1, alternates: [ALT("first"), ALT("second")] });
  renderCampaign();
  await screen.findByText("second");

  await waitFor(() => expect(screen.getByTitle("second")).toBeInTheDocument());
});


test("regenerate carries a pending override", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue({
    meta: { id: "s1", title: "Old", response_preset: "cinematic" },
    messages: [{ role: "user", content: "hi" }, { role: "assistant", content: "old reply" }],
  });
  renderCampaign();
  await screen.findByText("old reply");
  const picker = await screen.findByLabelText("Next reply words");
  fireEvent.change(picker, { target: { value: "120" } });
  fireEvent.click(await screen.findByTitle("Reroll"));
  fireEvent.click(screen.getByRole("button", { name: /reroll ▸/i })); // empty guidance = plain reroll
  await waitFor(() => expect(api.regenerate).toHaveBeenCalledWith(
    "run", "s1", expect.any(Function),
    { guidance: "", response: { response_continuation_words: "120" } },
    expect.any(AbortSignal), expect.any(String), expect.any(Function)));
});
