import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { MemoryRouter, Link } from "react-router-dom";
import { api } from "../api/client";
import type { SceneMeta, ScenePage } from "../api/client";
import { installCampaignMocks, ONE_SCENE, openScene, playRoutes, renderCampaign, withPalette } from "../testkit/campaignHarness";
import { fixture } from "../testkit/fixture";
import { relistsAs, ALT } from "../testkit/campaignFixtures";

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



test("a rerolled post carries a swipe control counting its alternates", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "fresh reply" }] }));
  vi.mocked(api.getAlternates).mockResolvedValue({
    active: 1, alternates: [ALT("old reply"), ALT("fresh reply")] });
  renderCampaign();
  await screen.findByText("fresh reply");
  expect(await screen.findByText("2/2")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: /previous alternate/i }));

  await waitFor(() => expect(api.pickAlternate).toHaveBeenCalledWith("run", "s1", "id-old reply"));
});

test("the counter names the guidance that produced the take on screen", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a colder take" }] }));
  vi.mocked(api.getAlternates).mockResolvedValue({
    active: 1,
    alternates: [ALT("old"), { ...ALT("a colder take"), guidance: "make it colder" }],
  });
  renderCampaign();

  const counter = await screen.findByText("2/2");

  expect(counter).toHaveAttribute("title", expect.stringContaining("make it colder"));
  expect(counter).toHaveAttribute("title", expect.stringContaining("a colder take"));
});

test("the swipe control wraps, so cycling tours the whole set", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "take three" }] }));
  vi.mocked(api.getAlternates).mockResolvedValue({
    active: 2, alternates: [ALT("one"), ALT("two"), ALT("take three")] });
  renderCampaign();
  await screen.findByText("3/3");

  fireEvent.click(screen.getByRole("button", { name: /next alternate/i }));

  await waitFor(() => expect(api.pickAlternate).toHaveBeenCalledWith("run", "s1", "id-one"));
});

test("no swipe control when the live reply is the only variant there is", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  vi.mocked(api.getAlternates).mockResolvedValue({ active: 0, alternates: [ALT("a reply")] });
  renderCampaign();
  await screen.findByText("a reply");
  expect(screen.queryByRole("button", { name: /alternate/i })).toBeNull();
});

test("a reroll whose stream died still offers the reply it parked", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [{ role: "user", content: "hi" }] }));
  vi.mocked(api.getAlternates).mockResolvedValue({ active: null, alternates: [ALT("old reply")] });
  renderCampaign();
  await screen.findByText("–/1");

  fireEvent.click(screen.getByRole("button", { name: /next alternate/i }));

  await waitFor(() => expect(api.pickAlternate).toHaveBeenCalledWith("run", "s1", "id-old reply"));
});

test("a second swipe click is ignored while the first is in flight", async () => {
  // both clicks would otherwise read the same `active` snapshot and send the
  // same index, so two ‹ presses would step back only once
  let release: (v: Awaited<ReturnType<typeof api.pickAlternate>>) => void = () => {};
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "take three" }] }));
  vi.mocked(api.getAlternates).mockResolvedValue({
    active: 2, alternates: [ALT("one"), ALT("two"), ALT("take three")] });
  vi.mocked(api.pickAlternate).mockImplementation(() => new Promise((res) => { release = res; }));
  renderCampaign();
  await screen.findByText("3/3");

  fireEvent.click(screen.getByRole("button", { name: /previous alternate/i }));
  fireEvent.click(screen.getByRole("button", { name: /previous alternate/i }));

  expect(api.pickAlternate).toHaveBeenCalledTimes(1);
  expect(api.pickAlternate).toHaveBeenCalledWith("run", "s1", "id-two");
  release({ ok: true });
});

test("renaming the active scene keeps its alternates on screen", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
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

  // the sidecar moved with the scene file; the control must move with it too
  await waitFor(() => expect(api.renameScene).toHaveBeenCalled());
  expect(await screen.findByText("2/2")).toBeInTheDocument();
});

test("the swipe control renders on a windowed page, where indices are absolute", async () => {
  // a windowed read starts at `offset`, so the per-post index the gutter sees is
  // absolute; matching it against the window-relative one puts the control on
  // the wrong post, or on none at all
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({
    meta: {}, offset: 40, total: 42, has_older: true, has_user_message: true,
    messages: [{ role: "user", content: "hi" }, { role: "assistant", content: "a reply" }],
  }));
  vi.mocked(api.getAlternates).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  renderCampaign();
  await screen.findByText("a reply");

  expect(await screen.findByText("2/2")).toBeInTheDocument();
});

