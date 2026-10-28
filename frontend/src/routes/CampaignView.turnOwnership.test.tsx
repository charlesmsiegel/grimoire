import { screen, fireEvent, waitFor } from "@testing-library/react";
import { api } from "../api/client";
import { onConfigChanged } from "../appEvents";
import { installCampaignMocks, ONE_SCENE, openScene, renderCampaign } from "../testkit/campaignHarness";
import { fixture } from "../testkit/fixture";
import { PROPOSAL_PAYLOAD } from "../testkit/campaignFixtures";
import { hangingChat } from "../testkit/campaignStreams";

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



test("Retry after a failed reroll rerolls again, with its guidance", async () => {
  // `/retry` continues from the transcript as it stands. A failed reroll now
  // puts the old reply back, so retrying through `/retry` would generate a
  // continuation of the very reply the player asked to replace — and drop the
  // guidance they typed with it.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({
    meta: {}, total: 2, messages: [
      { role: "user", content: "and then?" },
      { role: "assistant", content: "The tide turns." }],
  }));
  vi.mocked(api.regenerate).mockImplementation(
    async (_c, _s, onEvent) => {
      // A failure a retry can get past: a refused key is not one (`setupFix`).
      onEvent({ error: { detail: "upstream timed out", kind: "timeout" } });
    });
  renderCampaign();
  await screen.findByText("The tide turns.");
  fireEvent.click(screen.getByRole("button", { name: /reroll/i }));
  fireEvent.change(screen.getByPlaceholderText(/reroll/i),
                   { target: { value: "darker this time" } });
  fireEvent.click(screen.getByRole("button", { name: /reroll ▸/i }));
  await screen.findByText(/upstream timed out/);
  expect(api.regenerate).toHaveBeenCalledTimes(1);

  fireEvent.click(screen.getByRole("button", { name: /^retry$/i }));
  await waitFor(() => expect(api.regenerate).toHaveBeenCalledTimes(2));
  expect(api.retry).not.toHaveBeenCalled();
  expect(vi.mocked(api.regenerate).mock.calls[1][3]?.guidance).toBe("darker this time");
});

test("a remembered reroll does not follow the player to another scene", async () => {
  // The remembered operation had no scene identity, and Retry acts on whatever
  // scene is open — so a reroll that failed in one scene, retried after
  // switching, would replace a reply in the *other* scene with guidance written
  // for the first. Switching also clears the banner now; the scene check is
  // what makes that airtight rather than merely likely.
  vi.mocked(api.listScenes).mockResolvedValue([
    { id: "s1", title: "Old", model: "", created: "", updated: "", date: "" },
    { id: "s2", title: "Later", model: "", created: "", updated: "", date: "" },
  ]);
  vi.mocked(api.getScene).mockResolvedValue(fixture({
    meta: {}, total: 2, messages: [
      { role: "user", content: "and then?" },
      { role: "assistant", content: "The tide turns." }],
  }));
  vi.mocked(api.regenerate).mockImplementation(
    async (_c, _s, onEvent) => {
      onEvent({ error: { detail: "OpenRouter API key is not set", kind: "missing_key" } });
    });
  renderCampaign();
  await screen.findByText("The tide turns.");
  fireEvent.click(screen.getAllByRole("button", { name: /reroll/i })[0]);
  fireEvent.change(screen.getByPlaceholderText(/reroll/i),
                   { target: { value: "darker this time" } });
  fireEvent.click(screen.getByRole("button", { name: /reroll ▸/i }));
  await screen.findByText(/OpenRouter API key is not set/);

  await openScene(/Later/);       // leave the failed scene
  // the banner belonged to the scene being left, so it goes with it
  await waitFor(() => expect(screen.queryByText(/OpenRouter API key is not set/)).toBeNull());

  // Now fail something in the new scene, so a banner — and a Retry — exist here
  // on their own account. The remembered reroll is still in the ref, and this
  // is where a Retry with no scene identity would have rerolled the wrong scene.
  vi.mocked(api.chat).mockImplementation(
    async (_c, _s, _t, onEvent) => {
      onEvent({ error: { detail: "connection reset", kind: "network" } });
    });
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "onward" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByText(/connection reset/);
  fireEvent.click(screen.getByRole("button", { name: /^retry$/i }));

  await waitFor(() => expect(api.retry).toHaveBeenCalled());
  expect(vi.mocked(api.retry).mock.calls[0][1]).toBe("s2");   // this scene
  expect(api.regenerate).toHaveBeenCalledTimes(1);          // not s1's reroll again
});

