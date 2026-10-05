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
import { api, type Message, type ResponseSwipe } from "../api/client";
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
    can_reroll: true, editable: true, round_open: false, ...over };
}
function reads(over: Partial<ResponseSwipe> = {}) {
  (api.getResponseSwipe as any).mockResolvedValue(swipeRead(over));
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
  const button = screen.getByRole("button", { name: "Generate a new reply variant" });
  fireEvent.click(button);
  fireEvent.click(button);
  await waitFor(() => expect(api.regenerateResponse).toHaveBeenCalledTimes(1));
  const call = (api.regenerateResponse as any).mock.calls[0];
  expect(call[2]).toBe("rB");
  expect(call[4]).toEqual({ guidance: "", connection_id: "", model: "" });
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

test("one complete variant that can be rerolled shows 1/1, with › generating", async () => {
  playing();
  reads({ active: 0, variants: [v("v1")] });
  renderCampaign();
  expect(await screen.findByText("1/1")).toBeInTheDocument();
  expect(previous()).toBeDisabled();
  expect(screen.getByRole("button", { name: "Generate a new reply variant" })).toBeEnabled();
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
      { guidance: "", connection_id: "", model: "" });
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
  expect(bodies.map((b) => declares(b, "touch-action"))).toContain("pan-y");
});
