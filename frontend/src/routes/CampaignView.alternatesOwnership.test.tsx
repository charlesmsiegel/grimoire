import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { MemoryRouter, Link } from "react-router-dom";
import { api } from "../api/client";
import type { SceneAlternates, SceneMeta, ScenePage } from "../api/client";
import { installCampaignMocks, ONE_SCENE, openScene, playRoutes, renderCampaign, withPalette } from "../testkit/campaignHarness";
import { fixture } from "../testkit/fixture";
import { relistsAs, ALT } from "../testkit/campaignFixtures";
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



test("a message cannot be edited while a swap is in flight", async () => {
  // the edit carries the index and text of the message the promotion is
  // replacing, so whichever write loses the race discards the other silently
  let release: (v: Awaited<ReturnType<typeof api.pickAlternate>>) => void = () => {};
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  vi.mocked(api.getAlternates).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  vi.mocked(api.pickAlternate).mockImplementation(() => new Promise((res) => { release = res; }));
  renderCampaign();
  await screen.findByText("2/2");

  fireEvent.click(screen.getByRole("button", { name: /previous alternate/i }));

  // no form can be opened for the duration — and the reverse order is covered
  // by `a swap is refused while an edit form is open`, since the two guards
  // together mean an edit and a swap can never overlap in either direction
  const edit = await screen.findByRole("button", { name: /edit message 1/i });
  expect(edit).toBeDisabled();
  fireEvent.click(edit);
  expect(screen.queryByRole("textbox", { name: /edit message/i })).toBeNull();
  expect(api.editMessage).not.toHaveBeenCalled();

  release({ ok: true });
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
  expect(await screen.findByRole("button", { name: /edit message 2/i })).toBeEnabled();
});

test("alternates stay hidden until the scene's own transcript has landed", async () => {
  // the scope tracked the SELECTED id, not the id of the transcript on screen —
  // so B's set could render its picker against A's posts, and clicking it would
  // promote a variant in B while the user was still reading A
  let landB: (v: ScenePage) => void = () => {};
  vi.mocked(api.listScenes).mockResolvedValue([
    { id: "s1", title: "One", model: "", created: "", updated: "", date: "" },
    { id: "s2", title: "Two", model: "", created: "", updated: "", date: "" },
  ]);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  vi.mocked(api.getAlternates).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  renderCampaign();
  await screen.findByText("2/2");

  // s2's alternates resolve immediately; its transcript is still in flight
  vi.mocked(api.getScene).mockImplementationOnce(
    () => new Promise((res) => { landB = res; }));
  await openScene(/^Two$/);
  await waitFor(() => expect(api.getAlternates).toHaveBeenCalledWith("run", "s2"));

  expect(screen.queryByText("2/2")).toBeNull();          // not against s1's posts
  expect(screen.queryByRole("button", { name: /alternate/i })).toBeNull();

  landB(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  expect(await screen.findByText("2/2")).toBeInTheDocument();
});

test("the old set stops offering itself the moment a reroll clears the reply", async () => {
  // matching tokens only prove a set and a FETCH describe the same scene — the
  // optimistic truncation changes the posts under both without touching either.
  // The window is after the stream ends and before the refresh lands: `busy` is
  // already false, so the gutter is back, but the reply the set is keyed to is
  // still off the screen — and the picker drops onto the user post above it.
  let landRefresh: (v: ScenePage) => void = () => {};
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  vi.mocked(api.getAlternates).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  vi.mocked(api.regenerate).mockImplementation(async () => {});   // ends, lands nothing
  renderCampaign();
  await screen.findByText("2/2");

  // BOTH halves of the post-stream refresh are held open. That is the state the
  // token pair cannot see: the old set and the old transcript still agree with
  // each other, so they stay mutually valid while the posts under them have
  // already changed.
  let landAlts: (v: SceneAlternates) => void = () => {};
  vi.mocked(api.getScene).mockImplementationOnce(
    () => new Promise((res) => { landRefresh = res; }));
  vi.mocked(api.getAlternates).mockImplementationOnce(
    () => new Promise((res) => { landAlts = res; }));
  fireEvent.click(screen.getByRole("button", { name: /^reroll$/i }));
  fireEvent.click(await screen.findByRole("button", { name: /reroll ▸/i }));

  await waitFor(() => expect(screen.queryByText(/a reply/)).toBeNull());
  expect(screen.queryByText("2/2")).toBeNull();
  expect(screen.queryByRole("button", { name: /alternate/i })).toBeNull();

  landRefresh(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "second take" }] }));
  landAlts({ active: 1, alternates: [ALT("a reply"), ALT("second take")] });
  expect(await screen.findByText("2/2")).toBeInTheDocument();
});

