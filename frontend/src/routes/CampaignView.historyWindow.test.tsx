import { render, screen, fireEvent, waitFor, act, within } from "@testing-library/react";
import { MemoryRouter, Link } from "react-router-dom";
import { api, ApiError } from "../api/client";
import type { SceneMeta } from "../api/client";
import { here, Here, installCampaignMocks, ONE_SCENE, openScene, playRoutes, renderCampaign, withPalette } from "../testkit/campaignHarness";
import { fixture } from "../testkit/fixture";
import { SCENES_WITH_DATE } from "../testkit/campaignFixtures";

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



test("the inspector is a panel behind the composer's link, not a permanent third column", async () => {
  // It used to be a column that was open by definition. What it answers — what
  // went into the last prompt and what was dropped to fit — is a question about
  // a turn, so it belongs beside the control that takes the next one.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  await screen.findByRole("heading", { name: /^Old$/ });
  expect(screen.queryByText("Active characters")).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: /what the model saw/i }));
  await screen.findByText("Active characters");
  fireEvent.click(screen.getByRole("button", { name: /hide what the model saw/i }));
  expect(screen.queryByText("Active characters")).not.toBeInTheDocument();
});

test("the continuity the inspector used to hold is in the column, not behind a toggle", async () => {
  // Cast, threads and commitments are what the app is for. They are not a
  // panel any more; nothing has to be reopened to check them.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getCast).mockResolvedValue([
    { kind: "characters", id: "aud", role: "npc", name: "Sister Aud" }]);
  vi.mocked(api.sceneBriefing).mockResolvedValue({
    focus: ["Sister Aud"],
    plot: [{ id: "t1", title: "The priory's debt", status: "open", last_scene: "iv",
             latest_beat: "", involves: ["Sister Aud"] }],
    commitments: [{ id: "c1", title: "The Reeve will call it in", status: "open",
                    kind: "threat", due: "by the turn of the tide", last_scene: "iv",
                    latest_beat: "", involves: [] }],
    relationships: [], last_time: null,
  });
  renderCampaign();
  const column = within(await screen.findByRole("complementary"));
  await column.findByText("Sister Aud");
  expect(column.getByText("The priory's debt")).toBeInTheDocument();
  expect(column.getByText("The Reeve will call it in")).toBeInTheDocument();
  expect(column.getByText("THREAT")).toHaveClass("alert");
});



test("a scene transition renders as unlabelled narration, with no Scene plate", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" },
    { role: "assistant", content: "a reply" },
    { role: "assistant", content: "*Time passes. It is now dusk.*", speaker: "⁣Scene" }] }));
  const { container } = renderCampaign();
  await screen.findByText(/Time passes/);
  expect(screen.queryByText(/⁣Scene/)).toBeNull();
  // the tagged transition joins the reply's run instead of opening its own
  // plate — exactly how an untagged transition rendered before the tag existed
  const names = [...container.querySelectorAll(".plate-name")].map((n) => n.textContent);
  expect(names).toEqual(["You", "Grimoire"]);
});

test("Reroll is offered past a trailing scene transition and keeps it", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" },
    { role: "assistant", content: "a reply" },
    { role: "assistant", content: "*Time passes. It is now dusk.*", speaker: "⁣Scene" }] }));
  renderCampaign();
  await screen.findByText(/Time passes/);
  fireEvent.click(await screen.findByTitle("Reroll"));
  fireEvent.click(screen.getByRole("button", { name: /reroll ▸/i }));
  // the optimistic trim drops the reply but leaves the transition standing
  expect(screen.queryByText("a reply")).toBeNull();
  expect(screen.getByText(/Time passes/)).toBeInTheDocument();
  await waitFor(() => expect(api.regenerate).toHaveBeenCalled());
});

