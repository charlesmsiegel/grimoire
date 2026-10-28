import { screen, fireEvent, waitFor, act, within } from "@testing-library/react";
import { api, ApiError } from "../api/client";
import type { SceneMeta } from "../api/client";
import { here, installCampaignMocks, ONE_SCENE, renderCampaign } from "../testkit/campaignHarness";
import { fixture } from "../testkit/fixture";
import { twoPostScene } from "../testkit/campaignFixtures";

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



// --- branching (play controls III) -------------------------------------------

// The sibling is listed, as the relist after a branch would list it: a scene id
// the list does not hold is one the view redirects away from.
const WITH_SIBLING: SceneMeta[] = [...ONE_SCENE,
  { id: "s1-b", title: "Old (branch)", model: "", created: "", updated: "", date: "" }];

test("⑂ on an unabsorbed scene branches and opens the sibling", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(WITH_SIBLING);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "Mara waits." },
    { role: "assistant", speaker: "Mara", content: "The tide turns." }] }));
  renderCampaign();
  fireEvent.click(await screen.findByRole("button", { name: "Branch from message 1" }));
  await waitFor(() => expect(api.branchScene).toHaveBeenCalledWith("run", "s1", 0));
  await waitFor(() => expect(here()).toBe("/campaigns/run/scenes/s1-b"));
  expect(api.forkCampaign).not.toHaveBeenCalled();
});

// The sibling does not exist until the branch does, so the list the view holds
// when ⑂ is clicked lacks it. Navigating before the relist installs hands the
// resolver an id the installed list does not know, which it reads as stale and
// replaces with the scene the reader was on (codex review, PR #458).
function relistAddsSiblingLate() {
  let release: () => void = () => {};
  const late = new Promise<void>((resolve) => { release = resolve; });
  let branched = false;
  vi.mocked(api.branchScene).mockImplementation(async () => {
    branched = true;
    return fixture({ id: "s1-b", scene: { meta: {}, messages: [] } });
  });
  vi.mocked(api.listScenes).mockImplementation(async () => {
    if (!branched) return ONE_SCENE;
    await late;
    return WITH_SIBLING;
  });
  return () => release();
}

test("⑂ waits for the relist that lists the sibling before opening it", async () => {
  const release = relistAddsSiblingLate();
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "Mara waits." },
    { role: "assistant", speaker: "Mara", content: "The tide turns." }] }));
  renderCampaign();
  fireEvent.click(await screen.findByRole("button", { name: "Branch from message 1" }));
  await waitFor(() => expect(api.branchScene).toHaveBeenCalled());
  await act(async () => release());
  await waitFor(() => expect(here()).toBe("/campaigns/run/scenes/s1-b"));
});

test("⑂ whose relist fails says so instead of opening a sibling the list lacks", async () => {
  let branched = false;
  vi.mocked(api.branchScene).mockImplementation(async () => {
    branched = true;
    return fixture({ id: "s1-b", scene: { meta: {}, messages: [] } });
  });
  vi.mocked(api.listScenes).mockImplementation(async () => {
    if (branched) throw new ApiError(500, "list down");
    return ONE_SCENE;
  });
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "Mara waits." },
    { role: "assistant", speaker: "Mara", content: "The tide turns." }] }));
  renderCampaign();
  fireEvent.click(await screen.findByRole("button", { name: "Branch from message 1" }));
  expect(await screen.findByText(/branch was created, but the scene list could not be refreshed: list down/i))
    .toBeInTheDocument();
  expect(here()).toBe("/campaigns/run/scenes/s1");
});

test("⑂ is held while its branch request is in flight, so a double click makes one", async () => {
  let finish: () => void = () => {};
  vi.mocked(api.branchScene).mockImplementation(() => new Promise((resolve) => {
    finish = () => resolve(fixture({ id: "s1-b", scene: { meta: {}, messages: [] } }));
  }));
  vi.mocked(api.listScenes).mockResolvedValue(WITH_SIBLING);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "Mara waits." },
    { role: "assistant", speaker: "Mara", content: "The tide turns." }] }));
  renderCampaign();
  const button = await screen.findByRole("button", { name: "Branch from message 1" });
  fireEvent.click(button);
  await waitFor(() => expect(api.branchScene).toHaveBeenCalledTimes(1));
  expect(screen.getByRole("button", { name: "Branch from message 1" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Branch from message 1" }));
  await act(async () => finish());
  await waitFor(() => expect(here()).toBe("/campaigns/run/scenes/s1-b"));
  expect(api.branchScene).toHaveBeenCalledTimes(1);
});

test("⑂ on an absorbed scene forks the campaign at that post", async () => {
  vi.mocked(api.listScenes).mockResolvedValue([{ ...ONE_SCENE[0], done: true }]);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "Mara waits." },
    { role: "assistant", speaker: "Mara", content: "The tide turns." }] }));
  vi.mocked(api.forkCampaign).mockResolvedValue({
    id: "branch", from_scene: "s1", removed_scenes: [], records: 0, refused: [], failed: [],
    replayed: false, cut_at: 1 });
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.spyOn(window, "prompt").mockReturnValue("Run One (fork)");
  renderCampaign();
  await screen.findByText(/scene complete/i);
  fireEvent.click(await screen.findByRole("button", { name: "Branch from message 2" }));
  await waitFor(() =>
    expect(api.forkCampaign).toHaveBeenCalledWith("run", "Run One (fork)", "s1", {}, 1));
  expect(confirm.mock.calls[0][0]).toContain("copy of the campaign");
  expect(api.branchScene).not.toHaveBeenCalled();
});

