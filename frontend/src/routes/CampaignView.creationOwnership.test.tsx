import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { MemoryRouter, Link } from "react-router-dom";
import { api } from "../api/client";
import type { SceneMeta, ScenePage } from "../api/client";
import { here, Here, installCampaignMocks, ONE_SCENE, openScene, playRoutes, renderCampaign, withPalette } from "../testkit/campaignHarness";
import { fixture } from "../testkit/fixture";
import { ShowState, readCounter, SCENES_WITH_DATE, TWO_SCENES, transcriptsPerScene } from "../testkit/campaignFixtures";

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



test("a premise generated in one campaign is not offered to another's scene", async () => {
  // `seedPrompt` is recorded before the relist that follows it, so a reader who
  // switches campaigns during that await leaves it behind — and scene ids
  // repeat, so a sid-only match hands A's premise to B's identically-numbered
  // empty scene.
  let landRelist: (v: SceneMeta[]) => void = () => {};
  const nth = readCounter();
  vi.mocked(api.listScenes).mockImplementation((c) =>
    c === "other"
      ? Promise.resolve([{ id: "s9", title: "B nine", model: "", created: "", updated: "", date: "" }])
      : nth(c) > 1
        ? new Promise((res) => { landRelist = res; })
        : Promise.resolve(ONE_SCENE));
  // every scene is empty, so CastPanel (which renders the premise) is shown
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [] }));
  render(
    <MemoryRouter initialEntries={["/campaigns/run/scenes/s1"]}>
      {withPalette(<>
        <Here />
        <Link to="/campaigns/other/scenes/whichever">switch campaign</Link>
        {playRoutes()}
      </>)}
    </MemoryRouter>,
  );
  await screen.findByTestId("cast-panel");

  // the chooser creates s9 in "run" with a generated premise…
  fireEvent.click(screen.getByRole("button", { name: /\+ new scene/i }));
  fireEvent.click(screen.getByText("stub-pick"));       // onCreated("s9", "A premise")
  fireEvent.click(screen.getByText("switch campaign")); // …then the reader leaves
  await waitFor(() => expect(here()).toBe("/campaigns/other/scenes/s9"));
  landRelist([...ONE_SCENE, { id: "s9", title: "New", model: "", created: "", updated: "", date: "" }]);
  await act(async () => { for (let i = 0; i < 4; i++) await Promise.resolve(); });

  // "other" also has an s9, and it must not be handed run's premise
  expect(screen.getByTestId("cast-panel")).not.toHaveTextContent("A premise");
});

// A scene created from the SCENES LIST reaches this page by navigating, not
// through `sceneCreated`, so the premise comes in on the history entry. Wiring
// only the in-campaign chooser was the whole bug: picking a suggestion from the
// list landed the reader on a scene whose opener box was empty.
test("a premise that rode the navigation seeds the opener box", async () => {
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [] }));
  render(
    <MemoryRouter initialEntries={[{ pathname: "/campaigns/run/scenes/s1",
                                     state: { seedPrompt: "A debt-collector arrives." } }]}>
      {withPalette(<><Here />{playRoutes()}</>)}
    </MemoryRouter>,
  );
  const panel = await screen.findByTestId("cast-panel");
  await waitFor(() => expect(panel).toHaveTextContent("A debt-collector arrives."));
});

// The clear matters as much as the adoption: history entries outlive the visit.
// Left in place, a premise the reader had since edited or emptied would be
// reinstated in the box by any Back into this entry, or any reload of it.
test("the premise is cleared off the history entry once adopted", async () => {
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [] }));
  render(
    <MemoryRouter initialEntries={[{ pathname: "/campaigns/run/scenes/s1",
                                     state: { seedPrompt: "A debt-collector arrives." } }]}>
      {withPalette(<><Here /><ShowState />{playRoutes()}</>)}
    </MemoryRouter>,
  );
  await screen.findByTestId("cast-panel");
  await waitFor(() => expect(screen.getByTestId("hist-state")).toHaveTextContent("none"));
  // the premise is still IN the box -- only the history copy is gone
  expect(screen.getByTestId("cast-panel")).toHaveTextContent("A debt-collector arrives.");
});

