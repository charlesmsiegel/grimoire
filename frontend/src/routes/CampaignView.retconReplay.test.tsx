import { screen, fireEvent, waitFor } from "@testing-library/react";
import { api } from "../api/client";
import type { RetconReport } from "../api/client";
import { installCampaignMocks, ONE_SCENE, openScene, renderCampaign } from "../testkit/campaignHarness";
import { fixture } from "../testkit/fixture";
import { CASCADE_OK, twoPostScene, DONE_SCENE, TWO_SCENES } from "../testkit/campaignFixtures";

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



// ---- retcon and its replay (#78/#79/#80) ----
//
// Retcon sits beside Save in the edit form rather than replacing it, because
// the two are different intentions: Save fixes the words, Retcon says the scene
// did not happen that way and takes back what it recorded. The gutter's ⏩ is
// the escalation — re-run the turns that followed — and is priced before
// anything is cut.

const RETCON_OK: RetconReport = { ...CASCADE_OK, later: [] };

test("retcon rewrites the post through the retcon route, not the plain edit", async () => {
  twoPostScene();
  vi.mocked(api.retconMessage).mockResolvedValue(RETCON_OK);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  renderCampaign();
  await screen.findByText("a reply");

  fireEvent.click((await screen.findAllByTitle("Edit message"))[0]);
  const ta = await screen.findByRole("textbox", { name: "Edit message" });
  fireEvent.change(ta, { target: { value: "she never said it" } });
  fireEvent.click(screen.getByRole("button", { name: /^retcon$/i }));
  await waitFor(() =>
    expect(api.retconMessage).toHaveBeenCalledWith("run", "s1", 0, "she never said it"));
  expect(api.editMessage).not.toHaveBeenCalled();
});

test("the retcon confirm names what an absorbed scene gives back", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(DONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  vi.mocked(api.retconMessage).mockResolvedValue({ ...RETCON_OK, was_absorbed: true });
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  renderCampaign();
  await screen.findByText("a reply");

  fireEvent.click((await screen.findAllByTitle("Edit message"))[0]);
  fireEvent.click(screen.getByRole("button", { name: /^retcon$/i }));
  // Awaited before reading the prompt: the click's own work (the retcon, the
  // rail re-read, the refresh) settles inside the test rather than after it.
  await waitFor(() => expect(api.retconMessage).toHaveBeenCalled());
  await waitFor(() => expect(vi.mocked(api.listScenes).mock.calls.length).toBeGreaterThan(1));
  const asked = confirm.mock.calls[0][0] as string;
  expect(asked).toMatch(/chronicle record/i);
  // The half a reader is most likely to get wrong: a retcon is not a cut.
  expect(asked).toMatch(/posts after it are left alone/i);
});

test("declining the retcon confirm does nothing", async () => {
  twoPostScene();
  vi.spyOn(window, "confirm").mockReturnValue(false);
  renderCampaign();
  await screen.findByText("a reply");

  fireEvent.click((await screen.findAllByTitle("Edit message"))[0]);
  fireEvent.click(screen.getByRole("button", { name: /^retcon$/i }));
  expect(api.retconMessage).not.toHaveBeenCalled();
});

test("a retcon says how many later scenes can now disagree", async () => {
  twoPostScene();
  vi.mocked(api.retconMessage).mockResolvedValue(
    { ...RETCON_OK, was_absorbed: true, later: ["002--the-long-quay", "003--low-water"] });
  vi.spyOn(window, "confirm").mockReturnValue(true);
  renderCampaign();
  await screen.findByText("a reply");

  fireEvent.click((await screen.findAllByTitle("Edit message"))[0]);
  fireEvent.click(screen.getByRole("button", { name: /^retcon$/i }));
  // The whole point of re-absorbing: the badges are on the review, and nothing
  // else tells the reader they are worth going to look at.
  await screen.findByText(/2 later scenes already recorded state/i);
});

test("the gutter offers a replay only where there is something after the post", async () => {
  twoPostScene();
  renderCampaign();
  await screen.findByText("a reply");
  // Awaited: the gutter is gated on `transcriptIsActive`, which settles a tick
  // after the posts themselves render — grabbing it the instant the text lands
  // is the racy pattern, not a stable one.
  await screen.findByLabelText("Replay the turns after message 1");
  // Nothing follows the last post, so there is no turn to replay.
  expect(screen.queryByLabelText("Replay the turns after message 2")).toBeNull();
});

