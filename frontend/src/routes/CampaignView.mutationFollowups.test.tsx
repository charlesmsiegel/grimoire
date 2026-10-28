import { screen, fireEvent, waitFor, act } from "@testing-library/react";
import { api, ApiError } from "../api/client";
import { installCampaignMocks, ONE_SCENE, renderCampaign } from "../testkit/campaignHarness";
import { fixture } from "../testkit/fixture";
import { twoPostScene, ALT } from "../testkit/campaignFixtures";

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



test("renaming the scene on screen does not clear the composer", async () => {
  // `adoptSceneId` re-keys every piece of client state a rename invalidates,
  // including a prompt parked under the old id. That parked case used to be
  // reachable directly — a prompt parked under s1 while the reader sat on s2,
  // then s1 renamed from the rail — and the rail is gone: rename belongs to the
  // scene on screen, and opening a scene is what hands its parked prompt back.
  // The map re-key is defensive now; what a player can still hit is this, the
  // invariant it was protecting. The other two re-keys have their own tests
  // ("a seeded premise survives the rename from the first date set" and the
  // reroll-Retry pair below).
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.renameScene).mockResolvedValue(fixture({ id: "s1-renamed" }));
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });

  vi.mocked(api.listScenes).mockResolvedValue(
    [{ id: "s1-renamed", title: "Renamed", model: "", created: "", updated: "", date: "" }]);
  fireEvent.click(screen.getByRole("button", { name: /rename scene/i }));
  const nameInput = screen.getByDisplayValue("Old");
  fireEvent.change(nameInput, { target: { value: "Renamed" } });
  fireEvent.keyDown(nameInput, { key: "Enter" });
  await waitFor(() =>
    expect(screen.getByRole("heading", { name: /Renamed/ })).toBeInTheDocument());

  // The only copy of what the player wrote is in that box.
  expect(screen.getByRole("textbox")).toHaveValue("I draw my blade.");
});

test("renaming the scene keeps a failed reroll's Retry a reroll", async () => {
  // The remembered reroll carries the scene it belongs to so Retry cannot act
  // on a different one. A rename mints a new id, and leaving the ref on the old
  // one makes that same check misfire in the other direction: Retry decides
  // this is not the reroll's scene, falls back to `/retry`, and continues from
  // the restored old reply — dropping the guidance the player wrote.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({
    meta: {}, total: 2, messages: [
      { role: "user", content: "and then?" },
      { role: "assistant", content: "The tide turns." }],
  }));
  vi.mocked(api.renameScene).mockResolvedValue(fixture({ id: "s1-renamed" }));
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

  // Rename the active scene. The banner stays up, so Retry is still offered.
  vi.mocked(api.listScenes).mockResolvedValue(
    [{ id: "s1-renamed", title: "Renamed", model: "", created: "", updated: "", date: "" }]);
  const rename = () => screen.getByRole("button", { name: /rename scene/i });
  await waitFor(() => expect(rename()).not.toBeDisabled(), { timeout: 15000 });
  fireEvent.click(rename());
  const nameInput = screen.getByDisplayValue("Old");
  fireEvent.change(nameInput, { target: { value: "Renamed" } });
  fireEvent.keyDown(nameInput, { key: "Enter" });
  await waitFor(() => expect(api.renameScene).toHaveBeenCalled());

  fireEvent.click(screen.getByRole("button", { name: /^retry$/i }));
  await waitFor(() => expect(api.regenerate).toHaveBeenCalledTimes(2));
  expect(api.retry).not.toHaveBeenCalled();
  expect(vi.mocked(api.regenerate).mock.calls[1][1]).toBe("s1-renamed");
  expect(vi.mocked(api.regenerate).mock.calls[1][3]?.guidance).toBe("darker this time");
});

// ------------------------------------------ #110/#112: confidence routing

