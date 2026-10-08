// Swipes on the last response: the ‹ n/m › arrows, the ←/→ keys and the touch
// gesture on the post the transcript ends on, and the guards they inherit from
// the controls they mirror.
//
// A suite of its own for the reason `CampaignView.shortcuts` is one (#378): it
// drives the same page through the same harness, and what it is about is not
// what `CampaignView.test.tsx` is about.
import { act, screen, fireEvent, waitFor, within } from "@testing-library/react";

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
import { api, ApiError, type Message, type ResponseSwipe } from "../api/client";
import { installCampaignMocks, ONE_SCENE, renderCampaign } from "../testkit/campaignHarness";
import { TestPointerEvent } from "../testkit/pointer";
import { bodiesOf, declares, stylesheet } from "../testkit/stylesheet";

beforeEach(installCampaignMocks);

const press = (key: string, on: Window | Element = window) => fireEvent.keyDown(on, { key });

const EARLIER: Message = {
  role: "assistant", content: "The first answer.", speaker: "Mara",
  response_id: "rA", response_status: "complete", response_can_reroll: true };
const LAST: Message = {
  role: "assistant", content: "The second answer.", speaker: "Winifred",
  response_id: "rB", response_status: "complete", response_can_reroll: true };

function scene(messages: Message[]) {
  return { meta: { id: "s1", title: "Old" }, messages };
}
function playing(last: Message = LAST) {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue(scene([
    { role: "user", content: "hi" }, EARLIER, { role: "user", content: "and then?" }, last]));
}

const v = (id: string, status: "complete" | "incomplete" = "complete") => ({ id, status });
function swipeRead(over: Partial<ResponseSwipe> = {}): ResponseSwipe {
  return {
    active: 1, variants: [v("v1"), v("v2"), v("v3")],
    settings: null, resume_settings: null,
    can_reroll: true, editable: true, round_open: false, edited: false, ...over };
}
function reads(over: Partial<ResponseSwipe> = {}) {
  (api.getResponseSwipe as any).mockResolvedValue(swipeRead(over));
}

/** A touch drag right-to-left on `el`: the gesture's ›. */
function swipeLeft(el: Element) {
  fireEvent(el, new TestPointerEvent("pointerdown",
    { bubbles: true, pointerType: "touch", pointerId: 5, clientX: 200, clientY: 100 }));
  fireEvent(el, new TestPointerEvent("pointerup",
    { bubbles: true, pointerType: "touch", pointerId: 5, clientX: 110, clientY: 104 }));
}

/** The transcript row holding `text`. */
const row = (text: string) => screen.getByText(text).closest(".msg") as HTMLElement;
const previous = () => screen.getByRole("button", { name: "Previous reply variant" });
const next = () => screen.getByRole("button", { name: /^(Next reply variant|Generate a new reply variant)$/ });

test("the arrows hang off the last response only, counting complete variants", async () => {
  playing();
  reads();
  renderCampaign();
  expect(await screen.findByText("2/3")).toBeInTheDocument();
  expect(within(row("The second answer.")).getByRole("button", { name: "Previous reply variant" }))
    .toBeInTheDocument();
  expect(within(row("The first answer.")).queryByRole("button", { name: "Previous reply variant" }))
    .toBeNull();
  expect(screen.getAllByRole("button", { name: "Previous reply variant" })).toHaveLength(1);
  expect(api.getResponseSwipe).toHaveBeenCalledWith("run", "s1", "rB");
  expect(api.getResponseSwipe).not.toHaveBeenCalledWith("run", "s1", "rA");
});

test("‹ activates the previous complete variant, skipping an incomplete one, and the counter follows", async () => {
  playing();
  reads({ active: 2, variants: [v("v1"), v("v2", "incomplete"), v("v3")] });
  (api.activateResponseVariant as any).mockImplementation(async () => {
    // Same rid, new content: what an activate does to the post on screen.
    (api.getScene as any).mockResolvedValue(scene([
      { role: "user", content: "hi" }, EARLIER, { role: "user", content: "and then?" },
      { ...LAST, content: "The older take." }]));
    reads({ active: 0, variants: [v("v1"), v("v2", "incomplete"), v("v3")] });
    return { ok: true };
  });
  renderCampaign();
  expect(await screen.findByText("2/2")).toBeInTheDocument();
  fireEvent.click(previous());
  await waitFor(() => expect(api.activateResponseVariant).toHaveBeenCalledWith("run", "s1", "rB", "v1"));
  await screen.findByText("The older take.");
  expect(await screen.findByText("1/2")).toBeInTheDocument();
  expect(previous()).toBeDisabled();
});

