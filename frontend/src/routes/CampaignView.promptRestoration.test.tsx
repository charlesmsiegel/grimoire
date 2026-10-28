import { screen, fireEvent, waitFor } from "@testing-library/react";
import { api } from "../api/client";
import { installCampaignMocks, ONE_SCENE, renderCampaign } from "../testkit/campaignHarness";
import { fixture } from "../testkit/fixture";
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



test("a failed send that was rolled back gives the player their words back", async () => {
  // The backend removes the post when a turn fails having produced nothing, so
  // without this the text exists nowhere: the composer was cleared on send and
  // the refresh drops the optimistic copy. Retry cannot recover it either — it
  // calls /retry, which has no prompt of its own.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.chat).mockImplementation(
    async (_c, _s, _t, onEvent) => {
      onEvent({ error: { detail: "OpenRouter API key is not set", kind: "missing_key",
                         post_returned: true } });
    });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByText(/OpenRouter API key is not set/);
  await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("I draw my blade."));
});

test("a Stop before the request lands gives the prompt back too", async () => {
  // The abort beats the request to the server, so there is no post to roll back
  // and no error frame to carry `post_returned` — but the player is in exactly
  // the same position, with the composer cleared and nothing durable anywhere.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.chat).mockImplementation(async () => {
    const err: Error & { beforeResponse?: boolean } = new Error("The operation was aborted.");
    err.name = "AbortError";
    err.beforeResponse = true;   // set by streamPost when fetch never resolved
    throw err;
  });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("I draw my blade."));
});

test("an abort whose post did land does not duplicate the prompt", async () => {
  // `beforeResponse` only means no response arrived. The server can have
  // appended the post and had the abort beat its headers back — restoring then
  // puts the text in the composer *and* the transcript, and the next Send
  // sends it twice. Growth in the refreshed transcript is what settles it.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  let landedOnServer = false;
  vi.mocked(api.getScene).mockImplementation(async () => fixture({
    meta: {}, total: landedOnServer ? 1 : 0,
    messages: landedOnServer ? [{ role: "user", content: "I draw my blade." }] : [],
  }));
  vi.mocked(api.chat).mockImplementation(async () => {
    landedOnServer = true;      // it did reach post_chat, headers just never came back
    const err: Error & { beforeResponse?: boolean } = new Error("The operation was aborted.");
    err.name = "AbortError";
    err.beforeResponse = true;
    throw err;
  });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByText("I draw my blade.");   // the post is really there
  await new Promise((r) => setTimeout(r, 50));
  expect(screen.getByRole("textbox")).toHaveValue("");
});

test("a cancel after the request landed leaves the composer alone", async () => {
  // The post is durably stored by then — a cancel keeps it — so restoring would
  // have the player send the same line twice.
  //
  // The refreshed transcript has to actually contain that post, or the test
  // asserts its opposite: this used to run against the default empty-scene
  // mock, so it modelled a cancel whose post was NOT stored and passed only
  // because nothing restored on this path at all. Growth is now what tells the
  // two apart (review, #95).
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  let posted = false;
  vi.mocked(api.getScene).mockImplementation(async () => fixture({
    meta: {}, total: posted ? 1 : 0,
    messages: posted ? [{ role: "user", content: "I draw my blade." }] : [],
  }));
  vi.mocked(api.chat).mockImplementation((...args) => {
    posted = true;   // post_chat appended before returning the stream
    return hangingChat(["The tide "])(...args);
  });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  await waitFor(() => expect(screen.getByRole("button", { name: /continue ▶/i })).toBeEnabled());
  expect(screen.getByRole("textbox")).toHaveValue("");
});

test("a failure the backend did not roll back leaves the composer alone", async () => {
  // The post is still in the transcript, so restoring it would have the player
  // send the same line twice. Only the backend knows which happened.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.chat).mockImplementation(
    async (_c, _s, _t, onEvent) => {
      onEvent({ delta: "The tide " });
      onEvent({ error: { detail: "connection reset", kind: "network" } });
    });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByText(/connection reset/);
  expect(screen.getByRole("textbox")).toHaveValue("");
});

