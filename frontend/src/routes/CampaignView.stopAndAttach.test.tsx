import { screen, fireEvent, waitFor, act } from "@testing-library/react";
import { api } from "../api/client";
import { installCampaignMocks, ONE_SCENE, renderCampaign } from "../testkit/campaignHarness";
import { fixture } from "../testkit/fixture";
import { PROPOSAL_PAYLOAD } from "../testkit/campaignFixtures";
import { hangingChat, hangingStream } from "../testkit/campaignStreams";

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



test("a turn in flight offers Stop beside the disabled composer action", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.chat).mockImplementation(hangingChat(["The tide "]));
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  const stop = await screen.findByRole("button", { name: /stop ■/i });
  expect(screen.queryByRole("button", { name: /send ▸/i })).toBeNull();
  expect(document.querySelector(".run:last-child .role-chip")).toHaveTextContent("narrator");
  fireEvent.click(stop);
  // back to a composer that can send again, with no error banner: the player
  // asked for this, and the partial the backend kept arrives with the re-fetch
  await waitFor(() => expect(screen.getByRole("button", { name: /continue ▶/i })).toBeEnabled());
  expect(screen.queryByText(/aborted/i)).toBeNull();
  expect(api.getScene).toHaveBeenCalled();
});

test("Stop tells the run to stop, not just this socket", async () => {
  // The single most important consequence of detaching a turn from its request:
  // closing the connection is no longer the cancel. Aborting the fetch now only
  // drops this subscriber -- the provider keeps generating, keeps spending, and
  // persists a reply the player explicitly stopped, while holding the scene
  // against the next send. The run has to be told.
  //
  // The run id comes from the leading `run` frame, which is why the mock emits
  // one before its delta: a Stop pressed before that frame arrives has nothing
  // to address, and this test would pass vacuously if it asserted only that
  // `cancelRun` was called with *something*.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
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
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));

  await waitFor(() => expect(screen.getByRole("button", { name: /continue ▶/i })).toBeEnabled());
  expect(api.cancelRun).toHaveBeenCalledWith("run", "s1", "run-7");
});