// Follow-up to PR #318: a soft failure inside SceneConfirmForm (a failed
// location/date/rename step) still creates the scene -- `salvaged` -- and
// clears `writing`, so Escape and the backdrop can dismiss the chooser from
// there instead of only "Continue to scene". `sceneCreated` is what relists
// after a normal create; dismissing never reached it, so the rail stayed one
// scene short of reality until a manual reload.
test("dismissing the chooser after a soft failure refreshes the scene list", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  await screen.findByTestId("cast-panel");

  fireEvent.click(screen.getByRole("button", { name: /\+ new scene/i }));
  await screen.findByTestId("scene-chooser");
  const before = vi.mocked(api.listScenes).mock.calls.length;
  const wasAt = here();
  fireEvent.click(screen.getByText("stub-close-salvaged"));
  await waitFor(() => expect(vi.mocked(api.listScenes).mock.calls.length).toBeGreaterThan(before));
  // a dismissal, not "Continue to scene" -- the URL does not move to the
  // salvaged scene, unlike a normal `sceneCreated`
  expect(here()).toBe(wasAt);
});

// The other half: a PLAIN dismissal (nothing was salvaged) must not pay for
// a relist it doesn't need -- most Cancels and idle Escapes wrote nothing.
test("a plain dismissal (nothing salvaged) does not refresh the scene list", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  await screen.findByTestId("cast-panel");

  fireEvent.click(screen.getByRole("button", { name: /\+ new scene/i }));
  await screen.findByTestId("scene-chooser");
  const before = vi.mocked(api.listScenes).mock.calls.length;
  fireEvent.click(screen.getByText("stub-close"));
  await act(async () => { for (let i = 0; i < 4; i++) await Promise.resolve(); });
  expect(vi.mocked(api.listScenes).mock.calls.length).toBe(before);
});

test("a created scene is opened against a list that knows it exists", async () => {
  // The rail stays interactive while a creation's relist is open, so a rename
  // can issue a newer read that retires it. If the retired read resolved
  // anyway, `sceneCreated` would navigate to the new row while the installed
  // list still predates the creation — and the resolver would read that
  // brand-new id as stale and redirect straight back to the previous scene.
  let landCreateRelist: (v: SceneMeta[]) => void = () => {};
  let landRenameRelist: (v: SceneMeta[]) => void = () => {};
  let fresh = 0;
  const WITH_S9 = [...ONE_SCENE, { id: "s9", title: "New", model: "", created: "", updated: "", date: "" }];
  const nth = readCounter();
  vi.mocked(api.listScenes).mockImplementation((c) => {
    if (nth(c) === 1) return Promise.resolve(ONE_SCENE);
    fresh += 1;
    if (fresh === 1) return new Promise((res) => { landCreateRelist = res; });
    return new Promise((res) => { landRenameRelist = res; });
  });
  transcriptsPerScene();
  vi.mocked(api.renameScene).mockResolvedValue(fixture({ id: "s1-renamed" }));
  renderCampaign();
  await screen.findByText("transcript of s1");

  // create s9 — its relist is issued and hangs
  fireEvent.click(screen.getByRole("button", { name: /\+ new scene/i }));
  fireEvent.click(screen.getByText("stub-pick"));
  await waitFor(() => expect(fresh).toBe(1));

  // …and a rename from the still-interactive rail issues a NEWER read
  fireEvent.click(screen.getByRole("button", { name: /rename scene/i }));
  const box = screen.getByDisplayValue("Old");
  fireEvent.change(box, { target: { value: "Renamed" } });
  fireEvent.keyDown(box, { key: "Enter" });
  await waitFor(() => expect(fresh).toBe(2));

  // the creation's own relist answers first and is retired
  landCreateRelist(WITH_S9);
  await act(async () => { for (let i = 0; i < 4; i++) await Promise.resolve(); });

  // the newer read finally lands, and only now may the new scene be opened
  landRenameRelist([{ id: "s1-renamed", title: "Renamed", model: "", created: "", updated: "", date: "" },
                    WITH_S9[1]]);
  await waitFor(() => expect(here()).toBe("/campaigns/run/scenes/s9"));
  await screen.findByText("transcript of s9");
});

