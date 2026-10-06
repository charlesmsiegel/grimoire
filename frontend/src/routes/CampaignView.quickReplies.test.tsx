// Quick replies in the composer: the strip renders the campaign's effective
// set, and each kind does what the control it stands for does -- without ever
// touching the player's draft.
//
// A suite of its own for the reason `CampaignView.swipes` is one: it drives the
// same page through the same harness, about something else.
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

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
import { api, ApiError, type QuickReply } from "../api/client";
import { installCampaignMocks, ONE_SCENE, playRoutes, renderCampaign, withPalette }
  from "../testkit/campaignHarness";
import { RunRegistryProvider } from "../runs/RunRegistryProvider";

beforeEach(installCampaignMocks);

const POSTS = [{ role: "user", content: "hi" }, { role: "assistant", content: "a reply" }];
const LOOK: QuickReply = { id: "q1", label: "Look around", kind: "send", text: "I take in the room.", mode: "send" };
const STEER: QuickReply = { id: "q4", label: "Steer", kind: "direct", text: "Bring in the rain.", mode: "send" };

function withPosts(messages: unknown[] = POSTS) {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages });
}

function offer(...replies: QuickReply[]) {
  (api.getEffectiveQuickReplies as any).mockResolvedValue({ replies });
}

const speakBox = () => screen.getByPlaceholderText("Speak your intent…");

test("the strip shows the effective set in order, and is absent when empty", async () => {
  withPosts();
  offer(LOOK, STEER);
  const view = renderCampaign();
  await screen.findByText("a reply");
  const bar = await screen.findByRole("toolbar", { name: "Quick replies" });
  expect(within(bar).getAllByRole("button").map((b) => b.textContent)).toEqual(["Look around", "Steer"]);
  expect(api.getEffectiveQuickReplies).toHaveBeenCalledWith("run");
  view.unmount();
  offer();
  renderCampaign();
  await screen.findByText("a reply");
  expect(screen.queryByRole("toolbar", { name: "Quick replies" })).toBeNull();
});

test("send posts the canned text and leaves the draft alone", async () => {
  withPosts();
  offer(LOOK);
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.change(speakBox(), { target: { value: "half a thought" } });
  fireEvent.click(await screen.findByRole("button", { name: "Look around" }));
  await waitFor(() => expect(api.chat).toHaveBeenCalledWith("run", "s1", "I take in the room.",
    expect.any(Function), undefined, expect.any(AbortSignal), expect.any(String), expect.any(Function), false));
  expect(speakBox()).toHaveValue("half a thought");
});

test("direct sends a director note without flipping the composer", async () => {
  withPosts();
  offer(STEER);
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(await screen.findByRole("button", { name: "Steer" }));
  await waitFor(() => expect(api.chat).toHaveBeenCalledWith("run", "s1", "Bring in the rain.",
    expect.any(Function), undefined, expect.any(AbortSignal), expect.any(String), expect.any(Function), true));
  expect(screen.getByRole("button", { name: "Speak" })).toHaveAttribute("aria-pressed", "true");
  expect(speakBox()).toHaveValue("");
});

test("a failed quick send does not hand the canned text back", async () => {
  withPosts();
  offer(LOOK, STEER);
  (api.chat as any).mockRejectedValue(new Error("boom"));
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(await screen.findByRole("button", { name: "Look around" }));
  await waitFor(() => expect(api.chat).toHaveBeenCalledTimes(1));
  fireEvent.click(await screen.findByRole("button", { name: "Steer" }));
  await waitFor(() => expect(api.chat).toHaveBeenCalledTimes(2));
  expect(speakBox()).toHaveValue("");
  expect(screen.getByRole("button", { name: "Speak" })).toHaveAttribute("aria-pressed", "true");
});

test("a quick send consumes the one-shot response targets", async () => {
  withPosts();
  offer(LOOK);
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.change(screen.getByLabelText("Next reply words"), { target: { value: "300" } });
  fireEvent.click(await screen.findByRole("button", { name: "Look around" }));
  await waitFor(() => expect(api.chat).toHaveBeenCalledWith("run", "s1", "I take in the room.",
    expect.any(Function), expect.objectContaining({ response_continuation_words: "300" }),
    expect.any(AbortSignal), expect.any(String), expect.any(Function), false));
});

test("insert fills an empty composer and switches its mode to the reply's", async () => {
  withPosts();
  offer({ ...STEER, mode: "insert" });
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(await screen.findByRole("button", { name: "Steer" }));
  expect(await screen.findByPlaceholderText(/direct the scene/i)).toHaveValue("Bring in the rain.");
  expect(screen.getByRole("button", { name: "Direct" })).toHaveAttribute("aria-pressed", "true");
  expect(api.chat).not.toHaveBeenCalled();
});