test("a swap in flight does not disable the scene the reader moved to", async () => {
  // `rolling` is component-wide, so an operation belonging to the scene that was
  // left held every control in the scene that was entered — Send, Retry, reroll,
  // edit and roll — until an unrelated request settled.
  vi.mocked(api.listScenes).mockResolvedValue([
    { id: "s1", title: "One", model: "", created: "", updated: "", date: "" },
    { id: "s2", title: "Two", model: "", created: "", updated: "", date: "" },
  ]);
  vi.mocked(api.getScene).mockImplementation(async (_c, s) => fixture({
    meta: {}, messages: [
      { role: "user", content: "hi" },
      { role: "assistant", content: s === "s1" ? "a reply" : "the other scene" }] }));
  vi.mocked(api.getAlternates).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  vi.mocked(api.pickAlternate).mockImplementation(() => new Promise(() => {}));  // never settles
  renderCampaign();
  await screen.findByText("2/2");

  fireEvent.click(screen.getByRole("button", { name: /previous alternate/i }));
  await openScene(/^Two$/);
  await screen.findByText("the other scene");

  expect(screen.getByRole("button", { name: /^Reroll$/i })).not.toBeDisabled();
});

test("End scene is refused while a swap is in flight", async () => {
  // Absorb takes its transcript snapshot once, so a swap committing afterwards
  // means the review summarises the take the reader replaced — and saving it
  // marks the swapped transcript absorbed against narration it never read.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  vi.mocked(api.getAlternates).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  vi.mocked(api.pickAlternate).mockImplementation(() => new Promise(() => {}));  // never settles
  renderCampaign();
  await screen.findByText("2/2");

  fireEvent.click(screen.getByRole("button", { name: /previous alternate/i }));

  await waitFor(() =>
    expect(screen.getByRole("button", { name: /End scene/ })).toBeDisabled());
  fireEvent.click(screen.getByRole("button", { name: /End scene/ }));
  expect(api.absorbScene).not.toHaveBeenCalled();
});

test("a rename whose relist fails still re-reads the renamed transcript", async () => {
  // The re-read is what replaces posts a swap against the OLD id skipped. Behind
  // the rail relist, an unrelated failure there left the reader on the old take
  // under the new id, with edits saving against indices that have shifted.
  vi.mocked(api.listScenes).mockResolvedValueOnce(ONE_SCENE)
    .mockRejectedValue(new Error("relist failed"));
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  vi.mocked(api.renameScene).mockResolvedValue({ id: "s1-renamed", title: "New" });
  renderCampaign();
  await screen.findByText("a reply");
  const before = vi.mocked(api.getScene).mock.calls.length;

  fireEvent.click(screen.getByRole("button", { name: /rename/i }));
  const input = screen.getByDisplayValue("Old");
  fireEvent.change(input, { target: { value: "New" } });
  fireEvent.keyDown(input, { key: "Enter" });

  await waitFor(() =>
    expect(vi.mocked(api.getScene).mock.calls.length).toBeGreaterThan(before));
  expect(vi.mocked(api.getScene).mock.lastCall?.[1]).toBe("s1-renamed");
});