test("Stop before the run frame arrives cancels nothing rather than the wrong run", async () => {
  // The handle is cleared when a stream ends, beside `abortRef`. Left set, a
  // Stop pressed while the NEXT turn is still connecting would address the
  // PREVIOUS turn'''s run -- terminal by then, so the route answers politely, the
  // press is swallowed, and the live turn keeps generating with nothing to stop
  // it. That is a worse failure than doing nothing, because it looks like it
  // worked.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.chat).mockImplementationOnce(async (
    _c, _s, _t, onEvent, _r, signal) => {
    onEvent({ run: { id: "run-first", attempt_id: "a1", state: "running", next_index: 1 } });
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
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  await waitFor(() => expect(screen.getByRole("button", { name: /continue ▶/i })).toBeEnabled());
  vi.mocked(api.cancelRun).mockClear();

  // A second turn whose leading frame never arrives.
  vi.mocked(api.chat).mockImplementation(hangingChat());
  fireEvent.change(await screen.findByRole("textbox"), { target: { value: "again?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  await waitFor(() => expect(screen.getByRole("button", { name: /continue ▶/i })).toBeEnabled());

  expect(api.cancelRun).not.toHaveBeenCalled();
});

test("Stop cancels a turn whose leading frame never arrived", async () => {
  // The window this exists for: the server accepted and detached the turn, and
  // the response died before its first frame reached the browser. With nothing
  // but the leading frame to go on, Stop had no target -- it closed the
  // subscriber and the provider ran on, spending and eventually persisting a
  // reply the player had stopped.
  //
  // The attempt id is minted client-side BEFORE the request, so the turn is
  // addressable from the first byte; `findRun` trades it for the run id.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.chat).mockImplementation(hangingChat());   // never emits a run frame
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  await waitFor(() => expect(screen.getByRole("button", { name: /continue ▶/i })).toBeEnabled());

  // Cancelled by ATTEMPT, not by looking the run up first. A lookup is a
  // one-shot question and the POST may still be in the server's setup, so it
  // can answer "no such run" and then reserve one; the attempt route is
  // remembered and consumed by that reservation instead.
  const [, sid, attempt] = vi.mocked(api.cancelAttempt).mock.calls[0];
  expect(sid).toBe("s1");
  // and the attempt it cancelled is the one the send actually carried
  expect(vi.mocked(api.chat).mock.calls[0][6]).toBe(attempt);
});

/** Each turn producer, how to reach it from a fresh render, and where its
 *  attempt sits in the call. */
const PRODUCERS: { api: "retry" | "regenerate" | "resolveProposal";
                   arrange: () => void; reach: () => Promise<void>;
                   attemptAt: number }[] = [
  {
    api: "retry",
    // Retry is only offered after a turn failed, so one has to fail first.
    arrange: () => {
      vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [] }));
      vi.mocked(api.chat).mockImplementation(
        async (_c, _s, _t, onEvent) => {
          onEvent(fixture({ error: { detail: "boom" } }));
        });
    },
    reach: async () => {
      fireEvent.change(await screen.findByRole("textbox"), { target: { value: "hello" } });
      fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
      fireEvent.click(await screen.findByRole("button", { name: /^retry$/i }));
    },
    attemptAt: 5,
  },
  {
    api: "regenerate",
    arrange: () => {
      vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
        { role: "user", content: "hello" },
        { role: "assistant", content: "The tide turns." }] }));
    },
    reach: async () => {
      fireEvent.click(await screen.findByRole("button", { name: /^reroll$/i }));
      fireEvent.click(screen.getByRole("button", { name: /reroll ▸/i }));
    },
    // One lower than it was: reroll's four optional body fields became one
    // object argument (#77), which is what stops the position moving again
    // the next time a field is added.
    attemptAt: 5,
  },
  {
    api: "resolveProposal",
    // A declined record whose narration never landed: one button, no fence.
    arrange: () => {
      vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
        { role: "assistant", content: "a reply" }] }));
      vi.mocked(api.getRollProposal).mockResolvedValue({
        record: { id: "pr-1", status: "declined", payload: PROPOSAL_PAYLOAD,
                  resolution: null } });
    },
    reach: async () => {
      fireEvent.click(await screen.findByRole("button", { name: "Continue narration" }));
    },
    attemptAt: 5,
  },
];

test.each(PRODUCERS)("$api carries the attempt Stop will cancel",
                     async ({ api: name, arrange, reach, attemptAt }) => {
  // The defect: only `api.chat` forwarded the attempt. Retry, reroll and the
  // proposal continuation dropped it, so the SERVER minted its own -- and the
  // id this client recorded named a run that never existed. Stop then addressed
  // nothing and the adoption pass asked about an attempt no route had heard of,
  // while the detached run went on generating. Exactly the window the registry
  // was built to close, left open on four routes out of five.
  //
  // Asserted through Stop rather than by reading the call: what matters is not
  // that *an* attempt was passed, but that it is the one the cancel names.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  arrange();
  vi.mocked(api[name]).mockImplementation(hangingStream());
  renderCampaign();
  await reach();
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  await waitFor(() => expect(api.cancelAttempt).toHaveBeenCalled());

  const sent = vi.mocked(api[name]).mock.calls[0][attemptAt];
  expect(sent).toEqual(expect.any(String));
  expect(vi.mocked(api.cancelAttempt).mock.calls[0][2]).toBe(sent);
});