test("the play view does not read the ledger", async () => {
  // The ledger is a screen of its own (4e), reached from the rail and the hub.
  // What matters here is the half that stayed true: this view never fetches it.
  vi.mocked(api.campaignProvenance).mockResolvedValue({});
  renderCampaign();
  await screen.findByText("Run One");
  expect(api.campaignLedger).not.toHaveBeenCalled();
});

test("renaming a scene re-reads the continuity column", async () => {
  // Every thread and commitment carries the TITLE of the scene that last moved
  // it, so a rename changes what those reads return — and this path touches
  // none of their other dependencies: same campaign, no absorb saved. Without
  // the revision bump the column keeps the old title until the user selects a
  // scene. (Asserted on the briefing, the continuity read that stayed on this
  // page when the ledger moved to its own route.)
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.campaignProvenance).mockResolvedValue({});
  vi.mocked(api.renameScene).mockResolvedValue({ id: "s1-renamed", title: "New" });
  renderCampaign();
  await screen.findByRole("button", { name: /rename/i });
  const before = vi.mocked(api.sceneBriefing).mock.calls.length;

  fireEvent.click(screen.getByRole("button", { name: /rename/i }));
  const input = screen.getByDisplayValue("Old");
  fireEvent.change(input, { target: { value: "New" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(api.renameScene).toHaveBeenCalled());
  await waitFor(() =>
    expect(vi.mocked(api.sceneBriefing).mock.calls.length).toBeGreaterThan(before));
});

test("a rename that keeps the scene id still re-reads the continuity column", async () => {
  // A capitalisation or punctuation edit slugs to the same id, so the route
  // returns the scene unchanged — but `meta.title` moved, and the title is
  // exactly what those rows carry. The bump sits ahead of `adoptSceneId`'s
  // same-id guard for that reason: everything else in that function is about
  // the id, and this one thing is not. Nothing else re-reads here, so this is
  // the case that isolates the bump.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.campaignProvenance).mockResolvedValue({});
  vi.mocked(api.renameScene).mockResolvedValue({ id: "s1", title: "OLD" });
  renderCampaign();
  await screen.findByRole("button", { name: /rename/i });
  const before = vi.mocked(api.sceneBriefing).mock.calls.length;

  fireEvent.click(screen.getByRole("button", { name: /rename/i }));
  const input = screen.getByDisplayValue("Old");
  fireEvent.change(input, { target: { value: "OLD" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(api.renameScene).toHaveBeenCalled());
  await waitFor(() =>
    expect(vi.mocked(api.sceneBriefing).mock.calls.length).toBeGreaterThan(before));
});

// ---- paginated scene history (#94) ----

// jsdom has no layout: scrollTop is a no-op setter and every metric reads 0.
// These stubs give the stream just enough geometry for the scroll handler and
// the restore to be exercised for real. scrollHeight grows with the number of
// rendered posts, which is what makes the prepend's height change observable.
function stubStreamGeometry(el: HTMLElement, clientHeight = 300, pxPerPost = 500) {
  let top = 0;
  Object.defineProperty(el, "scrollTop", {
    configurable: true, get: () => top, set: (v: number) => { top = v; },
  });
  Object.defineProperty(el, "clientHeight", { configurable: true, get: () => clientHeight });
  Object.defineProperty(el, "scrollHeight", {
    configurable: true, get: () => el.querySelectorAll(".msg").length * pxPerPost,
  });
}

test("a scene opens at its most recent page, not its whole history", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [{ role: "assistant", content: "recent" }], offset: 40, total: 41, has_older: true }));
  renderCampaign();
  await screen.findByText("recent");
  expect(api.getScene).toHaveBeenCalledWith("run", "s1", { limit: 60 });
});

test("a windowed post is edited by its absolute index, not its position on the page", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "page two" }], offset: 40, total: 41, has_older: true }));
  renderCampaign();
  await screen.findByText("page two");
  fireEvent.click(screen.getAllByTitle("Edit message")[0]);
  fireEvent.change(await screen.findByLabelText(/edit message/i), { target: { value: "fixed" } });
  fireEvent.click(screen.getByRole("button", { name: /save/i }));
  await waitFor(() => expect(api.editMessage).toHaveBeenCalledWith("run", "s1", 40, "fixed"));
});