test("a first-date rename whose re-read fails says so instead of going quiet", async () => {
  // The re-read is what replaces posts a swap against the old id skipped, and
  // this path fired it without awaiting: the rejection went nowhere, no banner
  // appeared, and the pre-rename messages stayed on screen under the new id —
  // editable, against indices that have shifted.
  relistsAs(ONE_SCENE, [{ id: "s1-dated", title: "Old", model: "", created: "", updated: "", date: "" }]);
  // only the read for the NEW id fails, so nothing else in the view is disturbed
  vi.mocked(api.getScene).mockImplementation(async (_c, s) => {
    if (s === "s1-dated") throw Object.assign(new Error("boom"), { detail: "scene read failed" });
    return fixture({ meta: {}, messages: [
      { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  });
  vi.mocked(api.getSceneDatetime).mockResolvedValue(
    { current: null, history: [], suggested: "2026-07-06" });
  vi.mocked(api.getCalendarMonths).mockResolvedValue({ months: [
    { key: "07", name: "July", days: 31 }] });
  vi.mocked(api.setSceneDatetime).mockResolvedValue(fixture({ id: "s1-dated" }));
  renderCampaign();
  await screen.findByText("a reply");

  // the first date set re-slugs the file, so this is the rename path. The
  // control lives in the inspector, which is a panel now.
  fireEvent.click(screen.getByRole("button", { name: /what the model saw/i }));
  const setDate = await screen.findByRole("button", { name: /set date/i });
  await waitFor(() => expect(setDate).not.toBeDisabled());
  fireEvent.click(setDate);
  await waitFor(() => expect(api.setSceneDatetime).toHaveBeenCalled());

  expect(await screen.findByText(/scene read failed/)).toBeTruthy();
});

test("a rename whose relist is slow does not pull the reader back", async () => {
  // The rename PUT is not the only await in that path. Deciding whether to
  // refresh from a flag captured before the relist means a reader who moved on
  // during it gets dragged back: `selectScene` calls `setActive`.
  let finishRelist: (v: SceneMeta[]) => void = () => {};
  vi.mocked(api.listScenes).mockResolvedValueOnce([
    { id: "s1", title: "One", model: "", created: "", updated: "", date: "" },
    { id: "s2", title: "Two", model: "", created: "", updated: "", date: "" },
  ]).mockImplementation(() => new Promise((res) => { finishRelist = res; }));
  vi.mocked(api.getScene).mockImplementation(async (_c, s) => fixture({
    meta: {}, messages: [
      { role: "user", content: "hi" },
      { role: "assistant", content: s === "s2" ? "the other scene" : "a reply" }] }));
  vi.mocked(api.renameScene).mockResolvedValue({ id: "s1-renamed", title: "New" });
  renderCampaign();
  await screen.findByText("a reply");

  fireEvent.click(screen.getByRole("button", { name: /rename scene/i }));
  const input = screen.getByDisplayValue("One");
  fireEvent.change(input, { target: { value: "New" } });
  fireEvent.keyDown(input, { key: "Enter" });
  // the PUT has landed and the id is adopted; the relist is still open
  await waitFor(() => expect(api.listScenes).toHaveBeenCalledTimes(2));
  await openScene(/^Two$/);
  await waitFor(() => expect(vi.mocked(api.getScene).mock.calls.map((c) => c[1])).toContain("s2"));
  finishRelist([{ id: "s1-renamed", title: "New", model: "", created: "", updated: "", date: "" },
                { id: "s2", title: "Two", model: "", created: "", updated: "", date: "" }]);
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });

  // no read for the renamed scene after the reader left it: `selectScene` calls
  // `setActive`, so one would have navigated them back and loaded it over Two
  expect(vi.mocked(api.getScene).mock.calls.map((c) => c[1])).toEqual(["s1", "s2"]);
});

test("a stale swap succeeding does not clear the new scene's banner", async () => {
  // The scoped latch deliberately leaves the destination usable, so it can
  // raise a failure of its own while the old scene's POST is still open. That
  // POST landing must not wipe a banner belonging to a scene it never touched.
  let release: (v: Awaited<ReturnType<typeof api.pickAlternate>>) => void = () => {};
  vi.mocked(api.listScenes).mockResolvedValue([
    { id: "s1", title: "One", model: "", created: "", updated: "", date: "" },
    { id: "s2", title: "Two", model: "", created: "", updated: "", date: "" },
  ]);
  vi.mocked(api.getScene).mockImplementation(async (_c, s) => fixture({
    meta: {}, messages: [
      { role: "user", content: "hi" },
      { role: "assistant", content: s === "s1" ? "a reply" : "the other scene" }] }));
  vi.mocked(api.getAlternates).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  vi.mocked(api.pickAlternate).mockImplementation(() => new Promise((res) => { release = res; }));
  renderCampaign();
  await screen.findByText("2/2");
  fireEvent.click(screen.getByRole("button", { name: /previous alternate/i }));
  await openScene(/^Two$/);
  await screen.findByText("the other scene");

  // scene Two raises its own failure while One's swap is still open
  vi.mocked(api.chat).mockRejectedValue({ detail: "two is broken" });
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByText("two is broken");

  release({ ok: true });   // One's swap lands after the move
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });

  expect(screen.getByText("two is broken")).toBeInTheDocument();
});