test("a dead response on a scene nobody left re-attaches instead of giving up", async () => {
  // Mount and `visibilitychange` are the only adoption triggers, and NEITHER
  // fires here: the component stays mounted and visible while the response
  // resets before its leading frame. The server accepted the POST and a run is
  // generating, so both of the obvious answers are wrong -- treating it as a
  // failure hands the prompt back and the player sends again, landing the turn
  // twice against a run that was always fine; leaving it locked strands the
  // scene until a remount.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.chat).mockImplementation(async () => {
    // Not an abort: the socket died on its own, which is the case with no
    // trigger. An abort is a Stop, and that path is settled elsewhere.
    throw Object.assign(new Error("network error"), { beforeResponse: true });
  });
  // Null for the MOUNT pass, which now asks the scene for its newest run even
  // with nothing pending (a reload leaves the provider empty). Answering
  // "running" there would attach before this test has sent anything, and the
  // reattach it means to observe would be a different one.
  vi.mocked(api.findRun)
    .mockResolvedValueOnce({ run: null })
    .mockResolvedValue(
      { run: { id: "r-live", attempt_id: "a", state: "running", next_index: 0 } });
  // Emits and then stays open, so the streamed preview is still on screen to
  // assert on: `attachToRun` clears it and refetches the scene once the run is
  // done, and the scene mock has no such post.
  vi.mocked(api.attachRun).mockImplementation(
    async (_c, _s, _r, _from, onEvent) => {
      onEvent({ delta: "The lamps are already lit." });
      await new Promise(() => {});
    });
  renderCampaign();
  fireEvent.change(await screen.findByRole("textbox"), { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));

  // The text generated while the response was dead -- the only thing that
  // proves a reattach happened. Asserting on the submitted post would prove
  // nothing: the scene refetch carries that back either way.
  expect(await screen.findByText(/The lamps are already lit\./)).toBeInTheDocument();
  // and asked by ATTEMPT, which is what names the send in the window where no
  // frame ever arrived. Not `calls[0]`: the mount pass asks the same route
  // with no attempt at all, precisely because it has none to ask about.
  const asked = vi.mocked(api.findRun).mock.calls.map((c: unknown[]) => c[2]);
  expect(asked).toContain(vi.mocked(api.chat).mock.calls[0][6]);
  // and the prompt was NOT handed back, which would have been a second send
  expect(screen.getByRole("textbox")).toHaveValue("");
});

test("a send whose socket AND lookup both died stays pending", async () => {
  // These are the same outage: the response reset before its leading frame,
  // and the discovery request that would say whether a run exists cannot get
  // out either. Reading that failure as "no run" runs the whole teardown --
  // unlocking the composer and letting the next Send overwrite the registry's
  // one pending entry for this scene -- while the original turn may be
  // generating perfectly well on the server.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.chat).mockImplementation(async () => {
    throw Object.assign(new Error("network error"), { beforeResponse: true });
  });
  vi.mocked(api.findRun).mockRejectedValue(new Error("offline"));

  renderCampaign();
  fireEvent.change(await screen.findByRole("textbox"), { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await waitFor(() => expect(api.findRun).toHaveBeenCalled());

  // Still Stop, because nothing has established that the turn is over -- and
  // the words were NOT handed back, which would invite a second send of a post
  // that may well be in the transcript.
  expect(screen.getByRole("button", { name: /stop ■/i })).toBeInTheDocument();
  expect(screen.getByRole("textbox")).toHaveValue("");
});

test("a reattach that could not connect keeps the scene locked", async () => {
  // A reattach failing is the same outage as the socket dying, so clearing
  // `busy` here put Send back over a run that still owns the scene -- and the
  // next send's `registry.begin()` overwrites this scene's one pending entry,
  // so a rollback by the original run takes the held words with it.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.chat).mockImplementation(async () => {
    throw Object.assign(new Error("network error"), { beforeResponse: true });
  });
  vi.mocked(api.findRun)
    .mockResolvedValueOnce({ run: null })          // the mount pass
    .mockResolvedValue(
      { run: { id: "r-live", attempt_id: "a", state: "running", next_index: 0 } });
  vi.mocked(api.attachRun).mockRejectedValue(new Error("offline"));

  renderCampaign();
  fireEvent.change(await screen.findByRole("textbox"), { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await waitFor(() => expect(api.attachRun).toHaveBeenCalled());

  // Nothing established what became of that run, so nothing acts as if it had.
  expect(screen.getByRole("button", { name: /stop ■/i })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /(send ▸|continue ▶)/i })).toBeDisabled();
});