test("a superseded relist that FAILS still opens the scene the newer one listed", async () => {
  // The mirror of the retired-read handoff, on the rejection branch: a
  // creation's relist superseded by a rename's can fail, and its failure
  // describes a request nobody is waiting on. Rejecting the caller meant
  // `sceneCreated` never navigated to the scene it had created — as an
  // unhandled rejection, since the chooser drops the promise.
  let failCreateRelist: (e: unknown) => void = () => {};
  let landRenameRelist: (v: SceneMeta[]) => void = () => {};
  let fresh = 0;
  const nth = readCounter();
  vi.mocked(api.listScenes).mockImplementation((c) => {
    if (nth(c) === 1) return Promise.resolve(ONE_SCENE);
    fresh += 1;
    if (fresh === 1) return new Promise((_res, rej) => { failCreateRelist = rej; });
    return new Promise((res) => { landRenameRelist = res; });
  });
  transcriptsPerScene();
  vi.mocked(api.renameScene).mockResolvedValue(fixture({ id: "s1-renamed" }));
  renderCampaign();
  await screen.findByText("transcript of s1");

  fireEvent.click(screen.getByRole("button", { name: /\+ new scene/i }));
  fireEvent.click(screen.getByText("stub-pick"));            // creates s9
  await waitFor(() => expect(fresh).toBe(1));

  fireEvent.click(screen.getByRole("button", { name: /rename scene/i }));
  const box = screen.getByDisplayValue("Old");
  fireEvent.change(box, { target: { value: "Renamed" } });
  fireEvent.keyDown(box, { key: "Enter" });
  await waitFor(() => expect(fresh).toBe(2));

  failCreateRelist(Object.assign(new Error("boom"), { detail: "list read failed" }));
  await act(async () => { for (let i = 0; i < 4; i++) await Promise.resolve(); });

  landRenameRelist([{ id: "s1-renamed", title: "Renamed", model: "", created: "", updated: "", date: "" },
                    { id: "s9", title: "New", model: "", created: "", updated: "", date: "" }]);

  await waitFor(() => expect(here()).toBe("/campaigns/run/scenes/s9"));
  // the superseded failure is not reported: it described a retired request
  expect(screen.queryByText(/list read failed/)).toBeNull();
});

test("a scene created whose only list read fails says so instead of going quiet", async () => {
  // The other side of the same change: when the failure IS the campaign's
  // newest read, the scene exists and the rail does not list it, so silence
  // would strand a real scene behind an unhandled rejection.
  const nth = readCounter();
  vi.mocked(api.listScenes).mockImplementation((c) =>
    nth(c) > 1
      ? Promise.reject(Object.assign(new Error("boom"), { detail: "list read failed" }))
      : Promise.resolve(ONE_SCENE));
  transcriptsPerScene();
  renderCampaign();
  await screen.findByText("transcript of s1");

  fireEvent.click(screen.getByRole("button", { name: /\+ new scene/i }));
  fireEvent.click(screen.getByText("stub-pick"));

  expect(await screen.findByText(/list read failed/)).toBeInTheDocument();
});