test("insert appends to a same-kind draft after a blank line", async () => {
  withPosts();
  offer({ ...LOOK, mode: "insert" });
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.change(speakBox(), { target: { value: "draft  " } });
  fireEvent.click(await screen.findByRole("button", { name: "Look around" }));
  await waitFor(() => expect(speakBox()).toHaveValue("draft\n\nI take in the room."));
  expect(api.chat).not.toHaveBeenCalled();
});

test("insert behind a different-kind draft waits under the held-draft notice", async () => {
  withPosts();
  offer({ ...STEER, mode: "insert" });
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.change(speakBox(), { target: { value: "my own words" } });
  fireEvent.click(await screen.findByRole("button", { name: "Steer" }));
  expect(await screen.findByText(/🎬 note held · clear the box to get it back/)).toBeInTheDocument();
  expect(speakBox()).toHaveValue("my own words");
  expect(screen.getByRole("button", { name: "Speak" })).toHaveAttribute("aria-pressed", "true");
  // Clearing the box is what releases it, with its own turn kind.
  fireEvent.change(speakBox(), { target: { value: "" } });
  expect(await screen.findByPlaceholderText(/direct the scene/i)).toHaveValue("Bring in the rain.");
  expect(api.chat).not.toHaveBeenCalled();
});

test("insert is refused rather than parked over words already waiting", async () => {
  withPosts();
  offer({ ...STEER, mode: "insert" });
  let fail: (e: Error) => void = () => {};
  (api.chat as any).mockImplementation(() => new Promise<void>((_res, rej) => { fail = rej; }));
  renderCampaign();
  await screen.findByText("a reply");
  // A typed note fails while a Speak draft holds the box: the note is parked.
  fireEvent.click(screen.getByRole("button", { name: "Direct" }));
  fireEvent.change(screen.getByPlaceholderText(/direct the scene/i), { target: { value: "a typed note" } });
  fireEvent.click(screen.getByRole("button", { name: "Direct 🎬" }));
  await waitFor(() => expect(api.chat).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByRole("button", { name: "Speak" }));
  fireEvent.change(speakBox(), { target: { value: "my own words" } });
  fail(new Error("boom"));
  expect(await screen.findByText(/🎬 note held/)).toBeInTheDocument();
  fireEvent.click(await screen.findByRole("button", { name: "Steer" }));
  expect(await screen.findByRole("status")).toHaveTextContent(/note not inserted/i);
  expect(speakBox()).toHaveValue("my own words");
  // The words that were waiting are the ones that come back.
  fireEvent.change(speakBox(), { target: { value: "" } });
  expect(await screen.findByPlaceholderText(/direct the scene/i)).toHaveValue("a typed note");
});

test("a send reply is disabled in a PC-less scene", async () => {
  (api.listScenes as any).mockResolvedValue([{ ...ONE_SCENE[0], pcless: true }]);
  (api.getScene as any).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: POSTS });
  offer(LOOK, STEER);
  renderCampaign();
  await screen.findByText("a reply");
  const look = await screen.findByRole("button", { name: "Look around" });
  expect(look).toBeDisabled();
  expect(look).toHaveAttribute("title", "This scene has no player character");
  expect(screen.getByRole("button", { name: "Steer" })).toBeEnabled();
});

test("the strip goes with the composer on a finished scene", async () => {
  (api.listScenes as any).mockResolvedValue([{ ...ONE_SCENE[0], done: true }]);
  (api.getScene as any).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: POSTS });
  offer(LOOK);
  renderCampaign();
  await screen.findByText("a reply");
  await screen.findByText(/Scene complete/);
  expect(screen.queryByRole("toolbar", { name: "Quick replies" })).toBeNull();
});

// ---- saved rolls, tasks, next scene ----

const ROLL: QuickReply = { id: "q2", label: "Search", kind: "roll", notation: "1d20+2", roll_label: "Perception" };
const SUMMARIZE: QuickReply = { id: "q3", label: "Summarize", kind: "task", task: "rolling_summary" };
const BREAK: QuickReply = { id: "q5", label: "Break?", kind: "task", task: "scene_break" };
const NEXT: QuickReply = { id: "q6", label: "Next scene", kind: "task", task: "next_scene" };

test("roll calls the roll route with the saved notation, and is offered without a bound module", async () => {
  withPosts();
  (api.getCampaignModule as any).mockResolvedValue({ setting: "", resolved: null, source: null });
  offer(ROLL);
  (api.roll as any).mockResolvedValue({ ok: true, roll: { id: "r1" }, message: "🎲" });
  renderCampaign();
  await screen.findByText("a reply");
  const search = await screen.findByRole("button", { name: "Search" });
  await waitFor(() => expect(search).toBeEnabled());
  expect(search).toHaveAttribute("title", "1d20+2 — Perception");
  fireEvent.click(search);
  await waitFor(() => expect(api.roll).toHaveBeenCalledWith("run", "s1", "1d20+2", "Perception"));
  expect(screen.queryByRole("button", { name: "Roll dice" })).toBeNull();   // the dice button stays hidden
});

