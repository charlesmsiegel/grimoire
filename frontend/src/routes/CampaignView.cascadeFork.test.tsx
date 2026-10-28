import { screen, fireEvent, waitFor, act, within } from "@testing-library/react";
import { api } from "../api/client";
import type { CascadeReport, ForkReport, SceneMeta } from "../api/client";
import { DIRECTOR_SPEAKER, ROLL_SPEAKER, TRANSITION_SPEAKER } from "../components/play/TranscriptPost";
import { here, installCampaignMocks, ONE_SCENE, renderCampaign } from "../testkit/campaignHarness";
import { fixture } from "../testkit/fixture";
import { CASCADE_OK, twoPostScene, DONE_SCENE } from "../testkit/campaignFixtures";

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



test("the cut deletes the post and everything after it", async () => {
  twoPostScene();
  vi.mocked(api.deleteMessagesFrom).mockResolvedValue(CASCADE_OK);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  renderCampaign();
  await screen.findByText("a reply");

  fireEvent.click(screen.getByLabelText("Delete message 1 and everything after it"));
  await waitFor(() => expect(api.deleteMessagesFrom).toHaveBeenCalledWith("run", "s1", 0));
  // The rail is re-read too: an absorbed scene has just stopped being done, and
  // the composer is hidden off that flag.
  await waitFor(() => expect(vi.mocked(api.listScenes).mock.calls.length).toBeGreaterThan(1));
});

test("declining the cut's confirm does nothing", async () => {
  twoPostScene();
  vi.spyOn(window, "confirm").mockReturnValue(false);
  renderCampaign();
  await screen.findByText("a reply");

  fireEvent.click(screen.getByLabelText("Delete message 2 and everything after it"));
  expect(api.deleteMessagesFrom).not.toHaveBeenCalled();
});

test("the confirm counts the posts and names what an absorbed scene loses", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(DONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" },
    { role: "user", content: "and then?" }] });
  vi.mocked(api.deleteMessagesFrom).mockResolvedValue({ ...CASCADE_OK, was_absorbed: true });
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  renderCampaign();
  await screen.findByText("and then?");

  fireEvent.click(screen.getByLabelText("Delete message 2 and everything after it"));
  const asked = confirm.mock.calls[0][0] as string;
  expect(asked).toContain("this post and the 1 after it");
  expect(asked).toMatch(/chronicle record/i);
  // The two records this deliberately does not touch, said before the fact.
  expect(asked).toMatch(/roll log/i);
  expect(asked).toMatch(/timeline/i);
});

// ---- hide a post from context ----

test("the hide toggle is on player and model posts only", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" },
    { role: "assistant", speaker: ROLL_SPEAKER, content: "🎲 1d20 = 12" },
    { role: "assistant", speaker: TRANSITION_SPEAKER, content: "*Time passes.*" },
    { role: "assistant", speaker: DIRECTOR_SPEAKER, content: "faster" }] });
  renderCampaign();
  await screen.findByText("a reply");
  // The note is not drawn until asked for, so the count below would be vacuous
  // for it: show it first, then assert it still has no toggle.
  fireEvent.click(screen.getByRole("button", { name: "Show director notes · 1" }));
  const note = (await screen.findByText("faster")).closest(".msg") as HTMLElement;
  expect(within(note).queryByTitle("Hide from context")).toBeNull();
  expect(screen.getAllByTitle("Hide from context")).toHaveLength(2);
  expect(screen.getByLabelText("Hide message 1 from context")).toBeTruthy();
  expect(screen.getByLabelText("Hide message 2 from context")).toBeTruthy();
});

test("hiding a post calls setExcluded and reloads the scene", async () => {
  twoPostScene();
  renderCampaign();
  await screen.findByText("a reply");
  const loads = vi.mocked(api.getScene).mock.calls.length;
  const toggle = screen.getAllByTitle("Hide from context")[0];
  expect(toggle.getAttribute("aria-pressed")).toBe("false");
  fireEvent.click(toggle);
  await waitFor(() => expect(api.setExcluded).toHaveBeenCalledWith("run", "s1", 0, true));
  await waitFor(() => expect(vi.mocked(api.getScene).mock.calls.length).toBeGreaterThan(loads));
});