test("the gutter hands the panel the post AFTER the one clicked", async () => {
  // The retconned post stands; what a replay redoes is everything past it. The
  // panel's own tests cover what it then does with that index.
  twoPostScene();
  renderCampaign();
  await screen.findByText("a reply");
  expect(screen.getByTestId("replay-panel").getAttribute("data-start-at")).toBe("");

  fireEvent.click(await screen.findByLabelText("Replay the turns after message 1"));
  await waitFor(() =>
    expect(screen.getByTestId("replay-panel").getAttribute("data-start-at")).toBe("1"));
  expect(api.startReplay).not.toHaveBeenCalled();
});

test("forking lands in the same scene of the copy, with the ask still standing", async () => {
  // A fork copies the scenes wholesale, so the id is there — and the reader
  // asked to replay one particular post, not to be dropped at a front door.
  //
  // The fork's rail is ordered so the two answers differ: dropped at the
  // campaign, the reader is redirected to the copy's most recent scene (s2),
  // which is not the one they were replaying.
  vi.mocked(api.listScenes).mockResolvedValue(TWO_SCENES);
  vi.mocked(api.getScene).mockImplementation(async (_c, sid) => fixture({
    meta: { id: sid }, messages: [{ role: "user", content: `hi from ${sid}` },
                                  { role: "assistant", content: `a reply in ${sid}` }],
  }));
  renderCampaign();
  await screen.findByText("a reply in s1");
  fireEvent.click(await screen.findByLabelText("Replay the turns after message 1"));
  await waitFor(() =>
    expect(screen.getByTestId("replay-panel").getAttribute("data-start-at")).toBe("1"));

  vi.mocked(api.listScenes).mockResolvedValue(
    [...TWO_SCENES].sort((a, b) => (a.id < b.id ? 1 : -1)));   // s2 first in the copy
  fireEvent.click(screen.getByText("stub-replay-forked"));
  await waitFor(() =>
    expect(screen.getByTestId("here").textContent).toBe("/campaigns/forked/scenes/s1"));
  // ... and the post they asked about is still the one the panel is holding.
  expect(screen.getByTestId("replay-panel").getAttribute("data-start-at")).toBe("1");
});

test("a replay step asks for a fresh rolling summary", async () => {
  // The stored fold covers a prefix the walk has just cut, regenerated or put
  // back, so its digest no longer describes the transcript (#85).
  twoPostScene();
  renderCampaign();
  await screen.findByText("a reply");
  vi.mocked(api.getRollingSummary).mockClear?.();

  fireEvent.click(screen.getByText("stub-replay-changed"));
  await waitFor(() => expect(vi.mocked(api.listScenes).mock.calls.length).toBeGreaterThan(1));
  await waitFor(() => expect(api.refreshRollingSummary).toHaveBeenCalled());
});

test("the asked-for post does not follow the reader into another scene", async () => {
  // An index means nothing in another scene: carried across, it would price —
  // and cut — whatever post happens to sit there.
  vi.mocked(api.listScenes).mockResolvedValue(TWO_SCENES);
  vi.mocked(api.getScene).mockImplementation(async (_c, sid) => fixture({
    meta: { id: sid }, messages: [{ role: "user", content: `hi from ${sid}` },
                                  { role: "assistant", content: `a reply in ${sid}` }],
  }));
  renderCampaign();
  await screen.findByText("a reply in s1");
  fireEvent.click(await screen.findByLabelText("Replay the turns after message 1"));
  await waitFor(() =>
    expect(screen.getByTestId("replay-panel").getAttribute("data-start-at")).toBe("1"));

  await openScene(/The Saltmarch Gate/);
  await screen.findByText("a reply in s2");
  expect(screen.getByTestId("replay-panel").getAttribute("data-start-at")).toBe("");
});

test("a replay request latches the transcript against a second generation", async () => {
  // The panel takes the same latch retry, reroll and the roll paths take —
  // which is what stops the composer and the gutter offering to start another
  // generation into the scene a replayed turn is being written into.
  twoPostScene();
  renderCampaign();
  await screen.findByText("a reply");
  expect(await screen.findByLabelText("Delete message 1 and everything after it"))
    .not.toBeDisabled();

  fireEvent.click(screen.getByText("stub-replay-latch"));
  await waitFor(() =>
    expect(screen.getByLabelText("Delete message 1 and everything after it")).toBeDisabled());
});

