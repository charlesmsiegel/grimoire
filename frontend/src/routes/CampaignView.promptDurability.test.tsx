import { StrictMode } from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { LOCKED_WHILE_GENERATING } from "../components/sceneLock";
import { installCampaignMocks, ONE_SCENE, openScene, playRoutes, renderCampaign, withPalette } from "../testkit/campaignHarness";
import { fixture } from "../testkit/fixture";
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



test("StrictMode's mount cycle does not switch the flush poll off", async () => {
  // main.tsx renders the app inside StrictMode, so in development React runs
  // setup / cleanup / setup on mount. A cleanup-only mounted flag is left false
  // by that middle step, and `owns()` reads it: every post-cancel poll bows out
  // before its first look and a late flush stays invisible. Same scenario as
  // the late-flush test above, rendered the way development renders it.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  let flushed = false;
  vi.mocked(api.getScene).mockImplementation(async () => fixture({
    meta: {},
    messages: flushed ? [{ role: "assistant", content: "the whole persisted partial" }] : [],
  }));
  vi.mocked(api.chat).mockImplementation(hangingChat(["a streamed fragment"]));
  render(
    <StrictMode>
      <MemoryRouter initialEntries={["/campaigns/run/scenes/s1"]}>
        {withPalette(<>
          {playRoutes()}
        </>)}
      </MemoryRouter>
    </StrictMode>,
  );
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  setTimeout(() => { flushed = true; }, 100);
  await waitFor(() =>
    expect(screen.getByText("the whole persisted partial")).toBeInTheDocument());
});

test("a send on a scene still loading is measured against that scene", async () => {
  // Send stays enabled while a freshly clicked scene loads, and the cached
  // length belongs to whichever scene was read last. Measuring the new scene's
  // growth against the old scene's length answers by which transcript happened
  // to be longer: here the post did land, but the scene left behind is longer
  // than the one it landed in, so the stale baseline reads that as "nothing
  // stored" and hands back a prompt the player would then send twice.
  vi.mocked(api.listScenes).mockResolvedValue([
    { id: "s1", title: "Old", model: "", created: "", updated: "", date: "" },
    { id: "s2", title: "Later", model: "", created: "", updated: "", date: "" },
  ]);
  let postLanded = false;
  let pagesOfS2 = 0;
  vi.mocked(api.getScene).mockImplementation(async (_c, sid, w) => {
    if (sid !== "s2") {
      return fixture({ meta: {}, total: 4, messages: [
        { role: "user", content: "a" }, { role: "assistant", content: "b" },
        { role: "user", content: "c" }, { role: "assistant", content: "d" }] });
    }
    // s2's first page never arrives, so its length is still unknown when the
    // turn starts — the window the stale baseline is reachable through.
    if (w?.limit !== 1 && pagesOfS2++ === 0) return new Promise(() => {});
    return fixture(postLanded
      ? { meta: {}, total: 1, messages: [{ role: "user", content: "I draw my blade." }] }
      : { meta: {}, total: 0, messages: [] });
  });
  vi.mocked(api.chat).mockImplementation(async () => {
    postLanded = true;          // post_chat appended, then the abort beat the headers
    const err: Error & { beforeResponse?: boolean } = new Error("The operation was aborted.");
    err.name = "AbortError";
    err.beforeResponse = true;
    throw err;
  });
  renderCampaign();
  await openScene(/Later/);
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByText("I draw my blade.");   // the refreshed transcript has it
  await new Promise((r) => setTimeout(r, 50));
  expect(screen.getByRole("textbox")).toHaveValue("");
});

test("a refresh that fails with the send still gives the prompt back", async () => {
  // The verification read fails for the same reason the send did — the server
  // is unreachable — so the one case that most needs the player's words back is
  // the case that cannot confirm anything. Throwing out of the refresh skipped
  // the restore entirely; now an unverifiable turn restores, because a visible
  // duplicate is recoverable and a destroyed prompt is not.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  let loaded = false;
  vi.mocked(api.getScene).mockImplementation(async () => {
    if (loaded) throw new Error("Failed to fetch");
    loaded = true;
    return fixture({ meta: {}, total: 0, messages: [] });
  });
  vi.mocked(api.chat).mockImplementation(async () => {
    const err: Error & { beforeResponse?: boolean } = new Error("Failed to fetch");
    err.beforeResponse = true;
    throw err;
  });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("I draw my blade."));
});