// Spec §7: a refused activate is reported, and the read is asked again so the
// arrows reflect the server's state rather than the one they were drawn from.
test("an activate refused with 409 shows the refusal and refetches the swipe read", async () => {
  playing();
  reads();
  (api.activateResponseVariant as any).mockRejectedValue(
    new ApiError(409, "a roll was applied after this response"));
  renderCampaign();
  expect(await screen.findByText("2/3")).toBeInTheDocument();
  const before = (api.getResponseSwipe as any).mock.calls.length;
  fireEvent.click(previous());
  await waitFor(() => expect(api.activateResponseVariant).toHaveBeenCalledWith("run", "s1", "rB", "v1"));
  expect(await screen.findByText(/a roll was applied after this response/)).toBeInTheDocument();
  await waitFor(() => expect((api.getResponseSwipe as any).mock.calls.length).toBeGreaterThan(before));
  expect((api.getResponseSwipe as any).mock.calls.at(-1)).toEqual(["run", "s1", "rB"]);
  // Unchanged content: the refetch came from the refresh, not from the text.
  expect(screen.getByText("The second answer.")).toBeInTheDocument();
});

test("› at the newest generates once, with no guidance, and keeps the pending length chip", async () => {
  playing();
  reads({ active: 2 });
  let finish: (() => void) | undefined;
  (api.regenerateResponse as any).mockImplementation(
    (_c: string, _s: string, _r: string, onEvent: (e: unknown) => void) => new Promise<void>((resolve) => {
      finish = () => { onEvent({ done: true }); resolve(); };
    }));
  renderCampaign();
  expect(await screen.findByText("3/3")).toBeInTheDocument();
  const picker = screen.getByLabelText("Next reply words");
  await waitFor(() => expect(picker).toHaveValue(null));
  fireEvent.change(picker, { target: { value: "120" } });
  fireEvent.click(screen.getByRole("button", { name: "Generate a new reply variant" }));
  await waitFor(() => expect(api.regenerateResponse).toHaveBeenCalledTimes(1));
  // A second attempt while the first is in flight. Through the gesture, not
  // the button: the gutter hides its icons while busy, but the target row is
  // still mounted with its handlers spread, so this reaches `stepVariant`'s
  // own re-check of the disabled flag.
  vi.stubGlobal("PointerEvent", TestPointerEvent);
  try {
    swipeLeft(screen.getByText("The second answer."));
  } finally {
    vi.unstubAllGlobals();
  }
  expect(row("The second answer.")).toHaveClass("swipe-target");
  const call = (api.regenerateResponse as any).mock.calls[0];
  expect(call[2]).toBe("rB");
  expect(call[4]).toEqual({ guidance: "" });
  expect(call[4].response).toBeUndefined();
  await act(async () => finish?.());
  await waitFor(() => expect(screen.getByText("The second answer.")).toBeInTheDocument());
  expect(picker).toHaveValue(120);
  expect(api.regenerateResponse).toHaveBeenCalledTimes(1);
});

test.each([
  ["an unfinished round is open", { round_open: true }],
  ["the server says the response is not editable", { editable: false }],
])("every arrow is disabled when %s", async (_why, over) => {
  playing();
  reads(over);
  renderCampaign();
  expect(await screen.findByText("2/3")).toBeInTheDocument();
  expect(previous()).toBeDisabled();
  expect(next()).toBeDisabled();
  press("ArrowLeft");
  press("ArrowRight");
  expect(api.activateResponseVariant).not.toHaveBeenCalled();
  expect(screen.queryByLabelText("Reroll guidance")).toBeNull();
});

test("every arrow is disabled while a roll proposal is pending", async () => {
  playing();
  reads();
  (api.getRollProposal as any).mockResolvedValue({
    record: { id: "p1", status: "pending", resolution: null,
              payload: { id: "p1", check: "wits", check_label: "Wits", problems: [] } } });
  renderCampaign();
  await screen.findByRole("button", { name: /decline/i });
  expect(await screen.findByText("2/3")).toBeInTheDocument();
  expect(previous()).toBeDisabled();
  expect(next()).toBeDisabled();
});