test("a rename whose relist fails keeps an offscreen scene offscreen", async () => {
  // `adoptSceneId` re-points `activeId` to the new id, but the rail's metadata
  // is keyed by the old one. Tolerating a failed relist made that a state the
  // reader sits in: `pcless` is derived from the row, so it silently became
  // false — the Offscreen badge gone and the PC composer offered for a scene
  // the backend still treats as offscreen.
  const offscreen = [{ id: "s1", title: "Cabal", model: "", created: "", updated: "", date: "", pcless: true }];
  vi.mocked(api.listScenes).mockResolvedValueOnce(offscreen)
    .mockRejectedValue(new Error("relist failed"));
  vi.mocked(api.renameScene).mockResolvedValue({ id: "s1-renamed", title: "New" });
  renderCampaign();
  await screen.findByPlaceholderText(/direct the scene/i);

  fireEvent.click(screen.getByRole("button", { name: /rename/i }));
  const input = screen.getByDisplayValue("Cabal");
  fireEvent.change(input, { target: { value: "New" } });
  fireEvent.keyDown(input, { key: "Enter" });

  await screen.findByText(/could not be refreshed/);
  expect(screen.getByPlaceholderText(/direct the scene/i)).toBeInTheDocument();
  expect(screen.getAllByText("Offscreen").length).toBeGreaterThan(0);
});

test("a swap retires the roll proposal it supersedes", async () => {
  // The backend supersedes the pending decision as part of the swap, so a chip
  // left enabled adjudicates narration that is no longer on screen — and its
  // 409 surfaces a Retry that generates instead.
  let release: (v: Awaited<ReturnType<typeof api.pickAlternate>>) => void = () => {};
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  vi.mocked(api.getAlternates).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  vi.mocked(api.getRollProposal).mockResolvedValue({
    record: { id: "p1", status: "pending", resolution: null,
              payload: { id: "p1", check: "wits", check_label: "Wits", problems: [] } } });
  vi.mocked(api.pickAlternate).mockImplementation(() => new Promise((res) => { release = res; }));
  renderCampaign();
  await screen.findByText("2/2");
  const rollIt = await screen.findByRole("button", { name: "Roll it" });

  fireEvent.click(screen.getByRole("button", { name: /previous alternate/i }));

  // in flight: the decision cannot be adjudicated against a take being replaced
  await waitFor(() => expect(rollIt).toBeDisabled());

  // committed: the backend retired it, so the chip goes without waiting for a read
  vi.mocked(api.getRollProposal).mockResolvedValue({ record: null });
  release({ ok: true });
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: "Roll it" })).toBeNull());
});

test("a failed post-swap read does not retry the reader back onto the old scene", async () => {
  // The retry is a second `selectScene`, and `selectScene` calls `setActive` —
  // so once the reader has moved on it does not merely refresh a scene nobody
  // is looking at, it navigates them back to it.
  let rejectRefresh: (v: unknown) => void = () => {};
  vi.mocked(api.listScenes).mockResolvedValue([
    { id: "s1", title: "One", model: "", created: "", updated: "", date: "" },
    { id: "s2", title: "Two", model: "", created: "", updated: "", date: "" },
  ]);
  const page = (c: string) => fixture<ScenePage>({ meta: {}, messages: [
    { role: "user", content: "hi" },
    { role: "assistant", content: c === "s1" ? "a reply" : "the other scene" }] });
  let firstLoadDone = false;
  vi.mocked(api.getScene).mockImplementation(async (_c, s) => {
    // the post-swap read-back — the only s1 read after the initial load
    if (s === "s1" && firstLoadDone) return new Promise((_r, rej) => { rejectRefresh = rej; });
    if (s === "s1") firstLoadDone = true;
    return page(s);
  });
  vi.mocked(api.getAlternates).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  renderCampaign();
  await screen.findByText("2/2");

  fireEvent.click(screen.getByRole("button", { name: /previous alternate/i }));
  await waitFor(() => expect(api.pickAlternate).toHaveBeenCalled());
  await openScene(/^Two$/);
  await screen.findByText("the other scene");
  const reads = vi.mocked(api.getScene).mock.calls.length;

  rejectRefresh({ detail: "scene read failed" });   // s1's read-back gives up
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });

  expect(vi.mocked(api.getScene).mock.calls.length).toBe(reads);   // no retry issued
  expect(screen.getByRole("heading", { name: /^Two$/ })).toBeInTheDocument();
  expect(screen.queryByText(/could not be re-read/)).toBeNull();
});

