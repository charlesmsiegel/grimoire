import { screen, fireEvent, waitFor, act, within } from "@testing-library/react";
import { api } from "../api/client";
import type { TrackerEntry, TrackerSummary } from "../api/client";
import { installCampaignMocks, ONE_SCENE, openScene, renderCampaign } from "../testkit/campaignHarness";
import { fixture } from "../testkit/fixture";
import { trackerCallsFor } from "../testkit/campaignFixtures";

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



// ---- what each player post cost to answer (#153) ----
/** One `by_post` bucket, complete. Written out rather than spread from a
 *  helper: this is a payload shape the transcript reads, and a fixture that
 *  quietly omits a column is how a NaN reaches a price. */
function postBucket(post: number, over: Record<string, number> = {}) {
  return { post, rerolls: 0, calls: 1, errors: 0, prompt_tokens: 0, completion_tokens: 0,
           total_tokens: 900, cache_read_tokens: 0, cache_write_tokens: 0,
           cost_usd: 0.02, estimated_usd: 0, modelled_usd: 0, priced_calls: 1,
           unpriced_calls: 0, subscription_calls: 0, modelled_calls: 0,
           unmetered_calls: 0, duration_ms: 0, ...over };
}

function withPostCosts(buckets: ReturnType<typeof postBucket>[]) {
  vi.mocked(api.getSceneUsage).mockResolvedValue({
    campaign: "run", scene: "s1", since: "", until: "", clamped: false,
    generated_at: "",
    totals: { calls: 0, errors: 0, prompt_tokens: 0, completion_tokens: 0,
              total_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0,
              cost_usd: 0, estimated_usd: 0, modelled_usd: 0, priced_calls: 0,
              unpriced_calls: 0, subscription_calls: 0, modelled_calls: 0,
              unmetered_calls: 0, duration_ms: 0 },
    by_task: [], by_post: buckets, turns: [], listed: 0, truncated: false });
}

test("a player post says what answering it cost, over every reroll", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  // Three generations answering post 0: the reply on screen, and two takes that
  // were paid for and thrown away — which is the number the transcript itself
  // cannot show.
  withPostCosts([postBucket(0, { calls: 3, rerolls: 2, priced_calls: 3, cost_usd: 0.06 })]);
  renderCampaign();

  // Re-queried after the wait rather than held across it, which is what three
  // CI failures here were actually about. The chip's effect clears itself on
  // every `ctxKey` beat (`setPostCosts(null)`, CampaignView.tsx) and re-reads,
  // so the span is DESTROYED and rebuilt whenever a bump lands — and the wait
  // above does not end at the found element: `test-setup.ts` settles the page
  // for up to 25 more macrotasks afterwards. A bump inside that window detaches
  // the very node `findBy` just returned, and jest-dom then reports "element
  // could not be found in the document" for an element that is on screen. Not a
  // timeout: a longer budget cannot help, and 15s did not.
  await screen.findByText(/\$0\.06/);
  expect(screen.getByText(/\$0\.06/)).toBeInTheDocument();
  expect(screen.getByText(/2 rerolls/)).toBeInTheDocument();
});

test("the chip is on the player's post, not on the reply it paid for", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  // A bucket at index 1 as well: the reply's own index. Chipping both would
  // print the same spend twice, once under the text that paid for it.
  withPostCosts([postBucket(0), postBucket(1, { cost_usd: 0.99 })]);
  renderCampaign();

  await screen.findByText("$0.02");
  expect(screen.queryByText("$0.99")).toBeNull();
});

test("a post nothing could price carries no chip rather than a $0.00 one", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  withPostCosts([postBucket(0, { priced_calls: 0, unpriced_calls: 1, cost_usd: 0 })]);
  renderCampaign();

  await screen.findByRole("heading", { name: /^Old$/ });
  expect(screen.queryByText("$0.00")).toBeNull();
  expect(screen.queryByText("unpriced")).toBeNull();
});