test("the keys are inert once a review has landed", async () => {
  playing();
  reads();
  renderCampaign();
  expect(await screen.findByText("2/3")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  await screen.findByLabelText("Scene summary");
  expect(screen.queryByRole("button", { name: "Previous reply variant" })).toBeNull();
  press("ArrowLeft");
  press("ArrowRight");
  expect(api.activateResponseVariant).not.toHaveBeenCalled();
  expect(screen.queryByLabelText("Reroll guidance")).toBeNull();
  press("?");
  expect(screen.getByText("Previous reply variant").closest(".shortcuts-row")!.className).toContain("off");
  expect(screen.getByText("Next reply variant").closest(".shortcuts-row")!.className).toContain("off");
});

test("one complete variant and nowhere to generate shows no arrows", async () => {
  playing();
  reads({ active: 0, variants: [v("v1")], can_reroll: false });
  renderCampaign();
  await screen.findByText("The second answer.");
  await waitFor(() => expect(api.getResponseSwipe).toHaveBeenCalledWith("run", "s1", "rB"));
  expect(screen.queryByRole("button", { name: "Previous reply variant" })).toBeNull();
  expect(screen.queryByText("1/1")).toBeNull();
});

// A hand edit keeps the response id but the prose is no variant's, so the
// server answers `edited` with no active variant: no count, no provenance, and
// no arrow that would swap the player's own words for a variant.
test("hand-edited prose shows no arrows and no provenance", async () => {
  playing();
  reads({ active: null, edited: true, variants: [
    { id: "v1", status: "complete", made_by: { model: "realm/mara-70b" } }, v("v2")] });
  renderCampaign();
  await screen.findByText("The second answer.");
  await waitFor(() => expect(api.getResponseSwipe).toHaveBeenCalledWith("run", "s1", "rB"));
  expect(screen.queryByRole("button", { name: "Previous reply variant" })).toBeNull();
  expect(screen.queryByText(/\/2$/)).toBeNull();
  expect(document.querySelector('[title*="realm/mara-70b"]')).toBeNull();
  press("ArrowRight");
  expect(api.activateResponseVariant).not.toHaveBeenCalled();
  expect(screen.queryByLabelText("Reroll guidance")).toBeNull();
});

test("one complete variant that can be rerolled shows 1/1, with › generating", async () => {
  playing();
  reads({ active: 0, variants: [v("v1")] });
  renderCampaign();
  expect(await screen.findByText("1/1")).toBeInTheDocument();
  expect(previous()).toBeDisabled();
  expect(screen.getByRole("button", { name: "Generate a new reply variant" })).toBeEnabled();
});

// `canReroll` is false where the transcript holds no player post to answer
// (an offscreen or all-assistant scene), so › would have nothing to generate.
test("one complete variant in a scene with no player post shows no arrows, even if the ledger can reroll", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue(scene([EARLIER, LAST]));
  reads({ active: 0, variants: [v("v1")], can_reroll: true });
  renderCampaign();
  await screen.findByText("The second answer.");
  await waitFor(() => expect(api.getResponseSwipe).toHaveBeenCalledWith("run", "s1", "rB"));
  expect(screen.queryAllByRole("button", { name: /Previous reply variant|Generate a new reply variant/ })).toHaveLength(0);
  expect(screen.queryByText("1/1")).toBeNull();
});

test("the counter's title carries the active variant's provenance", async () => {
  playing();
  reads({
    variants: [v("v1"), { ...v("v2"), made_by: { model: "realm/mara-70b", composed: "primary" } }, v("v3")] });
  renderCampaign();
  const counter = await screen.findByText("2/3");
  expect(counter.getAttribute("title")).toContain("Model: realm/mara-70b");
});