test.each(["failure", "success"])("a retired mount list %s cannot replace a creation's confirming-read refusal", async (outcome) => {
  let finishMount: () => void = () => {};
  let refuseRefresh: () => void = () => {};
  vi.mocked(api.listScenes).mockImplementationOnce(() => new Promise((resolve, reject) => {
    finishMount = () => outcome === "failure" ? reject(new Error("Retired mount refused")) : resolve(SCENES_WITH_DATE);
  })).mockImplementationOnce(() => new Promise((_, reject) => {
    refuseRefresh = () => reject(new Error("Creation refresh refused"));
  }));
  renderCampaign();
  fireEvent.click(await screen.findByRole("button", { name: /\+ new scene/i }));
  fireEvent.click(screen.getByText("stub-pick"));
  await waitFor(() => expect(api.listScenes).toHaveBeenCalledTimes(2));
  await act(async () => finishMount());
  // The mount now follows the mutation's newer read; only its caller owns
  // the explanation that the server already created the requested scene.
  await act(async () => refuseRefresh());
  expect(screen.getByText(/The scene was created, but the scene list could not be refreshed:.*Creation refresh refused/))
    .toBeInTheDocument();
  expect(screen.queryByText(/The scene list could not be read:/)).not.toBeInTheDocument();
  expect(here()).toBe("/campaigns/run/scenes/s1");
});

test("an edit cannot write one scene's index into another's transcript", async () => {
  // `runStream`'s finally refreshes the TURN's scene, which is deliberately
  // allowed to pull the view back (a failed turn's recovered prompt has to be
  // shown against the scene it was written for). The resolver then corrects
  // `activeId` back to the scene the URL names, and until that corrective read
  // lands the previous scene's messages are still rendered.
  //
  // `editing.index` indexes what is RENDERED and `activeId` is where it would
  // be written, so a save in that window overwrites an unrelated message of a
  // scene the player never edited. The write must not go out at all.
  vi.mocked(api.listScenes).mockResolvedValue(TWO_SCENES);
  vi.mocked(api.getScene).mockImplementation(async (_c, sid) => fixture({
    meta: {}, total: 2, messages: [
      { role: "user", content: `${sid} first` },
      { role: "assistant", content: `${sid} second` }],
  }));
  renderCampaign();
  await screen.findByText("s1 second");

  // open an edit on s1's message, then move to s2 while it is open
  fireEvent.click(screen.getByRole("button", { name: /edit message 2/i }));
  await screen.findByRole("button", { name: /^save$/i });

  // hold s2's read open so the divergence window stays observable
  let landS2: (v: ScenePage) => void = () => {};
  vi.mocked(api.getScene).mockImplementationOnce(() => new Promise((res) => { landS2 = res; }));
  await openScene(/The Saltmarch Gate/);
  await waitFor(() => expect(here()).toBe("/campaigns/run/scenes/s2"));

  // s1's transcript is still on screen while s2 is the active scene
  expect(screen.getByText("s1 second")).toBeInTheDocument();
  const save = screen.getByRole("button", { name: /^save$/i });
  fireEvent.click(save);
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });

  // the write never goes out — this is the whole of it
  expect(api.editMessage).not.toHaveBeenCalled();

  // and the corrective read still lands normally afterwards
  landS2(fixture({ meta: {}, total: 2, messages: [
    { role: "user", content: "s2 first" }, { role: "assistant", content: "s2 second" }] }));
  await waitFor(() => expect(screen.getByText("s2 first")).toBeInTheDocument());
});