test("a turn that the provider refused tells the shell its status is stale", async () => {
  // The server records what a provider did as it happens (#146), but a turn
  // fails over SSE — it never passes through the api client's rejection path,
  // where every other provider call raises this. Without it the status dot
  // keeps saying "connected" until the reader navigates somewhere.
  const seen: string[] = [];
  const off = onConfigChanged(() => seen.push("config"));
  vi.mocked(api.chat).mockImplementation(
    async (_c, _s, _t, onEvent) => {
      onEvent({ error: { detail: "No auth credentials found", kind: "auth" } });
    });
  renderCampaign();
  await screen.findByRole("textbox");
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "onward" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));

  await screen.findByText(/No auth credentials found/);
  expect(seen).toEqual(["config"]);
  off();
});

test("a turn that works after one that failed says so, without a navigation", async () => {
  // Recovery has to be announced too, or the dot the failure turned red stays
  // red until an unrelated navigation — the Retry that fixed it is the moment
  // the reader is watching (#146).
  const seen: string[] = [];
  const off = onConfigChanged(() => seen.push("config"));
  vi.mocked(api.chat).mockImplementation(
    async (_c, _s, _t, onEvent) => {
      onEvent({ error: { detail: "No auth credentials found", kind: "auth" } });
    });
  renderCampaign();
  await screen.findByRole("textbox");
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "onward" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByText(/No auth credentials found/);
  expect(seen).toEqual(["config"]);

  vi.mocked(api.chat).mockImplementation(
    async (_c, _s, _t, onEvent) => {
      onEvent({ delta: "The tide turns." });
      onEvent({ done: true });
    });
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "again" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));

  await waitFor(() => expect(seen).toEqual(["config", "config"]));
  off();
});

test("an ordinary turn does not re-read the config every time", async () => {
  // The counterpart to the test above: announcing on every success would put a
  // config read behind every message in the game.
  const seen: string[] = [];
  const off = onConfigChanged(() => seen.push("config"));
  vi.mocked(api.chat).mockImplementation(
    async (_c, _s, _t, onEvent) => {
      onEvent({ delta: "The tide turns." });
      onEvent({ done: true });
    });
  renderCampaign();
  await screen.findByRole("textbox");
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "onward" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));

  await screen.findByText("The tide turns.");
  expect(seen).toEqual([]);
  off();
});

test("a turn that failed for a reason of ours leaves the status alone", async () => {
  // A scene that is busy, a stale write, a 404 — none of them are a statement
  // about the provider, and re-reading the config over one is noise. `busy` is
  // the kind `streaming.py` really sends for this, not an invented one: a
  // negative test against a string the app never emits proves nothing.
  const seen: string[] = [];
  const off = onConfigChanged(() => seen.push("config"));
  vi.mocked(api.chat).mockImplementation(
    async (_c, _s, _t, onEvent) => {
      onEvent({ error: { detail: "that scene is busy", kind: "busy" } });
    });
  renderCampaign();
  await screen.findByRole("textbox");
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "onward" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));

  await screen.findByText(/that scene is busy/);
  expect(seen).toEqual([]);
  off();
});

test("End scene stays disabled while the cancelled turn's flush is still coming", async () => {
  // Absorption reads the transcript and commits a chronicle against it. `busy`
  // clears when the socket dies, but the backend's shielded write lands seconds
  // later — absorb inside that window and the summary describes a transcript
  // the partial has not reached, then the partial lands under a scene already
  // marked absorbed. Unlike the other flush races, that one does not heal.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, total: 0, messages: [] }));
  vi.mocked(api.chat).mockImplementation(hangingChat(["The tide "]));
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  await waitFor(() => expect(screen.getByRole("button", { name: /continue ▶/i })).toBeEnabled());   // busy is clear
  await new Promise((r) => setTimeout(r, 400));                 // still flushing
  expect(screen.getByRole("button", { name: /end scene/i })).toBeDisabled();
});