test("cost chips never land on the transcript they were not read for", async () => {
  // The race: this view keeps scene A's messages on screen until B's
  // transcript read lands, and the usage read is a separate request that can
  // land first. Index-keyed alone, B's costs appeared beside A's posts — and
  // stayed there if B's transcript read never landed at all.
  vi.mocked(api.listScenes).mockResolvedValue([
    { id: "s1", title: "First", model: "", created: "", updated: "2026-01-02", date: "" },
    { id: "s2", title: "Second", model: "", created: "", updated: "2026-01-01", date: "" },
  ]);
  vi.mocked(api.getScene).mockImplementation(async (_cid, sid) => {
    if (sid === "s1") {
      return { meta: { id: "s1", title: "First" },
               messages: [{ role: "user", content: "the first scene" }] };
    }
    return new Promise(() => {});          // B's transcript never arrives
  });
  vi.mocked(api.getSceneUsage).mockImplementation(async (_cid, sid) => {
    const empty = { campaign: "run", scene: sid, since: "", until: "", clamped: false,
      generated_at: "",
      totals: { calls: 0, errors: 0, prompt_tokens: 0, completion_tokens: 0,
                total_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0,
                cost_usd: 0, estimated_usd: 0, modelled_usd: 0, priced_calls: 0,
                unpriced_calls: 0, subscription_calls: 0, modelled_calls: 0,
                unmetered_calls: 0, duration_ms: 0 },
      by_task: [], by_post: [], turns: [], listed: 0, truncated: false };
    // Only the SECOND scene has spend, and its read resolves at once.
    return sid === "s2"
      ? { ...empty, by_post: [postBucket(0, { cost_usd: 9.99, priced_calls: 1 })] }
      : empty;
  });
  renderCampaign();
  await screen.findByText("the first scene");

  await openScene(/Second/);

  // A's message is still what is rendered, and B's figure is not beside it.
  expect(screen.getByText("the first scene")).toBeInTheDocument();
  expect(screen.queryByText(/\$9\.99/)).toBeNull();
});

test("the inspector's Cost section never shows the scene being left as the one being opened", async () => {
  // The Cost section renders the usage this view read for its own chips rather
  // than asking again -- and that read follows the transcript ON SCREEN, which
  // stays the scene being left until the new one's lands, or for good if it
  // never does. Handed down unscoped, a section headed by the second scene
  // showed the first scene's spend.
  vi.mocked(api.listScenes).mockResolvedValue([
    { id: "s1", title: "First", model: "", created: "", updated: "2026-01-02", date: "" },
    { id: "s2", title: "Second", model: "", created: "", updated: "2026-01-01", date: "" },
  ]);
  vi.mocked(api.getScene).mockImplementation(async (_cid, sid) => {
    if (sid === "s1") {
      return { meta: { id: "s1", title: "First" },
               messages: [{ role: "user", content: "the first scene" }] };
    }
    return new Promise(() => {});          // the second transcript never arrives
  });
  vi.mocked(api.getSceneUsage).mockImplementation(async (_cid, sid) => {
    const totals = { calls: 0, errors: 0, prompt_tokens: 0, completion_tokens: 0,
                     total_tokens: 0, cache_read_tokens: 0, cache_write_tokens: 0,
                     cost_usd: 0, estimated_usd: 0, modelled_usd: 0, priced_calls: 0,
                     unpriced_calls: 0, subscription_calls: 0, modelled_calls: 0,
                     unmetered_calls: 0, duration_ms: 0 };
    return { campaign: "run", scene: sid, since: "", until: "", clamped: false,
      generated_at: "", by_task: [], by_post: [], turns: [], listed: 0, truncated: false,
      totals: sid === "s1" ? { ...totals, calls: 1, cost_usd: 4.56, priced_calls: 1 } : totals };
  });
  renderCampaign();
  await screen.findByText("the first scene");
  fireEvent.click(screen.getByRole("button", { name: /What the model saw/ }));
  fireEvent.click(await screen.findByRole("button", { name: /^cost/i }));
  const panel = await waitFor(() => {
    const el = document.querySelector(".cost-panel");
    expect(el).not.toBeNull();
    return el as HTMLElement;
  });
  expect(await within(panel).findByText(/\$4\.56 · 1 turn/)).toBeInTheDocument();

  await openScene(/Second/);

  expect(within(panel).queryByText(/\$4\.56/)).toBeNull();
});