test("a manual dice roll's transcript line has no Edit control", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "assistant", content: "an ordinary reply" },
    { role: "assistant", content: "🎲 2d6 = 7", speaker: "⁣Roll" }] });
  renderCampaign();
  await screen.findByText(/2d6 = 7/);
  expect(screen.getAllByTitle("Edit message")).toHaveLength(1);
  // The cascade cut IS offered on it (#75). Edit is refused because a roll
  // line's text must stay in lockstep with the immutable rolls.json entry; a cut
  // removes the line rather than rewriting it, and the ledger entry survives.
  expect(screen.getByLabelText("Delete message 2 and everything after it")).toBeTruthy();
});

// Author notes live in the transcript as HTML comments: kept in the stored text
// so they survive an edit, but never shown to the reader alongside the prose.
test("HTML comments are invisible in the rendered transcript but survive into the edit box", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "assistant",
      content: "The tide turns.\n\n<!-- remember: she is lying here -->\n\nShe looks away." }] });
  renderCampaign();
  await screen.findByText("The tide turns.");
  expect(screen.getByText("She looks away.")).toBeInTheDocument();
  // the note itself is nowhere in what the reader sees
  expect(screen.queryByText(/remember: she is lying/)).toBeNull();
  expect(document.body.textContent).not.toContain("remember: she is lying");

  // ...but editing the message hands back the whole stored text, comment included
  fireEvent.click(screen.getByTitle("Edit message"));
  expect(await screen.findByLabelText("Edit message"))
    .toHaveValue("The tide turns.\n\n<!-- remember: she is lying here -->\n\nShe looks away.");
});

test("an inline HTML comment is invisible without breaking the sentence around it", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "assistant", content: "She smiles <!-- beat --> and turns away." }] });
  renderCampaign();
  await waitFor(() => expect(document.body.textContent).toContain("She smiles"));
  expect(document.body.textContent).not.toContain("beat");
  expect(document.body.textContent).toContain("and turns away.");
});

test("a complete scene replaces the composer with a notice", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(DONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  renderCampaign();
  await screen.findByText("a reply");

  // Waited for, not asserted flat: the notice renders off `activeDone`, which
  // the scene LIST supplies, while the reply above comes from the transcript
  // read. Two chains are two commits, so a poll that lands between them sees
  // the reply without the notice — and the five absence assertions below would
  // then be measuring a composer that had simply not been re-rendered yet.
  await screen.findByText(/scene complete/i);
  // the whole composer, not a disabled entry box: a greyed-out one still says
  // "you could type here"
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(screen.queryByRole("button", { name: /continue ▶/i })).toBeNull();
  expect(screen.queryByRole("button", { name: /send ▸/i })).toBeNull();
  expect(screen.queryByLabelText("Next reply words")).toBeNull();
  expect(screen.queryByRole("button", { name: "Roll dice" })).toBeNull();
});

test("an unfinished scene keeps its composer", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  renderCampaign();
  await screen.findByText("a reply");
  expect(screen.queryByText(/scene complete/i)).toBeNull();
  expect(screen.getByRole("textbox")).toBeInTheDocument();
});

// Editing and rerolling stay available on a finished scene: those change what
// was absorbed rather than adding to a scene that is closed.
test("a complete scene can still have its existing posts edited", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(DONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  renderCampaign();
  await screen.findByText("a reply");
  expect(screen.getAllByTitle("Edit message").length).toBeGreaterThan(0);
});

test("⌘K marks an absorbed scene and leaves the others unmarked", async () => {
  vi.mocked(api.listScenes).mockResolvedValue([
    { id: "002--two", title: "Second", model: "", created: "", updated: "2026-01-02", date: "", done: true },
    { id: "001--one", title: "First", model: "", created: "", updated: "2026-01-01", date: "" },
  ]);
  renderCampaign();
  await screen.findByRole("heading", { name: /Second/ });
  fireEvent.keyDown(window, { key: "k", metaKey: true });
  expect(await screen.findByRole("option", { name: /Second.*absorbed/i })).toBeInTheDocument();
  expect(screen.getByRole("option", { name: /First/ })).not.toHaveTextContent(/absorbed/i);
});