test("a scene rename in flight holds off the next turn", async () => {
  // Renaming is a PUT that moves the scene file. Until it answers, which id is
  // current is genuinely unknown — so a turn started inside that window can be
  // handed the old one and have its reply written to a path that no longer
  // exists. The lock covers rename-during-a-turn; this is the other direction.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  let finishRename: ((v: Awaited<ReturnType<typeof api.renameScene>>) => void) | null = null;
  vi.mocked(api.renameScene).mockImplementation(
    () => new Promise((res) => { finishRename = res; }));
  renderCampaign();
  await screen.findByRole("heading", { name: /^Old$/ });
  fireEvent.click(screen.getByRole("button", { name: /rename/i }));
  const input = screen.getByDisplayValue("Old");
  fireEvent.change(input, { target: { value: "Renamed" } });
  fireEvent.keyDown(input, { key: "Enter" });

  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  await waitFor(() =>
    expect(screen.getByRole("button", { name: /send ▸/i })).toBeDisabled());
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  expect(api.chat).not.toHaveBeenCalled();

  finishRename!({ id: "s1", title: "Renamed" });     // the PUT answers
  await waitFor(() =>
    expect(screen.getByRole("button", { name: /send ▸/i })).not.toBeDisabled());
});

test("a verification read retired by a scene switch still gives the prompt back", async () => {
  // `selectScene` returns -1 when a newer owner takes the view: it read nothing
  // and applied nothing. The await did not throw, though, so `refreshed` was
  // true and the turn counted as verified — with no growth to point at, an
  // undelivered prompt went unrestored. "It did not look" is unverifiable.
  vi.mocked(api.listScenes).mockResolvedValue([
    { id: "s1", title: "Old", model: "", created: "", updated: "", date: "" },
    { id: "s2", title: "Later", model: "", created: "", updated: "", date: "" },
  ]);
  let releaseVerify: (() => void) | null = null;
  let loaded = false;
  vi.mocked(api.getScene).mockImplementation(async (_c, sid) => {
    // the verification read for s1 hangs until the player has moved to s2
    if (sid === "s1" && loaded) {
      await new Promise<void>((r) => { releaseVerify = r; });
    }
    loaded = true;
    return fixture({ meta: {}, total: 0, messages: [] });
  });
  vi.mocked(api.chat).mockImplementation(async () => {
    const err: Error & { beforeResponse?: boolean } = new Error("Failed to fetch");
    err.beforeResponse = true;      // nothing reached the server
    throw err;
  });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await waitFor(() => expect(releaseVerify).not.toBeNull());
  await openScene(/Later/);   // retires the pending read
  releaseVerify!();
  await new Promise((r) => setTimeout(r, 60));
  // Recovered, but not into the composer the player is looking at: these are
  // scene s1's words and Send here would post them to s2. The prompt is held
  // under the scene it was written for (review, #95) …
  expect(screen.getByRole("heading", { name: /Later/ })).toBeInTheDocument();
  expect(screen.getByRole("textbox")).toHaveValue("");
  // … and handed back when that scene is on screen again. Recovered, not lost,
  // which is the whole point of counting a retired read as unverifiable.
  await openScene(/^Old$/);
  await waitFor(() =>
    expect(screen.getByRole("textbox")).toHaveValue("I draw my blade."));
});