// ---- the live rolling summary (#85) and the scene-break detector (#84) ----
//
// Both used to be fired from here once a turn's streaming promise settled, and
// #397 moved that trigger to the backend: a phone that locks mid-turn suspends
// this JavaScript, the turn lands server-side anyway, and nothing here is left
// to ask. Every other transcript write below still asks for itself, because
// each is a request the player is waiting on -- it is only the turn whose end
// this side can miss.
test("a finished turn leaves both follow-ups to the server", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  fireEvent.change(await screen.findByRole("textbox"), { target: { value: "Go on." } });
  fireEvent.click(screen.getByRole("button", { name: /Send/ }));
  // Settled: the composer is out of its Stop state, so the whole of `runStream`
  // -- including the point these two used to be fired from -- has run.
  await waitFor(() => expect(screen.queryByRole("button", { name: /Stop/ })).toBeNull());
  expect(api.refreshRollingSummary).not.toHaveBeenCalled();
  expect(api.askSceneBreak).not.toHaveBeenCalled();
});

test("a break question that fails never surfaces an error over the play view", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  vi.mocked(api.roll).mockResolvedValue(fixture({ ok: true, message: "" }));
  vi.mocked(api.askSceneBreak).mockRejectedValue(
    new ApiError(409, "OpenRouter key not set", "missing_key"));
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(screen.getByRole("button", { name: "Roll dice" }));
  fireEvent.change(screen.getByLabelText("Dice notation"), { target: { value: "2d6" } });
  fireEvent.click(screen.getByRole("button", { name: "Roll ▸" }));
  await waitFor(() => expect(api.askSceneBreak).toHaveBeenCalled());
  expect(screen.queryByText(/OpenRouter key not set/)).toBeNull();
});

test("a failed summary refold does not take the break question down with it", async () => {
  // Fired separately rather than chained, precisely so neither is a
  // precondition for the other.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  vi.mocked(api.roll).mockResolvedValue(fixture({ ok: true, message: "" }));
  vi.mocked(api.refreshRollingSummary).mockRejectedValue(
    new ApiError(409, "OpenRouter key not set", "missing_key"));
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(screen.getByRole("button", { name: "Roll dice" }));
  fireEvent.change(screen.getByLabelText("Dice notation"), { target: { value: "2d6" } });
  fireEvent.click(screen.getByRole("button", { name: "Roll ▸" }));
  await waitFor(() => expect(api.askSceneBreak).toHaveBeenCalled());
});

test("a manual dice roll also asks whether the scene has reached a break", async () => {
  // Rolls append narrator posts, so a mechanics-heavy stretch of play can cross
  // the cadence with no generated turn to carry the request.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  vi.mocked(api.roll).mockResolvedValue(fixture({ ok: true, message: "" }));
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(screen.getByRole("button", { name: "Roll dice" }));
  fireEvent.change(screen.getByLabelText("Dice notation"), { target: { value: "2d6" } });
  fireEvent.click(screen.getByRole("button", { name: "Roll ▸" }));
  await waitFor(() => expect(api.askSceneBreak).toHaveBeenCalledWith(
    "run", "s1", false, expect.anything()));
});

test("a refresh that fails never surfaces an error over the play view", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  vi.mocked(api.roll).mockResolvedValue(fixture({ ok: true, message: "" }));
  vi.mocked(api.refreshRollingSummary).mockRejectedValue(
    new ApiError(409, "OpenRouter key not set", "missing_key"));
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(screen.getByRole("button", { name: "Roll dice" }));
  fireEvent.change(screen.getByLabelText("Dice notation"), { target: { value: "2d6" } });
  fireEvent.click(screen.getByRole("button", { name: "Roll ▸" }));
  await waitFor(() => expect(api.refreshRollingSummary).toHaveBeenCalled());
  // The summary is a background reading aid; a play session must not be
  // interrupted by one that could not be written.
  expect(screen.queryByText(/OpenRouter key not set/)).toBeNull();
});