test("a creation's list failure does not raise a banner in another campaign", async () => {
  // The switch to another campaign already cleared that campaign's errors, so
  // a late rejection from the campaign left behind lands as a banner claiming
  // the CURRENT campaign's scene list failed.
  let failRelist: (e: unknown) => void = () => {};
  const nth = readCounter();
  vi.mocked(api.listScenes).mockImplementation((c) =>
    c === "other"
      ? Promise.resolve([{ id: "b1", title: "B one", model: "", created: "", updated: "", date: "" }])
      : nth(c) > 1
        ? new Promise((_res, rej) => { failRelist = rej; })
        : Promise.resolve(ONE_SCENE));
  transcriptsPerScene();
  render(
    <MemoryRouter initialEntries={["/campaigns/run/scenes/s1"]}>
      {withPalette(<>
        <Here />
        <Link to="/campaigns/other/scenes/whichever">switch campaign</Link>
        {playRoutes()}
      </>)}
    </MemoryRouter>,
  );
  await screen.findByText("transcript of s1");

  fireEvent.click(screen.getByRole("button", { name: /\+ new scene/i }));
  fireEvent.click(screen.getByText("stub-pick"));
  fireEvent.click(screen.getByText("switch campaign"));
  await waitFor(() => expect(here()).toBe("/campaigns/other/scenes/b1"));

  failRelist(Object.assign(new Error("boom"), { detail: "list read failed" }));
  await act(async () => { for (let i = 0; i < 4; i++) await Promise.resolve(); });

  expect(screen.queryByText(/list read failed/)).toBeNull();
});

test("a reroll cannot regenerate a scene whose transcript is not the one on screen", async () => {
  // Same divergence window as the edit guard, and worse: the affordance and the
  // optimistic removal both read the RENDERED messages while `api.regenerate`
  // targets `activeId`, so Regenerate replaces a reply of the active scene that
  // the reader was never shown.
  vi.mocked(api.listScenes).mockResolvedValue(TWO_SCENES);
  vi.mocked(api.getScene).mockImplementation(async (_c, sid) => fixture({
    meta: {}, total: 2, messages: [
      { role: "user", content: `${sid} asked` },
      { role: "assistant", content: `${sid} replied` }],
  }));
  renderCampaign();
  await screen.findByText("s1 replied");
  // the reroll affordance exists while s1's transcript is genuinely s1's
  expect(screen.getByRole("button", { name: /reroll/i })).toBeInTheDocument();

  // hold s2's read open so the divergence window stays observable
  let landS2: (v: ScenePage) => void = () => {};
  vi.mocked(api.getScene).mockImplementationOnce(() => new Promise((res) => { landS2 = res; }));
  await openScene(/The Saltmarch Gate/);
  await waitFor(() => expect(here()).toBe("/campaigns/run/scenes/s2"));

  // s1's transcript is still rendered while s2 is the active scene
  expect(screen.getByText("s1 replied")).toBeInTheDocument();
  // …and the control that would regenerate s2 off it is gone
  expect(screen.queryByRole("button", { name: /reroll/i })).toBeNull();
  expect(api.regenerate).not.toHaveBeenCalled();

  landS2(fixture({ meta: {}, total: 2, messages: [
    { role: "user", content: "s2 asked" }, { role: "assistant", content: "s2 replied" }] }));
  await screen.findByText("s2 replied");
  // once s2's own transcript has landed, rerolling is available again
  expect(screen.getByRole("button", { name: /reroll/i })).toBeInTheDocument();
});