test("a rolled-back prompt comes back beside a draft typed since", async () => {
  // The composer stays editable while a turn runs, so the player can be typing
  // the next line when this one fails. `cur || content` dropped the failed
  // prompt in exactly that case — it is in no transcript and no composer, which
  // is the loss the restore exists to prevent. Both texts survive.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  let fail: (() => void) | null = null;
  vi.mocked(api.chat).mockImplementation(
    async (_c, _s, _t, onEvent) => {
      await new Promise<void>((r) => { fail = r; });
      onEvent({ error: { detail: "OpenRouter API key is not set", kind: "missing_key",
                         post_returned: true } });
    });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByRole("button", { name: /stop ■/i });
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Wait, I hesitate." } });
  fail!();
  await screen.findByText(/OpenRouter API key is not set/);
  await waitFor(() => expect(screen.getByRole("textbox"))
    .toHaveValue("I draw my blade.\n\nWait, I hesitate."));
});

test("a pre-response abort whose post landed still waits for the flush", async () => {
  // `beforeResponse` says no response came back, not that nothing was written:
  // the server can append the post, start generating, and have the abort beat
  // its headers home. Growth in the refresh proves that happened, so there is a
  // turn on the server and its abort write is still coming — skipping the poll
  // on `unreached` alone left that partial invisible.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  let landed = false;
  let flushed = false;
  vi.mocked(api.getScene).mockImplementation(async () => fixture({
    meta: {},
    total: flushed ? 2 : landed ? 1 : 0,
    messages: flushed
      ? [{ role: "user", content: "I draw my blade." },
         { role: "assistant", content: "the whole persisted partial" }]
      : landed ? [{ role: "user", content: "I draw my blade." }] : [],
  }));
  vi.mocked(api.chat).mockImplementation(async () => {
    landed = true;              // post_chat appended and began generating
    const err: Error & { beforeResponse?: boolean } = new Error("The operation was aborted.");
    err.name = "AbortError";
    err.beforeResponse = true;
    throw err;
  });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByText("I draw my blade.");
  setTimeout(() => { flushed = true; }, 100);   // the shielded write lands after
  await waitFor(() =>
    expect(screen.getByText("the whole persisted partial")).toBeInTheDocument());
});

test("a refused turn neither polls for a flush nor loses the prompt", async () => {
  // A non-2xx is the whole outcome: `streamPost` throws it before any body
  // exists, so no stream was cut short and no abort write is coming. The poll
  // ran anyway — twelve seconds of refreshes for a turn that never started.
  // And every 4xx `post_chat` raises comes from a check that runs before the
  // post is appended, so the prompt was cleared and stored nowhere.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, total: 0, messages: [] }));
  vi.mocked(api.chat).mockImplementation(async () => {
    throw new ApiError(409, "a turn is already running on this scene", "busy");
  });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByText(/a turn is already running/);
  await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("I draw my blade."));
  const readsAfterSettling = vi.mocked(api.getScene).mock.calls.length;
  await new Promise((r) => setTimeout(r, 400));   // past the first two poll ticks
  expect(vi.mocked(api.getScene).mock.calls.length).toBe(readsAfterSettling);
});