test("scrolling to the top of the stream prepends the previous page", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockImplementation((_c, _s, w) =>
    Promise.resolve(fixture(w?.before === 2
      ? { meta: {}, messages: [{ role: "user", content: "older post" }], offset: 1, total: 3, has_older: true }
      : { meta: {}, messages: [{ role: "user", content: "newer post" }], offset: 2, total: 3, has_older: true })));
  const { container } = renderCampaign();
  await screen.findByText("newer post");
  fireEvent.scroll(container.querySelector(".stream")!);
  await screen.findByText("older post");
  expect(api.getScene).toHaveBeenCalledWith("run", "s1", { limit: 60, before: 2 });
  // prepended, so the older post reads first
  const posts = [...container.querySelectorAll(".msg-body")].map((n) => n.textContent);
  expect(posts).toEqual(["older post", "newer post"]);
});

test("loading older posts holds the viewport instead of jumping to the bottom", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockImplementation((_c, _s, w) =>
    Promise.resolve(fixture(w?.before === 2
      ? { meta: {}, messages: [{ role: "user", content: "older post" }], offset: 0, total: 3, has_older: false }
      : { meta: {}, messages: [{ role: "user", content: "newer post" }, { role: "assistant", content: "a reply" }],
          offset: 2, total: 4, has_older: true })));
  const { container } = renderCampaign();
  await screen.findByText("newer post");
  const stream = container.querySelector(".stream") as HTMLElement;
  stubStreamGeometry(stream);
  const scrollTo = vi.fn<(...args: [options?: ScrollToOptions | undefined] | [x: number, y: number]) => void>();
  stream.scrollTo = scrollTo;

  fireEvent.scroll(stream); // scrollTop 0 with two posts on screen: at the top
  await screen.findByText("older post");
  // two posts (1000px) with the viewport at the top means 1000px sat below the
  // fold; after the prepend (1500px) the same 1000px must still sit below it
  expect(stream.scrollTop).toBe(500);
  expect(scrollTo).not.toHaveBeenCalled();
});

test("the older-history button loads the previous page and disappears at the top", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockImplementation((_c, _s, w) =>
    Promise.resolve(fixture(w?.before === 1
      ? { meta: {}, messages: [{ role: "user", content: "the opener" }], offset: 0, total: 2, has_older: false }
      : { meta: {}, messages: [{ role: "assistant", content: "newer post" }], offset: 1, total: 2, has_older: true })));
  renderCampaign();
  await screen.findByText("newer post");
  fireEvent.click(screen.getByRole("button", { name: /load 1 older post/i }));
  await screen.findByText("the opener");
  expect(screen.queryByRole("button", { name: /older posts/i })).toBeNull();
});

test("an older page that will not load says so", async () => {
  // Both the button and the scroll handler drop the promise, so a failed page
  // used to be an unhandled rejection: the button went back to its label as if
  // it had never been pressed.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockImplementation((_c: string, _s: string, w?: Parameters<typeof api.getScene>[2]) =>
    (w?.before === 1
      ? Promise.reject(new ApiError(503, "store busy", "busy"))
      : Promise.resolve(fixture<Awaited<ReturnType<typeof api.getScene>>>({ meta: {}, messages: [{ role: "assistant", content: "newer post" }],
                          offset: 1, total: 2, has_older: true }))));
  renderCampaign();
  await screen.findByText("newer post");
  fireEvent.click(screen.getByRole("button", { name: /load 1 older post/i }));
  expect(await screen.findByText(/Older posts could not be loaded: store busy/)).toBeInTheDocument();
  expect(screen.getByText("newer post")).toBeInTheDocument();
});