const CLOSED_SCENES: SceneMeta[] = [
  { ...ONE_SCENE[0], branch_group: "g", closed_by: { sid: "s0", title: "Mara" } },
  { id: "s0", title: "Mara", model: "", created: "", updated: "", date: "", done: true, branch_group: "g" },
];

test("a closed branch shows the banner and offers nothing to write with", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(CLOSED_SCENES);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  renderCampaign();
  await screen.findByText("a reply");
  await screen.findByText(/A sibling branch was absorbed/);
  expect(screen.getByRole("link", { name: "Mara" })).toHaveAttribute(
    "href", "/campaigns/run/scenes/s0");
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(screen.queryByRole("button", { name: "Branch from message 1" })).toBeNull();
  expect(screen.queryByLabelText("Edit message 1")).toBeNull();
  expect(screen.queryByRole("button", { name: "End scene" })).toBeNull();
  const head = document.querySelector(".scene-head")!;
  expect(within(head as HTMLElement).getByText("closed")).toHaveAttribute(
    "title", "A sibling branch was absorbed");
  expect(within(head as HTMLElement).getByText("branch")).toBeInTheDocument();
  // A replay running in it can be accepted or stopped, never stepped.
  expect(screen.getByTestId("replay-panel").getAttribute("data-closed")).toBe("true");
  fireEvent.keyDown(window, { key: "r" });
  expect(screen.queryByLabelText("Reroll guidance")).toBeNull();
});

test("an open branch keeps its composer and wears a branch chip", async () => {
  vi.mocked(api.listScenes).mockResolvedValue([
    { ...ONE_SCENE[0], branch_group: "g" },
    { id: "s0", title: "Mara", model: "", created: "", updated: "", date: "", branch_group: "g" },
  ]);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  renderCampaign();
  await screen.findByText("a reply");
  const head = document.querySelector(".scene-head")!;
  await waitFor(() => expect(within(head as HTMLElement).getByText("branch")).toBeInTheDocument());
  expect(within(head as HTMLElement).queryByText("closed")).toBeNull();
  expect(screen.queryByText(/A sibling branch was absorbed/)).toBeNull();
  expect(await screen.findByRole("button", { name: "Branch from message 1" })).toBeInTheDocument();
  expect(screen.getByTestId("replay-panel").getAttribute("data-closed")).toBe("false");
});

test("the replay dialog can branch on an unabsorbed scene, and follows the branch", async () => {
  twoPostScene();
  vi.mocked(api.listScenes).mockResolvedValue(WITH_SIBLING);
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(await screen.findByLabelText("Replay the turns after message 1"));
  const panel = screen.getByTestId("replay-panel");
  expect(panel.getAttribute("data-branchable")).toBe("true");
  fireEvent.click(within(panel).getByText("stub-replay-branched"));
  await waitFor(() => expect(here()).toBe("/campaigns/run/scenes/s1-b"));
});

test("a replay in a branch opens the sibling only once the relist lists it", async () => {
  twoPostScene();
  let release: () => void = () => {};
  const late = new Promise<void>((resolve) => { release = resolve; });
  let branched = false;
  vi.mocked(api.listScenes).mockImplementation(async () => {
    if (!branched) return ONE_SCENE;
    await late;
    return WITH_SIBLING;
  });
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(await screen.findByLabelText("Replay the turns after message 1"));
  const panel = screen.getByTestId("replay-panel");
  branched = true;
  fireEvent.click(within(panel).getByText("stub-replay-branched"));
  await act(async () => release());
  await waitFor(() => expect(here()).toBe("/campaigns/run/scenes/s1-b"));
});

test("an absorbed scene's replay dialog does not offer a branch", async () => {
  vi.mocked(api.listScenes).mockResolvedValue([{ ...ONE_SCENE[0], done: true }]);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  renderCampaign();
  await screen.findByText(/scene complete/i);
  expect(screen.getByTestId("replay-panel").getAttribute("data-branchable")).toBe("false");
});