describe("the keys", () => {
  test("← steps to the previous variant", async () => {
    playing();
    reads();
    renderCampaign();
    expect(await screen.findByText("2/3")).toBeInTheDocument();
    press("ArrowLeft");
    await waitFor(() => expect(api.activateResponseVariant).toHaveBeenCalledWith("run", "s1", "rB", "v1"));
  });

  test("→ before the newest steps to the next variant", async () => {
    playing();
    reads();
    renderCampaign();
    expect(await screen.findByText("2/3")).toBeInTheDocument();
    press("ArrowRight");
    await waitFor(() => expect(api.activateResponseVariant).toHaveBeenCalledWith("run", "s1", "rB", "v3"));
  });

  // Nothing bound bare spends money without a further confirmation: at the
  // newest, → is `r`, not ›.
  test("→ at the newest opens the reroll box and spends nothing", async () => {
    playing();
    reads({ active: 2 });
    renderCampaign();
    expect(await screen.findByText("3/3")).toBeInTheDocument();
    press("ArrowRight");
    expect(await screen.findByLabelText("Reroll guidance")).toBeInTheDocument();
    expect(api.regenerateResponse).not.toHaveBeenCalled();
    expect(api.activateResponseVariant).not.toHaveBeenCalled();
  });

  // The box it opens is a ledger reroll, which streams the frozen snapshot and
  // ignores a `response` override -- so it neither sends the pending length
  // chip nor spends it.
  test("→ at the newest, then Reroll ▸, regenerates without spending the length chip", async () => {
    playing();
    reads({ active: 2 });
    let finish: (() => void) | undefined;
    (api.regenerateResponse as any).mockImplementation(
      (_c: string, _s: string, _r: string, onEvent: (e: unknown) => void) => new Promise<void>((resolve) => {
        finish = () => { onEvent({ done: true }); resolve(); };
      }));
    renderCampaign();
    expect(await screen.findByText("3/3")).toBeInTheDocument();
    const picker = screen.getByLabelText("Next reply words");
    await waitFor(() => expect(picker).toHaveValue(null));
    fireEvent.change(picker, { target: { value: "120" } });
    press("ArrowRight");
    fireEvent.change(await screen.findByLabelText("Reroll guidance"), { target: { value: "Colder" } });
    fireEvent.click(screen.getByRole("button", { name: "Reroll ▸" }));
    await waitFor(() => expect(api.regenerateResponse).toHaveBeenCalledTimes(1));
    const call = (api.regenerateResponse as any).mock.calls[0];
    expect(call[2]).toBe("rB");
    expect(call[4]).toEqual({ guidance: "Colder" });
    expect(call[4].response).toBeUndefined();
    await act(async () => finish?.());
    await waitFor(() => expect(screen.getByText("The second answer.")).toBeInTheDocument());
    expect(picker).toHaveValue(120);
  });

  test("→ at the newest does nothing when the response cannot be rerolled", async () => {
    playing();
    reads({ active: 2, can_reroll: false });
    renderCampaign();
    expect(await screen.findByText("3/3")).toBeInTheDocument();
    press("ArrowRight");
    expect(screen.queryByLabelText("Reroll guidance")).toBeNull();
    expect(api.regenerateResponse).not.toHaveBeenCalled();
    expect(api.activateResponseVariant).not.toHaveBeenCalled();
  });

  test("both are listed in the shortcut sheet", async () => {
    playing();
    reads();
    renderCampaign();
    expect(await screen.findByText("2/3")).toBeInTheDocument();
    press("?");
    const sheet = screen.getByRole("dialog", { name: /keyboard/i });
    expect(sheet.textContent).toContain("Previous reply variant");
    expect(sheet.textContent).toContain("Next reply variant");
  });

  test("neither fires with the caret in the composer", async () => {
    playing();
    reads();
    renderCampaign();
    expect(await screen.findByText("2/3")).toBeInTheDocument();
    const composer = screen.getByPlaceholderText(/speak your intent/i);
    composer.focus();
    press("ArrowLeft", composer);
    press("ArrowRight", composer);
    expect(api.activateResponseVariant).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("Reroll guidance")).toBeNull();
  });

  // The reroll box is an overlay, so it holds the scene's bindings off while it
  // is up. Its model list is radios, which are not typing targets: without the
  // hold, ← on one activated another variant behind the box, and → at the
  // newest reopened the box and threw away the route being chosen.
  test("neither fires from a model radio in the open reroll box", async () => {
    playing();
    reads({ active: 2 });
    (api.readConnectionCapabilities as any).mockImplementation(
      (_provider: string, need: string) => Promise.resolve({
        provider_preset: { id: "openrouter", label: "OpenRouter", kind: "openrouter", base_url: "",
                           url_locked: true, billing: "metered", reports_price: true,
                           always: [], possible: [], never: [] },
        need, reason: null, hidden: [],
        groups: { unverified: [], fits: ["Saltmarch One", "Saltmarch Two"].map((name) => ({
          id: name.toLowerCase().replace(" ", "-"), name, context: null, prompt: null,
          completion: null, reason: "the catalog says so", capabilities: {} })) } }));
    renderCampaign();
    expect(await screen.findByText("3/3")).toBeInTheDocument();
    press("ArrowRight");
    fireEvent.change(await screen.findByLabelText("Provider"), { target: { value: "openrouter" } });
    const radio = await screen.findByRole("radio", { name: "Saltmarch One" });
    fireEvent.click(radio);
    radio.focus();

    press("ArrowLeft", radio);
    press("ArrowRight", radio);
    // Held off by the overlay, not only filtered at the radios: from the box's
    // buttons, or with nothing focused at all, the scene's arrows are out too.
    press("ArrowLeft", screen.getByRole("button", { name: "Default" }));
    press("ArrowLeft");
    press("ArrowRight");

    expect(api.activateResponseVariant).not.toHaveBeenCalled();
    // The box is still up, still holding the model chosen in it.
    expect(screen.getByRole("radio", { name: "Saltmarch One" })).toBeChecked();
    expect(screen.getByLabelText("Provider")).toHaveValue("openrouter");
    // And the arrows were not spoken for: the radio group keeps its own travel.
    const travel = new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true, cancelable: true });
    screen.getByRole("radio", { name: "Saltmarch One" }).dispatchEvent(travel);
    expect(travel.defaultPrevented).toBe(false);

    // Escape still backs out of it, from wherever focus is.
    press("Escape");
    await waitFor(() => expect(screen.queryByLabelText("Reroll guidance")).toBeNull());
  });

  // A typed model id is the route's only once "Use this id" takes it.
  // Rerolling before that sent the provider alone -- the scene route's
  // standing model, or the provider's own -- while the box showed another.
  test("Reroll ▸ waits for a typed model id to be taken, and Enter does too", async () => {
    playing();
    reads({ active: 2 });
    (api.readConnectionCapabilities as any).mockImplementation(
      (_provider: string, need: string) => Promise.resolve({
        provider_preset: { id: "openrouter", label: "OpenRouter", kind: "openrouter", base_url: "",
                           url_locked: true, billing: "metered", reports_price: true,
                           always: [], possible: [], never: [] },
        need, reason: null, hidden: [], groups: { unverified: [], fits: [] } }));
    (api.regenerateResponse as any).mockResolvedValue(undefined);
    renderCampaign();
    expect(await screen.findByText("3/3")).toBeInTheDocument();
    press("ArrowRight");
    fireEvent.change(await screen.findByLabelText("Provider"), { target: { value: "openrouter" } });
    fireEvent.change(await screen.findByLabelText("Model id"), { target: { value: "vendor/typed" } });

    expect(screen.getByRole("button", { name: "Reroll ▸" })).toBeDisabled();
    fireEvent.keyDown(screen.getByLabelText("Reroll guidance"), { key: "Enter" });
    fireEvent.click(screen.getByRole("button", { name: "Reroll ▸" }));
    expect(api.regenerateResponse).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Use this id" }));
    fireEvent.click(screen.getByRole("button", { name: "Reroll ▸" }));
    await waitFor(() => expect(api.regenerateResponse).toHaveBeenCalledTimes(1));
    expect((api.regenerateResponse as any).mock.calls[0][4]).toEqual(
      expect.objectContaining({ provider: "openrouter", model: "vendor/typed" }));
  });

  // The same picker sits in the reply's own Response actions, which is a
  // disclosure in the page rather than an overlay -- so nothing holds the
  // scene's bindings off there, and the picker keeps its radios' arrows itself.
  test("neither fires from a model radio in the reply's own route picker", async () => {
    playing();
    reads();
    (api.readConnectionCapabilities as any).mockImplementation(
      (_provider: string, need: string) => Promise.resolve({
        provider_preset: { id: "openrouter", label: "OpenRouter", kind: "openrouter", base_url: "",
                           url_locked: true, billing: "metered", reports_price: true,
                           always: [], possible: [], never: [] },
        need, reason: null, hidden: [],
        groups: { unverified: [], fits: ["Saltmarch One", "Saltmarch Two"].map((name) => ({
          id: name.toLowerCase().replace(" ", "-"), name, context: null, prompt: null,
          completion: null, reason: "the catalog says so", capabilities: {} })) } }));
    renderCampaign();
    expect(await screen.findByText("2/3")).toBeInTheDocument();
    const last = row("The second answer.");
    fireEvent.click(within(last).getByText("Response actions"));
    fireEvent.click(within(last).getByText("Model for this reroll"));
    fireEvent.change(await within(last).findByLabelText("Provider"), { target: { value: "openrouter" } });
    const radio = await within(last).findByRole("radio", { name: "Saltmarch One" });
    fireEvent.click(radio);
    radio.focus();

    const travel = new KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true, cancelable: true });
    act(() => { radio.dispatchEvent(travel); });
    press("ArrowRight", radio);

    expect(travel.defaultPrevented).toBe(false);
    expect(api.activateResponseVariant).not.toHaveBeenCalled();
    expect(within(row("The second answer.")).getByRole("radio", { name: "Saltmarch One" })).toBeChecked();
  });
});