test.each(["failure", "success"])("an older-page %s belongs to its campaign while the next campaign's list is pending", async (outcome) => {
  let finishPage: () => void = () => {};
  let landOther: (value: SceneMeta[]) => void = () => {};
  vi.mocked(api.listScenes).mockImplementation((cid) => cid === "other"
    ? new Promise((resolve) => { landOther = resolve; }) : Promise.resolve(SCENES_WITH_DATE));
  vi.mocked(api.getScene).mockImplementation((cid, _sid, window) => {
    if (cid === "run" && window?.before === 1) {
      return new Promise((resolve, reject) => {
        finishPage = () => outcome === "failure" ? reject(new Error("Retired page refused"))
          : resolve({ meta: { id: "s1", title: "Old" }, messages: [{ role: "user", content: "retired opener" }], offset: 0 });
      });
    }
    return Promise.resolve({ meta: { id: "s1", title: "Old" },
      messages: [{ role: "assistant", content: `${cid} recent` }], offset: 1, total: 2, has_older: true });
  });
  render(<MemoryRouter initialEntries={["/campaigns/run/scenes/s1"]}>
    {withPalette(<><Here /><Link to="/campaigns/other/scenes/s1">switch campaign</Link>{playRoutes()}</>)}
  </MemoryRouter>);
  await screen.findByText("run recent");
  fireEvent.click(screen.getByRole("button", { name: /load 1 older post/i }));
  fireEvent.click(screen.getByRole("link", { name: "switch campaign" }));
  await waitFor(() => expect(here()).toBe("/campaigns/other/scenes/s1"));
  await waitFor(() => expect(api.listScenes).toHaveBeenCalledWith("other"));
  // The new list has not landed, so its selectScene cannot have retired the
  // old window token yet. Campaign ownership must already be authoritative.
  expect(screen.getByText("run recent")).toBeInTheDocument();
  await act(async () => finishPage());
  expect(screen.queryByText(/Retired page refused/)).not.toBeInTheDocument();
  expect(screen.queryByText("retired opener")).not.toBeInTheDocument();
  await act(async () => landOther(SCENES_WITH_DATE));
  await screen.findByText("other recent");
  expect(screen.queryByText(/Retired page refused/)).not.toBeInTheDocument();
});

test.each([false, true])("an older-page retry clears its failure, including an empty page: %s", async (empty) => {
  vi.mocked(api.listScenes).mockResolvedValue(SCENES_WITH_DATE);
  let attempts = 0;
  vi.mocked(api.getScene).mockImplementation((_cid, _sid, window) => {
    const meta = { id: "s1", title: "Old" };
    if (window?.before === 1) {
      if (++attempts === 1) return Promise.reject(new ApiError(503, "paging refusal", "busy"));
      return Promise.resolve(fixture({ meta, messages: empty ? [] : [{ role: "user", content: "the opener" }],
        offset: 0, total: 2, has_older: false }));
    }
    return Promise.resolve(fixture({ meta, messages: [{ role: "assistant", content: "newer post" }],
      offset: 1, total: 2, has_older: true }));
  });
  renderCampaign();
  await screen.findByText("newer post");
  fireEvent.click(screen.getByRole("button", { name: /load 1 older post/i }));
  await screen.findByText(/Older posts could not be loaded: paging refusal/);
  fireEvent.click(screen.getByRole("button", { name: /load 1 older post/i }));
  await waitFor(() => expect(screen.queryByText(/paging refusal/)).not.toBeInTheDocument());
  expect(screen.queryByRole("button", { name: /load 1 older post/i })).toBeNull();
  if (!empty) expect(screen.getByText("the opener")).toBeInTheDocument();
});