test("roll is disabled while the module read is out", async () => {
  withPosts();
  (api.getCampaignModule as any).mockReturnValue(new Promise(() => {}));
  offer(ROLL);
  renderCampaign();
  await screen.findByText("a reply");
  expect(await screen.findByRole("button", { name: "Search" })).toBeDisabled();
});

test("roll is disabled on an empty scene", async () => {
  withPosts([]);
  offer(ROLL);
  renderCampaign();
  expect(await screen.findByRole("button", { name: "Search" })).toBeDisabled();
});

test("a failed saved roll says so in the composer", async () => {
  withPosts();
  offer(ROLL);
  (api.roll as any).mockRejectedValue(new ApiError(400, "bad"));
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(await screen.findByRole("button", { name: "Search" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Roll failed: bad");
});

test("summary task forces a bounded fold and bumps the context on success", async () => {
  withPosts();
  offer(SUMMARIZE);
  (api.refreshRollingSummary as any).mockResolvedValue({
    summary: "Folded.", at: 2, total: 2, stale: false, every: 10, due: false, refreshed: true });
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(await screen.findByRole("button", { name: "Summarize" }));
  await waitFor(() => expect(api.refreshRollingSummary).toHaveBeenCalledWith("run", "s1", true, 2));
  expect(screen.queryByRole("status")).toBeNull();
});

test("a task's bound is absolute in a windowed transcript", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: POSTS,
    offset: 40, total: 42, has_older: true });
  offer(BREAK);
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(await screen.findByRole("button", { name: "Break?" }));
  await waitFor(() => expect(api.askSceneBreak).toHaveBeenCalledWith("run", "s1", true, 42));
});

test("a declined fold or question is a notice", async () => {
  withPosts();
  offer(SUMMARIZE, BREAK);
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(await screen.findByRole("button", { name: "Summarize" }));
  expect(await screen.findByRole("status")).toHaveTextContent(/already current/i);
  fireEvent.click(await screen.findByRole("button", { name: "Break?" }));
  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(/No scene-break question/i));
});

test("a failed task says so in the composer", async () => {
  withPosts();
  offer(SUMMARIZE);
  (api.refreshRollingSummary as any).mockRejectedValue(new ApiError(502, "provider down"));
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(await screen.findByRole("button", { name: "Summarize" }));
  expect(await screen.findByRole("status")).toHaveTextContent("Summary failed: provider down");
});

test("tasks wait for a connection", async () => {
  withPosts();
  offer(SUMMARIZE);
  render(
    <RunRegistryProvider>
      <MemoryRouter initialEntries={["/campaigns/run/scenes/s1"]}>
        {withPalette(playRoutes(false))}
      </MemoryRouter>
    </RunRegistryProvider>);
  await screen.findByText("a reply");
  expect(await screen.findByRole("button", { name: "Summarize" })).toBeDisabled();
});

test("a task running from the strip disables the inspector's button", async () => {
  withPosts();
  offer(SUMMARIZE);
  (api.refreshRollingSummary as any).mockReturnValue(new Promise(() => {}));
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(screen.getByRole("button", { name: /What the model saw/ }));
  const refresh = await screen.findByRole("button", { name: /refresh now/i });
  await waitFor(() => expect(refresh).toBeEnabled());
  fireEvent.click(await screen.findByRole("button", { name: "Summarize" }));
  await waitFor(() => expect(refresh).toBeDisabled());
  expect(screen.getByRole("button", { name: "Summarize" })).toBeDisabled();
  expect(api.refreshRollingSummary).toHaveBeenCalledTimes(1);
});

test("a task running from the inspector disables the strip's button", async () => {
  withPosts();
  offer(SUMMARIZE);
  (api.refreshRollingSummary as any).mockReturnValue(new Promise(() => {}));
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(screen.getByRole("button", { name: /What the model saw/ }));
  const refresh = await screen.findByRole("button", { name: /refresh now/i });
  await waitFor(() => expect(refresh).toBeEnabled());
  const summarize = screen.getByRole("button", { name: "Summarize" });
  expect(summarize).toBeEnabled();
  fireEvent.click(refresh);
  await waitFor(() => expect(summarize).toBeDisabled());
  fireEvent.click(summarize);
  expect(api.refreshRollingSummary).toHaveBeenCalledTimes(1);
});

test("next_scene opens the scene chooser", async () => {
  withPosts();
  offer(NEXT);
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(await screen.findByRole("button", { name: "Next scene" }));
  expect(await screen.findByTestId("scene-chooser")).toBeInTheDocument();
});