test("opening a scene reads its costs and the budget once each, and a turn once more", async () => {
  // Both used to be read on the way in AND again when the transcript's landing
  // bumped the refresh beat, the first answer only ever discarded. With a
  // budget set, each is a scan of the usage ledger.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  renderCampaign();
  await screen.findByText("a reply");
  expect(api.getSceneUsage).toHaveBeenCalledTimes(1);
  expect(api.getSceneUsage).toHaveBeenCalledWith("run", "s1");
  expect(api.getCampaignBudget).toHaveBeenCalledTimes(1);

  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Go on." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await waitFor(() => expect(api.getSceneUsage).toHaveBeenCalledTimes(2));
  expect(api.getCampaignBudget).toHaveBeenCalledTimes(2);
});

test("a failed cost read leaves the transcript alone", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "user", content: "hi" }] });
  vi.mocked(api.getSceneUsage).mockRejectedValue(new Error("no"));
  renderCampaign();

  expect(await screen.findByText("hi")).toBeInTheDocument();
});

// ---- the per-post Tracker disclosure ----
const TF = { upstream_changed: false, text_changed: false };
function withTracker(over: Partial<TrackerSummary> = {}) {
  vi.mocked(api.getTracker).mockResolvedValue({
    enabled: true, names: { "characters:mara": "Mara" }, moods: {},
    labels: { visible_mood: "Visible mood" },
    keys: [{ index: 0, key: "p-0" }, { index: 1, key: "p-1" }],
    entries: { "p-0": { status: "ok", changed: [], flags: TF },
               "p-1": { status: "ok", changed: [["characters:mara", "visible_mood", "fear"]], flags: TF } },
    ...over });
}
function twoPosts() {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply", response_id: "r1" }] });
}

test("a tracked post shows its Tracker summary", async () => {
  twoPosts();
  withTracker();
  renderCampaign();
  expect(await screen.findByText("Tracker · no change")).toBeInTheDocument();
  expect(screen.getByText("Tracker · Mara: Visible mood → fear")).toBeInTheDocument();
  expect(api.getTracker).toHaveBeenCalledWith("run", "s1");
});

test("a cast tile shows the tracker's visible mood", async () => {
  twoPosts();
  vi.mocked(api.getCast).mockResolvedValue([
    { kind: "characters", id: "mara", role: "npc", name: "Mara" },
  ]);
  withTracker({ moods: { "characters:mara": "fear" } });
  renderCampaign();
  const mood = await screen.findByText("fear");
  expect(mood).toHaveClass("cast-mood");
});

test("Mara's dossier reads the latest ok key while the newest post is still pending", async () => {
  twoPosts();
  vi.mocked(api.getCast).mockResolvedValue([
    { kind: "characters", id: "mara", role: "npc", name: "Mara" },
  ]);
  vi.mocked(api.getCasefile).mockResolvedValue({
    kind: "characters", id: "mara", name: "Mara", version: "v1", role: "npc",
    scenes: [], last_seen: "", standing: "Watchful.", knows: "", suspects: "",
    dossier: "", tagline: "", feels_toward: [], standing_facts: [],
  });
  vi.mocked(api.getTrackerRecord).mockResolvedValue({
    key: "p-0", status: "ok", flags: TF, names: { "characters:mara": "Mara" },
    fields: [{ key: "visible_mood", label: "Visible mood", type: "text", aware: "present", hint: "" }],
    snapshot: { "characters:mara": { present: true, fields: {
      visible_mood: { value: "calm", aware: "present" } } } },
  });
  withTracker({ entries: { "p-0": { status: "ok", changed: [], flags: TF },
                           "p-1": { status: "pending", changed: [], flags: TF } } });
  renderCampaign();
  await screen.findByText("Tracker · updating…");
  fireEvent.click(document.querySelector(".cast-tile-name") as HTMLElement);
  expect(await screen.findByText("Now")).toBeInTheDocument();
  expect(api.getTrackerRecord).toHaveBeenCalledWith("run", "s1", "p-0");
  expect(api.getTrackerRecord).not.toHaveBeenCalledWith("run", "s1", "p-1");
  // p-1's update was built on p-0 as it stands: an edit now would land under it.
  const now = (await screen.findByText(/calm/)).closest(".column-section") as HTMLElement;
  expect(within(now).getByRole("button", { name: "Edit" })).toBeDisabled();
});