test("a roll does not wait on the summary refresh", async () => {
  // "Non-blocking" is the property, and the roll is where it can still be
  // observed from this side: the turn's own follow-ups are the server's now
  // (#397), so there is nothing left in `runStream` for a hung fold to hold up.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  vi.mocked(api.roll).mockResolvedValue(fixture({ ok: true, message: "" }));
  let release: (() => void) | undefined;
  vi.mocked(api.refreshRollingSummary).mockImplementation(
    () => new Promise((resolve) => { release = () => resolve({
      summary: "Late.", at: 1, total: 1, stale: false, every: 10, due: false,
      refreshed: true }); }));
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(screen.getByRole("button", { name: "Roll dice" }));
  fireEvent.change(screen.getByLabelText("Dice notation"), { target: { value: "2d6" } });
  fireEvent.click(screen.getByRole("button", { name: "Roll ▸" }));
  // The roll is over — its form is gone and the composer takes input again —
  // while the refresh is still unresolved.
  await waitFor(() => expect(api.roll).toHaveBeenCalled());
  expect(await screen.findByRole("button", { name: /(send ▸|continue ▶)/i }))
    .not.toBeDisabled();
  expect(release).toBeDefined();      // ...and the refresh really is still in flight
  await act(async () => { release!(); });
});

test("a manual dice roll also asks whether the summary is due", async () => {
  // Rolls append narrator posts, so a mechanics-heavy stretch of play can cross
  // the threshold with no generated turn to carry the request (#85).
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  vi.mocked(api.roll).mockResolvedValue(fixture({ ok: true, message: "" }));
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(screen.getByRole("button", { name: "Roll dice" }));
  fireEvent.change(screen.getByLabelText("Dice notation"), { target: { value: "2d6" } });
  fireEvent.click(screen.getByRole("button", { name: "Roll ▸" }));
  await waitFor(() => expect(api.roll).toHaveBeenCalled());
  // Without `force`, exactly like the per-turn call: the server still decides.
  await waitFor(() => expect(api.refreshRollingSummary).toHaveBeenCalledWith(
    "run", "s1", false, expect.anything()));
});

test("a check also asks whether the summary is due", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  vi.mocked(api.getSceneChecks).mockResolvedValue({ actors: [
    { ref: "characters:mara", label: "Mara", sheet_type: "vampire",
      checks: [["brawl", "Vigor + Brawl"]] },
  ] });
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(screen.getByRole("button", { name: "Roll dice" }));
  fireEvent.click(screen.getByRole("button", { name: "Check" }));
  fireEvent.change(await screen.findByLabelText("Check actor"),
                   { target: { value: "characters:mara" } });
  fireEvent.change(screen.getByLabelText("Check"), { target: { value: "brawl" } });
  fireEvent.click(screen.getByRole("button", { name: "Roll ▸" }));
  await waitFor(() => expect(api.rollCheck).toHaveBeenCalled());
  await waitFor(() => expect(api.refreshRollingSummary).toHaveBeenCalledWith(
    "run", "s1", false, expect.anything()));
});

test("a replay reroll leaves its follow-ups to the server", async () => {
  // `ReplayPanel.again` goes through `/regenerate`, which schedules both
  // server-side (#397). Asking from here as well would put a second
  // scene-break question at the provider: that route has no in-flight
  // coalescing, so both are billed and one answer is thrown away as
  // superseded. Every other replay action still asks — a cut, an accept and a
  // replayed turn schedule nothing of their own.
  twoPostScene();
  renderCampaign();
  await screen.findByText("a reply");
  vi.mocked(api.refreshRollingSummary).mockClear();
  vi.mocked(api.askSceneBreak).mockClear();

  fireEvent.click(await screen.findByText("stub-replay-changed-asked"));

  await waitFor(() => expect(api.getScene).toHaveBeenCalled());
  expect(api.refreshRollingSummary).not.toHaveBeenCalled();
  expect(api.askSceneBreak).not.toHaveBeenCalled();

  // ...and the plain report still does ask, so this is about the flag rather
  // than about the panel having stopped reporting at all.
  fireEvent.click(screen.getByText("stub-replay-changed"));
  await waitFor(() => expect(api.refreshRollingSummary).toHaveBeenCalled());
});