test("a swap that fails after the user moved on does not banner the new scene", async () => {
  // Switching scenes clears the banner on purpose — one scene's failure must
  // not offer its Retry against another. A swap rejecting afterwards put it
  // straight back, under a scene it has nothing to do with.
  let reject: (v: unknown) => void = () => {};
  vi.mocked(api.listScenes).mockResolvedValue([
    { id: "s1", title: "One", model: "", created: "", updated: "", date: "" },
    { id: "s2", title: "Two", model: "", created: "", updated: "", date: "" },
  ]);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  vi.mocked(api.getAlternates).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  vi.mocked(api.pickAlternate).mockImplementation(() => new Promise((_r, rej) => { reject = rej; }));
  renderCampaign();
  await screen.findByText("2/2");
  fireEvent.click(screen.getByRole("button", { name: /previous alternate/i }));
  await openScene(/^Two$/);
  await waitFor(() => expect(screen.getByRole("heading", { name: /^Two$/ })).toBeInTheDocument());

  reject({ detail: "no space left on device" });   // s1's swap fails after the move
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });

  expect(screen.queryByText("no space left on device")).toBeNull();
});

test("switching scenes while a swap is in flight does not yank the user back", async () => {
  let release: (v: Awaited<ReturnType<typeof api.pickAlternate>>) => void = () => {};
  vi.mocked(api.listScenes).mockResolvedValue([
    { id: "s1", title: "One", model: "", created: "", updated: "", date: "" },
    { id: "s2", title: "Two", model: "", created: "", updated: "", date: "" },
  ]);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  vi.mocked(api.getAlternates).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  vi.mocked(api.pickAlternate).mockImplementation(() => new Promise((res) => { release = res; }));
  renderCampaign();
  await screen.findByText("2/2");
  fireEvent.click(screen.getByRole("button", { name: /previous alternate/i }));
  await openScene(/^Two$/);
  await waitFor(() => expect(screen.getByRole("heading", { name: /^Two$/ })).toBeInTheDocument());

  release({ ok: true }); // s1's swap finishes after the user moved on
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });

  expect(screen.getByRole("heading", { name: /^Two$/ })).toBeInTheDocument();
});

test("another campaign's alternates are not offered while its set is still loaded", async () => {
  // React Router reuses this component between /campaigns/A and /campaigns/B,
  // so during the switch `cid` is already B while `activeId` and the loaded set
  // are still A's — a sid-only gate compares two stale values and passes.
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

  // navigate to another campaign; the component stays mounted and only `cid`
  // changes, and this campaign's own fetches never land
  vi.mocked(api.getAlternates).mockImplementation(() => new Promise(() => {}));
  fireEvent.click(screen.getByText("switch campaign"));

  await waitFor(() => expect(screen.queryByText("2/2")).toBeNull());
  expect(screen.queryByRole("button", { name: /alternate/i })).toBeNull();
});

test("a swap that finishes after a campaign switch does not load the old campaign", async () => {
  // scene ids repeat between campaigns, so "same sid" is not "same scene": if
  // B happens to select s1 too, a sid-only completion guard passes and the
  // stale closure refreshes s1 out of A, showing A's transcript under B.
  let release: (v: Awaited<ReturnType<typeof api.pickAlternate>>) => void = () => {};
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE); // both campaigns: s1
  vi.mocked(api.getScene).mockImplementation(async (c) => fixture({
    meta: {}, messages: [
      { role: "user", content: "hi" },
      { role: "assistant", content: c === "run" ? "a reply" : "another campaign's reply" },
    ],
  }));
  vi.mocked(api.getAlternates).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  vi.mocked(api.pickAlternate).mockImplementation(() => new Promise((res) => { release = res; }));
  render(
    <MemoryRouter initialEntries={["/campaigns/run/scenes/s1"]}>
      {withPalette(<>
        <Link to="/campaigns/other/scenes/whichever">switch campaign</Link>
        {playRoutes()}
      </>)}
    </MemoryRouter>,
  );
  await screen.findByText("2/2");
  fireEvent.click(screen.getByRole("button", { name: /previous alternate/i }));
  fireEvent.click(screen.getByText("switch campaign"));
  await screen.findByText("another campaign's reply");

  release({ ok: true }); // run's swap finishes after the user moved on
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });

  expect(screen.getByText("another campaign's reply")).toBeInTheDocument();
  expect(screen.queryByText("a reply")).toBeNull();
});