test("a post whose key the tracker does not list shows no disclosure", async () => {
  twoPosts();
  withTracker({ keys: [{ index: 0, key: "p-0" }] });
  renderCampaign();
  await screen.findByText("Tracker · no change");
  expect(screen.queryByText(/Tracker · Mara/)).toBeNull();
});

test("with the tracker off, an untracked post shows nothing", async () => {
  twoPosts();
  withTracker({ enabled: false, entries: {} });
  renderCampaign();
  await screen.findByText("a reply");
  expect(screen.queryByText(/^Tracker · /)).toBeNull();
});

// Fake timers, but with the clock still running (`shouldAdvanceTime`), the
// repo's idiom for a view that also awaits real promises (StatsView.test).
const PENDING: Record<string, TrackerEntry> = { "p-0": { status: "pending", changed: [], flags: TF },
                                                "p-1": { status: "ok", changed: [], flags: TF } };
const DONE: Record<string, TrackerEntry> = { ...PENDING, "p-0": { status: "ok", changed: [], flags: TF } };

async function tick(ms: number) {
  await act(async () => { vi.advanceTimersByTime(ms); });
}

test("a pending post is re-read every two seconds until it lands, then the chain stops", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    twoPosts();
    withTracker({ entries: PENDING });
    renderCampaign();
    expect(await screen.findByText("Tracker · updating…")).toBeInTheDocument();
    const before = trackerCallsFor("s1");
    await tick(1500);
    expect(trackerCallsFor("s1")).toBe(before);       // not before two seconds
    withTracker({ entries: DONE });
    await tick(600);
    await waitFor(() => expect(screen.queryByText("Tracker · updating…")).toBeNull());
    expect(screen.getAllByText("Tracker · no change")).toHaveLength(2);
    const landed = trackerCallsFor("s1");
    await tick(10_000);
    expect(trackerCallsFor("s1")).toBe(landed);       // nothing pending, nothing asked
  } finally { vi.useRealTimers(); }
}, 15_000);

test("a stale poll answer landing after a newer read does not overwrite it or stop the poll", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    twoPosts();
    withTracker({ entries: PENDING });
    renderCampaign();
    await screen.findByText("Tracker · updating…");
    // The poll's read is issued first and answers last.
    let answerStale: (v: TrackerSummary) => void = () => {};
    vi.mocked(api.getTracker).mockImplementationOnce(() => new Promise((res) => { answerStale = res; }));
    await tick(2000);
    // A newer read (a re-run asked for one) lands first, with news in it.
    vi.mocked(api.getTrackerRecord).mockResolvedValue({
      key: "p-1", status: "ok", flags: TF, snapshot: {}, fields: [], names: {} });
    vi.mocked(api.rerunTrackerFrom).mockResolvedValue(fixture({}));
    fireEvent.click((await screen.findAllByText("Tracker", { selector: "summary" }))[1]);
    const newer: Record<string, TrackerEntry> = { "p-0": PENDING["p-0"],
      "p-1": { status: "ok", changed: [["characters:mara", "visible_mood", "fear"]], flags: TF } };
    vi.mocked(api.getTracker).mockResolvedValueOnce({
      enabled: true, names: { "characters:mara": "Mara" }, moods: {},
      labels: { visible_mood: "Visible mood" },
      keys: [{ index: 0, key: "p-0" }, { index: 1, key: "p-1" }], entries: newer });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    fireEvent.click(await screen.findByRole("button", { name: "Re-run tracker from here" }));
    // p-1 is the second of two tracked posts: a re-run from it covers one post.
    expect(confirm).toHaveBeenCalledWith(
      "Re-run the tracker for 1 post from here? This uses paid tracker and expression selection calls.");
    confirm.mockRestore();
    await screen.findByText("Tracker · Mara: Visible mood → fear");
    // Now the older answer arrives, still showing the old state of p-1.
    const calls = trackerCallsFor("s1");
    await act(async () => {
      answerStale({ enabled: true, names: { "characters:mara": "Mara" }, moods: {},
        labels: { visible_mood: "Visible mood" },
        keys: [{ index: 0, key: "p-0" }, { index: 1, key: "p-1" }], entries: PENDING });
    });
    expect(screen.getByText("Tracker · Mara: Visible mood → fear")).toBeInTheDocument();
    expect(screen.getByText("Tracker · updating…")).toBeInTheDocument();
    // And the poll carries on from it.
    vi.mocked(api.getTracker).mockResolvedValue({
      enabled: true, names: { "characters:mara": "Mara" }, moods: {},
      labels: { visible_mood: "Visible mood" },
      keys: [{ index: 0, key: "p-0" }, { index: 1, key: "p-1" }], entries: { ...newer, "p-0": DONE["p-0"] } });
    await tick(2100);
    await waitFor(() => expect(trackerCallsFor("s1")).toBeGreaterThan(calls));
    await waitFor(() => expect(screen.queryByText("Tracker · updating…")).toBeNull());
  } finally { vi.useRealTimers(); }
}, 15_000);