test("a pending rename also blocks a proposal continuation", async () => {
  // Resolving a roll streams a continuation through `runStream` without passing
  // `send`/`retry`/`reroll`, so the per-call-site rename checks all missed it.
  // The guard belongs where every stream enters.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getRollProposal).mockResolvedValue({
    record: { id: "p1", status: "pending", resolution: null,
              payload: { id: "p1", check: "wits", check_label: "Wits", problems: [] } },
  });
  let finishRename: ((v: Awaited<ReturnType<typeof api.renameScene>>) => void) | null = null;
  vi.mocked(api.renameScene).mockImplementation(
    () => new Promise((res) => { finishRename = res; }));
  renderCampaign();
  await screen.findByRole("heading", { name: /^Old$/ });
  fireEvent.click(screen.getByRole("button", { name: /rename/i }));
  const input = screen.getByDisplayValue("Old");
  fireEvent.change(input, { target: { value: "Renamed" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(api.renameScene).toHaveBeenCalled());

  const decline = await screen.findByRole("button", { name: /decline/i });
  fireEvent.click(decline);
  await new Promise((r) => setTimeout(r, 50));
  expect(api.resolveProposal).not.toHaveBeenCalled();   // the file may be moving
  // and the chip is still there: refusing to send must not also hide the
  // decision, or the roll becomes unreachable until some later refresh
  expect(screen.getByRole("button", { name: /decline/i })).toBeInTheDocument();

  finishRename!({ id: "s1", title: "Renamed" });
  await waitFor(() => expect(screen.queryByDisplayValue("Renamed")).toBeNull());
});

