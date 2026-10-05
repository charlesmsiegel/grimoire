// Keep writing (play controls IV): continue the trailing response where it
// stops. A suite of its own for the reason `CampaignView.swipes` is one: it
// drives the same page through the same harness, about the swipe target's
// response controls rather than the play loop.
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
import type { ChatEvent } from "../api/stream";
import { installCampaignMocks, ONE_SCENE, renderCampaign } from "../testkit/campaignHarness";

beforeEach(installCampaignMocks);

const EARLIER: Message = {
  role: "assistant", content: "The first answer.", speaker: "Winifred",
  response_id: "response-a", response_status: "complete", response_can_reroll: true };
const LAST: Message = {
  role: "assistant", content: "The accepted response.", speaker: "Mara",
  response_id: "response-b", response_status: "complete", response_can_reroll: true };

function scene(messages: Message[]) {
  return { meta: { id: "s1", title: "Old" }, messages };
}
function playing(last: Message = LAST) {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue(scene([
    { role: "user", content: "hi" }, EARLIER, { role: "user", content: "and then?" }, last]));
}
function reads(over: Partial<ResponseSwipe> = {}) {
  (api.getResponseSwipe as any).mockResolvedValue({
    active: 0, variants: [{ id: "v1", status: "complete" }],
    settings: null, resume_settings: null,
    can_reroll: true, editable: true, round_open: false, edited: false, ...over });
}

const keepWriting = () => screen.getByRole("button", { name: "Keep writing ▸" });

/** Open every "Response actions" disclosure on the page. */
function openActions() {
  for (const summary of screen.getAllByText("Response actions")) fireEvent.click(summary);
}

test("keep writing is offered on the trailing response only and calls the extend route", async () => {
  playing();
  reads();
  renderCampaign();
  await screen.findByText("The accepted response.");
  await waitFor(() => expect(api.getResponseSwipe).toHaveBeenCalledWith("run", "s1", "response-b"));
  openActions();
  expect(screen.getAllByRole("button", { name: "Keep writing ▸" })).toHaveLength(1);
  const last = screen.getByText("The accepted response.").closest(".msg") as HTMLElement;
  expect(within(last).getByRole("button", { name: "Keep writing ▸" })).toBeEnabled();
  fireEvent.click(keepWriting());
  await waitFor(() => expect(api.extendResponse).toHaveBeenCalledOnce());
  const call = (api.extendResponse as any).mock.calls[0];
  expect(call.slice(0, 3)).toEqual(["run", "s1", "response-b"]);
  expect(call[4]).toEqual({ guidance: "", connection_id: "", model: "" });
  expect(call[4].response).toBeUndefined();
  expect(api.regenerateResponse).not.toHaveBeenCalled();
});

test("the extend bubble grows from the old text and hides the target post", async () => {
  playing();
  reads();
  let emit: (e: ChatEvent) => void = () => {};
  let finish: () => void = () => {};
  (api.extendResponse as any).mockImplementation(
    (_c: string, _s: string, _r: string, onEvent: (e: ChatEvent) => void) => new Promise<void>((resolve) => {
      emit = onEvent;
      finish = () => {
        (api.getScene as any).mockResolvedValue(scene([
          { role: "user", content: "hi" }, EARLIER, { role: "user", content: "and then?" },
          { ...LAST, content: "The accepted response. Then more." }]));
        onEvent({ done: true }); resolve();
      };
    }));
  renderCampaign();
  await screen.findByText("The accepted response.");
  await waitFor(() => expect(api.getResponseSwipe).toHaveBeenCalledWith("run", "s1", "response-b"));
  openActions();
  fireEvent.click(keepWriting());
  await waitFor(() => expect(api.extendResponse).toHaveBeenCalledOnce());
  act(() => {
    emit({ response_start: { id: "response-b", speaker: "Mara", actor_ref: "characters:mara",
                             extend: { seed: "The accepted response." } } });
    emit({ delta: "Then more." });
  });
  const bubble = await screen.findByText("The accepted response. Then more.");
  expect(bubble.closest(".streaming-response")).not.toBeNull();
  // The target row is not drawn beside the bubble that redraws it whole.
  expect(screen.queryByText("The accepted response.")).toBeNull();
  expect(screen.getByText("The first answer.")).toBeInTheDocument();
  await act(async () => finish());
  const landed = await screen.findByText("The accepted response. Then more.");
  expect(landed.closest(".streaming-response")).toBeNull();
});

test.each([
  ["an unfinished round is open", { round_open: true }],
  ["the server says the response is not editable", { editable: false }],
  ["the ledger cannot reroll it", { can_reroll: false }],
  ["the active variant is not complete", { variants: [{ id: "v1", status: "incomplete" as const }] }],
])("keep writing is disabled when %s", async (_why, over) => {
  playing();
  reads(over);
  renderCampaign();
  await screen.findByText("The accepted response.");
  await waitFor(() => expect(api.getResponseSwipe).toHaveBeenCalledWith("run", "s1", "response-b"));
  openActions();
  expect(keepWriting()).toBeDisabled();
});

test("keep writing is disabled while the swipe read has not answered", async () => {
  // The harness default: a failed read, so nothing says the reply may change.
  playing();
  renderCampaign();
  await screen.findByText("The accepted response.");
  await waitFor(() => expect(api.getResponseSwipe).toHaveBeenCalled());
  openActions();
  expect(keepWriting()).toBeDisabled();
});

test("a hand-edited reply can still be continued, from the trim", async () => {
  playing();
  reads({ active: null, edited: true });
  renderCampaign();
  await screen.findByText("The accepted response.");
  await waitFor(() => expect(api.getResponseSwipe).toHaveBeenCalledWith("run", "s1", "response-b"));
  openActions();
  expect(keepWriting()).toBeEnabled();
});

test("keep writing has no key of its own", async () => {
  playing();
  reads();
  renderCampaign();
  await screen.findByText("The accepted response.");
  await waitFor(() => expect(api.getResponseSwipe).toHaveBeenCalledWith("run", "s1", "response-b"));
  fireEvent.keyDown(window, { key: "?" });
  expect(screen.queryByText(/Keep writing/)).toBeNull();
});