test("and a later pass that finds nothing running is what releases it", async () => {
  // The counterweight. A view locked by a failed reattach with no pending
  // attempt has no other pass that would ever let go, so the attemptless
  // lookup answering "nothing is running here" has to be the release.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.findRun).mockResolvedValue(
    { run: { id: "r-live", attempt_id: "x", state: "running", next_index: 0 } });
  vi.mocked(api.attachRun).mockRejectedValue(new Error("offline"));

  renderCampaign();
  await waitFor(() => expect(api.attachRun).toHaveBeenCalled());
  await screen.findByRole("button", { name: /stop ■/i });

  // The run ended while we could not reach it.
  vi.mocked(api.findRun).mockResolvedValue(
    { run: { id: "r-live", attempt_id: "x", state: "landed", next_index: 4 } });
  act(() => { document.dispatchEvent(new Event("visibilitychange")); });

  await waitFor(() => expect(screen.getByRole("button", { name: /(send ▸|continue ▶)/i })).toBeEnabled());
});

test("Stop on an adopted run keeps polling by run id, not by an empty attempt", async () => {
  // An adopted run is recorded with `attempt: ""` -- the provider was recreated
  // empty, so this client never knew the id the backend reserved under.
  // Switching to `cancelAttempt(..., "")` after the first round matched nothing,
  // so every poll answered null and a cancellation that was completing normally
  // was reported unconfirmed, leaving the composer locked.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.findRun).mockResolvedValue(
    { run: { id: "r-live", attempt_id: "x", state: "running", next_index: 0 } });
  // Honours the signal, which is the point: `attachRun` is a real fetch in
  // production, so a Stop aborts it and `attachToRun`'s catch is what has to
  // wait for the cancel. A mock that ignored the signal would hang forever and
  // the branch under test would never run.
  vi.mocked(api.attachRun).mockImplementation(
    async (_c, _s, _r, _from, onEvent,
           signal) => {
      onEvent({ delta: "The tide " });
      await new Promise<void>((_resolve, reject) => {
        signal?.addEventListener("abort", () => {
          const err = new Error("The operation was aborted.");
          err.name = "AbortError";
          reject(err);
        });
      });
    });
  vi.mocked(api.cancelRun)
    .mockResolvedValueOnce({ run: { id: "r-live", attempt_id: "x", state: "running", next_index: 0 } })
    .mockResolvedValue({ run: { id: "r-live", attempt_id: "x", state: "cancelled", next_index: 0 } });

  renderCampaign();
  await screen.findByText(/The tide/);
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));

  await waitFor(() => expect(screen.getByRole("button", { name: /(send ▸|continue ▶)/i })).toBeEnabled());
  // both rounds by id; the empty attempt was never asked about
  expect(vi.mocked(api.cancelRun).mock.calls.length).toBeGreaterThan(1);
  expect(api.cancelAttempt).not.toHaveBeenCalled();
});