test("a write whose re-read never landed asks for no fold at all", async () => {
  // The boundary passed to the fold comes from a re-read. When that read is
  // retired — a newer write superseded it, or it failed outright — there is no
  // verified boundary, and the earlier code fell back to an UNBOUNDED request.
  // That is backwards: the case with a newer turn in flight is exactly the case
  // where an unbounded fold can cover a player post whose reply has not been
  // written yet, keeping that reply out of the summary until another threshold.
  // Nothing is the right thing to send (#85).
  //
  // Driven through scene birth rather than through a turn: `refreshAndAsk` is
  // the caller that swallows a failed read into a −1 boundary, and the turn's
  // own follow-ups moved server-side (#397).
  vi.mocked(api.listScenes)
    .mockResolvedValueOnce(ONE_SCENE)                // initial load
    .mockResolvedValue([{ id: "s9", title: "New", model: "", created: "", updated: "", date: "" }]);
  renderCampaign();
  await screen.findByText(/Run One/);
  fireEvent.click(screen.getByRole("button", { name: /\+ new scene/i }));
  vi.mocked(api.refreshRollingSummary).mockClear();
  vi.mocked(api.getScene).mockRejectedValue(new ApiError(503, "store busy", "busy"));
  fireEvent.click(await screen.findByText("stub-pick"));
  await waitFor(() => expect(api.getScene).toHaveBeenCalledWith(
    "run", "s9", expect.anything()));
  expect(api.refreshRollingSummary).not.toHaveBeenCalled();
});

test("saving an edit asks whether the summary is due", async () => {
  // An edit appends nothing, so it never crosses the threshold by count — but
  // it rewrites text the stored summary already covers, which moves the fold's
  // validity key and makes a from-scratch refold due. Without this the panel
  // could only flag the summary stale and wait for a generated turn (#85).
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: { id: "s1" }, messages: [
    { role: "assistant", content: "hi" }] }));
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getAllByTitle("Edit message")[0]);
  fireEvent.change(await screen.findByLabelText(/edit message/i),
                   { target: { value: "hello" } });
  fireEvent.click(screen.getByRole("button", { name: /save/i }));
  await waitFor(() => expect(api.editMessage).toHaveBeenCalled());
  await waitFor(() => expect(api.refreshRollingSummary).toHaveBeenCalledWith(
    "run", "s1", false, expect.any(Number)));
});

test("swapping to another take asks whether the summary is due", async () => {
  // Same reasoning as the edit: the transcript is no shorter or longer, but the
  // words the summary covers are a take the player just rejected (#85).
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "fresh reply" }] }));
  vi.mocked(api.getAlternates).mockResolvedValue({
    active: 1, alternates: [ALT("old reply"), ALT("fresh reply")] });
  renderCampaign();
  await screen.findByText("2/2");
  vi.mocked(api.refreshRollingSummary).mockClear();

  fireEvent.click(screen.getByRole("button", { name: /previous alternate/i }));

  await waitFor(() => expect(api.pickAlternate).toHaveBeenCalled());
  await waitFor(() => expect(api.refreshRollingSummary).toHaveBeenCalledWith(
    "run", "s1", false, expect.any(Number)));
});

test("a scene born from a greeting asks whether it is already due", async () => {
  // Starting a scene from a greeting appends that greeting's posts, and a
  // multi-speaker one appends several — so a scene can be past the threshold
  // before anyone plays a turn in it. None of this component's other triggers
  // fire here: they all hang off a write the reader made in an open scene (#85).
  vi.mocked(api.listScenes)
    .mockResolvedValueOnce(ONE_SCENE)                // initial load
    .mockResolvedValue([{ id: "s9", title: "New", model: "", created: "", updated: "", date: "" }]);
  renderCampaign();
  await screen.findByText(/Run One/);
  fireEvent.click(screen.getByRole("button", { name: /\+ new scene/i }));
  vi.mocked(api.refreshRollingSummary).mockClear();
  fireEvent.click(await screen.findByText("stub-pick"));
  await waitFor(() => expect(api.refreshRollingSummary).toHaveBeenCalledWith(
    "run", "s9", false, expect.any(Number)));
});

test("adopting a generated opener asks whether the summary is due", async () => {
  // `firstPost` persists the opener as the scene's first post — a transcript
  // write like any other, and the only one the cast panel makes (#85).
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [] }));
  renderCampaign();
  await screen.findByTestId("cast-panel");
  vi.mocked(api.refreshRollingSummary).mockClear();

  fireEvent.click(screen.getByText("stub-seeded"));

  await waitFor(() => expect(api.refreshRollingSummary).toHaveBeenCalledWith(
    "run", "s1", false, expect.any(Number)));
});