test("a network failure names the recovery, not just the socket error (#210)", async () => {
  // Being offline used to read as the toast "connection reset" and nothing
  // else: the `network` kind reached the browser and was thrown away. What the
  // reader needs is that the library is local and a local model connection
  // keeps play going -- and a way to get there.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.chat).mockImplementation(
    async (_c, _s, _t, onEvent) => {
      onEvent({ error: { detail: "connection reset", kind: "network" } });
    });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByText(/Couldn.t reach the model provider/);
  expect(screen.getByRole("link", { name: /Providers/ }))
    .toHaveAttribute("href", "/providers");
  // The provider's own words survive alongside it: `network` is also what a
  // local endpoint that is not running raises, and that reader needs them.
  expect(screen.getByText(/connection reset/)).toBeInTheDocument();
  // Still retryable -- the network coming back is exactly the fix.
  expect(screen.getByRole("button", { name: /^retry$/i })).toBeInTheDocument();
});

test("an unconfigured key is not an offline app", async () => {
  // The two were flagged as conflated in #210 and have opposite fixes; a
  // missing key gets the plain detail and no offline advice.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.chat).mockImplementation(
    async (_c, _s, _t, onEvent) => {
      onEvent({ error: { detail: "OpenRouter API key is not set", kind: "missing_key" } });
    });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByText(/OpenRouter API key is not set/);
  expect(screen.queryByText(/Couldn.t reach the model provider/)).toBeNull();
});

test("a body that ends before the done frame is an interrupted turn", async () => {
  // `reader.read()` reporting EOF resolves streamPost normally, so a proxy
  // cutting the body short used to look identical to a completed turn — the
  // one-shot override was spent and the flush never waited for.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  // The truncated turn is treated as interrupted, so the flush poll runs; let
  // the partial land so it exits on its first tick rather than sitting out the
  // whole budget — `runStream` awaits it before the override is settled either
  // way, and a `waitFor` that ran before that would pass on any implementation.
  let flushed = false;
  vi.mocked(api.getScene).mockImplementation(async () => ({
    meta: { id: "s1", title: "Old" },
    total: flushed ? 1 : 0,
    messages: flushed ? [{ role: "assistant", content: "The tide" }] : [],
  }));
  vi.mocked(api.chat).mockImplementation(
    async (_c, _s, _t, onEvent) => {
      onEvent({ delta: "The tide " });   // ...and then the body just ends
    });
  renderCampaign();
  const picker = await screen.findByLabelText("Next reply words");
  fireEvent.change(picker, { target: { value: "120" } });
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  setTimeout(() => { flushed = true; }, 100);
  await screen.findByText("The tide");          // poll caught the flush and ended
  await new Promise((r) => setTimeout(r, 600)); // let the poll exit and send() settle
  // unspent: the reply never confirmed, so the override rides the retry
  expect(picker).toHaveValue(120);
});

test("the post-cancel poll stops once a new turn owns the view", async () => {
  // Stop clears `busy` before the poll finishes, so the player can send again
  // while it is still running. Left alone it would keep calling selectScene,
  // clearing the new stream's live preview on every tick.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [] }));
  vi.mocked(api.chat).mockImplementation(hangingChat(["a streamed fragment"]));
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  await waitFor(() => expect(screen.getByRole("button", { name: /continue ▶/i })).toBeEnabled());   // cancel settled
  const afterCancel = vi.mocked(api.getScene).mock.calls.length;

  fireEvent.change(screen.getByRole("textbox"), { target: { value: "again" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByRole("button", { name: /stop ■/i });       // second turn in flight
  await new Promise((r) => setTimeout(r, 700));                 // past two poll ticks
  // The live preview of the second turn survives, and the stale poll made no
  // further fetches of its own.
  expect(screen.getByText("a streamed fragment")).toBeInTheDocument();
  expect(vi.mocked(api.getScene).mock.calls.length).toBe(afterCancel);
});

test("cancelling keeps a one-shot response override for the retry", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.chat).mockImplementation(hangingChat());
  renderCampaign();
  const picker = await screen.findByLabelText("Next reply words");
  fireEvent.change(picker, { target: { value: "120" } });
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  await waitFor(() => expect(picker).toHaveValue(120)); // unspent, like a failure
});