test("a poll fetch already in flight cannot clear a new turn's preview", async () => {
  // The check-then-await window: the poll verifies it owns the view, then
  // awaits getScene, and a turn starting during that await would otherwise have
  // the stale response run setStreaming("") over the new stream's text.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  let releaseFetch: (() => void) | null = null;
  vi.mocked(api.getScene).mockImplementation(async () => {
    if (releaseFetch) await new Promise<void>((r) => { releaseFetch = r; });
    return fixture({ meta: {}, messages: [] });
  });
  vi.mocked(api.chat)
    .mockImplementationOnce(hangingChat(["first fragment"]))
    .mockImplementation(hangingChat(["second fragment"]));
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  await waitFor(() => expect(screen.getByRole("button", { name: /continue ▶/i })).toBeEnabled());

  releaseFetch = () => {};                       // the next getScene blocks
  const before = vi.mocked(api.getScene).mock.calls.length;
  await waitFor(() => expect(vi.mocked(api.getScene).mock.calls.length).toBeGreaterThan(before));

  fireEvent.change(screen.getByRole("textbox"), { target: { value: "again" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByText("second fragment");
  releaseFetch();                // the stale poll's fetch lands
  await new Promise((r) => setTimeout(r, 50));
  expect(screen.getByText("second fragment")).toBeInTheDocument();
});

test("a cancelled fence's proposal survives a stale proposal read", async () => {
  // selectScene fires getRollProposal and awaits only getScene, so on the tick
  // that catches the flush the two race it independently. finalize writes the
  // proposal before the narration, so the scene read that saw growth is after
  // both writes while the proposal read beside it can be before either — and
  // its late null would clear a chip that does exist, with the poll already
  // stopped. Here the first proposal read returns null and the second (awaited,
  // after growth) returns the record.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  let flushed = false;
  let sceneSawGrowth = false;
  vi.mocked(api.getScene).mockImplementation(async () => {
    if (flushed) sceneSawGrowth = true;
    return fixture({
      meta: {}, total: flushed ? 1 : 0,
      messages: flushed ? [{ role: "assistant", content: "She lunges" }] : [],
    });
  });
  // Evaluated when the call is made, not when it resolves — so the read
  // selectScene fires before awaiting getScene still sees the pre-flush world,
  // which is precisely the stale answer that used to win.
  vi.mocked(api.getRollProposal).mockImplementation(async () => ({
    record: sceneSawGrowth
      ? { id: "pr-1", status: "pending", payload: PROPOSAL_PAYLOAD, resolution: null }
      : null,
  }));
  vi.mocked(api.chat).mockImplementation(hangingChat(["She lunges"]));
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I punch him" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  setTimeout(() => { flushed = true; }, 100);
  expect(await screen.findByRole("button", { name: "Roll it" })).toBeInTheDocument();
});

test("a slow pre-flush proposal read cannot undo the settling read", async () => {
  // selectScene's proposal read is fired and not awaited. If it resolves after
  // settleProposal has installed the record, last-write-wins puts its pre-flush
  // null back — chip gone, poll already finished. The settling read is issued
  // later, so it must win regardless of which lands first.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  let flushed = false;
  let sceneSawGrowth = false;
  let releaseStale: (() => void) | null = null;
  vi.mocked(api.getScene).mockImplementation(async () => {
    if (flushed) sceneSawGrowth = true;
    return fixture({
      meta: {}, total: flushed ? 1 : 0,
      messages: flushed ? [{ role: "assistant", content: "She lunges" }] : [],
    });
  });
  // The two reads are told apart by when they are *made*, not by an argument:
  // selectScene fires its one before awaiting getScene, so it still sees the
  // pre-growth world; settleProposal's comes after. That is the real ordering,
  // and it is the only thing left distinguishing them now the endpoint opts out
  // of coalescing for every caller.
  vi.mocked(api.getRollProposal).mockImplementation(async () => {
    if (sceneSawGrowth) {
      return { record: { id: "pr-1", status: "pending", payload: PROPOSAL_PAYLOAD, resolution: null } };
    }
    // the unawaited read: parked, so it lands *after* the settling one
    if (flushed) await new Promise<void>((r) => { releaseStale = r; });
    return { record: null };
  });
  vi.mocked(api.chat).mockImplementation(hangingChat(["She lunges"]));
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I punch him" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  setTimeout(() => { flushed = true; }, 100);
  await screen.findByRole("button", { name: "Roll it" });
  (releaseStale as unknown as () => void)();   // the stale null finally arrives
  await new Promise((r) => setTimeout(r, 50));
  expect(screen.getByRole("button", { name: "Roll it" })).toBeInTheDocument();
});

test("the refresh right after a cancel cannot wipe the next turn's preview", async () => {
  // Stop clears `busy` before the immediate refresh resolves, so the next turn
  // can begin while that fetch is in flight — and its response would otherwise
  // run setMessages/setStreaming("") straight over the new turn's state.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  let holdRefresh: (() => void) | null = null;
  vi.mocked(api.getScene).mockImplementation(async () => {
    if (holdRefresh) await new Promise<void>((r) => { holdRefresh = r; });
    return fixture({ meta: {}, total: 0, messages: [] });
  });
  vi.mocked(api.chat)
    .mockImplementationOnce(hangingChat(["first fragment"]))
    .mockImplementation(hangingChat(["second fragment"]));
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));

  holdRefresh = () => {};                       // the post-cancel refresh parks
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  await waitFor(() => expect(holdRefresh).not.toBe(null));
  await waitFor(() => expect(screen.getByRole("button", { name: /continue ▶/i })).toBeEnabled());   // Send is live again

  fireEvent.change(screen.getByRole("textbox"), { target: { value: "again" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByText("second fragment");
  holdRefresh();     // the stale refresh finally lands
  await new Promise((r) => setTimeout(r, 50));
  expect(screen.getByText("second fragment")).toBeInTheDocument();
});

test("Stop after the done frame is a finished turn, not a cancellation", async () => {
  // `done` is parsed off the stream before the body reports EOF, and Stop stays
  // live until it does. A press in that gap used to be classed as a cancel,
  // which handed back a one-shot response length the reply had already
  // consumed — and spent it again on the next turn.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old" }, total: 0, messages: [] });
  vi.mocked(api.chat).mockImplementation(
    async (_c, _s, _t, onEvent, _r, signal) => {
      onEvent({ delta: "All told." });
      onEvent({ done: true });          // persisted server-side from here on
      await new Promise<void>((_res, reject) => {   // body still open
        signal?.addEventListener("abort", () => {
          const err = new Error("The operation was aborted.");
          err.name = "AbortError";
          reject(err);
        });
      });
    });
  renderCampaign();
  const picker = await screen.findByLabelText("Next reply words");
  fireEvent.change(picker, { target: { value: "120" } });
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  await waitFor(() => expect(screen.getByRole("button", { name: /continue ▶/i })).toBeEnabled());
  // spent by the reply that did land, so it must not ride the next turn
  await waitFor(() => expect(picker).not.toHaveValue(120));
});
