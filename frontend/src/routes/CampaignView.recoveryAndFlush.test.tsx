import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { MemoryRouter, Link } from "react-router-dom";
import { api } from "../api/client";
import type { Message } from "../api/client";
import { here, Here, installCampaignMocks, ONE_SCENE, playRoutes, renderCampaign, withPalette } from "../testkit/campaignHarness";
import { fixture } from "../testkit/fixture";
import { hangingChat } from "../testkit/campaignStreams";
import { PROPOSAL_PAYLOAD } from "../testkit/campaignFixtures";

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



test("a reload with no local attempt still finds the run generating on this scene",
     async () => {
  // The Android case this whole feature exists for, one layer up: the WebView's
  // renderer can be restarted out from under a perfectly healthy backend turn,
  // and a full reload recreates the provider EMPTY. With no pending attempt to
  // ask about, adoption used to return -- leaving an unlocked composer over a
  // stale transcript, and the next send refused with `run_in_flight` by a run
  // this view could not see.
  //
  // The attemptless lookup is a different question: "what is the newest run on
  // this scene?" -- which is the only one a client with no local state can ask.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.findRun).mockResolvedValue(
    { run: { id: "r-live", attempt_id: "someone-elses", state: "running", next_index: 0 } });
  vi.mocked(api.attachRun).mockImplementation(
    async (_c, _s, _r, _from, onEvent) => {
      onEvent({ delta: "The lamps are already lit." });
      await new Promise(() => {});
    });

  renderCampaign();

  expect(await screen.findByText(/The lamps are already lit\./)).toBeInTheDocument();
  // asked WITHOUT an attempt, because there is none to ask about
  expect(vi.mocked(api.findRun).mock.calls[0][2]).toBeUndefined();
  // and the composer says what is true: a turn is running here
  expect(screen.getByRole("button", { name: /stop ■/i })).toBeInTheDocument();
});

test("a finished run found by that lookup is left alone", async () => {
  // The counterweight. A terminal run belongs to a turn whose words some other
  // client holds; there is nothing here to restore and nothing to settle, so
  // attaching would lock this composer over a scene that is perfectly free.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.findRun).mockResolvedValue(
    { run: { id: "r-done", attempt_id: "someone-elses", state: "landed", next_index: 4 } });

  renderCampaign();

  await waitFor(() => expect(screen.getByRole("button", { name: /(send ▸|continue ▶)/i })).toBeEnabled());
  expect(api.attachRun).not.toHaveBeenCalled();
});

test("a Stop the server has not reserved yet is not a stopped turn", async () => {
  // A precancel recorded before the POST reserved answers `run: null`. That is
  // not "there is nothing to stop" -- it is "cancellation is pending": the
  // original request is still in its synchronous setup and will consume that
  // record when it reserves. Resolving on it settled the attempt and unlocked
  // the composer while that was still to happen, so the next Send raced the
  // very request it had just cancelled.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.chat).mockImplementation(hangingChat());
  vi.mocked(api.cancelAttempt).mockResolvedValue({ run: null });
  renderCampaign();
  fireEvent.change(await screen.findByRole("textbox"), { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));

  // Unconfirmed, so it reports that and keeps the button rather than handing
  // Send back over a request that is about to reserve.
  expect(await screen.findByText(/may still be generating/i)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /stop ■/i })).toBeInTheDocument();
  // and it kept asking rather than concluding from the first null
  expect(vi.mocked(api.cancelAttempt).mock.calls.length).toBeGreaterThan(1);
});

test("a Stop confirmed once the request reserves does settle", async () => {
  // The counterweight to the test above: the pending answer resolves as soon
  // as the original request reserves and consumes the precancel. Treating
  // every null as permanent would leave the composer locked on the ordinary
  // Stop-before-first-frame, which is the commonest Stop there is.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.chat).mockImplementation(hangingChat());
  vi.mocked(api.cancelAttempt)
    .mockResolvedValueOnce({ run: null })
    .mockResolvedValue({ run: { id: "r", attempt_id: "a", state: "cancelled", next_index: 0 } });
  renderCampaign();
  fireEvent.change(await screen.findByRole("textbox"), { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));

  await waitFor(() => expect(screen.getByRole("button", { name: /(send ▸|continue ▶)/i })).toBeEnabled());
  expect(screen.queryByText(/may still be generating/i)).toBeNull();
});

