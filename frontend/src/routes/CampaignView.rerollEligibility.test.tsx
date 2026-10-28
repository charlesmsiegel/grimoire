import { screen, fireEvent, within } from "@testing-library/react";
import { api } from "../api/client";
import { installCampaignMocks, ONE_SCENE, renderCampaign } from "../testkit/campaignHarness";
import { fixture } from "../testkit/fixture";

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



test("no Reroll when a manual dice roll trails the assistant reply", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" },
    { role: "assistant", content: "🎲 2d6 = 7", speaker: "⁣Roll" }] }));
  renderCampaign();
  await screen.findByText(/2d6 = 7/);
  expect(screen.queryByRole("button", { name: /reroll/i })).toBeNull();
});

test("Reroll is offered when the last post is merely spoken by a character actually named Roll", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "hello", speaker: "Roll" }] }));
  renderCampaign();
  await screen.findByText("hello");
  expect(screen.getByRole("button", { name: /reroll/i })).toBeInTheDocument();
});

test("no Reroll when the last post is the user's", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "assistant", content: "a reply" }, { role: "user", content: "hi" }] }));
  renderCampaign();
  await screen.findByText("hi");
  expect(screen.queryByRole("button", { name: /reroll/i })).toBeNull();
});

test("no Reroll on a sole opening post", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "assistant", content: "the greeting" }] }));
  renderCampaign();
  await screen.findByText("the greeting");
  expect(screen.queryByRole("button", { name: /reroll/i })).toBeNull();
});

test("only the last assistant post shows Reroll", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "one" }, { role: "assistant", content: "first reply" },
    { role: "user", content: "two" }, { role: "assistant", content: "second reply" }] }));
  renderCampaign();
  await screen.findByText("second reply");
  expect(screen.getAllByRole("button", { name: /reroll/i })).toHaveLength(1);
});

test("a turn whose refresh fails says so, rather than showing an empty banner", async () => {
  // The turn itself succeeded, so nothing else has reported anything: this is
  // the one writer of `error` that has to build the object rather than defer to
  // an earlier one. Written as a bare string it rendered a banner with no text
  // and no Retry — worse than the failure it was reporting, because the view is
  // now showing a transcript it could not confirm.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene)
    .mockResolvedValueOnce(fixture({ meta: {}, messages: [{ role: "user", content: "hi" }] }))
    .mockRejectedValue(Object.assign(new Error("boom"), { detail: "scene read failed" }));
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  expect(await screen.findByText(/scene read failed/)).toBeTruthy();
  expect(screen.getByRole("button", { name: /^Retry$/ })).toBeTruthy();
});

// ---- the dossier phase's own scoped retry (#286) ----

// ---- absorb phases: a run the time budget cut short says so ----

test("Changes tab reveals the changes panel", async () => {
  renderCampaign();
  fireEvent.click(await screen.findByRole("button", { name: /^Changes$/ }));
  expect(await screen.findByText(/No record changes yet/)).toBeInTheDocument();
});

test("an unstamped user line renders the sole player's name", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getCast).mockResolvedValue([
    { kind: "pcs", id: "elara-vane", role: "player", name: "Elara Vane" },
    { kind: "characters", id: "seraphine", role: "npc", name: "Seraphine Vale" },
  ]);
  vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "user", content: "I open the door." },
    { role: "assistant", content: "She waits.", speaker: "Seraphine Vale" },
  ] });
  renderCampaign();
  // Scoped to the transcript: the cast column names them both too.
  const stream = within(await screen.findByTestId("stream"));
  await stream.findByText("Elara Vane");
  expect(stream.getByText("Seraphine Vale")).toBeInTheDocument();
});

test("a stored speaker beats the player-name fallback", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getCast).mockResolvedValue([
    { kind: "pcs", id: "elara-vane", role: "player", name: "Elara Vane" }]);
  vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "user", content: "spoken as someone else", speaker: "Old Name" }] });
  renderCampaign();
  const stream = within(await screen.findByTestId("stream"));
  await stream.findByText("Old Name");
  expect(stream.queryByText("Elara Vane")).toBeNull();
});

test("after a stream completes the scene is re-fetched", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene)
    .mockResolvedValueOnce(fixture({ meta: {}, messages: [] }))
    .mockResolvedValue(fixture({ meta: {}, messages: [
      { role: "user", content: "hello" },
      { role: "assistant", content: "Thunder rolls." },
      { role: "assistant", content: "Who goes there?", speaker: "Seraphine Vale" },
    ] }));
  vi.mocked(api.chat).mockImplementation(async (_c, _s, _t, onEvent) => {
    onEvent({ delta: "**Grimoire:** Thunder rolls." });
    onEvent({ done: true });
  });
  renderCampaign();
  await screen.findByRole("heading", { name: /^Old$/ });
  vi.mocked(api.findRun).mockClear();
  const ta = screen.getByRole("textbox");
  fireEvent.change(ta, { target: { value: "hello" } });
  fireEvent.keyDown(ta, { key: "Enter" });
  await screen.findByText("Who goes there?");
  expect(api.getScene).toHaveBeenCalledTimes(2);
  // A completed stream needs no recovery lookup or lingering flush poll.
  expect(api.findRun).not.toHaveBeenCalled();
});

test("no Reroll when every message is assistant-side (multi-post opener)", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "assistant", content: "opener one" },
    { role: "assistant", content: "opener two", speaker: "Seraphine Vale" }] }));
  renderCampaign();
  await screen.findByText("opener two");
  expect(screen.queryByRole("button", { name: /reroll/i })).toBeNull();
});

test("world name comes from the campaign payload, with no world fetch", async () => {
  vi.mocked(api.getCampaign).mockResolvedValue(fixture({
    meta: { id: "run", name: "Run One", world: "w", world_name: "Saltmarch" }, body: "" }));
  renderCampaign();
  // The column's world-copy link names it — the campaign's own copy, which is
  // the only world the play view ever reaches.
  expect(await screen.findByText(/Saltmarch · this campaign/)).toBeInTheDocument();
  expect(api.getWorld).not.toHaveBeenCalled();
});

test("a first-name speaker matches its cast member (fuzzy, unique prefix)", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getCast).mockResolvedValue([
    { kind: "characters", id: "winifred", role: "npc", name: "Winifred Vance" },
    { kind: "pcs", id: "yara", role: "player", name: "Yara Vane" },
  ]);
  vi.mocked(api.getScene).mockResolvedValue({
    meta: { id: "s1", title: "Old" },
    messages: [
      { role: "assistant", content: "She smiles.", speaker: "Winifred" },
      { role: "user", content: "Hello.", speaker: "Yara" },
    ],
  });
  renderCampaign();
  // both short labels resolve to cast members: clickable plates, pc coloring
  const winifred = await screen.findByRole("button", { name: "Winifred" });
  expect(winifred).toBeInTheDocument();
  expect(document.querySelector(".plate.pc")).not.toBeNull(); // "Yara" -> player Yara Vane
});