describe("the touch swipe", () => {
  beforeEach(() => vi.stubGlobal("PointerEvent", TestPointerEvent));
  afterEach(() => vi.unstubAllGlobals());

  function drag(el: Element, dx: number) {
    fireEvent(el, new TestPointerEvent("pointerdown",
      { bubbles: true, pointerType: "touch", pointerId: 3, clientX: 200, clientY: 100 }));
    fireEvent(el, new TestPointerEvent("pointerup",
      { bubbles: true, pointerType: "touch", pointerId: 3, clientX: 200 + dx, clientY: 104 }));
  }

  test("a right swipe on the last response steps back; one on an earlier response does nothing", async () => {
    playing();
    reads();
    renderCampaign();
    expect(await screen.findByText("2/3")).toBeInTheDocument();
    expect(row("The second answer.")).toHaveClass("swipe-target");
    expect(row("The first answer.")).not.toHaveClass("swipe-target");
    drag(screen.getByText("The first answer."), 90);
    expect(api.activateResponseVariant).not.toHaveBeenCalled();
    drag(screen.getByText("The second answer."), 90);
    await waitFor(() => expect(api.activateResponseVariant).toHaveBeenCalledWith("run", "s1", "rB", "v1"));
  });

  test("a left swipe past the newest generates", async () => {
    playing();
    reads({ active: 2 });
    renderCampaign();
    expect(await screen.findByText("3/3")).toBeInTheDocument();
    drag(screen.getByText("The second answer."), -90);
    await waitFor(() => expect(api.regenerateResponse).toHaveBeenCalledTimes(1));
    expect((api.regenerateResponse as any).mock.calls[0][4]).toEqual(
      { guidance: "" });
  });
});