test("an older-page retry preserves an unrelated delete refusal", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(SCENES_WITH_DATE);
  let attempts = 0;
  vi.mocked(api.getScene).mockImplementation((_cid, _sid, window) => {
    const meta = { id: "s1", title: "Old" };
    if (window?.before === 1) {
      if (++attempts === 1) return Promise.reject(new ApiError(503, "paging refusal", "busy"));
      return Promise.resolve(fixture({ meta, messages: [{ role: "user", content: "the opener" }], offset: 0 }));
    }
    return Promise.resolve(fixture({ meta, messages: [{ role: "assistant", content: "newer post" }], offset: 1 }));
  });
  vi.mocked(api.deleteScene).mockRejectedValue(new ApiError(409, "delete refusal", "scene_busy"));
  vi.spyOn(window, "confirm").mockReturnValue(true);
  renderCampaign();
  await screen.findByText("newer post");
  fireEvent.click(screen.getByRole("button", { name: /load 1 older post/i }));
  await screen.findByText(/Older posts could not be loaded: paging refusal/);
  fireEvent.click(screen.getByRole("button", { name: "Delete scene" }));
  await screen.findByText("delete refusal");
  fireEvent.click(screen.getByRole("button", { name: /load 1 older post/i }));
  await screen.findByText("the opener");
  expect(screen.getByText("delete refusal")).toBeInTheDocument();
});

test("no older-history button when the whole transcript is loaded", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [{ role: "assistant", content: "only post" }], offset: 0, total: 1, has_older: false }));
  renderCampaign();
  await screen.findByText("only post");
  expect(screen.queryByRole("button", { name: /older posts/i })).toBeNull();
});

test("Reroll survives the opening user post being off-window", async () => {
  // the run's own user turn is older than the loaded page — unloaded, not
  // absent, so this is still a reply to something and still rerollable
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "assistant", content: "a reply" }],
    offset: 12, total: 13, has_older: true, has_user_message: true }));
  renderCampaign();
  await screen.findByText("a reply");
  await screen.findByTitle("Reroll");
});

test("no Reroll on an all-assistant transcript, however much history is above", async () => {
  // an offscreen scene never stores a player turn, so unloaded history above
  // the window is not evidence of one — and regenerate 400s on that transcript
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "assistant", content: "narration" }],
    offset: 12, total: 13, has_older: true, has_user_message: false }));
  renderCampaign();
  await screen.findByText("narration");
  // The mirror of the races above, and the more dangerous half: this asserts an
  // ABSENCE, so the scheduling that hid Reroll from its siblings would make this
  // pass while proving nothing. `Edit message` hangs off `transcriptIsActive`
  // alone, so waiting for it establishes that the gutter has rendered at all —
  // and only then is "no Reroll" a statement about the all-assistant transcript.
  await screen.findByTitle("Edit message");
  expect(screen.queryByTitle("Reroll")).toBeNull();
});

test("an older page that lands after a scene switch is dropped", async () => {
  // otherwise scene A's posts prepend onto B and install A's offset, after
  // which an edit sends B's id with an A-derived index — onto an unrelated post
  vi.mocked(api.listScenes).mockResolvedValue([
    { id: "s1", title: "First", model: "", created: "", updated: "", date: "" },
    { id: "s2", title: "Second", model: "", created: "", updated: "", date: "" }]);
  let releaseOlder: (() => void) | null = null;
  vi.mocked(api.getScene).mockImplementation((_c, sid, w) => {
    if (sid === "s1" && w?.before === 5) {
      return new Promise((resolve) => {
        releaseOlder = () => resolve(fixture({ meta: {}, messages: [{ role: "user", content: "scene one, older" }],
                                       offset: 4, total: 6, has_older: true, has_user_message: true }));
      });
    }
    return Promise.resolve(fixture(sid === "s2"
      ? { meta: {}, messages: [{ role: "assistant", content: "scene two" }],
          offset: 0, total: 1, has_older: false, has_user_message: false }
      : { meta: {}, messages: [{ role: "assistant", content: "scene one, newest" }],
          offset: 5, total: 6, has_older: true, has_user_message: true }));
  });
  renderCampaign();
  await screen.findByText("scene one, newest");
  fireEvent.click(screen.getByRole("button", { name: /load .* older post/i }));
  await openScene(/Second/);
  await screen.findByText("scene two");
  releaseOlder!();
  await waitFor(() => expect(screen.getByText("scene two")).toBeInTheDocument());
  expect(screen.queryByText("scene one, older")).toBeNull();
  // and the retired page did not install scene one's offset on scene two
  expect(screen.queryByRole("button", { name: /older post/i })).toBeNull();
});