test("a run that failed while the tab was hidden says why", async () => {
  // Discovery returns the run already terminal, so there is nothing to attach
  // to -- and the rest of recovery only asks whether the post was retained,
  // refetches and unlocks. The backend's payload carries the structured
  // failure, and it went unread: the player came back to a scene with no
  // reply, no banner, and nothing to act on.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.chat).mockImplementation(hangingChat());
  vi.mocked(api.cancelAttempt).mockRejectedValue(new Error("offline"));
  renderCampaign();
  fireEvent.change(await screen.findByRole("textbox"), { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  await screen.findByText(/may still be generating/i);

  vi.mocked(api.findRun).mockResolvedValue({
    run: { id: "r", attempt_id: "a", state: "failed", next_index: 2,
           error: { detail: "the provider refused the request", kind: "upstream" } } });
  vi.mocked(api.attemptState).mockResolvedValue(fixture({ retained: true }));
  act(() => { document.dispatchEvent(new Event("visibilitychange")); });

  expect(await screen.findByText(/the provider refused the request/i)).toBeInTheDocument();
});

test("a prompt handed back by recovery survives a reload", async () => {
  // `settle` deleted the durable entry and moved the only copy of the player's
  // words into component state -- which is precisely what an Android renderer
  // restart destroys, the event the whole record exists to survive.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.chat).mockImplementation(hangingChat());
  vi.mocked(api.cancelAttempt).mockRejectedValue(new Error("offline"));
  const first = renderCampaign();
  fireEvent.change(await screen.findByRole("textbox"), { target: { value: "Mara waits." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  await screen.findByText(/may still be generating/i);

  // The run ended and its post was rolled back: the words come back.
  vi.mocked(api.findRun).mockResolvedValue({ run: null });
  vi.mocked(api.cancelAttempt).mockResolvedValue({ run: null });
  vi.mocked(api.attemptState).mockResolvedValue(fixture({ retained: false }));
  act(() => { document.dispatchEvent(new Event("visibilitychange")); });
  await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("Mara waits."));

  // Now the renderer restarts. Everything in component state is gone.
  first.unmount();
  renderCampaign();

  await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("Mara waits."));
});

test("an adopted run that fails and rolls back hands the words back", async () => {
  // An adopted run fails the same way a watched one does, and rolls the post
  // back the same way -- `post_returned` says so. At that moment the registry
  // holds the ONLY copy of those words: the transcript no longer has them, and
  // the component that typed them may be long gone. Settling without reading it
  // first destroyed the prompt permanently.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.chat).mockImplementation(async () => {
    throw Object.assign(new Error("network error"), { beforeResponse: true });
  });
  vi.mocked(api.findRun)
    .mockResolvedValueOnce({ run: null })          // the mount pass
    .mockResolvedValue(
      { run: { id: "r-live", attempt_id: "a", state: "running", next_index: 0 } });
  vi.mocked(api.attachRun).mockImplementation(
    async (_c, _s, _r, _from, onEvent) => {
      onEvent(fixture({ error: { detail: "the provider gave up", post_returned: true } }));
    });

  renderCampaign();
  fireEvent.change(await screen.findByRole("textbox"), { target: { value: "Mara waits." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));

  await waitFor(() => expect(api.attachRun).toHaveBeenCalled());
  await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("Mara waits."));
});

// Attachment keeps the token/attempt; terminal recovery retires the attempt;
// a following send advances the token and owns a different attempt. A delayed
// discovery must respect these owners before attaching or releasing.
test.each(["observing", "recovered", "newer discovery"] as const)(
  "delayed discovery yields to visibility recovery: %s", async (owner) => {
    vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
    vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, total: 0, messages: [] }));
    vi.mocked(api.chat).mockRejectedValue(
      Object.assign(new Error("network error"), { beforeResponse: true }));
    type Discovery = Awaited<ReturnType<typeof api.findRun>>;
    let releaseOld!: (answer: Discovery) => void;
    let releaseNew!: (answer: Discovery) => void;
    vi.mocked(api.findRun)
      .mockResolvedValueOnce({ run: null }) // mount, before any send
      .mockImplementationOnce(() => new Promise((resolve) => { releaseOld = resolve; }))
      .mockResolvedValueOnce({ run: {
        id: "r-recovered", attempt_id: "a", state: owner === "observing" ? "running" : "failed",
        next_index: 0,
      } })
      .mockImplementationOnce(() => new Promise((resolve) => { releaseNew = resolve; }));
    vi.mocked(api.attemptState).mockResolvedValue({ attempt: "a", retained: false, run: null });
    vi.mocked(api.attachRun).mockImplementation(async (_c, _s, _r, _from, onEvent) => {
      onEvent({ delta: "The lamps are already lit." });
      await new Promise(() => {});
    });
    renderCampaign();
    fireEvent.change(await screen.findByRole("textbox"), { target: { value: "and then?" } });
    fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
    await waitFor(() => expect(releaseOld).toBeDefined());
    act(() => { document.dispatchEvent(new Event("visibilitychange")); });
    if (owner === "observing") {
      await screen.findByText("The lamps are already lit.");
    } else {
      await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("and then?"));
      if (owner === "newer discovery") {
        fireEvent.change(screen.getByRole("textbox"), { target: { value: "I draw my blade." } });
        fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
        await waitFor(() => expect(releaseNew).toBeDefined());
      }
    }

    await act(async () => { releaseOld({ run: {
      id: "r-stale", attempt_id: "a", state: "running", next_index: 0,
    } }); });
    if (owner === "recovered") {
      expect(screen.getByRole("textbox")).toHaveValue("and then?");
      expect(screen.getByRole("button", { name: /send ▸/i })).toBeEnabled();
      expect(api.attachRun).not.toHaveBeenCalled();
    } else {
      expect(screen.getByRole("button", { name: /stop ■/i })).toBeInTheDocument();
      if (owner === "newer discovery") {
        expect(api.attachRun).not.toHaveBeenCalled();
        await act(async () => { releaseNew({ run: {
          id: "r-new", attempt_id: "b", state: "running", next_index: 0,
        } }); });
      }
      expect(await screen.findByText("The lamps are already lit.")).toBeInTheDocument();
      // Only the current owner's stream is observed, never the stale answer.
      expect(api.attachRun).toHaveBeenCalledTimes(1);
      expect(vi.mocked(api.attachRun).mock.calls[0][2]).toBe(owner === "observing" ? "r-recovered" : "r-new");
      expect(screen.getByRole("textbox")).toHaveValue("");
    }
  });