// Real reads migrate every assistant post into the ledger, so only a mocked
// scene can still hold one without a response id -- and that one keeps the
// arrows it always had.
test("a trailing reply with no response id keeps the legacy alternates arrows", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue(scene([
    { role: "user", content: "hi" }, { role: "assistant", content: "an old reply" }]));
  (api.getAlternates as any).mockResolvedValue({ active: 0, alternates: [
    { id: "a1", created: "", guidance: "", model: "", posts: 1, preview: "one" },
    { id: "a2", created: "", guidance: "", model: "", posts: 1, preview: "two" }] });
  renderCampaign();
  expect(await screen.findByRole("button", { name: "Previous alternate" })).toBeInTheDocument();
  expect(screen.getByText("1/2")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Previous reply variant" })).toBeNull();
  expect(api.getResponseSwipe).not.toHaveBeenCalled();
});

// jsdom applies no CSS, so the rule is read off the stylesheet: without it the
// browser would treat a sideways drag on the post as a pan and never deliver
// the pointerup the gesture decides on.
test("the swipe target leaves vertical panning to the browser", () => {
  const { css } = stylesheet();
  const bodies = bodiesOf(css, ".msg.swipe-target");
  expect(bodies.map((b) => declares(b, "touch-action"))).toContain("pan-y pinch-zoom");
});

// `pan-y` on the post also stops an ANCESTOR from panning sideways for a drag
// that starts on it, so wide content inside the swipe target has to be its own
// horizontal scroller -- the touch-action intersection stops at a scroller,
// which then pans natively (and useSwipe declines a gesture started in one).
test("wide content in the swipe target scrolls sideways on its own", () => {
  const { css } = stylesheet();
  const pre = bodiesOf(css, ".msg.swipe-target .msg-body pre");
  expect(pre.map((b) => declares(b, "overflow-x"))).toContain("auto");
  const table = bodiesOf(css, ".msg.swipe-target .msg-body table");
  expect(table.map((b) => declares(b, "display"))).toContain("block");
  expect(table.map((b) => declares(b, "max-width"))).toContain("100%");
  expect(table.map((b) => declares(b, "overflow-x"))).toContain("auto");
});