test("whitespace-only narration is an empty slot, and still offers Retry", async () => {
  // the backend persists a partial only when `watcher.narration.strip()` is
  // nonempty, so blank deltas leave the slot empty — reading them as a landed
  // partial takes away the one button that refills it
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  vi.mocked(api.getAlternates).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  const failStream = vi.fn<() => Promise<void>>().mockRejectedValue({ detail: "upstream is down" });
  vi.mocked(api.regenerate).mockImplementation(async (..._a) => {
    _a[2]({ delta: "  \n " });
    await failStream();
  });
  renderCampaign();
  await screen.findByText("2/2");

  fireEvent.click(screen.getByRole("button", { name: /^reroll$/i }));
  fireEvent.click(await screen.findByRole("button", { name: /reroll ▸/i }));

  expect(await screen.findByText("upstream is down")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /^retry$/i })).toBeInTheDocument();
});

test("a date rename that lands after a scene switch does not yank the reader back", async () => {
  // the callback belongs to the scene that asked for the stamp; if the reader
  // has moved on, forcing that scene back would also carry the turn state of
  // the one they left onto it
  let landList: (v: SceneMeta[]) => void = () => {};
  const TWO = [
    { id: "s1", title: "One", model: "", created: "", updated: "", date: "" },
    { id: "s2", title: "Two", model: "", created: "", updated: "", date: "" },
  ];
  vi.mocked(api.listScenes).mockResolvedValue(TWO);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [] }));  // CastPanel shows
  renderCampaign();
  await screen.findByText("stub-datestamp");

  // s1's inspector reports the stamp; its re-list is still in flight
  vi.mocked(api.listScenes).mockImplementationOnce(
    () => new Promise((res) => { landList = res; }));
  fireEvent.click(screen.getByText("stub-datestamp"));
  // …and the reader moves to s2 before it settles
  await openScene(/^Two$/);
  await waitFor(() => expect(api.getScene).toHaveBeenCalledWith("run", "s2", expect.anything()));
  const after = vi.mocked(api.getScene).mock.calls.length;

  landList(TWO);

  await waitFor(() => expect(vi.mocked(api.listScenes).mock.results.length).toBeGreaterThan(1));
  expect(vi.mocked(api.getScene).mock.calls.slice(after)
    .filter((c) => c[1] === "s10")).toHaveLength(0);
});

test("a rename keeps the turn state — it is not a scene switch", async () => {
  // a rename mints a new id, so the refresh reads as a switch and throws away
  // state that belongs to the turn: an open roll form, and the one-shot
  // response preset picked for the next reply. Same scene, same reader; only
  // the filename moved.
  relistsAs(ONE_SCENE, [{ id: "s1-renamed", title: "Old", model: "", created: "", updated: "", date: "" }]);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  vi.mocked(api.renameScene).mockResolvedValue(fixture({ id: "s1-renamed" }));
  renderCampaign();
  await screen.findByText(/a reply/);

  fireEvent.click(screen.getByRole("button", { name: /roll dice/i }));
  expect(await screen.findByLabelText(/roll label/i)).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: /rename/i }));
  const box = screen.getByDisplayValue("Old");
  fireEvent.change(box, { target: { value: "Renamed" } });
  fireEvent.keyDown(box, { key: "Enter" });
  await waitFor(() => expect(api.renameScene).toHaveBeenCalled());

  // the form the reader was filling in is still open
  expect(screen.getByLabelText(/roll label/i)).toBeInTheDocument();
});