test("a refresh of the open scene re-reads everything already on screen", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockImplementation((_c, _s, w) =>
    Promise.resolve(fixture(w?.before === 60
      ? { meta: {}, messages: [{ role: "user", content: "older post" }], offset: 59, total: 61, has_older: true }
      : { meta: {}, messages: [{ role: "assistant", content: "newer post" }], offset: 60, total: 61, has_older: true })));
  renderCampaign();
  await screen.findByText("newer post");
  fireEvent.click(screen.getByRole("button", { name: /load .* older posts/i }));
  await screen.findByText("older post");
  // editing forces a re-select; it must not collapse the reader back to one page
  fireEvent.click(screen.getAllByTitle("Edit message")[0]);
  // exact label: the OTHER post's gutter button is "Edit message <n>"
  fireEvent.change(await screen.findByLabelText("Edit message"), { target: { value: "fixed" } });
  fireEvent.click(screen.getByRole("button", { name: /save/i }));
  await waitFor(() => expect(api.getScene).toHaveBeenLastCalledWith("run", "s1", { limit: 61 }));
});

test("a recovered prompt is never shown against the scene the player moved to", async () => {
  // The composer is one shared box that survives a scene switch, so restoring
  // a rolled-back prompt straight into it puts scene A's words in front of a
  // player looking at scene B, and Send there posts them to B.
  //
  // The window is short but real: `runStream`'s finally refreshes the turn's
  // scene and `selectScene` sets `activeId` synchronously, so the player is
  // pulled back to A a couple of microtasks later. Measured against the old
  // code, the DOM does commit in between — scene B on screen, A's prompt in
  // the composer — and in the browser the stream read that sits between the
  // error frame and the body ending is a task boundary, so that state can
  // paint and be clicked. Relying on an unrelated navigation side effect to
  // close it is also the kind of accident this PR keeps finding.
  //
  // So the invariant is sampled across the whole window rather than at one
  // instant: the composer must never hold text while a scene other than the
  // turn's own is the active one.
  vi.mocked(api.listScenes).mockResolvedValue([
    { id: "s1", title: "Old", model: "", created: "", updated: "", date: "" },
    { id: "s2", title: "Later", model: "", created: "", updated: "", date: "" },
  ]);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, total: 0, messages: [] }));
  let fail: (() => void) | null = null;
  vi.mocked(api.chat).mockImplementation(
    async (_c, _s, _m, onEvent) => {
      await new Promise<void>((r) => {
        fail = () => {
          onEvent(fixture({ error: { detail: "OpenRouter API key is not set",
                             post_returned: true } }));
          r();
        };
      });
    });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue(""));

  await openScene(/Later/);          // leave before it fails
  // The scene on screen is the transcript's own heading now, not a rail row.
  const onScreen = () => document.querySelector(".scene-title")?.textContent ?? "";
  await waitFor(() => expect(onScreen()).toMatch(/Later/));

  const wrong: string[] = [];
  fail!();
  for (let i = 0; i < 8; i++) {
    await Promise.resolve();
    const composer = screen.getByRole<HTMLTextAreaElement>("textbox").value;
    if (composer && !/Old/.test(onScreen())) wrong.push(`${onScreen()} | ${composer}`);
  }
  expect(wrong).toEqual([]);

  // And it is not dropped: it comes back with the scene it was written for.
  await waitFor(() => expect(onScreen()).toMatch(/Old/));
  await waitFor(() =>
    expect(screen.getByRole("textbox")).toHaveValue("I draw my blade."));
});