test("a send followed by opening another scene does not expose its controls on the wrong posts", async () => {
  // `showOptimistically` used to null `loaded`, and the transcript guard read
  // null as "safe" on the reasoning that an optimistic edit extends the ACTIVE
  // scene's transcript. True when the edit happens, false the moment the reader
  // navigates: nothing re-establishes it, so the previous scene's posts stay
  // rendered under the new scene's id with Edit and Regenerate live on them.
  //
  // The optimistic state has to SURVIVE to the navigation for that to be
  // reachable, so every read after the first one is held open — the post-turn
  // refresh included. Letting it land would re-stamp `loaded` and the window
  // would never exist.
  let reads = 0;
  const held: ((v: ScenePage) => void)[] = [];
  vi.mocked(api.listScenes).mockResolvedValue(TWO_SCENES);
  vi.mocked(api.getScene).mockImplementation(async (_c, sid) => {
    reads += 1;
    if (reads === 1) {
      return fixture({ meta: {}, total: 2, messages: [
        { role: "user", content: `${sid} asked` },
        { role: "assistant", content: `${sid} replied` }] });
    }
    return new Promise((res) => { held.push(res); });
  });
  vi.mocked(api.chat).mockImplementation(async (_c, _s, _t, onEvent) => {
    onEvent(fixture({ error: { detail: "OpenRouter API key is not set", post_returned: true } }));
  });
  renderCampaign();
  await screen.findByText("s1 replied");
  expect(document.querySelector('button[aria-label^="Edit message"]')).not.toBeNull();

  const ta = screen.getByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  // the optimistic post (the composer keeps it too after the failed turn)
  await waitFor(() => expect(screen.getAllByText("and then?").length).toBeGreaterThan(0));

  await openScene(/The Saltmarch Gate/);
  await waitFor(() => expect(here()).toBe("/campaigns/run/scenes/s2"));

  // s1's posts are still rendered while s2 is active — no control may act on them
  expect(screen.getByText("s1 replied")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /reroll/i })).toBeNull();
  expect(document.querySelector('button[aria-label^="Edit message"]')).toBeNull();

  // and once s2's own transcript lands they come back, against s2's posts
  held[held.length - 1](fixture({ meta: {}, total: 2, messages: [
    { role: "user", content: "s2 asked" }, { role: "assistant", content: "s2 replied" }] }));
  await screen.findByText("s2 replied");
  expect(document.querySelector('button[aria-label^="Edit message"]')).not.toBeNull();
});

test("sending into a scene that has not loaded yet does not claim the old posts", async () => {
  // The reverse ordering of the previous case: navigation comes FIRST. Send
  // stays enabled while a freshly selected scene is still loading, so the
  // reader can open s2 and send while s2's read is in flight — `activeId` says
  // s2 while the messages on screen are still s1's. Deriving the optimistic
  // transcript's owner from the active scene labels s1's posts as s2's, which
  // is worse than not knowing: the guard believes it and offers edits whose
  // indices address s1 against s2's file.
  let landS2: (v: ScenePage) => void = () => {};
  let reads = 0;
  vi.mocked(api.listScenes).mockResolvedValue(TWO_SCENES);
  vi.mocked(api.getScene).mockImplementation(async (_c, sid, w) => {
    // `runStream` takes a one-message baseline read before posting whenever the
    // turn's scene is not the one last read — that has to answer, or the turn
    // never starts and there is nothing to observe.
    if (w?.limit === 1) return fixture({ meta: {}, total: 2, messages: [] });
    reads += 1;
    if (reads === 1) {
      return fixture({ meta: {}, total: 2, messages: [
        { role: "user", content: `${sid} asked` },
        { role: "assistant", content: `${sid} replied` }] });
    }
    return new Promise((res) => { landS2 = res; });
  });
  vi.mocked(api.chat).mockImplementation(async (_c, _s, _t, onEvent) => {
    onEvent(fixture({ error: { detail: "OpenRouter API key is not set", post_returned: true } }));
  });
  renderCampaign();
  await screen.findByText("s1 replied");

  // open s2; its read hangs, so s1's transcript is still what is rendered
  await openScene(/The Saltmarch Gate/);
  await waitFor(() => expect(here()).toBe("/campaigns/run/scenes/s2"));
  expect(screen.getByText("s1 replied")).toBeInTheDocument();

  // …and send anyway, which is allowed
  const ta = screen.getByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await waitFor(() => expect(api.chat).toHaveBeenCalled());

  // s1's posts must not become editable just because s2 is now active
  expect(screen.getByText("s1 replied")).toBeInTheDocument();
  expect(document.querySelector('button[aria-label^="Edit message"]')).toBeNull();
  expect(screen.queryByRole("button", { name: /reroll/i })).toBeNull();

  landS2(fixture({ meta: {}, total: 2, messages: [
    { role: "user", content: "s2 asked" }, { role: "assistant", content: "s2 replied" }] }));
  await screen.findByText("s2 replied");
  expect(document.querySelector('button[aria-label^="Edit message"]')).not.toBeNull();
});