test("an excluded post is marked and its toggle is pressed", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "user", content: "hi", excluded: "2026-10-05T12:00:00Z" },
    { role: "assistant", content: "a reply" }] });
  renderCampaign();
  await screen.findByText("a reply");
  const toggle = screen.getByTitle("Return to context");
  expect(toggle.getAttribute("aria-pressed")).toBe("true");
  expect(toggle.getAttribute("aria-label")).toBe("Return message 1 to context");
  expect(toggle.closest(".msg")?.classList.contains("excluded")).toBe(true);
  expect(screen.getByText("a reply").closest(".msg")?.classList.contains("excluded")).toBe(false);
  expect(screen.getAllByText("not in context")).toHaveLength(1);
  fireEvent.click(toggle);
  await waitFor(() => expect(api.setExcluded).toHaveBeenCalledWith("run", "s1", 0, false));
});

test("an absorbed scene offers no hide toggle", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(DONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  renderCampaign();
  await screen.findByText("a reply");
  await screen.findByText(/scene complete/i);
  expect(screen.queryAllByTitle("Hide from context")).toHaveLength(0);
  // The cut is still offered there; it is only the toggle that is withheld.
  expect(screen.getByLabelText("Delete message 1 and everything after it")).toBeTruthy();
});

test("a record the reversal could not put back is reported", async () => {
  twoPostScene();
  vi.mocked(api.deleteMessagesFrom).mockResolvedValue({
    ...CASCADE_OK, was_absorbed: true, records: 1,
    refused: [{ label: "The Pact — lore", reason: "this record changed after the edit" }] });
  vi.spyOn(window, "confirm").mockReturnValue(true);
  renderCampaign();
  await screen.findByText("a reply");

  fireEvent.click(screen.getByLabelText("Delete message 1 and everything after it"));
  // The one outcome the shortened transcript cannot show: the record still holds
  // what the deleted scene gave it.
  await screen.findByText(/The Pact — lore/);
  expect(document.body.textContent).toMatch(/could not be put back/i);
});

// ---- forking at a scene (#72) ----
//
// The ⑂ sits with rename and delete because the scene you are reading is the
// turn you would branch at — but unlike those two it writes nothing here, and
// most of what follows is about saying so.

const FORK_OK = fixture<ForkReport>({ id: "branch", from_scene: "0001--old", removed_scenes: [],
                  records: 0, refused: [], failed: [] });

const FORK_SCENES: SceneMeta[] = [
  { id: "0001--old", title: "Old", model: "", created: "", updated: "", date: "" },
  { id: "0002--newer", title: "Newer", model: "", created: "", updated: "", date: "" },
];

function openAt(sid: string) {
  vi.mocked(api.listScenes).mockResolvedValue(FORK_SCENES);
  vi.mocked(api.getScene).mockResolvedValue({
    meta: { id: sid, title: "Old" }, messages: [{ role: "user", content: "hi" }] });
  renderCampaign(`/campaigns/run/scenes/${sid}`);
}

test("forking at a scene names it, and cuts nothing here", async () => {
  openAt("0001--old");
  vi.mocked(api.forkCampaign).mockResolvedValue(FORK_OK);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  const prompt = vi.spyOn(window, "prompt").mockReturnValue("A Second Run");
  await screen.findByText("hi");

  fireEvent.click(screen.getByLabelText("Fork campaign at this scene"));
  expect(prompt.mock.calls[0][1]).toBe("Run One (fork)");
  await waitFor(() =>
    expect(api.forkCampaign).toHaveBeenCalledWith("run", "A Second Run", "0001--old"));
  expect(api.deleteScene).not.toHaveBeenCalled();
  expect(api.deleteMessagesFrom).not.toHaveBeenCalled();
  // The player asked to branch, so the branch is where they land. A prefix,
  // because the branch has this scene too and its own resolver then opens one.
  await waitFor(() => expect(here()).toMatch(/^\/campaigns\/branch\/scenes/));
});

test("the fork confirm counts the scenes the branch will not have", async () => {
  openAt("0001--old");
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  await screen.findByText("hi");

  fireEvent.click(screen.getByLabelText("Fork campaign at this scene"));
  const asked = confirm.mock.calls[0][0] as string;
  expect(asked).toContain("drops the 1 scene after it");
  // The two things the player cannot check afterwards: what a cut cannot put
  // back, and that this campaign is untouched.
  expect(asked).toMatch(/keep what they hold and are reported/i);
  expect(asked).toMatch(/This campaign is not changed/i);
  expect(api.forkCampaign).not.toHaveBeenCalled();
});