test("a rename landing after a scene switch does not refresh over the new scene", async () => {
  // `wasActive` from the render-captured `activeId` is still true after the
  // await, so the handler would select the renamed scene back over the one the
  // reader moved to — and as a *rename refresh*, carrying its turn state across
  let landRename: (v: Awaited<ReturnType<typeof api.renameScene>>) => void = () => {};
  vi.mocked(api.listScenes).mockResolvedValue([
    { id: "s1", title: "One", model: "", created: "", updated: "", date: "" },
    { id: "s2", title: "Two", model: "", created: "", updated: "", date: "" },
  ]);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  vi.mocked(api.renameScene).mockImplementation(
    () => new Promise((res) => { landRename = res; }));
  renderCampaign();
  await screen.findByText(/a reply/);

  fireEvent.click(screen.getByRole("button", { name: /rename scene/i }));
  const box = screen.getByDisplayValue("One");
  fireEvent.change(box, { target: { value: "Renamed" } });
  fireEvent.keyDown(box, { key: "Enter" });
  // the reader moves on while the PUT is still in flight
  await openScene(/^Two$/);
  await waitFor(() => expect(api.getScene).toHaveBeenCalledWith("run", "s2", expect.anything()));
  const after = vi.mocked(api.getScene).mock.calls.length;

  landRename(fixture({ id: "s1-renamed" }));

  await waitFor(() => expect(vi.mocked(api.listScenes).mock.results.length).toBeGreaterThan(1));
  expect(vi.mocked(api.getScene).mock.calls.slice(after)
    .filter((c) => c[1] === "s1-renamed")).toHaveLength(0);
});

test("a rename re-reads the transcript, not just the ids pointing at it", async () => {
  // a swap in flight against the OLD id finds `activeIdRef` already moved on
  // and skips its own refresh — so without this the pre-swap posts stay on
  // screen with the renamed set's counter on top of them
  relistsAs(ONE_SCENE, [{ id: "s1-renamed", title: "Old", model: "", created: "", updated: "", date: "" }]);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  vi.mocked(api.getAlternates).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  vi.mocked(api.renameScene).mockResolvedValue(fixture({ id: "s1-renamed" }));
  renderCampaign();
  await screen.findByText("2/2");
  const before = vi.mocked(api.getScene).mock.calls.length;

  fireEvent.click(screen.getByRole("button", { name: /rename/i }));
  const box = screen.getByDisplayValue("Old");
  fireEvent.change(box, { target: { value: "Renamed" } });
  fireEvent.keyDown(box, { key: "Enter" });

  await waitFor(() =>
    expect(vi.mocked(api.getScene).mock.calls.length).toBeGreaterThan(before));
  expect(vi.mocked(api.getScene).mock.lastCall?.[1]).toBe("s1-renamed");
});

test("a failed swap re-reads the scene, in case it emptied the slot", async () => {
  // `promote` removes the live run then appends the chosen one; if the append
  // failed the transcript no longer holds the reply on screen, and every index
  // below it is wrong
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  vi.mocked(api.getAlternates).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  vi.mocked(api.pickAlternate).mockRejectedValue({ detail: "no space left on device" });
  renderCampaign();
  await screen.findByText("2/2");
  const before = vi.mocked(api.getScene).mock.calls.length;

  fireEvent.click(screen.getByRole("button", { name: /previous alternate/i }));

  expect(await screen.findByText("no space left on device")).toBeInTheDocument();
  await waitFor(() =>
    expect(vi.mocked(api.getScene).mock.calls.length).toBeGreaterThan(before));
});

test("a swap whose read-back fails is not reported as a failed swap", async () => {
  // The POST committed: the take really did change. Saying the swap failed is
  // wrong twice — it denies a change that happened, and the recovery it implies
  // is to do it again. What is actually wrong is the transcript on screen, so
  // the set that indexes it stops being offered.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  vi.mocked(api.getAlternates).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  renderCampaign();
  await screen.findByText("2/2");
  // every read from here on fails, including the retry of the read-back
  vi.mocked(api.getScene).mockRejectedValue(
    Object.assign(new Error("boom"), { detail: "scene read failed" }));

  fireEvent.click(screen.getByRole("button", { name: /previous alternate/i }));

  const banner = await screen.findByText(/scene read failed/);
  expect(banner.textContent).toMatch(/swapped/i);      // the take DID change
  // and the counter is withheld, so nothing acts on the stale transcript
  await waitFor(() => expect(screen.queryByText("2/2")).toBeNull());
});