test("terminal discovery cannot release a visibility attachment that is resuming", async () => {
  // Responses can describe different moments: the socket's discovery learns
  // the turn ended while visibility's earlier snapshot still says running.
  // Both become runnable together, with terminal discovery first. Its ownership
  // decision and initial release must be atomic, so the buffered attachment
  // installed immediately afterward keeps its handle, preview and busy state.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, total: 0, messages: [] }));
  vi.mocked(api.chat).mockRejectedValue(
    Object.assign(new Error("network error"), { beforeResponse: true }));
  type Discovery = Awaited<ReturnType<typeof api.findRun>>;
  let releaseTerminal!: (answer: Discovery) => void;
  let releaseVisibility!: (answer: Discovery) => void;
  vi.mocked(api.findRun)
    .mockResolvedValueOnce({ run: null })
    .mockImplementationOnce(() => new Promise((resolve) => { releaseTerminal = resolve; }))
    .mockImplementationOnce(() => new Promise((resolve) => { releaseVisibility = resolve; }));
  vi.mocked(api.attachRun).mockImplementation(async (...args) => {
    args[4]({ delta: "The lamps are already lit." });
    await hangingStream()(...args);
  });
  renderCampaign();
  fireEvent.change(await screen.findByRole("textbox"), { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await waitFor(() => expect(releaseTerminal).toBeDefined());
  act(() => { document.dispatchEvent(new Event("visibilitychange")); });
  await waitFor(() => expect(releaseVisibility).toBeDefined());

  await act(async () => {
    releaseTerminal({ run: { id: "r-live", attempt_id: "a", state: "landed", next_index: 4 } });
    releaseVisibility({ run: { id: "r-live", attempt_id: "a", state: "running", next_index: 0 } });
  });
  expect(screen.getByText("The lamps are already lit.")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /stop ■/i }));
  await waitFor(() => expect(api.cancelRun).toHaveBeenCalledWith("run", "s1", "r-live"));
});