test("a recovery pass that could not reach the server keeps the send pending", async () => {
  // `visibilitychange` fires the instant the tab comes forward, which is
  // routinely BEFORE connectivity is actually back -- so of everything in this
  // file, these two requests are the likeliest to fail. Reading a failure as
  // "no run" and "not retained" discarded the only handle to a chat that may
  // still be generating, and handed back a prompt that is sitting in the
  // transcript, inviting the player to send it twice.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.chat).mockImplementation(hangingChat());
  // The Stop could not be confirmed, so the send is deliberately left pending
  // and the composer stays locked -- which is the state recovery exists to
  // resolve, and the only one where these two requests are ever made.
  vi.mocked(api.cancelAttempt).mockRejectedValue(new Error("offline"));
  renderCampaign();
  fireEvent.change(await screen.findByRole("textbox"), { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  await screen.findByText(/may still be generating/i);

  // Still offline when the tab comes back.
  vi.mocked(api.findRun).mockRejectedValue(new Error("offline"));
  act(() => { document.dispatchEvent(new Event("visibilitychange")); });
  await waitFor(() => expect(api.findRun).toHaveBeenCalled());
  // It did not conclude anything: no durable question was asked, and no prompt
  // was handed back over a post that may well be in the transcript.
  expect(api.attemptState).not.toHaveBeenCalled();
  expect(screen.getByRole("textbox")).toHaveValue("");

  // Connectivity returns, and the pass that can answer does.
  vi.mocked(api.findRun).mockResolvedValue({ run: null });
  vi.mocked(api.cancelAttempt).mockResolvedValue({ run: null });
  vi.mocked(api.attemptState).mockResolvedValue(fixture({ retained: false }));
  act(() => { document.dispatchEvent(new Event("visibilitychange")); });

  // The send was still pending, so the words came back.
  await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("and then?"));
});

test("Stop reaches the run's own campaign after the player has moved to another",
     async () => {
  // React Router reuses this component across `/campaigns/A` -> `/campaigns/B`,
  // so `cid` is whichever campaign is on screen when Stop is pressed. Scene ids
  // are campaign-local, so cancelling A's turn through B's id reaches nothing
  // and the turn goes on generating and persisting. The handle has to carry the
  // campaign the turn started in.
  //
  // This one compiles either way -- `cid` is in scope at the call site -- which
  // is why it needs a test rather than a type.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.chat).mockImplementation(hangingChat());
  render(
    <MemoryRouter initialEntries={["/campaigns/run/scenes/s1"]}>
      {withPalette(<>
        <Here />
        <Link to="/campaigns/elsewhere/scenes/whichever">elsewhere</Link>
        {playRoutes()}
      </>)}
    </MemoryRouter>,
  );
  fireEvent.change(await screen.findByRole("textbox"), { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByRole("button", { name: /stop ■/i });

  fireEvent.click(screen.getByRole("link", { name: /elsewhere/i }));
  // The other campaign resolves to its own newest scene, which is what landing
  // on `/scenes` does. What this test needs is only that it is no longer `run`.
  await waitFor(() => expect(here()).toMatch(/^\/campaigns\/elsewhere/));
  fireEvent.click(screen.getByRole("button", { name: /stop ■/i }));

  await waitFor(() => expect(api.cancelAttempt).toHaveBeenCalled());
  expect(vi.mocked(api.cancelAttempt).mock.calls[0][0]).toBe("run");
});

test("a Stop that cannot reach the server does not settle the turn", async () => {
  // Aborting the subscriber does not stop a detached run. So a cancel that
  // never arrived -- connectivity dropped as the phone was backgrounded -- must
  // not read as a finished one: settling would put Send back over a run that is
  // still generating, still spending, and still holding the scene, and the next
  // send would be refused. The player pressed a button and it did not work;
  // they have to be told.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.chat).mockImplementation(hangingChat());
  vi.mocked(api.cancelAttempt).mockRejectedValue(new Error("offline"));
  renderCampaign();
  fireEvent.change(await screen.findByRole("textbox"), { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));

  expect(await screen.findByText(/may still be generating/i)).toBeInTheDocument();
});

test("Stop that runs out of rounds leaves the turn unsettled", async () => {
  // A provider still stuck after the last round is not a stopped one. Resolving
  // there settles the turn on a guess: the backend still holds the scene, Send
  // comes back, and the next turn is refused with `run_in_flight`. Exhaustion
  // is an unconfirmed cancellation and takes the same path as one that never
  // reached the server at all.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.chat).mockImplementation(hangingChat());
  vi.mocked(api.cancelAttempt).mockResolvedValue(
    { run: { id: "r", attempt_id: "a", state: "running", next_index: 0 } });
  renderCampaign();
  fireEvent.change(await screen.findByRole("textbox"), { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));

  expect(await screen.findByText(/may still be generating/i)).toBeInTheDocument();
});

test("an unstopped turn keeps Stop, so the player can try again", async () => {
  // The failure this prevents: the cancel never reached the server, the banner
  // said so, and the composer went back to Send anyway. The backend run may
  // still hold the scene, so the next send is refused with `run_in_flight` --
  // and Stop is gone, so there is no way to try the cancel again. The turn is
  // not settled, so it stays as it was.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.chat).mockImplementation(hangingChat());
  vi.mocked(api.cancelAttempt).mockRejectedValue(new Error("offline"));
  renderCampaign();
  fireEvent.change(await screen.findByRole("textbox"), { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));

  expect(await screen.findByText(/may still be generating/i)).toBeInTheDocument();
  // Stop stays available and Send stays disabled while the run may be alive.
  expect(screen.getByRole("button", { name: /stop ■/i })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /(send ▸|continue ▶)/i })).toBeDisabled();
});

test("and the recovery pass is what eventually lets go", async () => {
  // The counterweight to the test above. A composer locked forever would be
  // worse than one unlocked early -- so the adoption pass, which runs when the
  // tab comes back, is what resolves the send and releases the scene.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.chat).mockImplementation(hangingChat());
  vi.mocked(api.cancelAttempt).mockRejectedValue(new Error("offline"));
  renderCampaign();
  fireEvent.change(await screen.findByRole("textbox"), { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  await screen.findByText(/may still be generating/i);

  // The run ended on its own while we could not reach it.
  vi.mocked(api.findRun).mockResolvedValue({
    run: { id: "r", attempt_id: "a", state: "landed", next_index: 2 } });
  act(() => { document.dispatchEvent(new Event("visibilitychange")); });

  await waitFor(() => expect(screen.getByRole("button", { name: /(send ▸|continue ▶)/i })).toBeEnabled());
});

test("Stop keeps asking while the server says the run is still unwinding", async () => {
  // The cancel route waits for the run, but that wait is bounded (30s) and can
  // answer while it is still `running`. Reading that as "it is over" put Send
  // back over a run that still held the scene -- the exact failure the wait was
  // added to prevent, arrived at by trusting its first answer.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.chat).mockImplementation(hangingChat());
  vi.mocked(api.cancelAttempt)
    .mockResolvedValueOnce({ run: { id: "r", attempt_id: "a", state: "running", next_index: 0 } })
    .mockResolvedValueOnce({ run: { id: "r", attempt_id: "a", state: "running", next_index: 0 } })
    .mockResolvedValue({ run: { id: "r", attempt_id: "a", state: "cancelled", next_index: 0 } });
  renderCampaign();
  fireEvent.change(await screen.findByRole("textbox"), { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  await waitFor(() => expect(screen.getByRole("button", { name: /continue ▶/i })).toBeEnabled());

  // three rounds: two that answered `running`, one that answered terminal
  expect(vi.mocked(api.cancelAttempt).mock.calls).toHaveLength(3);
});

test("Stop holds the scene until the run is really over", async () => {
  // The cancel route answers only once the run is terminal -- after its abort
  // hook has written the partial and freed the exclusion key. Releasing on the
  // flush poll's budget instead let a slow-unwinding provider keep the key
  // while Send came back, so the next send was refused with `run_in_flight`.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  let release: (() => void) | null = null;
  // Resolves with a real payload, not bare: `stopRun` reads the run's state to
  // decide whether the cancel was CONFIRMED, and an undefined body is -- quite
  // correctly -- treated as unconfirmed, which leaves the composer locked.
  vi.mocked(api.cancelRun).mockImplementation(
    () => new Promise((r) => {
      release = () => r({ run: { id: "r", attempt_id: "a", state: "cancelled",
                                 next_index: 0 } });
    }));
  vi.mocked(api.chat).mockImplementation(async (
    _c, _s, _t, onEvent, _r, signal) => {
    onEvent({ run: { id: "run-7", attempt_id: "a1", state: "running", next_index: 1 } });
    onEvent({ delta: "The tide " });
    await new Promise<void>((_resolve, reject) => {
      signal?.addEventListener("abort", () => {
        const err = new Error("The operation was aborted.");
        err.name = "AbortError";
        reject(err);
      });
    });
  });
  renderCampaign();
  fireEvent.change(await screen.findByRole("textbox"), { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));

  // The cancel has not answered yet, so the turn is still in flight as far as
  // the backend is concerned -- and the composer says so. `send` gates on
  // `busy` alone, so a Send button here IS a send the backend would refuse.
  await waitFor(() => expect(api.cancelRun).toHaveBeenCalled());
  expect(screen.getByRole("button", { name: /stop ■/i })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /(send ▸|continue ▶)/i })).toBeDisabled();

  release!();
  // and once the run really is over, the composer comes back
  await waitFor(() => expect(screen.getByRole("button", { name: /(send ▸|continue ▶)/i })).toBeEnabled());
  expect(vi.mocked(api.chat).mock.calls).toHaveLength(1);
});

test("a cancelled turn's partial appears even when the backend flush lands late", async () => {
  // The abort rejects the fetch as soon as the socket is torn down client-side;
  // the backend only then notices the disconnect and runs its shielded flush.
  // The refresh that follows Stop can therefore read a transcript the partial
  // has not reached yet, and without the poll the text sits on disk while the
  // screen denies it exists. Here the flush lands 100ms after the abort — after
  // the immediate refresh, before the first retry.
  //
  // The streamed text and the persisted message are deliberately different
  // strings. They are the same in life, but asserting on the streamed one here
  // proves nothing: the live preview renders it too, so the assertion would
  // pass against a node the refresh is about to tear down.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  let flushed = false;
  vi.mocked(api.getScene).mockImplementation(async () => fixture({
    meta: {},
    messages: flushed ? [{ role: "assistant", content: "the whole persisted partial" }] : [],
  }));
  vi.mocked(api.chat).mockImplementation(hangingChat(["a streamed fragment"]));
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  setTimeout(() => { flushed = true; }, 100);   // lands after the immediate refresh
  await waitFor(() =>
    expect(screen.getByText("the whole persisted partial")).toBeInTheDocument());
});

test("a cancel that streamed nothing still waits for the backend's flush", async () => {
  // What reached the client is not what the backend has to persist:
  // FenceWatcher emits nothing for a reply that opens with a roll fence, yet
  // the server still writes a proposal (and can write narration held back
  // behind a possible opener). Gating the poll on "did we see a delta" left
  // that invisible.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  let flushed = false;
  vi.mocked(api.getScene).mockImplementation(async () => fixture({
    meta: {},
    messages: flushed ? [{ role: "assistant", content: "held back all along" }] : [],
  }));
  vi.mocked(api.chat).mockImplementation(hangingChat());   // not one delta
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  setTimeout(() => { flushed = true; }, 100);
  await waitFor(() =>
    expect(screen.getByText("held back all along")).toBeInTheDocument());
});

test("the flush poll asks for the transcript's length, and refreshes the scene once it grows", async () => {
  // A tick used to be a whole scene refresh -- the cast, the roster, the
  // briefing, the column's reads -- so a Stop that waited out the poll cost a
  // dozen requests a tick to learn one number. The length is polled; the
  // refresh runs once, on the tick that sees it grow.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  let flushed = false;
  vi.mocked(api.getScene).mockImplementation(async () => fixture({
    meta: {}, total: flushed ? 1 : 0,
    messages: flushed ? [{ role: "assistant", content: "the flushed partial" }] : [],
  }));
  vi.mocked(api.chat).mockImplementation(hangingChat(["a fragment"]));
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  await waitFor(() => expect(screen.getByRole("button", { name: /continue ▶/i })).toBeEnabled());
  const castReads = vi.mocked(api.getCast).mock.calls.length;
  const lengthReads = () =>
    vi.mocked(api.getScene).mock.calls.filter((c) => c[2]?.limit === 1).length;

  await waitFor(() => expect(lengthReads()).toBeGreaterThanOrEqual(2), { timeout: 4000 });
  expect(vi.mocked(api.getCast).mock.calls.length).toBe(castReads);   // no refresh yet

  flushed = true;
  expect(await screen.findByText("the flushed partial", {}, { timeout: 4000 })).toBeInTheDocument();
  expect(vi.mocked(api.getCast).mock.calls.length).toBe(castReads + 1);
});

test("the flush poll re-reads a transcript that shrank under it", async () => {
  // Growth is what the poll waits for, but it is not the only thing the server
  // can do in the wait: a turn that fails after the connection has gone rolls
  // its post back. A tick used to be a whole refresh, so that reached the
  // screen as a side effect; polling the length alone must not lose it and
  // leave a post the transcript no longer has, still offering Edit and Cut
  // against its index.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  let rolledBack = false;
  const opening: Message = { role: "assistant", content: "the opening" };
  vi.mocked(api.getScene).mockImplementation(async () => fixture(rolledBack
    ? { meta: {}, total: 1, messages: [opening] }
    : { meta: {}, total: 2,
        messages: [opening, { role: "user", content: "a post the server took back" }] }));
  vi.mocked(api.chat).mockImplementation(hangingChat(["a fragment"]));
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  await waitFor(() => expect(screen.getByRole("button", { name: /continue ▶/i })).toBeEnabled());
  expect(screen.getByText("a post the server took back")).toBeInTheDocument();

  rolledBack = true;
  await waitFor(() => expect(screen.queryByText("a post the server took back")).toBeNull(),
                { timeout: 4000 });
  expect(screen.getByText("the opening")).toBeInTheDocument();
});

test("the flush poll retries a failed refresh after seeing growth", async () => {
  // Learning the length is not enough: the failed full read has not put the
  // persisted reply on screen. Ending the wait on that failure loses it.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  let flushed = false;
  let failedRefresh = false;
  vi.mocked(api.getScene).mockImplementation(async (_cid, _sid, options) => {
    if (flushed && options?.limit !== 1 && !failedRefresh) {
      failedRefresh = true;
      throw new Error("offline during refresh");
    }
    return fixture({ meta: {}, total: flushed ? 1 : 0,
      messages: flushed ? [{ role: "assistant", content: "the retried partial" }] : [] });
  });
  vi.mocked(api.chat).mockImplementation(hangingChat(["a fragment"]));
  renderCampaign();
  fireEvent.change(await screen.findByRole("textbox"), { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  await waitFor(() => expect(screen.getByRole("button", { name: /continue ▶/i })).toBeEnabled());

  flushed = true;
  expect(await screen.findByText("the retried partial", {}, { timeout: 4000 })).toBeInTheDocument();
});

test("a shrink refresh that discovers growth settles the proposal and ends polling", async () => {
  // The rollback is observed by the length read, but the flush lands before
  // the full read returns. That successful refresh must both install the reply
  // and settle the newer proposal, without spending the remaining poll budget.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  const opening: Message = { role: "assistant", content: "the opening" };
  const player: Message = { role: "user", content: "the earlier post" };
  let rolledBack = false;
  let sawGrowth = false;
  vi.mocked(api.getScene).mockImplementation(async (_cid, _sid, options) => {
    if (!rolledBack) return fixture({ meta: {}, total: 2, messages: [opening, player] });
    if (options?.limit === 1) return fixture({ meta: {}, total: 1, messages: [opening] });
    sawGrowth = true;
    return fixture({ meta: {}, total: 3, messages: [opening, player,
      { role: "assistant", content: "the partial after rollback" }] });
  });
  vi.mocked(api.getRollProposal).mockImplementation(async () => ({
    record: sawGrowth
      ? { id: "pr-1", status: "pending", payload: PROPOSAL_PAYLOAD, resolution: null }
      : null,
  }));
  vi.mocked(api.chat).mockImplementation(hangingChat(["a fragment"]));
  renderCampaign();
  fireEvent.change(await screen.findByRole("textbox"), { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  await waitFor(() => expect(screen.getByRole("button", { name: /continue ▶/i })).toBeEnabled());

  rolledBack = true;
  await screen.findByText("the partial after rollback");
  await screen.findByRole("button", { name: "Roll it" });
  const reads = vi.mocked(api.getScene).mock.calls.length;
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 800)); });
  expect(api.getScene).toHaveBeenCalledTimes(reads);
  expect(screen.getByRole("button", { name: "Roll it" })).toBeInTheDocument();
});