test("picking an alternate stops Retry from repeating the failed reroll", async () => {
  // The swap IS the recovery the user chose. Leaving the failed reroll's banner
  // up offers a Retry that regenerates over the take they just picked, with the
  // guidance that failed — undoing the recovery.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  vi.mocked(api.getAlternates).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  // an error FRAME, not a rejection: the backend ran its handler before sending
  // it, so there is no flush to wait out and the swap is available immediately
  vi.mocked(api.regenerate).mockImplementation(async (...args) => {
    const onEvent = args[2];
    onEvent(fixture({ error: { detail: "upstream is down" } }));
  });
  renderCampaign();
  await screen.findByText("2/2");
  fireEvent.click(await screen.findByTitle("Reroll"));
  fireEvent.click(await screen.findByRole("button", { name: /reroll ▸/i }));
  await screen.findByText("upstream is down");

  fireEvent.click(await screen.findByRole("button", { name: /previous alternate/i }));

  await waitFor(() => expect(api.pickAlternate).toHaveBeenCalled());
  await waitFor(() => expect(screen.queryByText("upstream is down")).toBeNull());
  expect(screen.queryByRole("button", { name: /^Retry$/ })).toBeNull();
});

test("a reroll that streams nothing still offers Retry", async () => {
  // the backend archived and removed the old reply, so the slot is EMPTY and
  // the transcript ends on the player's post — no gutter ↻ to fall back on.
  // `/retry` streams into that slot rather than appending, so Retry is both
  // safe and the only way forward.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  vi.mocked(api.getAlternates).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  vi.mocked(api.regenerate).mockRejectedValue({ detail: "upstream is down" });
  renderCampaign();
  await screen.findByText("2/2");

  fireEvent.click(screen.getByRole("button", { name: /^reroll$/i }));
  fireEvent.click(await screen.findByRole("button", { name: /reroll ▸/i }));

  expect(await screen.findByText("upstream is down")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /^retry$/i })).toBeInTheDocument();
});

test("a reroll's new set stays hidden until the reroll's own transcript lands", async () => {
  // the refresh after a reroll is a SAME-scene select, so campaign and scene
  // both still match — and reroll has optimistically dropped the trailing run,
  // so the messages on screen end at the user post. A set matched against them
  // hangs the picker off that post and promotes from it.
  let landRefresh: (v: ScenePage) => void = () => {};
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "first take" }] }));
  vi.mocked(api.getAlternates).mockResolvedValue({ active: 0, alternates: [ALT("first take")] });
  vi.mocked(api.regenerate).mockImplementation(async (_c, _s, onEvent) => {
    onEvent({ delta: "second take" });
  });
  renderCampaign();
  await screen.findByText(/first take/);

  // the reroll's refresh: its alternates land first, its transcript is held
  vi.mocked(api.getAlternates).mockResolvedValue({
    active: 1, alternates: [ALT("first take"), ALT("second take")] });
  vi.mocked(api.getScene).mockImplementationOnce(
    () => new Promise((res) => { landRefresh = res; }));
  fireEvent.click(screen.getByRole("button", { name: /^reroll$/i }));
  fireEvent.click(await screen.findByRole("button", { name: /reroll ▸/i }));
  await waitFor(() => expect(api.getAlternates).toHaveBeenCalledTimes(2));

  // the trailing reply is gone from the screen; the set must not offer itself
  expect(screen.queryByText("2/2")).toBeNull();
  expect(screen.queryByRole("button", { name: /alternate/i })).toBeNull();

  landRefresh(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "second take" }] }));
  expect(await screen.findByText("2/2")).toBeInTheDocument();
});