test("the scene being generated into cannot be renamed mid-turn", async () => {
  // A scene's id is its filename and renaming re-slugs it, so a rename mid-turn
  // moves the file out from under the stream: the abort write that saves the
  // partial fails with SceneNotFound and is swallowed during teardown.
  vi.mocked(api.listScenes).mockResolvedValue([
    { id: "s1", title: "Old", model: "", created: "", updated: "", date: "" },
    { id: "s2", title: "Later", model: "", created: "", updated: "", date: "" },
  ]);
  // The lock outlives the turn's `busy`, so the flush has to land for it to be
  // released — see the sibling test below.
  let flushed = false;
  vi.mocked(api.getScene).mockImplementation(async () => fixture({
    meta: {}, total: flushed ? 1 : 0,
    messages: flushed ? [{ role: "assistant", content: "The tide turns." }] : [],
  }));
  vi.mocked(api.chat).mockImplementation(hangingChat(["The tide "]));
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByRole("button", { name: /stop ■/i });
  // Rename and delete belong to the scene on screen — and the scene on screen
  // is the one being streamed into, so both are locked for the turn.
  expect(screen.getByRole("button", { name: /rename scene/i })).toBeDisabled();
  expect(screen.getByRole("button", { name: /delete scene/i })).toBeDisabled();
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  await waitFor(() => expect(screen.getByRole("button", { name: /continue ▶/i })).toBeEnabled());
  flushed = true;                            // the shielded write lands
  await waitFor(() =>
    expect(screen.getByRole("button", { name: /rename scene/i })).not.toBeDisabled());
});

test("the scene stays locked while the cancelled turn's flush is still coming", async () => {
  // `busy` clears as soon as the socket dies, but `on_abort` writes seconds
  // later — that gap is exactly what the flush poll waits out. Releasing the
  // lock with `busy` re-enabled rename and delete for the whole of it, so the
  // file could move out from under the very write being waited for.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, total: 0, messages: [] }));
  vi.mocked(api.chat).mockImplementation(hangingChat(["The tide "]));
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  // Send is back — the turn is over as far as the composer is concerned —
  // but the write it is waiting for has not landed, so the scene stays locked.
  await waitFor(() => expect(screen.getByRole("button", { name: /continue ▶/i })).toBeEnabled());
  await new Promise((r) => setTimeout(r, 400));   // past the first two poll ticks
  expect(screen.getByRole("button", { name: /rename/i })).toBeDisabled();
});

test("a manual roll cannot land in the window a cancelled reroll restores into", async () => {
  // The worst of the `busy`-instead-of-`streamingId` misses, because unlike
  // the others it destroys something outright. A reroll deletes the old reply
  // up front; cancelled before its first token, `on_abort` puts it back — but
  // `restore_trailing_assistant_run` steps over trailing *transitions* only
  // and refuses behind a manual roll, whose line must stay in lockstep with
  // rolls.json. So a roll in the flush window makes the restore refuse and the
  // reply is gone: nothing else holds it, and no backend hook can rescue it.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({
    meta: {}, total: 2,
    messages: [{ role: "user", content: "and then?" },
               { role: "assistant", content: "The tide turns." }],
  }));
  // `hangingStream`, not `hangingChat`: the chat-shaped one reads its signal
  // out of a fixed argument position, and reroll's body fields are one object
  // now (#77). `hangingStream` finds the signal by type, so it survives the
  // next signature change too.
  vi.mocked(api.regenerate).mockImplementation(hangingStream());   // no first token
  renderCampaign();
  await screen.findByText("The tide turns.");
  // The roll is available before the turn — this is the control being locked,
  // not one that happened to be disabled anyway.
  expect(screen.getByRole("button", { name: /roll dice/i })).not.toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: /reroll/i }));
  fireEvent.click(screen.getByRole("button", { name: /reroll ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  // Send is back, so `busy` has cleared — but the abort write that restores
  // the deleted reply has not landed yet, and a roll now would defeat it.
  await waitFor(() => expect(screen.getByRole("button", { name: /continue ▶/i })).toBeEnabled());
  await new Promise((r) => setTimeout(r, 400));   // past the first two poll ticks
  const roll = screen.getByRole("button", { name: /roll dice/i });
  expect(roll).toBeDisabled();
  expect(roll).toHaveAttribute("title", LOCKED_WHILE_GENERATING);
});

test("a lost error frame still gives the rolled-back prompt back", async () => {
  // The backend rolls the post back and *then* yields the error frame, so a
  // connection dropped in between leaves a rollback that happened and a client
  // never told. Nothing is set — not errored, not unreached, not refused — and
  // the poll cannot help, since it watches for growth and a rollback shrinks.
  // The refreshed transcript is the only witness left.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  let rolledBack = false;
  vi.mocked(api.getScene).mockImplementation(async () => fixture({
    meta: {}, total: rolledBack ? 0 : 0, messages: [],
  }));
  vi.mocked(api.chat).mockImplementation(async () => {
    rolledBack = true;   // headers arrived, post appended, then taken back off
    // resolves with no `done` and no error frame: the body just ended
  });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("I draw my blade."));
});