test("a failed poll backs off instead of asking every two seconds", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    twoPosts();
    withTracker({ entries: PENDING });
    renderCampaign();
    await screen.findByText("Tracker · updating…");
    vi.mocked(api.getTracker).mockRejectedValue(new Error("down"));
    const before = trackerCallsFor("s1");
    await tick(2000);                                  // fails: next wait is 4 s
    await tick(2100);
    expect(trackerCallsFor("s1")).toBe(before + 1);
    await tick(2000);                                  // the 4 s are up
    expect(trackerCallsFor("s1")).toBe(before + 2);
  } finally { vi.useRealTimers(); }
}, 15_000);

test("leaving the view clears the poll", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    twoPosts();
    withTracker({ entries: PENDING });
    const { unmount } = renderCampaign();
    await screen.findByText("Tracker · updating…");
    unmount();
    const before = trackerCallsFor("s1");
    await tick(10_000);
    expect(trackerCallsFor("s1")).toBe(before);
  } finally { vi.useRealTimers(); }
}, 15_000);

test("changing scene clears the old scene's poll and reads the new one", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  try {
    vi.mocked(api.listScenes).mockResolvedValue([
      { id: "s1", title: "First", model: "", created: "", updated: "2026-01-02", date: "" },
      { id: "s2", title: "Second", model: "", created: "", updated: "2026-01-01", date: "" },
    ]);
    vi.mocked(api.getScene).mockImplementation(async (_cid, sid) => ({
      meta: { id: sid, title: sid },
      messages: [{ role: "user", content: `post of ${sid}` }] }));
    vi.mocked(api.getTracker).mockImplementation(async (_cid, sid) => ({
      enabled: true, names: {}, moods: {}, labels: {},
      keys: [{ index: 0, key: "p-0" }],
      entries: { "p-0": { status: sid === "s1" ? "pending" : "ok", changed: [], flags: TF } } }));
    renderCampaign();
    await screen.findByText("Tracker · updating…");
    await openScene(/Second/);
    await screen.findByText("post of s2");
    await screen.findByText("Tracker · no change");
    const before = trackerCallsFor("s1");
    await tick(10_000);
    expect(trackerCallsFor("s1")).toBe(before);
  } finally { vi.useRealTimers(); }
}, 15_000);

test("Retry on an untracked post re-reads the tracker", async () => {
  twoPosts();
  withTracker({ entries: { "p-0": { status: "failed", changed: [], flags: TF, error: "interrupted" },
                           "p-1": { status: "ok", changed: [], flags: TF } } });
  vi.mocked(api.retryTracker).mockResolvedValue(fixture({}));
  vi.mocked(api.getTrackerRecord).mockResolvedValue({
    key: "p-0", status: "failed", flags: TF, snapshot: null, fields: [], names: {} });
  renderCampaign();
  fireEvent.click((await screen.findAllByText("Tracker", { selector: "summary" }))[0]);
  fireEvent.click(await screen.findByRole("button", { name: "Retry" }));
  const before = vi.mocked(api.getTracker).mock.calls.length;
  await waitFor(() => expect(api.retryTracker).toHaveBeenCalledWith("run", "s1", "p-0"));
  await waitFor(() => expect(vi.mocked(api.getTracker).mock.calls.length).toBeGreaterThan(before));
});