test("a colliding scene id across campaigns does not expose B's set on A's posts", async () => {
  // scene ids repeat between campaigns, so A→B with the same id reads as a
  // refresh rather than a switch — a sid-only key for the loaded transcript
  // never notices the posts on screen are still A's
  let landB: (v: ScenePage) => void = () => {};
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  vi.mocked(api.getAlternates).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  render(
    <MemoryRouter initialEntries={["/campaigns/run/scenes/s1"]}>
      {withPalette(<>
        <Link to="/campaigns/other/scenes/whichever">switch campaign</Link>
        {playRoutes()}
      </>)}
    </MemoryRouter>,
  );
  await screen.findByText("2/2");

  // B selects the same sid; its alternates land first, its transcript is held
  vi.mocked(api.getScene).mockImplementationOnce(
    () => new Promise((res) => { landB = res; }));
  fireEvent.click(screen.getByText("switch campaign"));
  await waitFor(() => expect(api.getAlternates).toHaveBeenCalledWith("other", "s1"));

  expect(screen.queryByText("2/2")).toBeNull();
  expect(screen.queryByRole("button", { name: /alternate/i })).toBeNull();

  landB(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  expect(await screen.findByText("2/2")).toBeInTheDocument();
});

test("a failed reroll does not offer the Retry that would discard its set", async () => {
  // Retry appends a generation, which moves the slot and retires the set the
  // reroll was building — including the reply it parked. The gutter's own ↻ is
  // still there and stays in the slot, so it is the recovery on offer.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  vi.mocked(api.getAlternates).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  // narration lands, THEN the error: the backend persisted that partial, so the
  // slot is full and Retry would append a second generation past it
  const failStream = vi.fn<() => Promise<void>>().mockRejectedValue({ detail: "upstream is down" });
  vi.mocked(api.regenerate).mockImplementation(async (..._a) => {
    const onEvent = _a[2];
    onEvent({ delta: "Ice on the" });
    await failStream();
  });
  renderCampaign();
  await screen.findByText("2/2");

  fireEvent.click(screen.getByRole("button", { name: /^reroll$/i }));
  fireEvent.click(await screen.findByRole("button", { name: /reroll ▸/i }));

  expect(await screen.findByText("upstream is down")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /^retry$/i })).toBeNull();
});

test("a failed swap does not offer to generate", async () => {
  // the banner's Retry generates, which appends a consecutive reply — moving the
  // slot and hiding the very set the user was trying to cycle
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  vi.mocked(api.getAlternates).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  vi.mocked(api.pickAlternate).mockRejectedValue({ detail: "alternate not found" });
  renderCampaign();
  await screen.findByText("2/2");

  fireEvent.click(screen.getByRole("button", { name: /previous alternate/i }));

  expect(await screen.findByText("alternate not found")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /^retry$/i })).toBeNull();
});

test("a swap is refused while a cancelled turn is still flushing", async () => {
  // `busy` clears the moment the socket is torn down, but the scene stays
  // locked until the backend's shielded flush lands — and a swap in that window
  // races the abort hook for the partial it is about to persist: landing first
  // it loses that text, landing second it parks it. Same rule every other
  // transcript mutation outside `runStream` reads (#95).
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  vi.mocked(api.getAlternates).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  vi.mocked(api.chat).mockImplementation(hangingChat(["a streamed fragment"]));
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));

  // the gutter is back (the stream is over) while the flush poll still runs
  const back = await screen.findByRole("button", { name: /previous alternate/i });
  expect(back).toBeDisabled();
  fireEvent.click(back);
  expect(api.pickAlternate).not.toHaveBeenCalled();
});

test("a swap is refused while an edit form is open", async () => {
  // the guard runs both ways: an edit opened before the swap outlives it, and
  // after the refresh the form rebinds to the promoted variant at the same
  // absolute index, so Save would overwrite it with the old variant's draft
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  vi.mocked(api.getAlternates).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  renderCampaign();
  await screen.findByText("2/2");

  fireEvent.click(screen.getByRole("button", { name: /edit message 1/i }));

  const prev = screen.getByRole("button", { name: /previous alternate/i });
  expect(prev).toBeDisabled();
  fireEvent.click(prev);
  expect(api.pickAlternate).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
  expect(screen.getByRole("button", { name: /previous alternate/i })).toBeEnabled();
});