test("an interrupted stream whose post is still there keeps the composer clear", async () => {
  // The other side of the same gate. Headers arrived, so `post_chat` appended —
  // and the post is still in the transcript, which means the backend did NOT
  // roll it back and the reply may still be flushing. Restoring here would put
  // the text in the composer and the transcript at once.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  let posted = false;
  vi.mocked(api.getScene).mockImplementation(async () => fixture({
    meta: {}, total: posted ? 1 : 0,
    messages: posted ? [{ role: "user", content: "I draw my blade." }] : [],
  }));
  vi.mocked(api.chat).mockImplementation(async () => {
    posted = true;   // post_chat appended; the body then ends with no frame
  });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByText("I draw my blade.");
  await new Promise((r) => setTimeout(r, 50));
  expect(screen.getByRole("textbox")).toHaveValue("");
});

test("the lock follows the scene being written to, not the one on screen", async () => {
  // Scene selection stays live during a turn, and the write still lands in the
  // scene the stream captured. A lock keyed on `activeId` unlocked the row
  // still being written to and locked an unrelated one.
  vi.mocked(api.listScenes).mockResolvedValue([
    { id: "s1", title: "Old", model: "", created: "", updated: "", date: "" },
    { id: "s2", title: "Later", model: "", created: "", updated: "", date: "" },
  ]);
  vi.mocked(api.chat).mockImplementation(hangingChat(["The tide "]));
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByRole("button", { name: /stop ■/i });
  await openScene(/Later/);          // navigate away mid-turn
  // The lock is keyed on the scene being WRITTEN to, not the one on screen, so
  // the scene merely being looked at stays editable while s1 streams.
  await waitFor(() =>
    expect(screen.getByRole("button", { name: /rename scene/i })).not.toBeDisabled());
  await openScene(/^Old$/);
  await waitFor(() =>
    expect(screen.getByRole("button", { name: /rename scene/i })).toBeDisabled());
});

test("Stop during the preflight read does not strand the turn", async () => {
  // The baseline read runs before the POST exists, so the turn's controller has
  // nothing to abort yet. A stalled read left `runStream` parked outside its
  // try/finally with `busy` set: no Send, no Stop that works, no prompt back.
  // The window is the same on any send: the baseline is fetched before the POST
  // exists, so the turn's controller has nothing to abort yet.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockImplementation(async (_c, _s, w) => {
    if (w?.limit === 1) return new Promise(() => {});   // the preflight never answers
    return fixture({ meta: {}, total: 0, messages: [] });
  });
  vi.mocked(api.createScene).mockResolvedValue({ id: "s2" });
  vi.mocked(api.chat).mockImplementation(async (_c, _s, _t,
                                              _e, _r, signal) => {
    const err: Error & { beforeResponse?: boolean } = new Error("The operation was aborted.");
    err.name = "AbortError";
    err.beforeResponse = true;
    if (signal?.aborted) throw err;
    await new Promise<void>((_res, rej) => signal?.addEventListener("abort", () => rej(err)));
  });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  // The turn unwinds instead of hanging: the composer accepts input again and
  // the prompt comes back. Asserted as "Stop is gone", not as a specific
  // label -- the composer reads `Continue ▶` while it is empty and `Send ▸`
  // once the text is restored, so naming one pins whichever happens to win the
  // race between the unwind and the recovery rather than the guarantee.
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: /stop ■/i })).toBeNull());
  await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("I draw my blade."));
});