test("forking at the newest scene says it is a copy rather than a cut", async () => {
  openAt("0002--newer");
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
  await screen.findByText("hi");

  fireEvent.click(screen.getByLabelText("Fork campaign at this scene"));
  expect(confirm.mock.calls[0][0]).toMatch(/copy of this campaign as it stands/i);
});

test("what the fork could not put back is reported, and outlives the navigation", async () => {
  openAt("0001--old");
  vi.mocked(api.forkCampaign).mockResolvedValue({
    ...FORK_OK, removed_scenes: ["0002--newer"], records: 1,
    refused: [{ label: "The Pact — lore", reason: "this record changed after the edit" }] });
  vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.spyOn(window, "prompt").mockReturnValue("A Second Run");
  await screen.findByText("hi");

  fireEvent.click(screen.getByLabelText("Fork campaign at this scene"));
  await screen.findByText(/The Pact — lore/);
  expect(document.body.textContent).toMatch(/still holds what a removed scene wrote/i);
  // A fork with refusals does NOT carry the reader off to it. The note is the
  // reason to look at the fork before playing on in it, and leaving for the
  // fork would unmount the only thing reporting it. The fork exists either
  // way -- the shelf and the rail both have it.
  expect(here()).toBe("/campaigns/run/scenes/0001--old");
  expect(document.body.textContent).toMatch(/The Pact — lore/);
});

test("a cancelled fork name forks nothing", async () => {
  openAt("0001--old");
  vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.spyOn(window, "prompt").mockReturnValue(null);
  await screen.findByText("hi");

  fireEvent.click(screen.getByLabelText("Fork campaign at this scene"));
  expect(api.forkCampaign).not.toHaveBeenCalled();
});

test("a fork the server refuses is reported and the reader stays put", async () => {
  // 409 CAMPAIGN BUSY is reachable: a fork holds two campaign locks. Dropped,
  // it would look exactly like a fork that worked and navigated nowhere.
  openAt("0001--old");
  vi.mocked(api.forkCampaign).mockRejectedValue({ detail: "campaign is busy" });
  vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.spyOn(window, "prompt").mockReturnValue("A Second Run");
  await screen.findByText("hi");

  fireEvent.click(screen.getByLabelText("Fork campaign at this scene"));
  await screen.findByText(/campaign is busy/i);
  expect(here()).toMatch(/^\/campaigns\/run/);
});

test("a cut in flight latches the gutter against a second one", async () => {
  twoPostScene();
  let land: (r: CascadeReport) => void = () => {};
  vi.mocked(api.deleteMessagesFrom).mockReturnValue(new Promise((res) => { land = res; }));
  vi.spyOn(window, "confirm").mockReturnValue(true);
  renderCampaign();
  await screen.findByText("a reply");

  const cut = screen.getByLabelText("Delete message 1 and everything after it");
  fireEvent.click(cut);
  await waitFor(() => expect(api.deleteMessagesFrom).toHaveBeenCalledTimes(1));
  // The same latch reroll and the roll paths take. A second cut landing against
  // indices the first is in the middle of moving is the least reversible
  // mistake this view can make.
  await waitFor(() => expect((cut as HTMLButtonElement).disabled).toBe(true));
  fireEvent.click(cut);
  expect(api.deleteMessagesFrom).toHaveBeenCalledTimes(1);

  await act(async () => { land(CASCADE_OK); });
  await waitFor(() => expect(vi.mocked(api.listScenes).mock.calls.length).toBeGreaterThan(1));
});

test("cleanup that could not run is reported separately from a refused record", async () => {
  twoPostScene();
  vi.mocked(api.deleteMessagesFrom).mockResolvedValue({
    ...CASCADE_OK, was_absorbed: true, failed: ["plot_beats"] });
  vi.spyOn(window, "confirm").mockReturnValue(true);
  renderCampaign();
  await screen.findByText("a reply");

  fireEvent.click(screen.getByLabelText("Delete message 1 and everything after it"));
  // A store file the cut could not parse. The cut still happened, so silence
  // would leave stale continuity looking authoritative — and this is NOT the
  // "could not be put back" story, which is about record VALUES.
  await screen.findByText(/could not be cleaned up/i);
  expect(document.body.textContent).toContain("plot_beats");
  expect(document.body.textContent).not.toMatch(/could not be put back/i);
});