test("a rename retires the in-flight alternates fetch instead of relabelling it", async () => {
  // the outstanding GET still carries the OLD scene id, so its rejection says
  // nothing about the renamed scene — honouring it would clear a valid set
  let reject: (e: unknown) => void = () => {};
  relistsAs(ONE_SCENE, [{ id: "s1-renamed", title: "Old", model: "", created: "", updated: "", date: "" }]);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  vi.mocked(api.getAlternates).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  vi.mocked(api.renameScene).mockResolvedValue({ id: "s1-renamed", title: "New" });
  renderCampaign();
  await screen.findByText("2/2");

  // re-select the scene so a GET for the OLD id is outstanding across the
  // rename; the fetch the rename issues for the new id still resolves normally
  vi.mocked(api.getAlternates).mockImplementationOnce(
    () => new Promise((_res, rej) => { reject = rej; }));
  await openScene(/^Old$/);
  await waitFor(() => expect(api.getAlternates).toHaveBeenCalledTimes(2));

  fireEvent.click(screen.getByRole("button", { name: /rename/i }));
  const input = screen.getByDisplayValue("Old");
  fireEvent.change(input, { target: { value: "New" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(api.renameScene).toHaveBeenCalled());

  reject(new Error("scene not found"));   // the old-id GET, answered after the move
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });

  expect(await screen.findByText("2/2")).toBeInTheDocument();
});

test("a swap still refreshes after the active scene was renamed", async () => {
  // renaming mints a new scene id without going through selectScene, so the
  // "is this scene still selected" ref has to move with it or the refresh is
  // skipped and the transcript keeps showing the pre-swap variant
  relistsAs(ONE_SCENE, [{ id: "s1-renamed", title: "Old", model: "", created: "", updated: "", date: "" }]);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  vi.mocked(api.getAlternates).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  vi.mocked(api.renameScene).mockResolvedValue({ id: "s1-renamed", title: "New" });
  renderCampaign();
  await screen.findByText("2/2");
  fireEvent.click(screen.getByRole("button", { name: /rename/i }));
  const input = screen.getByDisplayValue("Old");
  fireEvent.change(input, { target: { value: "New" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(api.renameScene).toHaveBeenCalled());
  vi.mocked(api.getScene).mockClear();

  fireEvent.click(screen.getByRole("button", { name: /previous alternate/i }));

  await waitFor(() => expect(api.pickAlternate).toHaveBeenCalledWith("run", "s1-renamed", "id-old"));
  await waitFor(() => expect(api.getScene).toHaveBeenCalled());   // the refresh ran
});

test("a previous scene's alternates fetch cannot clear the current scene's by failing", async () => {
  // the scoped-success guard cannot cover the reject path: a late rejection
  // would otherwise reset state that belongs to the scene now on screen
  let rejectFirst: (e: unknown) => void = () => {};
  vi.mocked(api.listScenes).mockResolvedValue([
    { id: "s1", title: "One", model: "", created: "", updated: "", date: "" },
    { id: "s2", title: "Two", model: "", created: "", updated: "", date: "" },
  ]);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  vi.mocked(api.getAlternates)
    .mockImplementationOnce(() => new Promise((_res, rej) => { rejectFirst = rej; }))
    .mockResolvedValue({ active: 1, alternates: [ALT("old"), ALT("a reply")] });
  renderCampaign();
  await openScene(/^Two$/);
  await screen.findByText("2/2");

  rejectFirst(new Error("s1 gave up")); // s1's request, failing late
  // flush the rejection's handler before asserting — `waitFor` would otherwise
  // pass on the state as it stands *now*, before the catch has had a turn
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });

  expect(screen.getByText("2/2")).toBeInTheDocument();
});

test("a slow alternates fetch from the previous scene is ignored", async () => {
  // scene s1's request resolves only after s2 is selected; its indices must not
  // show against s2, where the control swaps by index and would hit the wrong one
  let releaseFirst: (v: SceneAlternates) => void = () => {};
  vi.mocked(api.listScenes).mockResolvedValue([
    { id: "s1", title: "One", model: "", created: "", updated: "", date: "" },
    { id: "s2", title: "Two", model: "", created: "", updated: "", date: "" },
  ]);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  vi.mocked(api.getAlternates)
    .mockImplementationOnce(() => new Promise((res) => { releaseFirst = res; }))
    .mockResolvedValue({ active: 0, alternates: [ALT("only one")] });
  renderCampaign();
  await screen.findByText("a reply");
  await openScene(/^Two$/);
  await waitFor(() => expect(api.getAlternates).toHaveBeenCalledTimes(2));

  releaseFirst({ active: 2, alternates: [ALT("a"), ALT("b"), ALT("c")] }); // s1's, late

  await waitFor(() => expect(screen.queryByText("3/3")).toBeNull());
  expect(screen.queryByRole("button", { name: /alternate/i })).toBeNull();
});
