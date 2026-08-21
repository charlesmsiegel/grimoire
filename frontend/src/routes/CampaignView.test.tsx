import { StrictMode } from "react";
import { render, screen, fireEvent, waitFor, act, within } from "@testing-library/react";
import { MemoryRouter, Link, useNavigate } from "react-router-dom";
import { FocusProvider, useFocus } from "../components/focus";

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
vi.mock("../components/ResponsePresetPicker", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.ResponsePresetPicker());
vi.mock("../api/client", async () => (await import("../testkit/campaignMocks")).campaignApiMock());
vi.mock("../components/PostImagePicker", async () =>
  (await import("../testkit/campaignMocks")).componentStubs.PostImagePicker());
vi.mock("../api/models", () => ({ getModels: vi.fn() }));
import { api, ApiError } from "../api/client";
import { LOCKED_WHILE_GENERATING } from "../components/sceneLock";
import {
  here, Here, installCampaignMocks, ONE_SCENE, openScene, playRoutes,
  renderCampaign, RESPONSE_BUNDLE, RESPONSE_PRESETS, withPalette,
} from "../testkit/campaignHarness";

beforeEach(installCampaignMocks);

// `listScenes` as the server actually answers it around a rename: the mount
// read still sees the old id, and every read AFTER the rename landed sees the
// new one. The relists that follow a mutation are `fresh` reads issued once the
// write returned, so they cannot come back pre-rename — and mocking them that
// way models a server that lost the rename it just confirmed.
function relistsAs(before: any[], after: any[]) {
  (api.listScenes as any).mockResolvedValueOnce(before).mockResolvedValue(after);
}

// `listScenes` no longer takes a `fresh` flag — the endpoint never coalesces
// now (#87), so nothing distinguishes a mount read from a relist at the API
// level. These fixtures tell them apart by order instead: the first read of a
// campaign is its mount read, every later one is a relist.
function readCounter() {
  const seen = new Map<string, number>();
  return (cid: string) => {
    const n = (seen.get(cid) ?? 0) + 1;
    seen.set(cid, n);
    return n;
  };
}

test("the pinned conditions block names where, when and the campaign's world copy", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getSceneDatetime as any).mockResolvedValue({
    current: { native: "2026-07-03", friendly: "3 July 2026", weekday: "Friday",
               secondary_friendly: null, holidays_today: ["Independence Day"], upcoming: null, cast: [] },
    history: [],
  });
  (api.getSceneLocation as any).mockResolvedValue({
    current: { id: "tideflats", name: "The Tideflats" }, visited: [] });
  renderCampaign();
  const column = within(await screen.findByRole("complementary"));
  await column.findByText(/Friday 3 July 2026/i);
  expect(column.getByText("The Tideflats")).toBeInTheDocument();
  // The campaign's own copy of the world, said before the click rather than
  // after: edits there reach this campaign only.
  expect(column.getByRole("link", { name: /this campaign/i }))
    .toHaveAttribute("href", "/campaigns/run/world");
});

test("the scene heading counts the scene and its turns", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  renderCampaign();
  // `find`, not `get`: the title comes from the scene *list* and the turn count
  // from the separate `getScene`, so waiting for the heading proves nothing
  // about the count. On a fast machine the second promise happens to have
  // settled by now; on a loaded CI runner it has not, and this failed against
  // "SCENE 1 · 0 TURNS". Wait for the thing being asserted.
  await screen.findByRole("heading", { name: /^Old$/ });
  expect(await screen.findByText(/SCENE 1 · 2 TURNS/i)).toBeInTheDocument();
});

test("⌘K numbers a scene by its id's own number, not by list position", async () => {
  // listScenes is sorted by `updated` descending — an earlier scene edited
  // most recently sorts first, which must not desync the displayed number
  // from the scene's actual story position (its id's leading number).
  (api.listScenes as any).mockResolvedValue([
    { id: "003--2024-09-10--day-two", title: "Day Two", model: "", created: "", updated: "2026-07-07T00:49:35Z" },
    { id: "036--2024-09-24--froot-loops", title: "Froot Loops", model: "", created: "", updated: "2026-07-06T23:26:21Z" },
  ]);
  renderCampaign();
  await screen.findByRole("heading", { name: /Day Two/ });
  fireEvent.keyDown(window, { key: "k", metaKey: true });
  expect(await screen.findByRole("option", { name: /Day Two.*scene 3/i })).toBeInTheDocument();
  expect(screen.getByRole("option", { name: /Froot Loops.*scene 36/i })).toBeInTheDocument();
});

test("plates mark PC speakers and show avatars from the roster", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getCast as any).mockResolvedValue([
    { kind: "characters", id: "seraphine", role: "npc", name: "Seraphine Vale" },
    { kind: "pcs", id: "yara", role: "player", name: "Yara" },
  ]);
  (api.listAppearances as any).mockResolvedValue([
    { kind: "characters", id: "seraphine", version: "v1", role: "npc", scenes: ["s1"] },
  ]);
  (api.getScene as any).mockResolvedValue({
    meta: { id: "s1", title: "Old" },
    messages: [
      { role: "user", content: "Hello.", speaker: "Yara" },
      { role: "assistant", content: "She waits.", speaker: "Seraphine Vale" },
    ],
  });
  renderCampaign();
  // Two of her: the plate over her run, and her tile in the cast column.
  const stream = within(await screen.findByTestId("stream"));
  await stream.findByText("Seraphine Vale");
  expect(document.querySelector(".plate.pc")).not.toBeNull();          // Yara run
  expect(stream.getByText("pc")).toBeInTheDocument();
  expect(stream.getAllByText("npc").length).toBeGreaterThan(0);
  expect(stream.getByAltText("Seraphine Vale portrait")).toBeInTheDocument();
  // Yara is in the cast but not the roster, so there is no locked version to
  // build a URL from -- initials, not a broken img.
  expect(stream.queryByAltText("Yara portrait")).toBeNull();
});

test("a PC speaker's plate carries their portrait once the roster locks a version", async () => {
  // `plateAvatar` used to return null for every non-character, so the player's
  // own runs were the only ones in the transcript with no face on them (#219).
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getCast as any).mockResolvedValue([
    { kind: "pcs", id: "yara", role: "player", name: "Yara" },
  ]);
  (api.listAppearances as any).mockResolvedValue([
    { kind: "pcs", id: "yara", version: "v2", role: "player", scenes: ["s1"] },
  ]);
  (api.getScene as any).mockResolvedValue({
    meta: { id: "s1", title: "Old" },
    messages: [{ role: "user", content: "Hello.", speaker: "Yara" }],
  });
  renderCampaign();
  const stream = within(await screen.findByTestId("stream"));
  const portrait = await stream.findByAltText("Yara portrait");
  expect(portrait.getAttribute("src")).toBe("/img/pcs/yara/v2/avatar");
});

/** A scene whose transcript has one post, spoken by a cast member. */
function speakingScene() {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getCast as any).mockResolvedValue([
    { kind: "characters", id: "seraphine", role: "npc", name: "Seraphine Vale" },
  ]);
  (api.getScene as any).mockResolvedValue({
    meta: { id: "s1", title: "Old" },
    messages: [{ role: "assistant", content: "She waits.", speaker: "Seraphine Vale" }],
  });
}

test("clicking a speaker in the transcript opens them in the column, like the cast grid", async () => {
  // A drawer over the transcript to read about someone standing in it was a
  // modal answering the question the column beside it already answers.
  speakingScene();
  (api.getCasefile as any).mockResolvedValue({
    kind: "characters", id: "seraphine", name: "Seraphine Vale", version: "v1", role: "npc",
    scenes: ["s1"], last_seen: "s1", standing: "Keeps the tide gate.",
    knows: "", suspects: "", dossier: "", tagline: "",
    feels_toward: [], standing_facts: [],
  });
  renderCampaign();
  const plate = await screen.findByRole("button", { name: "Seraphine Vale" });
  fireEvent.click(plate);

  const column = within(await screen.findByRole("complementary"));
  expect(await column.findByText("Keeps the tide gate.")).toBeInTheDocument();
  // In the column, not over the transcript.
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(api.getCastDetail).not.toHaveBeenCalled();
});

test("a speaker who is not in the cast is not a link to anywhere", async () => {
  // Why the column can answer for every plate that IS clickable: the plate is
  // only a button when its speaker resolves against the scene's cast, which is
  // the same cast `casefile.build` requires membership in. A narrator, or
  // someone since removed from the scene, is plain text.
  speakingScene();
  (api.getCast as any).mockResolvedValue([]);
  renderCampaign();
  await screen.findByRole("heading", { name: /^Old$/ });
  expect(screen.getByText("Seraphine Vale").closest("button")).toBeNull();
  expect(api.getCasefile).not.toHaveBeenCalled();
});

test("shows the campaign name and loads its scenes", async () => {
  renderCampaign();
  await screen.findByText("Run One");
  await waitFor(() => expect(api.listScenes).toHaveBeenCalledWith("run"));
});

test("renders the inspector for an active scene", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  fireEvent.click(await screen.findByRole("button", { name: /what the model saw/i }));
  await screen.findByText(/Active characters/i);
  await screen.findByText(/^Context/);
});

test("hides Cast & scene setup once the scene has messages", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [{ role: "assistant", content: "hi" }] });
  renderCampaign();
  await screen.findByText("hi");
  expect(screen.queryByTestId("cast-panel")).toBeNull();
});

test("shows Cast & scene setup for an empty scene", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [] });
  renderCampaign();
  await screen.findByTestId("cast-panel");
});

test("editing a message saves and reloads", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [{ role: "assistant", content: "hi" }] });
  renderCampaign();
  await screen.findByText("hi");
  fireEvent.click(screen.getAllByTitle("Edit message")[0]);
  const ta = await screen.findByLabelText(/edit message/i);
  fireEvent.change(ta, { target: { value: "hello" } });
  fireEvent.click(screen.getByRole("button", { name: /save/i }));
  await waitFor(() => expect(api.editMessage).toHaveBeenCalledWith("run", "s1", 0, "hello"));
});

// ---- inserting an image into a post (#376) ----
//
// The button sits in the same gutter row as Edit, under the same gates, and
// hands the picker the post's own speaker: an actor post offers that actor's
// art, a narrator post the campaign's own library. The insert writes into the
// buffer Edit already opens, so there is no new persistence path at all --
// Save, Retcon and Cancel are exactly what they were.

test("a narrator post's picker is scoped to the campaign, not to an actor", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: { id: "s1", title: "Old" },
    messages: [{ role: "assistant", content: "The room is cold." }] });
  renderCampaign();
  await screen.findByText("The room is cold.");
  fireEvent.click(screen.getAllByTitle("Insert an image")[0]);
  const picker = await screen.findByTestId("image-picker");
  expect(JSON.parse(picker.getAttribute("data-target")!))
    .toEqual({ kind: "campaign", name: "Grimoire" });
});

test("an actor post's picker names the actor and the version the roster locked", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getCast as any).mockResolvedValue([
    { kind: "characters", id: "seraphine", role: "npc", name: "Seraphine Vale" },
  ]);
  (api.listAppearances as any).mockResolvedValue([
    { kind: "characters", id: "seraphine", version: "v1", role: "npc", scenes: ["s1"] },
  ]);
  (api.getScene as any).mockResolvedValue({ meta: { id: "s1", title: "Old" },
    messages: [{ role: "assistant", content: "She waits.", speaker: "Seraphine Vale" }] });
  renderCampaign();
  await screen.findByText("She waits.");
  fireEvent.click(screen.getAllByTitle("Insert an image")[0]);
  expect(JSON.parse((await screen.findByTestId("image-picker")).getAttribute("data-target")!))
    .toEqual({ kind: "characters", id: "seraphine", version: "v1", name: "Seraphine Vale" });
});

test("an actor the roster cannot place falls back to the campaign's library", async () => {
  // No appearance record, so there is no locked version to build image URLs
  // from -- the same gap that leaves the speaker plate showing initials.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getCast as any).mockResolvedValue([
    { kind: "pcs", id: "yara", role: "player", name: "Yara" },
  ]);
  (api.getScene as any).mockResolvedValue({ meta: { id: "s1", title: "Old" },
    messages: [{ role: "user", content: "Hello.", speaker: "Yara" }] });
  renderCampaign();
  await screen.findByText("Hello.");
  fireEvent.click(screen.getAllByTitle("Insert an image")[0]);
  expect(JSON.parse((await screen.findByTestId("image-picker")).getAttribute("data-target")!))
    .toEqual({ kind: "campaign", name: "Yara" });
});

test("picking an image opens the post for editing with the reference appended", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: { id: "s1", title: "Old" },
    messages: [{ role: "assistant", content: "The room is cold." }] });
  renderCampaign();
  await screen.findByText("The room is cold.");
  fireEvent.click(screen.getAllByTitle("Insert an image")[0]);
  fireEvent.click(await screen.findByText("stub-insert"));

  const ta = await screen.findByLabelText(/edit message/i);
  expect((ta as HTMLTextAreaElement).value).toBe(
    "The room is cold.\n\n![coastline](/api/campaigns/run/images/coastline)");
  expect(screen.queryByTestId("image-picker")).toBeNull();   // the pick closes it

  // and the existing Save is the save: no new route, no second write path
  fireEvent.click(screen.getByRole("button", { name: /save/i }));
  await waitFor(() => expect(api.editMessage).toHaveBeenCalledWith(
    "run", "s1", 0,
    "The room is cold.\n\n![coastline](/api/campaigns/run/images/coastline)"));
});

test("a post carrying an image reference renders it as an image", async () => {
  // The end of the whole round trip, and the one claim neither the picker's
  // tests nor the backend's can make: the transcript's own `RenderedMarkdown`
  // (react-markdown + remarkGfm, with the comment and quote rehype plugins)
  // turns the reference the picker wrote into an <img> pointing at the route
  // that serves it. If a plugin ever swallowed image nodes, this fails here
  // rather than in a campaign.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "assistant",
      content: "The coast.\n\n![coastline](/api/campaigns/run/images/coastline)" }] });
  renderCampaign();
  const img = await within(await screen.findByTestId("stream")).findByAltText("coastline");
  expect(img.getAttribute("src")).toBe("/api/campaigns/run/images/coastline");
});

test("a dice-roll line is offered no image button, for the reason it is offered no Edit", async () => {
  // Its text has to stay in lockstep with an immutable rolls.json entry, and an
  // insert rewrites the post.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "user", content: "hi" },
    { role: "assistant", content: "🎲 2d6 = 7", speaker: "⁣Roll" }] });
  renderCampaign();
  await screen.findByText("🎲 2d6 = 7");
  expect(screen.getAllByTitle("Insert an image")).toHaveLength(1);   // the user post only
  expect(screen.getAllByTitle("Edit message")).toHaveLength(1);
});

// ---- cascade post-delete (#75) ----
//
// The gutter's 🗑 takes the post it sits on and every one after it, and (for a
// scene that has been absorbed) reverses what that scene wrote. The confirm is
// part of the contract, not decoration: what the cascade reverts is invisible in
// the transcript afterwards, so the prompt has to name it before the fact and
// the reply has to report anything it could not put back.

const CASCADE_OK = { index: 1, removed: 2, was_absorbed: false, records: 0,
                     refused: [], chronicle: false, plot_beats: 0,
                     commitment_beats: 0, changes: 0, citations: 0, failed: [] };

function twoPostScene() {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
}

test("the cut deletes the post and everything after it", async () => {
  twoPostScene();
  (api.deleteMessagesFrom as any).mockResolvedValue(CASCADE_OK);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  renderCampaign();
  await screen.findByText("a reply");

  fireEvent.click(screen.getByLabelText("Delete message 1 and everything after it"));
  await waitFor(() => expect(api.deleteMessagesFrom).toHaveBeenCalledWith("run", "s1", 0));
  // The rail is re-read too: an absorbed scene has just stopped being done, and
  // the composer is hidden off that flag.
  await waitFor(() => expect((api.listScenes as any).mock.calls.length).toBeGreaterThan(1));
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
  (api.listScenes as any).mockResolvedValue(DONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" },
    { role: "user", content: "and then?" }] });
  (api.deleteMessagesFrom as any).mockResolvedValue({ ...CASCADE_OK, was_absorbed: true });
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

test("a record the reversal could not put back is reported", async () => {
  twoPostScene();
  (api.deleteMessagesFrom as any).mockResolvedValue({
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

const FORK_OK = { id: "branch", from_scene: "0001--old", removed_scenes: [],
                  records: 0, refused: [], failed: [] };

const FORK_SCENES = [
  { id: "0001--old", title: "Old", model: "", created: "", updated: "" },
  { id: "0002--newer", title: "Newer", model: "", created: "", updated: "" },
];

function openAt(sid: string) {
  (api.listScenes as any).mockResolvedValue(FORK_SCENES);
  (api.getScene as any).mockResolvedValue({
    meta: { id: sid, title: "Old" }, messages: [{ role: "user", content: "hi" }] });
  renderCampaign(`/campaigns/run/scenes/${sid}`);
}

test("forking at a scene names it, and cuts nothing here", async () => {
  openAt("0001--old");
  (api.forkCampaign as any).mockResolvedValue(FORK_OK);
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
  await waitFor(() => expect(here()).toMatch(/^\/campaigns\/branch(\/|$)/));
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
  (api.forkCampaign as any).mockResolvedValue({
    ...FORK_OK, removed_scenes: ["0002--newer"], records: 1,
    refused: [{ label: "The Pact — lore", reason: "this record changed after the edit" }] });
  vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.spyOn(window, "prompt").mockReturnValue("A Second Run");
  await screen.findByText("hi");

  fireEvent.click(screen.getByLabelText("Fork campaign at this scene"));
  await screen.findByText(/The Pact — lore/);
  expect(document.body.textContent).toMatch(/still holds what a removed scene wrote/i);
  // The note is about the FORK, so it has to survive the navigation that opens
  // it — which it does only because the campaign-scoped effect clears the
  // review state and deliberately not `error`.
  await waitFor(() => expect(here()).toMatch(/^\/campaigns\/branch(\/|$)/));
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
  (api.forkCampaign as any).mockRejectedValue({ detail: "campaign is busy" });
  vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.spyOn(window, "prompt").mockReturnValue("A Second Run");
  await screen.findByText("hi");

  fireEvent.click(screen.getByLabelText("Fork campaign at this scene"));
  await screen.findByText(/campaign is busy/i);
  expect(here()).toMatch(/^\/campaigns\/run/);
});

test("a cut in flight latches the gutter against a second one", async () => {
  twoPostScene();
  let land: (r: any) => void = () => {};
  (api.deleteMessagesFrom as any).mockReturnValue(new Promise((res) => { land = res; }));
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
  await waitFor(() => expect((api.listScenes as any).mock.calls.length).toBeGreaterThan(1));
});

test("cleanup that could not run is reported separately from a refused record", async () => {
  twoPostScene();
  (api.deleteMessagesFrom as any).mockResolvedValue({
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

// ---- retcon and its replay (#78/#79/#80) ----
//
// Retcon sits beside Save in the edit form rather than replacing it, because
// the two are different intentions: Save fixes the words, Retcon says the scene
// did not happen that way and takes back what it recorded. The gutter's ⏩ is
// the escalation — re-run the turns that followed — and is priced before
// anything is cut.

const RETCON_OK = { ...CASCADE_OK, later: [] };

test("retcon rewrites the post through the retcon route, not the plain edit", async () => {
  twoPostScene();
  (api.retconMessage as any).mockResolvedValue(RETCON_OK);
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
  (api.listScenes as any).mockResolvedValue(DONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  (api.retconMessage as any).mockResolvedValue({ ...RETCON_OK, was_absorbed: true });
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
  renderCampaign();
  await screen.findByText("a reply");

  fireEvent.click((await screen.findAllByTitle("Edit message"))[0]);
  fireEvent.click(screen.getByRole("button", { name: /^retcon$/i }));
  // Awaited before reading the prompt: the click's own work (the retcon, the
  // rail re-read, the refresh) settles inside the test rather than after it.
  await waitFor(() => expect(api.retconMessage).toHaveBeenCalled());
  await waitFor(() => expect((api.listScenes as any).mock.calls.length).toBeGreaterThan(1));
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
  (api.retconMessage as any).mockResolvedValue(
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
  (api.listScenes as any).mockResolvedValue(TWO_SCENES);
  (api.getScene as any).mockImplementation(async (_c: string, sid: string) => ({
    meta: { id: sid }, messages: [{ role: "user", content: `hi from ${sid}` },
                                  { role: "assistant", content: `a reply in ${sid}` }],
  }));
  renderCampaign();
  await screen.findByText("a reply in s1");
  fireEvent.click(await screen.findByLabelText("Replay the turns after message 1"));
  await waitFor(() =>
    expect(screen.getByTestId("replay-panel").getAttribute("data-start-at")).toBe("1"));

  (api.listScenes as any).mockResolvedValue(
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
  (api.getRollingSummary as any).mockClear?.();

  fireEvent.click(screen.getByText("stub-replay-changed"));
  await waitFor(() => expect((api.listScenes as any).mock.calls.length).toBeGreaterThan(1));
  await waitFor(() => expect(api.refreshRollingSummary).toHaveBeenCalled());
});

test("the asked-for post does not follow the reader into another scene", async () => {
  // An index means nothing in another scene: carried across, it would price —
  // and cut — whatever post happens to sit there.
  (api.listScenes as any).mockResolvedValue(TWO_SCENES);
  (api.getScene as any).mockImplementation(async (_c: string, sid: string) => ({
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
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
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
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
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
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "assistant", content: "She smiles <!-- beat --> and turns away." }] });
  renderCampaign();
  await waitFor(() => expect(document.body.textContent).toContain("She smiles"));
  expect(document.body.textContent).not.toContain("beat");
  expect(document.body.textContent).toContain("and turns away.");
});

// A scene absorbed into the chronicle is finished: its summary is written and
// its changes are applied, so anything appended now sits outside the record
// taken of it.
const DONE_SCENE = [{ id: "s1", title: "Old", model: "", created: "", updated: "", done: true }];

test("a complete scene replaces the composer with a notice", async () => {
  (api.listScenes as any).mockResolvedValue(DONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
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
  expect(screen.queryByLabelText("Response length")).toBeNull();
  expect(screen.queryByRole("button", { name: "Roll dice" })).toBeNull();
});

test("an unfinished scene keeps its composer", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  renderCampaign();
  await screen.findByText("a reply");
  expect(screen.queryByText(/scene complete/i)).toBeNull();
  expect(screen.getByRole("textbox")).toBeInTheDocument();
});

// Editing and rerolling stay available on a finished scene: those change what
// was absorbed rather than adding to a scene that is closed.
test("a complete scene can still have its existing posts edited", async () => {
  (api.listScenes as any).mockResolvedValue(DONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  renderCampaign();
  await screen.findByText("a reply");
  expect(screen.getAllByTitle("Edit message").length).toBeGreaterThan(0);
});

test("⌘K marks an absorbed scene and leaves the others unmarked", async () => {
  (api.listScenes as any).mockResolvedValue([
    { id: "002--two", title: "Second", model: "", created: "", updated: "2026-01-02", done: true },
    { id: "001--one", title: "First", model: "", created: "", updated: "2026-01-01" },
  ]);
  renderCampaign();
  await screen.findByRole("heading", { name: /Second/ });
  fireEvent.keyDown(window, { key: "k", metaKey: true });
  expect(await screen.findByRole("option", { name: /Second.*absorbed/i })).toBeInTheDocument();
  expect(screen.getByRole("option", { name: /First/ })).not.toHaveTextContent(/absorbed/i);
});

test("Enter sends a message in the active scene", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  await screen.findByRole("heading", { name: /^Old$/ });
  const ta = screen.getByRole("textbox");
  fireEvent.change(ta, { target: { value: "hello" } });
  fireEvent.keyDown(ta, { key: "Enter" });
  await waitFor(() =>
    expect(api.chat).toHaveBeenCalledWith("run", "s1", "hello", expect.any(Function), undefined, expect.any(AbortSignal), expect.any(String)),
  );
});

test("Shift+Enter does not send", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  await screen.findByRole("heading", { name: /^Old$/ });
  const ta = screen.getByRole("textbox");
  fireEvent.change(ta, { target: { value: "hello" } });
  fireEvent.keyDown(ta, { key: "Enter", shiftKey: true });
  expect(api.chat).not.toHaveBeenCalled();
});

test("the response length picker shows the scene's preset and reverts after a successful send", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({
    meta: { id: "s1", title: "Old", response_preset: "cinematic" }, messages: [] });
  (api.listResponsePresets as any).mockResolvedValue(RESPONSE_PRESETS);
  renderCampaign();
  const picker = await screen.findByLabelText("Response length");
  // Waited for, not asserted flat. The <select> renders as soon as `listScenes`
  // names the active scene; its VALUE arrives on the separate `getScene` chain,
  // so `findBy` returning the element says nothing about the preset being in it
  // yet. `findBy` polls on a timer, so under load a poll lands between the two
  // commits and the flat assert reads "" -- which is the whole of this file's
  // flakiness, one arbitrary victim per full-suite run.
  await waitFor(() => expect(picker).toHaveValue("cinematic"));
  fireEvent.change(picker, { target: { value: "terse" } });
  expect(picker).toHaveValue("terse");
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Go on." } });
  fireEvent.click(screen.getByRole("button", { name: /Send/ }));
  // the picker's promise is "the next reply" — once it lands, the one-shot
  // pick is spent and it falls back to the scene's own setting.
  await waitFor(() => expect(picker).toHaveValue("cinematic"));
});

// The Escape-closes-the-dropdown test that stood here is gone with the custom
// listbox it covered. A native <select> is opened, closed, Escape-dismissed and
// keyboard-driven by the browser; there is no longer any code of ours to test.

test("sends the one-shot override in the chat request payload", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({
    meta: { id: "s1", title: "Old", response_preset: "cinematic" }, messages: [] });
  (api.listResponsePresets as any).mockResolvedValue(RESPONSE_PRESETS);
  renderCampaign();
  const picker = await screen.findByLabelText("Response length");
  // Same wait, and for the same reason, as the test above: the scene's own
  // preset arrives on the `getScene` chain AFTER the <select> renders, so an
  // override picked before it lands is overwritten by it.
  await waitFor(() => expect(picker).toHaveValue("cinematic"));
  fireEvent.change(picker, { target: { value: "terse" } });
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Go on." } });
  fireEvent.click(screen.getByRole("button", { name: /Send/ }));
  await waitFor(() => expect(api.chat).toHaveBeenCalledWith(
    "run", "s1", "Go on.", expect.any(Function), { response_preset: "terse" },
    expect.any(AbortSignal), expect.any(String)));
});

test("a failed stream keeps the override, and retry carries it", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({
    meta: { id: "s1", title: "Old", response_preset: "cinematic" }, messages: [] });
  (api.listResponsePresets as any).mockResolvedValue(RESPONSE_PRESETS);
  (api.chat as any).mockRejectedValueOnce(new Error("stream failed"));
  renderCampaign();
  const picker = await screen.findByLabelText("Response length");
  await waitFor(() => expect(picker).toHaveValue("cinematic"));  // see above
  fireEvent.change(picker, { target: { value: "terse" } });
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Go on." } });
  fireEvent.click(screen.getByRole("button", { name: /Send/ }));
  await waitFor(() => expect(picker).toHaveValue("terse")); // NOT cleared by the failure
  fireEvent.click(screen.getByRole("button", { name: /Retry/ }));
  await waitFor(() => expect(api.retry).toHaveBeenCalledWith(
    "run", "s1", expect.any(Function), { response_preset: "terse" }, expect.any(AbortSignal)));
});

test("sending with no scene creates one first", async () => {
  (api.listScenes as any).mockResolvedValue([]);
  renderCampaign();
  await waitFor(() => expect(api.listScenes).toHaveBeenCalled());
  const ta = screen.getByRole("textbox");
  fireEvent.change(ta, { target: { value: "hi" } });
  fireEvent.keyDown(ta, { key: "Enter" });
  await waitFor(() => expect(api.createScene).toHaveBeenCalledWith("run"));
  await waitFor(() => expect(api.chat).toHaveBeenCalledWith("run", "s1", "hi", expect.any(Function), undefined, expect.any(AbortSignal), expect.any(String)));
});

test("+ New Scene opens the chooser without creating a scene", async () => {
  renderCampaign();
  await screen.findByText(/Run One/);
  fireEvent.click(screen.getByRole("button", { name: /\+ new scene/i }));
  expect(await screen.findByTestId("scene-chooser")).toBeInTheDocument();
  expect(api.createScene).not.toHaveBeenCalled();
});

test("a chooser pick refreshes the rail, selects the scene, and seeds the prompt", async () => {
  (api.listScenes as any)
    .mockResolvedValueOnce([])                       // initial load
    .mockResolvedValue([{ id: "s9", title: "New", model: "", created: "", updated: "" }]);
  renderCampaign();
  await screen.findByText(/Run One/);
  fireEvent.click(screen.getByRole("button", { name: /\+ new scene/i }));
  fireEvent.click(await screen.findByText("stub-pick"));
  await waitFor(() => expect(api.getScene).toHaveBeenCalledWith("run", "s9", { limit: 60 }));
  expect(screen.queryByTestId("scene-chooser")).toBeNull();
  // the premise reaches the empty scene's CastPanel
  expect(await screen.findByText("A premise")).toBeInTheDocument();
});

test("a seeded premise survives the rename from the first date set", async () => {
  (api.listScenes as any)
    .mockResolvedValueOnce([])                       // initial load
    .mockResolvedValueOnce([{ id: "s9", title: "New", model: "", created: "", updated: "" }])
    .mockResolvedValue([{ id: "s10", title: "New", model: "", created: "", updated: "" }]);
  renderCampaign();
  await screen.findByText(/Run One/);
  fireEvent.click(screen.getByRole("button", { name: /\+ new scene/i }));
  fireEvent.click(await screen.findByText("stub-pick"));
  await screen.findByText("A premise");
  fireEvent.click(screen.getByText("stub-datestamp"));   // first date set renames s9 -> s10
  await waitFor(() => expect(api.getScene).toHaveBeenCalledWith("run", "s10", { limit: 60 }));
  expect(screen.getByTestId("cast-panel")).toHaveTextContent("A premise");
});

test("closing the chooser creates nothing", async () => {
  renderCampaign();
  await screen.findByText(/Run One/);
  fireEvent.click(screen.getByRole("button", { name: /\+ new scene/i }));
  fireEvent.click(await screen.findByText("stub-close"));
  expect(screen.queryByTestId("scene-chooser")).toBeNull();
  expect(api.createScene).not.toHaveBeenCalled();
  expect(api.getScene).not.toHaveBeenCalled();
});

test("the edit button renames a scene", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  await screen.findByRole("heading", { name: /^Old$/ });
  fireEvent.click(screen.getByRole("button", { name: /rename/i }));
  const input = screen.getByDisplayValue("Old");
  fireEvent.change(input, { target: { value: "New" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(api.renameScene).toHaveBeenCalledWith("run", "s1", "New"));
});

test("the delete button deletes a scene after confirm", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  renderCampaign();
  await screen.findByRole("heading", { name: /^Old$/ });
  fireEvent.click(screen.getByRole("button", { name: /delete/i }));
  await waitFor(() => expect(api.deleteScene).toHaveBeenCalledWith("run", "s1"));
});

test("declining the delete confirm does nothing", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  vi.spyOn(window, "confirm").mockReturnValue(false);
  renderCampaign();
  await screen.findByRole("heading", { name: /^Old$/ });
  fireEvent.click(screen.getByRole("button", { name: /delete/i }));
  expect(api.deleteScene).not.toHaveBeenCalled();
});

test("an error shows a Retry button that retries the scene", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  // the server persisted the user turn even though the stream errored —
  // the post-stream re-fetch returns it
  (api.getScene as any)
    .mockResolvedValueOnce({ meta: {}, messages: [] })
    .mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hello" }] });
  (api.chat as any).mockImplementation(async (_c: string, _s: string, _t: string, onEvent: any) => {
    onEvent({ error: { detail: "boom" } });
  });
  renderCampaign();
  await screen.findByRole("heading", { name: /^Old$/ });
  const ta = screen.getByRole("textbox");
  fireEvent.change(ta, { target: { value: "hello" } });
  fireEvent.keyDown(ta, { key: "Enter" });
  const retryBtn = await screen.findByRole("button", { name: /retry/i });
  fireEvent.click(retryBtn);
  await waitFor(() => expect(api.retry).toHaveBeenCalledWith("run", "s1", expect.any(Function), undefined, expect.any(AbortSignal)));
  expect(screen.getAllByText("hello")).toHaveLength(1);
});

// ---- cancelling a turn (#95) ----

/** api.chat that streams `deltas` and then hangs until its signal aborts,
 *  rejecting the way fetch does — the shape a real in-flight turn has. */
function hangingChat(deltas: string[] = []) {
  return async (_c: string, _s: string, _t: string, onEvent: any,
                _r: unknown, signal: AbortSignal) => {
    deltas.forEach((d) => onEvent({ delta: d }));
    await new Promise<void>((_resolve, reject) => {
      signal.addEventListener("abort", () => {
        const err = new Error("The operation was aborted.");
        err.name = "AbortError";
        reject(err);
      });
    });
  };
}

test("a turn in flight offers Stop in place of Send", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.chat as any).mockImplementation(hangingChat(["The tide "]));
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  const stop = await screen.findByRole("button", { name: /stop ■/i });
  expect(screen.queryByRole("button", { name: /send ▸/i })).toBeNull();
  fireEvent.click(stop);
  // back to a composer that can send again, with no error banner: the player
  // asked for this, and the partial the backend kept arrives with the re-fetch
  await screen.findByRole("button", { name: /continue ▶/i });
  expect(screen.queryByText(/aborted/i)).toBeNull();
  expect(api.getScene).toHaveBeenCalled();
});

test("Stop tells the run to stop, not just this socket", async () => {
  // The single most important consequence of detaching a turn from its request:
  // closing the connection is no longer the cancel. Aborting the fetch now only
  // drops this subscriber -- the provider keeps generating, keeps spending, and
  // persists a reply the player explicitly stopped, while holding the scene
  // against the next send. The run has to be told.
  //
  // The run id comes from the leading `run` frame, which is why the mock emits
  // one before its delta: a Stop pressed before that frame arrives has nothing
  // to address, and this test would pass vacuously if it asserted only that
  // `cancelRun` was called with *something*.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.chat as any).mockImplementation(async (
    _c: string, _s: string, _t: string, onEvent: any, _r: unknown, signal: AbortSignal) => {
    onEvent({ run: { id: "run-7", attempt_id: "a1", state: "running", next_index: 1 } });
    onEvent({ delta: "The tide " });
    await new Promise<void>((_resolve, reject) => {
      signal.addEventListener("abort", () => {
        const err = new Error("The operation was aborted.");
        err.name = "AbortError";
        reject(err);
      });
    });
  });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));

  await screen.findByRole("button", { name: /continue ▶/i });
  expect(api.cancelRun).toHaveBeenCalledWith("run", "s1", "run-7");
});

test("Stop before the run frame arrives cancels nothing rather than the wrong run", async () => {
  // The handle is cleared when a stream ends, beside `abortRef`. Left set, a
  // Stop pressed while the NEXT turn is still connecting would address the
  // PREVIOUS turn'''s run -- terminal by then, so the route answers politely, the
  // press is swallowed, and the live turn keeps generating with nothing to stop
  // it. That is a worse failure than doing nothing, because it looks like it
  // worked.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.chat as any).mockImplementationOnce(async (
    _c: string, _s: string, _t: string, onEvent: any, _r: unknown, signal: AbortSignal) => {
    onEvent({ run: { id: "run-first", attempt_id: "a1", state: "running", next_index: 1 } });
    onEvent({ delta: "The tide " });
    await new Promise<void>((_resolve, reject) => {
      signal.addEventListener("abort", () => {
        const err = new Error("The operation was aborted.");
        err.name = "AbortError";
        reject(err);
      });
    });
  });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  await screen.findByRole("button", { name: /continue ▶/i });
  (api.cancelRun as any).mockClear();

  // A second turn whose leading frame never arrives.
  (api.chat as any).mockImplementation(hangingChat());
  fireEvent.change(await screen.findByRole("textbox"), { target: { value: "again?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  await screen.findByRole("button", { name: /continue ▶/i });

  expect(api.cancelRun).not.toHaveBeenCalled();
});

test("Stop cancels a turn whose leading frame never arrived", async () => {
  // The window this exists for: the server accepted and detached the turn, and
  // the response died before its first frame reached the browser. With nothing
  // but the leading frame to go on, Stop had no target -- it closed the
  // subscriber and the provider ran on, spending and eventually persisting a
  // reply the player had stopped.
  //
  // The attempt id is minted client-side BEFORE the request, so the turn is
  // addressable from the first byte; `findRun` trades it for the run id.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.chat as any).mockImplementation(hangingChat());   // never emits a run frame
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  await screen.findByRole("button", { name: /continue ▶/i });

  // Cancelled by ATTEMPT, not by looking the run up first. A lookup is a
  // one-shot question and the POST may still be in the server's setup, so it
  // can answer "no such run" and then reserve one; the attempt route is
  // remembered and consumed by that reservation instead.
  const [, sid, attempt] = (api.cancelAttempt as any).mock.calls[0];
  expect(sid).toBe("s1");
  // and the attempt it cancelled is the one the send actually carried
  expect((api.chat as any).mock.calls[0][6]).toBe(attempt);
});

test("Stop reaches the run's own campaign after the player has moved to another",
     async () => {
  // React Router reuses this component across `/campaigns/A` -> `/campaigns/B`,
  // so `cid` is whichever campaign is on screen when Stop is pressed. Scene ids
  // are campaign-local, so cancelling A's turn through B's id reaches nothing
  // and the turn goes on generating and persisting. The handle has to carry the
  // campaign the turn started in.
  //
  // This one compiles either way -- `cid` is in scope at the call site -- which
  // is why it needs a test rather than a type.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.chat as any).mockImplementation(hangingChat());
  render(
    <MemoryRouter initialEntries={["/campaigns/run"]}>
      {withPalette(<>
        <Here />
        <Link to="/campaigns/elsewhere">elsewhere</Link>
        {playRoutes()}
      </>)}
    </MemoryRouter>,
  );
  fireEvent.change(await screen.findByRole("textbox"), { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByRole("button", { name: /stop ■/i });

  fireEvent.click(screen.getByRole("link", { name: /elsewhere/i }));
  await waitFor(() => expect(here()).toBe("/campaigns/elsewhere"));
  fireEvent.click(screen.getByRole("button", { name: /stop ■/i }));

  await waitFor(() => expect(api.cancelAttempt).toHaveBeenCalled());
  expect((api.cancelAttempt as any).mock.calls[0][0]).toBe("run");
});

test("Stop keeps asking while the server says the run is still unwinding", async () => {
  // The cancel route waits for the run, but that wait is bounded (30s) and can
  // answer while it is still `running`. Reading that as "it is over" put Send
  // back over a run that still held the scene -- the exact failure the wait was
  // added to prevent, arrived at by trusting its first answer.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.chat as any).mockImplementation(hangingChat());
  (api.cancelAttempt as any)
    .mockResolvedValueOnce({ run: { id: "r", attempt_id: "a", state: "running", next_index: 0 } })
    .mockResolvedValueOnce({ run: { id: "r", attempt_id: "a", state: "running", next_index: 0 } })
    .mockResolvedValue({ run: { id: "r", attempt_id: "a", state: "cancelled", next_index: 0 } });
  renderCampaign();
  fireEvent.change(await screen.findByRole("textbox"), { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  await screen.findByRole("button", { name: /continue ▶/i });

  // three rounds: two that answered `running`, one that answered terminal
  expect((api.cancelAttempt as any).mock.calls).toHaveLength(3);
});

test("Stop holds the scene until the run is really over", async () => {
  // The cancel route answers only once the run is terminal -- after its abort
  // hook has written the partial and freed the exclusion key. Releasing on the
  // flush poll's budget instead let a slow-unwinding provider keep the key
  // while Send came back, so the next send was refused with `run_in_flight`.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  let release: (() => void) | null = null;
  (api.cancelRun as any).mockImplementation(
    () => new Promise<void>((r) => { release = () => r(); }));
  (api.chat as any).mockImplementation(async (
    _c: string, _s: string, _t: string, onEvent: any, _r: unknown, signal: AbortSignal) => {
    onEvent({ run: { id: "run-7", attempt_id: "a1", state: "running", next_index: 1 } });
    onEvent({ delta: "The tide " });
    await new Promise<void>((_resolve, reject) => {
      signal.addEventListener("abort", () => {
        const err = new Error("The operation was aborted.");
        err.name = "AbortError";
        reject(err);
      });
    });
  });
  renderCampaign();
  fireEvent.change(await screen.findByRole("textbox"), { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));

  // The cancel has not answered yet, so the turn is still in flight as far as
  // the backend is concerned -- and the composer says so. `send` gates on
  // `busy` alone, so a Send button here IS a send the backend would refuse.
  await waitFor(() => expect(api.cancelRun).toHaveBeenCalled());
  expect(screen.getByRole("button", { name: /stop ■/i })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /(send ▸|continue ▶)/i })).toBeNull();

  release!();
  // and once the run really is over, the composer comes back
  await screen.findByRole("button", { name: /(send ▸|continue ▶)/i });
  expect((api.chat as any).mock.calls).toHaveLength(1);
});

test("a cancelled turn's partial appears even when the backend flush lands late", async () => {
  // The abort rejects the fetch as soon as the socket is torn down client-side;
  // the backend only then notices the disconnect and runs its shielded flush.
  // The refresh that follows Stop can therefore read a transcript the partial
  // has not reached yet, and without the poll the text sits on disk while the
  // screen denies it exists. Here the flush lands 100ms after the abort — after
  // the immediate refresh, before the first retry.
  //
  // The streamed text and the persisted message are deliberately different
  // strings. They are the same in life, but asserting on the streamed one here
  // proves nothing: the live preview renders it too, so the assertion would
  // pass against a node the refresh is about to tear down.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  let flushed = false;
  (api.getScene as any).mockImplementation(async () => ({
    meta: {},
    messages: flushed ? [{ role: "assistant", content: "the whole persisted partial" }] : [],
  }));
  (api.chat as any).mockImplementation(hangingChat(["a streamed fragment"]));
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  setTimeout(() => { flushed = true; }, 100);   // lands after the immediate refresh
  await waitFor(() =>
    expect(screen.getByText("the whole persisted partial")).toBeInTheDocument());
});

test("a cancel that streamed nothing still waits for the backend's flush", async () => {
  // What reached the client is not what the backend has to persist:
  // FenceWatcher emits nothing for a reply that opens with a roll fence, yet
  // the server still writes a proposal (and can write narration held back
  // behind a possible opener). Gating the poll on "did we see a delta" left
  // that invisible.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  let flushed = false;
  (api.getScene as any).mockImplementation(async () => ({
    meta: {},
    messages: flushed ? [{ role: "assistant", content: "held back all along" }] : [],
  }));
  (api.chat as any).mockImplementation(hangingChat());   // not one delta
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  setTimeout(() => { flushed = true; }, 100);
  await waitFor(() =>
    expect(screen.getByText("held back all along")).toBeInTheDocument());
});

test("StrictMode's mount cycle does not switch the flush poll off", async () => {
  // main.tsx renders the app inside StrictMode, so in development React runs
  // setup / cleanup / setup on mount. A cleanup-only mounted flag is left false
  // by that middle step, and `owns()` reads it: every post-cancel poll bows out
  // before its first look and a late flush stays invisible. Same scenario as
  // the late-flush test above, rendered the way development renders it.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  let flushed = false;
  (api.getScene as any).mockImplementation(async () => ({
    meta: {},
    messages: flushed ? [{ role: "assistant", content: "the whole persisted partial" }] : [],
  }));
  (api.chat as any).mockImplementation(hangingChat(["a streamed fragment"]));
  render(
    <StrictMode>
      <MemoryRouter initialEntries={["/campaigns/run"]}>
        {withPalette(<>
          {playRoutes()}
        </>)}
      </MemoryRouter>
    </StrictMode>,
  );
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  setTimeout(() => { flushed = true; }, 100);
  await waitFor(() =>
    expect(screen.getByText("the whole persisted partial")).toBeInTheDocument());
});

test("a send on a scene still loading is measured against that scene", async () => {
  // Send stays enabled while a freshly clicked scene loads, and the cached
  // length belongs to whichever scene was read last. Measuring the new scene's
  // growth against the old scene's length answers by which transcript happened
  // to be longer: here the post did land, but the scene left behind is longer
  // than the one it landed in, so the stale baseline reads that as "nothing
  // stored" and hands back a prompt the player would then send twice.
  (api.listScenes as any).mockResolvedValue([
    { id: "s1", title: "Old", model: "", created: "", updated: "" },
    { id: "s2", title: "Later", model: "", created: "", updated: "" },
  ]);
  let postLanded = false;
  let pagesOfS2 = 0;
  (api.getScene as any).mockImplementation(async (_c: string, sid: string, w?: any) => {
    if (sid !== "s2") {
      return { meta: {}, total: 4, messages: [
        { role: "user", content: "a" }, { role: "assistant", content: "b" },
        { role: "user", content: "c" }, { role: "assistant", content: "d" }] };
    }
    // s2's first page never arrives, so its length is still unknown when the
    // turn starts — the window the stale baseline is reachable through.
    if (w?.limit !== 1 && pagesOfS2++ === 0) return new Promise(() => {});
    return postLanded
      ? { meta: {}, total: 1, messages: [{ role: "user", content: "I draw my blade." }] }
      : { meta: {}, total: 0, messages: [] };
  });
  (api.chat as any).mockImplementation(async () => {
    postLanded = true;          // post_chat appended, then the abort beat the headers
    const err: Error & { beforeResponse?: boolean } = new Error("The operation was aborted.");
    err.name = "AbortError";
    err.beforeResponse = true;
    throw err;
  });
  renderCampaign();
  await openScene(/Later/);
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByText("I draw my blade.");   // the refreshed transcript has it
  await new Promise((r) => setTimeout(r, 50));
  expect(screen.getByRole("textbox")).toHaveValue("");
});

test("a refresh that fails with the send still gives the prompt back", async () => {
  // The verification read fails for the same reason the send did — the server
  // is unreachable — so the one case that most needs the player's words back is
  // the case that cannot confirm anything. Throwing out of the refresh skipped
  // the restore entirely; now an unverifiable turn restores, because a visible
  // duplicate is recoverable and a destroyed prompt is not.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  let loaded = false;
  (api.getScene as any).mockImplementation(async () => {
    if (loaded) throw new Error("Failed to fetch");
    loaded = true;
    return { meta: {}, total: 0, messages: [] };
  });
  (api.chat as any).mockImplementation(async () => {
    const err: Error & { beforeResponse?: boolean } = new Error("Failed to fetch");
    err.beforeResponse = true;
    throw err;
  });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("I draw my blade."));
});

test("a rolled-back prompt comes back beside a draft typed since", async () => {
  // The composer stays editable while a turn runs, so the player can be typing
  // the next line when this one fails. `cur || content` dropped the failed
  // prompt in exactly that case — it is in no transcript and no composer, which
  // is the loss the restore exists to prevent. Both texts survive.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  let fail: (() => void) | null = null;
  (api.chat as any).mockImplementation(
    async (_c: string, _s: string, _t: string, onEvent: any) => {
      await new Promise<void>((r) => { fail = r; });
      onEvent({ error: { detail: "OpenRouter API key is not set", kind: "missing_key",
                         post_returned: true } });
    });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByRole("button", { name: /stop ■/i });
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Wait, I hesitate." } });
  fail!();
  await screen.findByText(/OpenRouter API key is not set/);
  await waitFor(() => expect(screen.getByRole("textbox"))
    .toHaveValue("I draw my blade.\n\nWait, I hesitate."));
});

test("a pre-response abort whose post landed still waits for the flush", async () => {
  // `beforeResponse` says no response came back, not that nothing was written:
  // the server can append the post, start generating, and have the abort beat
  // its headers home. Growth in the refresh proves that happened, so there is a
  // turn on the server and its abort write is still coming — skipping the poll
  // on `unreached` alone left that partial invisible.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  let landed = false;
  let flushed = false;
  (api.getScene as any).mockImplementation(async () => ({
    meta: {},
    total: flushed ? 2 : landed ? 1 : 0,
    messages: flushed
      ? [{ role: "user", content: "I draw my blade." },
         { role: "assistant", content: "the whole persisted partial" }]
      : landed ? [{ role: "user", content: "I draw my blade." }] : [],
  }));
  (api.chat as any).mockImplementation(async () => {
    landed = true;              // post_chat appended and began generating
    const err: Error & { beforeResponse?: boolean } = new Error("The operation was aborted.");
    err.name = "AbortError";
    err.beforeResponse = true;
    throw err;
  });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByText("I draw my blade.");
  setTimeout(() => { flushed = true; }, 100);   // the shielded write lands after
  await waitFor(() =>
    expect(screen.getByText("the whole persisted partial")).toBeInTheDocument());
});

test("a refused turn neither polls for a flush nor loses the prompt", async () => {
  // A non-2xx is the whole outcome: `streamPost` throws it before any body
  // exists, so no stream was cut short and no abort write is coming. The poll
  // ran anyway — twelve seconds of refreshes for a turn that never started.
  // And every 4xx `post_chat` raises comes from a check that runs before the
  // post is appended, so the prompt was cleared and stored nowhere.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, total: 0, messages: [] });
  (api.chat as any).mockImplementation(async () => {
    throw new ApiError(409, "a turn is already running on this scene", "busy");
  });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByText(/a turn is already running/);
  await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("I draw my blade."));
  const readsAfterSettling = (api.getScene as any).mock.calls.length;
  await new Promise((r) => setTimeout(r, 400));   // past the first two poll ticks
  expect((api.getScene as any).mock.calls.length).toBe(readsAfterSettling);
});

test("the scene being generated into cannot be renamed mid-turn", async () => {
  // A scene's id is its filename and renaming re-slugs it, so a rename mid-turn
  // moves the file out from under the stream: the abort write that saves the
  // partial fails with SceneNotFound and is swallowed during teardown.
  (api.listScenes as any).mockResolvedValue([
    { id: "s1", title: "Old", model: "", created: "", updated: "" },
    { id: "s2", title: "Later", model: "", created: "", updated: "" },
  ]);
  // The lock outlives the turn's `busy`, so the flush has to land for it to be
  // released — see the sibling test below.
  let flushed = false;
  (api.getScene as any).mockImplementation(async () => ({
    meta: {}, total: flushed ? 1 : 0,
    messages: flushed ? [{ role: "assistant", content: "The tide turns." }] : [],
  }));
  (api.chat as any).mockImplementation(hangingChat(["The tide "]));
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByRole("button", { name: /stop ■/i });
  // Rename and delete belong to the scene on screen — and the scene on screen
  // is the one being streamed into, so both are locked for the turn.
  expect(screen.getByRole("button", { name: /rename scene/i })).toBeDisabled();
  expect(screen.getByRole("button", { name: /delete scene/i })).toBeDisabled();
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  await screen.findByRole("button", { name: /continue ▶/i });
  flushed = true;                            // the shielded write lands
  await waitFor(() =>
    expect(screen.getByRole("button", { name: /rename scene/i })).not.toBeDisabled());
});

test("the scene stays locked while the cancelled turn's flush is still coming", async () => {
  // `busy` clears as soon as the socket dies, but `on_abort` writes seconds
  // later — that gap is exactly what the flush poll waits out. Releasing the
  // lock with `busy` re-enabled rename and delete for the whole of it, so the
  // file could move out from under the very write being waited for.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, total: 0, messages: [] });
  (api.chat as any).mockImplementation(hangingChat(["The tide "]));
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  // Send is back — the turn is over as far as the composer is concerned —
  // but the write it is waiting for has not landed, so the scene stays locked.
  await screen.findByRole("button", { name: /continue ▶/i });
  await new Promise((r) => setTimeout(r, 400));   // past the first two poll ticks
  expect(screen.getByRole("button", { name: /rename/i })).toBeDisabled();
});

test("a manual roll cannot land in the window a cancelled reroll restores into", async () => {
  // The worst of the `busy`-instead-of-`streamingId` misses, because unlike
  // the others it destroys something outright. A reroll deletes the old reply
  // up front; cancelled before its first token, `on_abort` puts it back — but
  // `restore_trailing_assistant_run` steps over trailing *transitions* only
  // and refuses behind a manual roll, whose line must stay in lockstep with
  // rolls.json. So a roll in the flush window makes the restore refuse and the
  // reply is gone: nothing else holds it, and no backend hook can rescue it.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({
    meta: {}, total: 2,
    messages: [{ role: "user", content: "and then?" },
               { role: "assistant", content: "The tide turns." }],
  });
  (api.regenerate as any).mockImplementation(hangingChat([]));   // no first token
  renderCampaign();
  await screen.findByText("The tide turns.");
  // The roll is available before the turn — this is the control being locked,
  // not one that happened to be disabled anyway.
  expect(screen.getByRole("button", { name: /roll dice/i })).not.toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: /reroll/i }));
  fireEvent.click(screen.getByRole("button", { name: /reroll ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  // Send is back, so `busy` has cleared — but the abort write that restores
  // the deleted reply has not landed yet, and a roll now would defeat it.
  await screen.findByRole("button", { name: /continue ▶/i });
  await new Promise((r) => setTimeout(r, 400));   // past the first two poll ticks
  const roll = screen.getByRole("button", { name: /roll dice/i });
  expect(roll).toBeDisabled();
  expect(roll).toHaveAttribute("title", LOCKED_WHILE_GENERATING);
});

test("a lost error frame still gives the rolled-back prompt back", async () => {
  // The backend rolls the post back and *then* yields the error frame, so a
  // connection dropped in between leaves a rollback that happened and a client
  // never told. Nothing is set — not errored, not unreached, not refused — and
  // the poll cannot help, since it watches for growth and a rollback shrinks.
  // The refreshed transcript is the only witness left.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  let rolledBack = false;
  (api.getScene as any).mockImplementation(async () => ({
    meta: {}, total: rolledBack ? 0 : 0, messages: [],
  }));
  (api.chat as any).mockImplementation(async () => {
    rolledBack = true;   // headers arrived, post appended, then taken back off
    // resolves with no `done` and no error frame: the body just ended
  });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("I draw my blade."));
});

test("an interrupted stream whose post is still there keeps the composer clear", async () => {
  // The other side of the same gate. Headers arrived, so `post_chat` appended —
  // and the post is still in the transcript, which means the backend did NOT
  // roll it back and the reply may still be flushing. Restoring here would put
  // the text in the composer and the transcript at once.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  let posted = false;
  (api.getScene as any).mockImplementation(async () => ({
    meta: {}, total: posted ? 1 : 0,
    messages: posted ? [{ role: "user", content: "I draw my blade." }] : [],
  }));
  (api.chat as any).mockImplementation(async () => {
    posted = true;   // post_chat appended; the body then ends with no frame
  });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByText("I draw my blade.");
  await new Promise((r) => setTimeout(r, 50));
  expect(screen.getByRole("textbox")).toHaveValue("");
});

test("the lock follows the scene being written to, not the one on screen", async () => {
  // Scene selection stays live during a turn, and the write still lands in the
  // scene the stream captured. A lock keyed on `activeId` unlocked the row
  // still being written to and locked an unrelated one.
  (api.listScenes as any).mockResolvedValue([
    { id: "s1", title: "Old", model: "", created: "", updated: "" },
    { id: "s2", title: "Later", model: "", created: "", updated: "" },
  ]);
  (api.chat as any).mockImplementation(hangingChat(["The tide "]));
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByRole("button", { name: /stop ■/i });
  await openScene(/Later/);          // navigate away mid-turn
  // The lock is keyed on the scene being WRITTEN to, not the one on screen, so
  // the scene merely being looked at stays editable while s1 streams.
  await waitFor(() =>
    expect(screen.getByRole("button", { name: /rename scene/i })).not.toBeDisabled());
  await openScene(/^Old$/);
  await waitFor(() =>
    expect(screen.getByRole("button", { name: /rename scene/i })).toBeDisabled());
});

test("Stop during the preflight read does not strand the turn", async () => {
  // The baseline read runs before the POST exists, so the turn's controller has
  // nothing to abort yet. A stalled read left `runStream` parked outside its
  // try/finally with `busy` set: no Send, no Stop that works, no prompt back.
  // No scene selected, so Send creates one and streams into it with no read in
  // between — the window where the baseline has to be fetched before the POST.
  (api.listScenes as any).mockResolvedValue([]);
  (api.getScene as any).mockImplementation(async (_c: string, _s: string, w?: any) => {
    if (w?.limit === 1) return new Promise(() => {});   // the preflight never answers
    return { meta: {}, total: 0, messages: [] };
  });
  (api.createScene as any).mockResolvedValue({ id: "s2" });
  (api.chat as any).mockImplementation(async (_c: string, _s: string, _t: string,
                                              _e: any, _r: unknown, signal: AbortSignal) => {
    const err: Error & { beforeResponse?: boolean } = new Error("The operation was aborted.");
    err.name = "AbortError";
    err.beforeResponse = true;
    if (signal.aborted) throw err;
    await new Promise<void>((_res, rej) => signal.addEventListener("abort", () => rej(err)));
  });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  // the turn unwinds instead of hanging: Send comes back and so does the prompt
  await screen.findByRole("button", { name: /continue ▶/i });
  await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("I draw my blade."));
});

test("Retry after a failed reroll rerolls again, with its guidance", async () => {
  // `/retry` continues from the transcript as it stands. A failed reroll now
  // puts the old reply back, so retrying through `/retry` would generate a
  // continuation of the very reply the player asked to replace — and drop the
  // guidance they typed with it.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({
    meta: {}, total: 2, messages: [
      { role: "user", content: "and then?" },
      { role: "assistant", content: "The tide turns." }],
  });
  (api.regenerate as any).mockImplementation(
    async (_c: string, _s: string, onEvent: any) => {
      onEvent({ error: { detail: "OpenRouter API key is not set", kind: "missing_key" } });
    });
  renderCampaign();
  await screen.findByText("The tide turns.");
  fireEvent.click(screen.getByRole("button", { name: /reroll/i }));
  fireEvent.change(screen.getByPlaceholderText(/reroll/i),
                   { target: { value: "darker this time" } });
  fireEvent.click(screen.getByRole("button", { name: /reroll ▸/i }));
  await screen.findByText(/OpenRouter API key is not set/);
  expect(api.regenerate).toHaveBeenCalledTimes(1);

  fireEvent.click(screen.getByRole("button", { name: /^retry$/i }));
  await waitFor(() => expect(api.regenerate).toHaveBeenCalledTimes(2));
  expect(api.retry).not.toHaveBeenCalled();
  expect((api.regenerate as any).mock.calls[1][3]).toBe("darker this time");
});

test("a remembered reroll does not follow the player to another scene", async () => {
  // The remembered operation had no scene identity, and Retry acts on whatever
  // scene is open — so a reroll that failed in one scene, retried after
  // switching, would replace a reply in the *other* scene with guidance written
  // for the first. Switching also clears the banner now; the scene check is
  // what makes that airtight rather than merely likely.
  (api.listScenes as any).mockResolvedValue([
    { id: "s1", title: "Old", model: "", created: "", updated: "" },
    { id: "s2", title: "Later", model: "", created: "", updated: "" },
  ]);
  (api.getScene as any).mockResolvedValue({
    meta: {}, total: 2, messages: [
      { role: "user", content: "and then?" },
      { role: "assistant", content: "The tide turns." }],
  });
  (api.regenerate as any).mockImplementation(
    async (_c: string, _s: string, onEvent: any) => {
      onEvent({ error: { detail: "OpenRouter API key is not set", kind: "missing_key" } });
    });
  renderCampaign();
  await screen.findByText("The tide turns.");
  fireEvent.click(screen.getAllByRole("button", { name: /reroll/i })[0]);
  fireEvent.change(screen.getByPlaceholderText(/reroll/i),
                   { target: { value: "darker this time" } });
  fireEvent.click(screen.getByRole("button", { name: /reroll ▸/i }));
  await screen.findByText(/OpenRouter API key is not set/);

  await openScene(/Later/);       // leave the failed scene
  // the banner belonged to the scene being left, so it goes with it
  await waitFor(() => expect(screen.queryByText(/OpenRouter API key is not set/)).toBeNull());

  // Now fail something in the new scene, so a banner — and a Retry — exist here
  // on their own account. The remembered reroll is still in the ref, and this
  // is where a Retry with no scene identity would have rerolled the wrong scene.
  (api.chat as any).mockImplementation(
    async (_c: string, _s: string, _t: string, onEvent: any) => {
      onEvent({ error: { detail: "connection reset", kind: "network" } });
    });
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "onward" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByText(/connection reset/);
  fireEvent.click(screen.getByRole("button", { name: /^retry$/i }));

  await waitFor(() => expect(api.retry).toHaveBeenCalled());
  expect((api.retry as any).mock.calls[0][1]).toBe("s2");   // this scene
  expect(api.regenerate).toHaveBeenCalledTimes(1);          // not s1's reroll again
});

test("End scene stays disabled while the cancelled turn's flush is still coming", async () => {
  // Absorption reads the transcript and commits a chronicle against it. `busy`
  // clears when the socket dies, but the backend's shielded write lands seconds
  // later — absorb inside that window and the summary describes a transcript
  // the partial has not reached, then the partial lands under a scene already
  // marked absorbed. Unlike the other flush races, that one does not heal.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, total: 0, messages: [] });
  (api.chat as any).mockImplementation(hangingChat(["The tide "]));
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  await screen.findByRole("button", { name: /continue ▶/i });   // busy is clear
  await new Promise((r) => setTimeout(r, 400));                 // still flushing
  expect(screen.getByRole("button", { name: /end scene/i })).toBeDisabled();
});

test("a scene rename in flight holds off the next turn", async () => {
  // Renaming is a PUT that moves the scene file. Until it answers, which id is
  // current is genuinely unknown — so a turn started inside that window can be
  // handed the old one and have its reply written to a path that no longer
  // exists. The lock covers rename-during-a-turn; this is the other direction.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  let finishRename: ((v: any) => void) | null = null;
  (api.renameScene as any).mockImplementation(
    () => new Promise((res) => { finishRename = res; }));
  renderCampaign();
  await screen.findByRole("heading", { name: /^Old$/ });
  fireEvent.click(screen.getByRole("button", { name: /rename/i }));
  const input = screen.getByDisplayValue("Old");
  fireEvent.change(input, { target: { value: "Renamed" } });
  fireEvent.keyDown(input, { key: "Enter" });

  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  await waitFor(() =>
    expect(screen.getByRole("button", { name: /send ▸/i })).toBeDisabled());
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  expect(api.chat).not.toHaveBeenCalled();

  finishRename!({ id: "s1", title: "Renamed" });     // the PUT answers
  await waitFor(() =>
    expect(screen.getByRole("button", { name: /send ▸/i })).not.toBeDisabled());
});

test("a verification read retired by a scene switch still gives the prompt back", async () => {
  // `selectScene` returns -1 when a newer owner takes the view: it read nothing
  // and applied nothing. The await did not throw, though, so `refreshed` was
  // true and the turn counted as verified — with no growth to point at, an
  // undelivered prompt went unrestored. "It did not look" is unverifiable.
  (api.listScenes as any).mockResolvedValue([
    { id: "s1", title: "Old", model: "", created: "", updated: "" },
    { id: "s2", title: "Later", model: "", created: "", updated: "" },
  ]);
  let releaseVerify: (() => void) | null = null;
  let loaded = false;
  (api.getScene as any).mockImplementation(async (_c: string, sid: string) => {
    // the verification read for s1 hangs until the player has moved to s2
    if (sid === "s1" && loaded) {
      await new Promise<void>((r) => { releaseVerify = r; });
    }
    loaded = true;
    return { meta: {}, total: 0, messages: [] };
  });
  (api.chat as any).mockImplementation(async () => {
    const err: Error & { beforeResponse?: boolean } = new Error("Failed to fetch");
    err.beforeResponse = true;      // nothing reached the server
    throw err;
  });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await waitFor(() => expect(releaseVerify).not.toBeNull());
  await openScene(/Later/);   // retires the pending read
  releaseVerify!();
  await new Promise((r) => setTimeout(r, 60));
  // Recovered, but not into the composer the player is looking at: these are
  // scene s1's words and Send here would post them to s2. The prompt is held
  // under the scene it was written for (review, #95) …
  expect(screen.getByRole("heading", { name: /Later/ })).toBeInTheDocument();
  expect(screen.getByRole("textbox")).toHaveValue("");
  // … and handed back when that scene is on screen again. Recovered, not lost,
  // which is the whole point of counting a retired read as unverifiable.
  await openScene(/^Old$/);
  await waitFor(() =>
    expect(screen.getByRole("textbox")).toHaveValue("I draw my blade."));
});

test("a pending rename also blocks a proposal continuation", async () => {
  // Resolving a roll streams a continuation through `runStream` without passing
  // `send`/`retry`/`reroll`, so the per-call-site rename checks all missed it.
  // The guard belongs where every stream enters.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getRollProposal as any).mockResolvedValue({
    record: { id: "p1", status: "pending", resolution: null,
              payload: { id: "p1", check: "wits", check_label: "Wits", problems: [] } },
  });
  let finishRename: ((v: any) => void) | null = null;
  (api.renameScene as any).mockImplementation(
    () => new Promise((res) => { finishRename = res; }));
  renderCampaign();
  await screen.findByRole("heading", { name: /^Old$/ });
  fireEvent.click(screen.getByRole("button", { name: /rename/i }));
  const input = screen.getByDisplayValue("Old");
  fireEvent.change(input, { target: { value: "Renamed" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(api.renameScene).toHaveBeenCalled());

  const decline = await screen.findByRole("button", { name: /decline/i });
  fireEvent.click(decline);
  await new Promise((r) => setTimeout(r, 50));
  expect(api.resolveProposal).not.toHaveBeenCalled();   // the file may be moving
  // and the chip is still there: refusing to send must not also hide the
  // decision, or the roll becomes unreachable until some later refresh
  expect(screen.getByRole("button", { name: /decline/i })).toBeInTheDocument();

  finishRename!({ id: "s1", title: "Renamed" });
  await waitFor(() => expect(screen.queryByDisplayValue("Renamed")).toBeNull());
});

test("a poll fetch already in flight cannot clear a new turn's preview", async () => {
  // The check-then-await window: the poll verifies it owns the view, then
  // awaits getScene, and a turn starting during that await would otherwise have
  // the stale response run setStreaming("") over the new stream's text.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  let releaseFetch: (() => void) | null = null;
  (api.getScene as any).mockImplementation(async () => {
    if (releaseFetch) await new Promise<void>((r) => { releaseFetch = r; });
    return { meta: {}, messages: [] };
  });
  (api.chat as any)
    .mockImplementationOnce(hangingChat(["first fragment"]))
    .mockImplementation(hangingChat(["second fragment"]));
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  await screen.findByRole("button", { name: /continue ▶/i });

  releaseFetch = () => {};                       // the next getScene blocks
  const before = (api.getScene as any).mock.calls.length;
  await waitFor(() => expect((api.getScene as any).mock.calls.length).toBeGreaterThan(before));

  fireEvent.change(screen.getByRole("textbox"), { target: { value: "again" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByText("second fragment");
  (releaseFetch as () => void)();                // the stale poll's fetch lands
  await new Promise((r) => setTimeout(r, 50));
  expect(screen.getByText("second fragment")).toBeInTheDocument();
});

test("a cancelled fence's proposal survives a stale proposal read", async () => {
  // selectScene fires getRollProposal and awaits only getScene, so on the tick
  // that catches the flush the two race it independently. finalize writes the
  // proposal before the narration, so the scene read that saw growth is after
  // both writes while the proposal read beside it can be before either — and
  // its late null would clear a chip that does exist, with the poll already
  // stopped. Here the first proposal read returns null and the second (awaited,
  // after growth) returns the record.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  let flushed = false;
  let sceneSawGrowth = false;
  (api.getScene as any).mockImplementation(async () => {
    if (flushed) sceneSawGrowth = true;
    return {
      meta: {}, total: flushed ? 1 : 0,
      messages: flushed ? [{ role: "assistant", content: "She lunges" }] : [],
    };
  });
  // Evaluated when the call is made, not when it resolves — so the read
  // selectScene fires before awaiting getScene still sees the pre-flush world,
  // which is precisely the stale answer that used to win.
  (api.getRollProposal as any).mockImplementation(async () => ({
    record: sceneSawGrowth
      ? { id: "pr-1", status: "pending", payload: PROPOSAL_PAYLOAD, resolution: null }
      : null,
  }));
  (api.chat as any).mockImplementation(hangingChat(["She lunges"]));
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I punch him" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  setTimeout(() => { flushed = true; }, 100);
  expect(await screen.findByRole("button", { name: "Roll it" })).toBeInTheDocument();
});

test("a slow pre-flush proposal read cannot undo the settling read", async () => {
  // selectScene's proposal read is fired and not awaited. If it resolves after
  // settleProposal has installed the record, last-write-wins puts its pre-flush
  // null back — chip gone, poll already finished. The settling read is issued
  // later, so it must win regardless of which lands first.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  let flushed = false;
  let sceneSawGrowth = false;
  let releaseStale: (() => void) | null = null;
  (api.getScene as any).mockImplementation(async () => {
    if (flushed) sceneSawGrowth = true;
    return {
      meta: {}, total: flushed ? 1 : 0,
      messages: flushed ? [{ role: "assistant", content: "She lunges" }] : [],
    };
  });
  // The two reads are told apart by when they are *made*, not by an argument:
  // selectScene fires its one before awaiting getScene, so it still sees the
  // pre-growth world; settleProposal's comes after. That is the real ordering,
  // and it is the only thing left distinguishing them now the endpoint opts out
  // of coalescing for every caller.
  (api.getRollProposal as any).mockImplementation(async () => {
    if (sceneSawGrowth) {
      return { record: { id: "pr-1", status: "pending", payload: PROPOSAL_PAYLOAD, resolution: null } };
    }
    // the unawaited read: parked, so it lands *after* the settling one
    if (flushed) await new Promise<void>((r) => { releaseStale = r; });
    return { record: null };
  });
  (api.chat as any).mockImplementation(hangingChat(["She lunges"]));
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I punch him" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  setTimeout(() => { flushed = true; }, 100);
  await screen.findByRole("button", { name: "Roll it" });
  (releaseStale as unknown as () => void)();   // the stale null finally arrives
  await new Promise((r) => setTimeout(r, 50));
  expect(screen.getByRole("button", { name: "Roll it" })).toBeInTheDocument();
});

test("the refresh right after a cancel cannot wipe the next turn's preview", async () => {
  // Stop clears `busy` before the immediate refresh resolves, so the next turn
  // can begin while that fetch is in flight — and its response would otherwise
  // run setMessages/setStreaming("") straight over the new turn's state.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  let holdRefresh: (() => void) | null = null;
  (api.getScene as any).mockImplementation(async () => {
    if (holdRefresh) await new Promise<void>((r) => { holdRefresh = r; });
    return { meta: {}, total: 0, messages: [] };
  });
  (api.chat as any)
    .mockImplementationOnce(hangingChat(["first fragment"]))
    .mockImplementation(hangingChat(["second fragment"]));
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));

  holdRefresh = () => {};                       // the post-cancel refresh parks
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  await waitFor(() => expect(holdRefresh).not.toBe(null));
  await screen.findByRole("button", { name: /continue ▶/i });   // Send is live again

  fireEvent.change(screen.getByRole("textbox"), { target: { value: "again" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByText("second fragment");
  (holdRefresh as unknown as () => void)();     // the stale refresh finally lands
  await new Promise((r) => setTimeout(r, 50));
  expect(screen.getByText("second fragment")).toBeInTheDocument();
});

test("Stop after the done frame is a finished turn, not a cancellation", async () => {
  // `done` is parsed off the stream before the body reports EOF, and Stop stays
  // live until it does. A press in that gap used to be classed as a cancel,
  // which handed back a one-shot response length the reply had already
  // consumed — and spent it again on the next turn.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.listResponsePresets as any).mockResolvedValue(RESPONSE_PRESETS);
  (api.getScene as any).mockResolvedValue({ meta: { id: "s1", title: "Old" }, total: 0, messages: [] });
  (api.chat as any).mockImplementation(
    async (_c: string, _s: string, _t: string, onEvent: any, _r: unknown, signal: AbortSignal) => {
      onEvent({ delta: "All told." });
      onEvent({ done: true });          // persisted server-side from here on
      await new Promise<void>((_res, reject) => {   // body still open
        signal.addEventListener("abort", () => {
          const err = new Error("The operation was aborted.");
          err.name = "AbortError";
          reject(err);
        });
      });
    });
  renderCampaign();
  const picker = await screen.findByLabelText("Response length");
  // Waited for, like the picker tests above: the <select> renders as soon as
  // `listScenes` names the scene, but its OPTIONS arrive on the
  // `listResponsePresets` chain. Selecting a value the list does not carry yet
  // leaves the native select on "" and reports that back as a CLEARED override,
  // so the test would go green having exercised nothing.
  await screen.findByRole("option", { name: "Terse" });
  fireEvent.change(picker, { target: { value: "terse" } });
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  await screen.findByRole("button", { name: /continue ▶/i });
  // spent by the reply that did land, so it must not ride the next turn
  await waitFor(() => expect(picker).not.toHaveValue("terse"));
});

test("a failed send that was rolled back gives the player their words back", async () => {
  // The backend removes the post when a turn fails having produced nothing, so
  // without this the text exists nowhere: the composer was cleared on send and
  // the refresh drops the optimistic copy. Retry cannot recover it either — it
  // calls /retry, which has no prompt of its own.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.chat as any).mockImplementation(
    async (_c: string, _s: string, _t: string, onEvent: any) => {
      onEvent({ error: { detail: "OpenRouter API key is not set", kind: "missing_key",
                         post_returned: true } });
    });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByText(/OpenRouter API key is not set/);
  await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("I draw my blade."));
});

test("a Stop before the request lands gives the prompt back too", async () => {
  // The abort beats the request to the server, so there is no post to roll back
  // and no error frame to carry `post_returned` — but the player is in exactly
  // the same position, with the composer cleared and nothing durable anywhere.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.chat as any).mockImplementation(async () => {
    const err: Error & { beforeResponse?: boolean } = new Error("The operation was aborted.");
    err.name = "AbortError";
    err.beforeResponse = true;   // set by streamPost when fetch never resolved
    throw err;
  });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue("I draw my blade."));
});

test("an abort whose post did land does not duplicate the prompt", async () => {
  // `beforeResponse` only means no response arrived. The server can have
  // appended the post and had the abort beat its headers back — restoring then
  // puts the text in the composer *and* the transcript, and the next Send
  // sends it twice. Growth in the refreshed transcript is what settles it.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  let landedOnServer = false;
  (api.getScene as any).mockImplementation(async () => ({
    meta: {}, total: landedOnServer ? 1 : 0,
    messages: landedOnServer ? [{ role: "user", content: "I draw my blade." }] : [],
  }));
  (api.chat as any).mockImplementation(async () => {
    landedOnServer = true;      // it did reach post_chat, headers just never came back
    const err: Error & { beforeResponse?: boolean } = new Error("The operation was aborted.");
    err.name = "AbortError";
    err.beforeResponse = true;
    throw err;
  });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByText("I draw my blade.");   // the post is really there
  await new Promise((r) => setTimeout(r, 50));
  expect(screen.getByRole("textbox")).toHaveValue("");
});

test("a cancel after the request landed leaves the composer alone", async () => {
  // The post is durably stored by then — a cancel keeps it — so restoring would
  // have the player send the same line twice.
  //
  // The refreshed transcript has to actually contain that post, or the test
  // asserts its opposite: this used to run against the default empty-scene
  // mock, so it modelled a cancel whose post was NOT stored and passed only
  // because nothing restored on this path at all. Growth is now what tells the
  // two apart (review, #95).
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  let posted = false;
  (api.getScene as any).mockImplementation(async () => ({
    meta: {}, total: posted ? 1 : 0,
    messages: posted ? [{ role: "user", content: "I draw my blade." }] : [],
  }));
  (api.chat as any).mockImplementation((...args: any[]) => {
    posted = true;   // post_chat appended before returning the stream
    return (hangingChat(["The tide "]) as any)(...args);
  });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  await screen.findByRole("button", { name: /continue ▶/i });
  expect(screen.getByRole("textbox")).toHaveValue("");
});

test("a failure the backend did not roll back leaves the composer alone", async () => {
  // The post is still in the transcript, so restoring it would have the player
  // send the same line twice. Only the backend knows which happened.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.chat as any).mockImplementation(
    async (_c: string, _s: string, _t: string, onEvent: any) => {
      onEvent({ delta: "The tide " });
      onEvent({ error: { detail: "connection reset", kind: "network" } });
    });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByText(/connection reset/);
  expect(screen.getByRole("textbox")).toHaveValue("");
});

test("a network failure names the recovery, not just the socket error (#210)", async () => {
  // Being offline used to read as the toast "connection reset" and nothing
  // else: the `network` kind reached the browser and was thrown away. What the
  // reader needs is that the library is local and a local model connection
  // keeps play going -- and a way to get there.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.chat as any).mockImplementation(
    async (_c: string, _s: string, _t: string, onEvent: any) => {
      onEvent({ error: { detail: "connection reset", kind: "network" } });
    });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByText(/Couldn.t reach the model provider/);
  expect(screen.getByRole("link", { name: /Connections/ }))
    .toHaveAttribute("href", "/connections");
  // The provider's own words survive alongside it: `network` is also what a
  // local endpoint that is not running raises, and that reader needs them.
  expect(screen.getByText(/connection reset/)).toBeInTheDocument();
  // Still retryable -- the network coming back is exactly the fix.
  expect(screen.getByRole("button", { name: /^retry$/i })).toBeInTheDocument();
});

test("an unconfigured key is not an offline app", async () => {
  // The two were flagged as conflated in #210 and have opposite fixes; a
  // missing key gets the plain detail and no offline advice.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.chat as any).mockImplementation(
    async (_c: string, _s: string, _t: string, onEvent: any) => {
      onEvent({ error: { detail: "OpenRouter API key is not set", kind: "missing_key" } });
    });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByText(/OpenRouter API key is not set/);
  expect(screen.queryByText(/Couldn.t reach the model provider/)).toBeNull();
});

test("a body that ends before the done frame is an interrupted turn", async () => {
  // `reader.read()` reporting EOF resolves streamPost normally, so a proxy
  // cutting the body short used to look identical to a completed turn — the
  // one-shot override was spent and the flush never waited for.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.listResponsePresets as any).mockResolvedValue(RESPONSE_PRESETS);
  // The truncated turn is treated as interrupted, so the flush poll runs; let
  // the partial land so it exits on its first tick rather than sitting out the
  // whole budget — `runStream` awaits it before the override is settled either
  // way, and a `waitFor` that ran before that would pass on any implementation.
  let flushed = false;
  (api.getScene as any).mockImplementation(async () => ({
    meta: { id: "s1", title: "Old" },
    total: flushed ? 1 : 0,
    messages: flushed ? [{ role: "assistant", content: "The tide" }] : [],
  }));
  (api.chat as any).mockImplementation(
    async (_c: string, _s: string, _t: string, onEvent: any) => {
      onEvent({ delta: "The tide " });   // ...and then the body just ends
    });
  renderCampaign();
  const picker = await screen.findByLabelText("Response length");
  await screen.findByRole("option", { name: "Terse" });
  fireEvent.change(picker, { target: { value: "terse" } });
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  setTimeout(() => { flushed = true; }, 100);
  await screen.findByText("The tide");          // poll caught the flush and ended
  await new Promise((r) => setTimeout(r, 600)); // let the poll exit and send() settle
  // unspent: the reply never confirmed, so the override rides the retry
  expect(picker).toHaveValue("terse");
});

test("the post-cancel poll stops once a new turn owns the view", async () => {
  // Stop clears `busy` before the poll finishes, so the player can send again
  // while it is still running. Left alone it would keep calling selectScene,
  // clearing the new stream's live preview on every tick.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [] });
  (api.chat as any).mockImplementation(hangingChat(["a streamed fragment"]));
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  await screen.findByRole("button", { name: /continue ▶/i });   // cancel settled
  const afterCancel = (api.getScene as any).mock.calls.length;

  fireEvent.change(screen.getByRole("textbox"), { target: { value: "again" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByRole("button", { name: /stop ■/i });       // second turn in flight
  await new Promise((r) => setTimeout(r, 700));                 // past two poll ticks
  // The live preview of the second turn survives, and the stale poll made no
  // further fetches of its own.
  expect(screen.getByText("a streamed fragment")).toBeInTheDocument();
  expect((api.getScene as any).mock.calls.length).toBe(afterCancel);
});

test("cancelling keeps a one-shot response override for the retry", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.listResponsePresets as any).mockResolvedValue(RESPONSE_PRESETS);
  (api.chat as any).mockImplementation(hangingChat());
  renderCampaign();
  const picker = await screen.findByLabelText("Response length");
  await screen.findByRole("option", { name: "Terse" });
  fireEvent.change(picker, { target: { value: "terse" } });
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "and then?" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  fireEvent.click(await screen.findByRole("button", { name: /stop ■/i }));
  await waitFor(() => expect(picker).toHaveValue("terse")); // unspent, like a failure
});

test("Reroll on the last assistant post replaces it with a fresh reply", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any)
    .mockResolvedValueOnce({ meta: {}, messages: [
      { role: "user", content: "hi" }, { role: "assistant", content: "old reply" }] })
    .mockResolvedValue({ meta: {}, messages: [
      { role: "user", content: "hi" }, { role: "assistant", content: "fresh reply" }] });
  (api.regenerate as any).mockImplementation(async (_c: string, _s: string, onEvent: any) => {
    onEvent({ delta: "fresh reply" });
  });
  renderCampaign();
  await screen.findByText("old reply");
  fireEvent.click(await screen.findByTitle("Reroll"));
  // clicking Reroll opens the popover instead of firing immediately
  expect(api.regenerate).not.toHaveBeenCalled();
  expect(screen.getByTitle("Reroll")).toBeInTheDocument(); // hovertext present
  fireEvent.click(screen.getByRole("button", { name: /reroll ▸/i })); // empty = plain reroll
  await waitFor(() => expect(api.regenerate).toHaveBeenCalledWith(
    "run", "s1", expect.any(Function), undefined, undefined, expect.any(AbortSignal)));
  await screen.findByText("fresh reply");
  expect(screen.queryByText("old reply")).toBeNull();
});

test("typed guidance is passed to regenerate", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "old reply" }] });
  renderCampaign();
  await screen.findByText("old reply");
  fireEvent.click(await screen.findByTitle("Reroll"));
  const input = screen.getByPlaceholderText(/guide the reroll/i);
  fireEvent.change(input, { target: { value: "make her angrier" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(api.regenerate).toHaveBeenCalledWith(
    "run", "s1", expect.any(Function), "make her angrier", undefined, expect.any(AbortSignal)));
});

test("Escape closes the reroll popover without firing", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "old reply" }] });
  renderCampaign();
  await screen.findByText("old reply");
  fireEvent.click(await screen.findByTitle("Reroll"));
  fireEvent.keyDown(screen.getByPlaceholderText(/guide the reroll/i), { key: "Escape" });
  expect(screen.queryByPlaceholderText(/guide the reroll/i)).toBeNull();
  expect(api.regenerate).not.toHaveBeenCalled();
});

test("regenerate carries a pending override", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({
    meta: { id: "s1", title: "Old", response_preset: "cinematic" },
    messages: [{ role: "user", content: "hi" }, { role: "assistant", content: "old reply" }],
  });
  (api.listResponsePresets as any).mockResolvedValue(RESPONSE_PRESETS);
  renderCampaign();
  await screen.findByText("old reply");
  const picker = await screen.findByLabelText("Response length");
  await screen.findByRole("option", { name: "Terse" });
  fireEvent.change(picker, { target: { value: "terse" } });
  fireEvent.click(await screen.findByTitle("Reroll"));
  fireEvent.click(screen.getByRole("button", { name: /reroll ▸/i })); // empty guidance = plain reroll
  await waitFor(() => expect(api.regenerate).toHaveBeenCalledWith(
    "run", "s1", expect.any(Function), undefined, { response_preset: "terse" }, expect.any(AbortSignal)));
});

// The wire addresses a variant by a content-derived `id`, not by position:
// retention shifts every index when a full set gains a take.
const ALT = (preview: string) =>
  ({ id: `id-${preview}`, created: "t", guidance: "", posts: 1, preview });

test("a rerolled post carries a swipe control counting its alternates", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "fresh reply" }] });
  (api.getAlternates as any).mockResolvedValue({
    active: 1, alternates: [ALT("old reply"), ALT("fresh reply")] });
  renderCampaign();
  await screen.findByText("fresh reply");
  expect(await screen.findByText("2/2")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: /previous alternate/i }));

  await waitFor(() => expect(api.pickAlternate).toHaveBeenCalledWith("run", "s1", "id-old reply"));
});

test("the counter names the guidance that produced the take on screen", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a colder take" }] });
  (api.getAlternates as any).mockResolvedValue({
    active: 1,
    alternates: [ALT("old"), { ...ALT("a colder take"), guidance: "make it colder" }],
  });
  renderCampaign();

  const counter = await screen.findByText("2/2");

  expect(counter).toHaveAttribute("title", expect.stringContaining("make it colder"));
  expect(counter).toHaveAttribute("title", expect.stringContaining("a colder take"));
});

test("the swipe control wraps, so cycling tours the whole set", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "take three" }] });
  (api.getAlternates as any).mockResolvedValue({
    active: 2, alternates: [ALT("one"), ALT("two"), ALT("take three")] });
  renderCampaign();
  await screen.findByText("3/3");

  fireEvent.click(screen.getByRole("button", { name: /next alternate/i }));

  await waitFor(() => expect(api.pickAlternate).toHaveBeenCalledWith("run", "s1", "id-one"));
});

test("no swipe control when the live reply is the only variant there is", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  (api.getAlternates as any).mockResolvedValue({ active: 0, alternates: [ALT("a reply")] });
  renderCampaign();
  await screen.findByText("a reply");
  expect(screen.queryByRole("button", { name: /alternate/i })).toBeNull();
});

test("a reroll whose stream died still offers the reply it parked", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "user", content: "hi" }] });
  (api.getAlternates as any).mockResolvedValue({ active: null, alternates: [ALT("old reply")] });
  renderCampaign();
  await screen.findByText("–/1");

  fireEvent.click(screen.getByRole("button", { name: /next alternate/i }));

  await waitFor(() => expect(api.pickAlternate).toHaveBeenCalledWith("run", "s1", "id-old reply"));
});

test("a second swipe click is ignored while the first is in flight", async () => {
  // both clicks would otherwise read the same `active` snapshot and send the
  // same index, so two ‹ presses would step back only once
  let release: (v: any) => void = () => {};
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "take three" }] });
  (api.getAlternates as any).mockResolvedValue({
    active: 2, alternates: [ALT("one"), ALT("two"), ALT("take three")] });
  (api.pickAlternate as any).mockImplementation(() => new Promise((res) => { release = res; }));
  renderCampaign();
  await screen.findByText("3/3");

  fireEvent.click(screen.getByRole("button", { name: /previous alternate/i }));
  fireEvent.click(screen.getByRole("button", { name: /previous alternate/i }));

  expect(api.pickAlternate).toHaveBeenCalledTimes(1);
  expect(api.pickAlternate).toHaveBeenCalledWith("run", "s1", "id-two");
  release({ ok: true });
});

test("renaming the active scene keeps its alternates on screen", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  (api.getAlternates as any).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  (api.renameScene as any).mockResolvedValue({ id: "s1-renamed", title: "New" });
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
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({
    meta: {}, offset: 40, total: 42, has_older: true, has_user_message: true,
    messages: [{ role: "user", content: "hi" }, { role: "assistant", content: "a reply" }],
  });
  (api.getAlternates as any).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  renderCampaign();
  await screen.findByText("a reply");

  expect(await screen.findByText("2/2")).toBeInTheDocument();
});

test("a swap in flight does not disable the scene the reader moved to", async () => {
  // `rolling` is component-wide, so an operation belonging to the scene that was
  // left held every control in the scene that was entered — Send, Retry, reroll,
  // edit and roll — until an unrelated request settled.
  (api.listScenes as any).mockResolvedValue([
    { id: "s1", title: "One", model: "", created: "", updated: "" },
    { id: "s2", title: "Two", model: "", created: "", updated: "" },
  ]);
  (api.getScene as any).mockImplementation(async (_c: string, s: string) => ({
    meta: {}, messages: [
      { role: "user", content: "hi" },
      { role: "assistant", content: s === "s1" ? "a reply" : "the other scene" }] }));
  (api.getAlternates as any).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  (api.pickAlternate as any).mockImplementation(() => new Promise(() => {}));  // never settles
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
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  (api.getAlternates as any).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  (api.pickAlternate as any).mockImplementation(() => new Promise(() => {}));  // never settles
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
  (api.listScenes as any).mockResolvedValueOnce(ONE_SCENE)
    .mockRejectedValue(new Error("relist failed"));
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  (api.renameScene as any).mockResolvedValue({ id: "s1-renamed", title: "New" });
  renderCampaign();
  await screen.findByText("a reply");
  const before = (api.getScene as any).mock.calls.length;

  fireEvent.click(screen.getByRole("button", { name: /rename/i }));
  const input = screen.getByDisplayValue("Old");
  fireEvent.change(input, { target: { value: "New" } });
  fireEvent.keyDown(input, { key: "Enter" });

  await waitFor(() =>
    expect((api.getScene as any).mock.calls.length).toBeGreaterThan(before));
  expect((api.getScene as any).mock.calls.at(-1)[1]).toBe("s1-renamed");
});

test("a first-date rename whose re-read fails says so instead of going quiet", async () => {
  // The re-read is what replaces posts a swap against the old id skipped, and
  // this path fired it without awaiting: the rejection went nowhere, no banner
  // appeared, and the pre-rename messages stayed on screen under the new id —
  // editable, against indices that have shifted.
  relistsAs(ONE_SCENE, [{ id: "s1-dated", title: "Old", model: "", created: "", updated: "" }]);
  // only the read for the NEW id fails, so nothing else in the view is disturbed
  (api.getScene as any).mockImplementation(async (_c: string, s: string) => {
    if (s === "s1-dated") throw Object.assign(new Error("boom"), { detail: "scene read failed" });
    return { meta: {}, messages: [
      { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] };
  });
  (api.getSceneDatetime as any).mockResolvedValue(
    { current: null, history: [], suggested: "2026-07-06" });
  (api.getCalendarMonths as any).mockResolvedValue({ months: [
    { key: "07", name: "July", days: 31 }] });
  (api.setSceneDatetime as any).mockResolvedValue({ id: "s1-dated" });
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
  let finishRelist: (v: any) => void = () => {};
  (api.listScenes as any).mockResolvedValueOnce([
    { id: "s1", title: "One", model: "", created: "", updated: "" },
    { id: "s2", title: "Two", model: "", created: "", updated: "" },
  ]).mockImplementation(() => new Promise((res) => { finishRelist = res; }));
  (api.getScene as any).mockImplementation(async (_c: string, s: string) => ({
    meta: {}, messages: [
      { role: "user", content: "hi" },
      { role: "assistant", content: s === "s2" ? "the other scene" : "a reply" }] }));
  (api.renameScene as any).mockResolvedValue({ id: "s1-renamed", title: "New" });
  renderCampaign();
  await screen.findByText("a reply");

  fireEvent.click(screen.getByRole("button", { name: /rename scene/i }));
  const input = screen.getByDisplayValue("One");
  fireEvent.change(input, { target: { value: "New" } });
  fireEvent.keyDown(input, { key: "Enter" });
  // the PUT has landed and the id is adopted; the relist is still open
  await waitFor(() => expect(api.listScenes).toHaveBeenCalledTimes(2));
  await openScene(/^Two$/);
  await waitFor(() => expect((api.getScene as any).mock.calls.map((c: any) => c[1])).toContain("s2"));
  finishRelist([{ id: "s1-renamed", title: "New", model: "", created: "", updated: "" },
                { id: "s2", title: "Two", model: "", created: "", updated: "" }]);
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });

  // no read for the renamed scene after the reader left it: `selectScene` calls
  // `setActive`, so one would have navigated them back and loaded it over Two
  expect((api.getScene as any).mock.calls.map((c: any) => c[1])).toEqual(["s1", "s2"]);
});

test("a stale swap succeeding does not clear the new scene's banner", async () => {
  // The scoped latch deliberately leaves the destination usable, so it can
  // raise a failure of its own while the old scene's POST is still open. That
  // POST landing must not wipe a banner belonging to a scene it never touched.
  let release: (v: any) => void = () => {};
  (api.listScenes as any).mockResolvedValue([
    { id: "s1", title: "One", model: "", created: "", updated: "" },
    { id: "s2", title: "Two", model: "", created: "", updated: "" },
  ]);
  (api.getScene as any).mockImplementation(async (_c: string, s: string) => ({
    meta: {}, messages: [
      { role: "user", content: "hi" },
      { role: "assistant", content: s === "s1" ? "a reply" : "the other scene" }] }));
  (api.getAlternates as any).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  (api.pickAlternate as any).mockImplementation(() => new Promise((res) => { release = res; }));
  renderCampaign();
  await screen.findByText("2/2");
  fireEvent.click(screen.getByRole("button", { name: /previous alternate/i }));
  await openScene(/^Two$/);
  await screen.findByText("the other scene");

  // scene Two raises its own failure while One's swap is still open
  (api.chat as any).mockRejectedValue({ detail: "two is broken" });
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
  const offscreen = [{ id: "s1", title: "Cabal", model: "", created: "", updated: "", pcless: true }];
  (api.listScenes as any).mockResolvedValueOnce(offscreen)
    .mockRejectedValue(new Error("relist failed"));
  (api.renameScene as any).mockResolvedValue({ id: "s1-renamed", title: "New" });
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
  let release: (v: any) => void = () => {};
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  (api.getAlternates as any).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  (api.getRollProposal as any).mockResolvedValue({
    record: { id: "p1", status: "pending", resolution: null,
              payload: { id: "p1", check: "wits", check_label: "Wits", problems: [] } } });
  (api.pickAlternate as any).mockImplementation(() => new Promise((res) => { release = res; }));
  renderCampaign();
  await screen.findByText("2/2");
  const rollIt = await screen.findByRole("button", { name: "Roll it" });

  fireEvent.click(screen.getByRole("button", { name: /previous alternate/i }));

  // in flight: the decision cannot be adjudicated against a take being replaced
  await waitFor(() => expect(rollIt).toBeDisabled());

  // committed: the backend retired it, so the chip goes without waiting for a read
  (api.getRollProposal as any).mockResolvedValue({ record: null });
  release({ ok: true });
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: "Roll it" })).toBeNull());
});

test("a failed post-swap read does not retry the reader back onto the old scene", async () => {
  // The retry is a second `selectScene`, and `selectScene` calls `setActive` —
  // so once the reader has moved on it does not merely refresh a scene nobody
  // is looking at, it navigates them back to it.
  let rejectRefresh: (v: any) => void = () => {};
  (api.listScenes as any).mockResolvedValue([
    { id: "s1", title: "One", model: "", created: "", updated: "" },
    { id: "s2", title: "Two", model: "", created: "", updated: "" },
  ]);
  const page = (c: string) => ({ meta: {}, messages: [
    { role: "user", content: "hi" },
    { role: "assistant", content: c === "s1" ? "a reply" : "the other scene" }] });
  let firstLoadDone = false;
  (api.getScene as any).mockImplementation(async (_c: string, s: string) => {
    // the post-swap read-back — the only s1 read after the initial load
    if (s === "s1" && firstLoadDone) return new Promise((_r, rej) => { rejectRefresh = rej; });
    if (s === "s1") firstLoadDone = true;
    return page(s);
  });
  (api.getAlternates as any).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  renderCampaign();
  await screen.findByText("2/2");

  fireEvent.click(screen.getByRole("button", { name: /previous alternate/i }));
  await waitFor(() => expect(api.pickAlternate).toHaveBeenCalled());
  await openScene(/^Two$/);
  await screen.findByText("the other scene");
  const reads = (api.getScene as any).mock.calls.length;

  rejectRefresh({ detail: "scene read failed" });   // s1's read-back gives up
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });

  expect((api.getScene as any).mock.calls.length).toBe(reads);   // no retry issued
  expect(screen.getByRole("heading", { name: /^Two$/ })).toBeInTheDocument();
  expect(screen.queryByText(/could not be re-read/)).toBeNull();
});

test("a swap that fails after the user moved on does not banner the new scene", async () => {
  // Switching scenes clears the banner on purpose — one scene's failure must
  // not offer its Retry against another. A swap rejecting afterwards put it
  // straight back, under a scene it has nothing to do with.
  let reject: (v: any) => void = () => {};
  (api.listScenes as any).mockResolvedValue([
    { id: "s1", title: "One", model: "", created: "", updated: "" },
    { id: "s2", title: "Two", model: "", created: "", updated: "" },
  ]);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  (api.getAlternates as any).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  (api.pickAlternate as any).mockImplementation(() => new Promise((_r, rej) => { reject = rej; }));
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
  let release: (v: any) => void = () => {};
  (api.listScenes as any).mockResolvedValue([
    { id: "s1", title: "One", model: "", created: "", updated: "" },
    { id: "s2", title: "Two", model: "", created: "", updated: "" },
  ]);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  (api.getAlternates as any).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  (api.pickAlternate as any).mockImplementation(() => new Promise((res) => { release = res; }));
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
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  (api.getAlternates as any).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  render(
    <MemoryRouter initialEntries={["/campaigns/run"]}>
      {withPalette(<>
        <Link to="/campaigns/other">switch campaign</Link>
        {playRoutes()}
      </>)}
    </MemoryRouter>,
  );
  await screen.findByText("2/2");

  // navigate to another campaign; the component stays mounted and only `cid`
  // changes, and this campaign's own fetches never land
  (api.getAlternates as any).mockImplementation(() => new Promise(() => {}));
  fireEvent.click(screen.getByText("switch campaign"));

  await waitFor(() => expect(screen.queryByText("2/2")).toBeNull());
  expect(screen.queryByRole("button", { name: /alternate/i })).toBeNull();
});

test("a swap that finishes after a campaign switch does not load the old campaign", async () => {
  // scene ids repeat between campaigns, so "same sid" is not "same scene": if
  // B happens to select s1 too, a sid-only completion guard passes and the
  // stale closure refreshes s1 out of A, showing A's transcript under B.
  let release: (v: any) => void = () => {};
  (api.listScenes as any).mockResolvedValue(ONE_SCENE); // both campaigns: s1
  (api.getScene as any).mockImplementation(async (c: string) => ({
    meta: {}, messages: [
      { role: "user", content: "hi" },
      { role: "assistant", content: c === "run" ? "a reply" : "another campaign's reply" },
    ],
  }));
  (api.getAlternates as any).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  (api.pickAlternate as any).mockImplementation(() => new Promise((res) => { release = res; }));
  render(
    <MemoryRouter initialEntries={["/campaigns/run"]}>
      {withPalette(<>
        <Link to="/campaigns/other">switch campaign</Link>
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

test("a message cannot be edited while a swap is in flight", async () => {
  // the edit carries the index and text of the message the promotion is
  // replacing, so whichever write loses the race discards the other silently
  let release: (v: any) => void = () => {};
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  (api.getAlternates as any).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  (api.pickAlternate as any).mockImplementation(() => new Promise((res) => { release = res; }));
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
  let landB: (v: any) => void = () => {};
  (api.listScenes as any).mockResolvedValue([
    { id: "s1", title: "One", model: "", created: "", updated: "" },
    { id: "s2", title: "Two", model: "", created: "", updated: "" },
  ]);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  (api.getAlternates as any).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  renderCampaign();
  await screen.findByText("2/2");

  // s2's alternates resolve immediately; its transcript is still in flight
  (api.getScene as any).mockImplementationOnce(
    () => new Promise((res) => { landB = res; }));
  await openScene(/^Two$/);
  await waitFor(() => expect(api.getAlternates).toHaveBeenCalledWith("run", "s2"));

  expect(screen.queryByText("2/2")).toBeNull();          // not against s1's posts
  expect(screen.queryByRole("button", { name: /alternate/i })).toBeNull();

  landB({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  expect(await screen.findByText("2/2")).toBeInTheDocument();
});

test("the old set stops offering itself the moment a reroll clears the reply", async () => {
  // matching tokens only prove a set and a FETCH describe the same scene — the
  // optimistic truncation changes the posts under both without touching either.
  // The window is after the stream ends and before the refresh lands: `busy` is
  // already false, so the gutter is back, but the reply the set is keyed to is
  // still off the screen — and the picker drops onto the user post above it.
  let landRefresh: (v: any) => void = () => {};
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  (api.getAlternates as any).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  (api.regenerate as any).mockImplementation(async () => {});   // ends, lands nothing
  renderCampaign();
  await screen.findByText("2/2");

  // BOTH halves of the post-stream refresh are held open. That is the state the
  // token pair cannot see: the old set and the old transcript still agree with
  // each other, so they stay mutually valid while the posts under them have
  // already changed.
  let landAlts: (v: any) => void = () => {};
  (api.getScene as any).mockImplementationOnce(
    () => new Promise((res) => { landRefresh = res; }));
  (api.getAlternates as any).mockImplementationOnce(
    () => new Promise((res) => { landAlts = res; }));
  fireEvent.click(screen.getByRole("button", { name: /^reroll$/i }));
  fireEvent.click(await screen.findByRole("button", { name: /reroll ▸/i }));

  await waitFor(() => expect(screen.queryByText(/a reply/)).toBeNull());
  expect(screen.queryByText("2/2")).toBeNull();
  expect(screen.queryByRole("button", { name: /alternate/i })).toBeNull();

  landRefresh({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "second take" }] });
  landAlts({ active: 1, alternates: [ALT("a reply"), ALT("second take")] });
  expect(await screen.findByText("2/2")).toBeInTheDocument();
});

test("whitespace-only narration is an empty slot, and still offers Retry", async () => {
  // the backend persists a partial only when `watcher.narration.strip()` is
  // nonempty, so blank deltas leave the slot empty — reading them as a landed
  // partial takes away the one button that refills it
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  (api.getAlternates as any).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  (api.regenerate as any).mockImplementation(async (..._a: any[]) => {
    _a[2]({ delta: "  \n " });
    throw { detail: "upstream is down" };
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
  let landList: (v: any) => void = () => {};
  const TWO = [
    { id: "s1", title: "One", model: "", created: "", updated: "" },
    { id: "s2", title: "Two", model: "", created: "", updated: "" },
  ];
  (api.listScenes as any).mockResolvedValue(TWO);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [] });  // CastPanel shows
  renderCampaign();
  await screen.findByText("stub-datestamp");

  // s1's inspector reports the stamp; its re-list is still in flight
  (api.listScenes as any).mockImplementationOnce(
    () => new Promise((res) => { landList = res; }));
  fireEvent.click(screen.getByText("stub-datestamp"));
  // …and the reader moves to s2 before it settles
  await openScene(/^Two$/);
  await waitFor(() => expect(api.getScene).toHaveBeenCalledWith("run", "s2", expect.anything()));
  const after = (api.getScene as any).mock.calls.length;

  landList(TWO);

  await waitFor(() => expect((api.listScenes as any).mock.results.length).toBeGreaterThan(1));
  expect((api.getScene as any).mock.calls.slice(after)
    .filter((c: any[]) => c[1] === "s10")).toHaveLength(0);
});

test("a rename keeps the turn state — it is not a scene switch", async () => {
  // a rename mints a new id, so the refresh reads as a switch and throws away
  // state that belongs to the turn: an open roll form, and the one-shot
  // response preset picked for the next reply. Same scene, same reader; only
  // the filename moved.
  relistsAs(ONE_SCENE, [{ id: "s1-renamed", title: "Old", model: "", created: "", updated: "" }]);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  (api.renameScene as any).mockResolvedValue({ id: "s1-renamed" });
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
  let landRename: (v: any) => void = () => {};
  (api.listScenes as any).mockResolvedValue([
    { id: "s1", title: "One", model: "", created: "", updated: "" },
    { id: "s2", title: "Two", model: "", created: "", updated: "" },
  ]);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  (api.renameScene as any).mockImplementation(
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
  const after = (api.getScene as any).mock.calls.length;

  landRename({ id: "s1-renamed" });

  await waitFor(() => expect((api.listScenes as any).mock.results.length).toBeGreaterThan(1));
  expect((api.getScene as any).mock.calls.slice(after)
    .filter((c: any[]) => c[1] === "s1-renamed")).toHaveLength(0);
});

test("a rename re-reads the transcript, not just the ids pointing at it", async () => {
  // a swap in flight against the OLD id finds `activeIdRef` already moved on
  // and skips its own refresh — so without this the pre-swap posts stay on
  // screen with the renamed set's counter on top of them
  relistsAs(ONE_SCENE, [{ id: "s1-renamed", title: "Old", model: "", created: "", updated: "" }]);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  (api.getAlternates as any).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  (api.renameScene as any).mockResolvedValue({ id: "s1-renamed" });
  renderCampaign();
  await screen.findByText("2/2");
  const before = (api.getScene as any).mock.calls.length;

  fireEvent.click(screen.getByRole("button", { name: /rename/i }));
  const box = screen.getByDisplayValue("Old");
  fireEvent.change(box, { target: { value: "Renamed" } });
  fireEvent.keyDown(box, { key: "Enter" });

  await waitFor(() =>
    expect((api.getScene as any).mock.calls.length).toBeGreaterThan(before));
  expect((api.getScene as any).mock.calls.at(-1)[1]).toBe("s1-renamed");
});

test("a failed swap re-reads the scene, in case it emptied the slot", async () => {
  // `promote` removes the live run then appends the chosen one; if the append
  // failed the transcript no longer holds the reply on screen, and every index
  // below it is wrong
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  (api.getAlternates as any).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  (api.pickAlternate as any).mockRejectedValue({ detail: "no space left on device" });
  renderCampaign();
  await screen.findByText("2/2");
  const before = (api.getScene as any).mock.calls.length;

  fireEvent.click(screen.getByRole("button", { name: /previous alternate/i }));

  expect(await screen.findByText("no space left on device")).toBeInTheDocument();
  await waitFor(() =>
    expect((api.getScene as any).mock.calls.length).toBeGreaterThan(before));
});

test("a swap whose read-back fails is not reported as a failed swap", async () => {
  // The POST committed: the take really did change. Saying the swap failed is
  // wrong twice — it denies a change that happened, and the recovery it implies
  // is to do it again. What is actually wrong is the transcript on screen, so
  // the set that indexes it stops being offered.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  (api.getAlternates as any).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  renderCampaign();
  await screen.findByText("2/2");
  // every read from here on fails, including the retry of the read-back
  (api.getScene as any).mockRejectedValue(
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
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  (api.getAlternates as any).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  // an error FRAME, not a rejection: the backend ran its handler before sending
  // it, so there is no flush to wait out and the swap is available immediately
  (api.regenerate as any).mockImplementation(async (...args: unknown[]) => {
    (args.find((a) => typeof a === "function") as ((e: any) => void))(
      { error: { detail: "upstream is down" } });
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
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  (api.getAlternates as any).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  (api.regenerate as any).mockRejectedValue({ detail: "upstream is down" });
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
  let landRefresh: (v: any) => void = () => {};
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "first take" }] });
  (api.getAlternates as any).mockResolvedValue({ active: 0, alternates: [ALT("first take")] });
  (api.regenerate as any).mockImplementation(async (_c: any, _s: any, onEvent: any) => {
    onEvent({ delta: "second take" });
  });
  renderCampaign();
  await screen.findByText(/first take/);

  // the reroll's refresh: its alternates land first, its transcript is held
  (api.getAlternates as any).mockResolvedValue({
    active: 1, alternates: [ALT("first take"), ALT("second take")] });
  (api.getScene as any).mockImplementationOnce(
    () => new Promise((res) => { landRefresh = res; }));
  fireEvent.click(screen.getByRole("button", { name: /^reroll$/i }));
  fireEvent.click(await screen.findByRole("button", { name: /reroll ▸/i }));
  await waitFor(() => expect(api.getAlternates).toHaveBeenCalledTimes(2));

  // the trailing reply is gone from the screen; the set must not offer itself
  expect(screen.queryByText("2/2")).toBeNull();
  expect(screen.queryByRole("button", { name: /alternate/i })).toBeNull();

  landRefresh({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "second take" }] });
  expect(await screen.findByText("2/2")).toBeInTheDocument();
});

test("a colliding scene id across campaigns does not expose B's set on A's posts", async () => {
  // scene ids repeat between campaigns, so A→B with the same id reads as a
  // refresh rather than a switch — a sid-only key for the loaded transcript
  // never notices the posts on screen are still A's
  let landB: (v: any) => void = () => {};
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  (api.getAlternates as any).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  render(
    <MemoryRouter initialEntries={["/campaigns/run"]}>
      {withPalette(<>
        <Link to="/campaigns/other">switch campaign</Link>
        {playRoutes()}
      </>)}
    </MemoryRouter>,
  );
  await screen.findByText("2/2");

  // B selects the same sid; its alternates land first, its transcript is held
  (api.getScene as any).mockImplementationOnce(
    () => new Promise((res) => { landB = res; }));
  fireEvent.click(screen.getByText("switch campaign"));
  await waitFor(() => expect(api.getAlternates).toHaveBeenCalledWith("other", "s1"));

  expect(screen.queryByText("2/2")).toBeNull();
  expect(screen.queryByRole("button", { name: /alternate/i })).toBeNull();

  landB({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  expect(await screen.findByText("2/2")).toBeInTheDocument();
});

test("a failed reroll does not offer the Retry that would discard its set", async () => {
  // Retry appends a generation, which moves the slot and retires the set the
  // reroll was building — including the reply it parked. The gutter's own ↻ is
  // still there and stays in the slot, so it is the recovery on offer.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  (api.getAlternates as any).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  // narration lands, THEN the error: the backend persisted that partial, so the
  // slot is full and Retry would append a second generation past it
  (api.regenerate as any).mockImplementation(async (..._a: any[]) => {
    const onEvent = _a[2];
    onEvent({ delta: "Ice on the" });
    throw { detail: "upstream is down" };
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
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  (api.getAlternates as any).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  (api.pickAlternate as any).mockRejectedValue({ detail: "alternate not found" });
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
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  (api.getAlternates as any).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  (api.chat as any).mockImplementation(hangingChat(["a streamed fragment"]));
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
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  (api.getAlternates as any).mockResolvedValue({
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
  let reject: (e: any) => void = () => {};
  relistsAs(ONE_SCENE, [{ id: "s1-renamed", title: "Old", model: "", created: "", updated: "" }]);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  (api.getAlternates as any).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  (api.renameScene as any).mockResolvedValue({ id: "s1-renamed", title: "New" });
  renderCampaign();
  await screen.findByText("2/2");

  // re-select the scene so a GET for the OLD id is outstanding across the
  // rename; the fetch the rename issues for the new id still resolves normally
  (api.getAlternates as any).mockImplementationOnce(
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
  relistsAs(ONE_SCENE, [{ id: "s1-renamed", title: "Old", model: "", created: "", updated: "" }]);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  (api.getAlternates as any).mockResolvedValue({
    active: 1, alternates: [ALT("old"), ALT("a reply")] });
  (api.renameScene as any).mockResolvedValue({ id: "s1-renamed", title: "New" });
  renderCampaign();
  await screen.findByText("2/2");
  fireEvent.click(screen.getByRole("button", { name: /rename/i }));
  const input = screen.getByDisplayValue("Old");
  fireEvent.change(input, { target: { value: "New" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(api.renameScene).toHaveBeenCalled());
  (api.getScene as any).mockClear();

  fireEvent.click(screen.getByRole("button", { name: /previous alternate/i }));

  await waitFor(() => expect(api.pickAlternate).toHaveBeenCalledWith("run", "s1-renamed", "id-old"));
  await waitFor(() => expect(api.getScene).toHaveBeenCalled());   // the refresh ran
});

test("a previous scene's alternates fetch cannot clear the current scene's by failing", async () => {
  // the scoped-success guard cannot cover the reject path: a late rejection
  // would otherwise reset state that belongs to the scene now on screen
  let rejectFirst: (e: any) => void = () => {};
  (api.listScenes as any).mockResolvedValue([
    { id: "s1", title: "One", model: "", created: "", updated: "" },
    { id: "s2", title: "Two", model: "", created: "", updated: "" },
  ]);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  (api.getAlternates as any)
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
  let releaseFirst: (v: any) => void = () => {};
  (api.listScenes as any).mockResolvedValue([
    { id: "s1", title: "One", model: "", created: "", updated: "" },
    { id: "s2", title: "Two", model: "", created: "", updated: "" },
  ]);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  (api.getAlternates as any)
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

test("no Reroll when a manual dice roll trails the assistant reply", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" },
    { role: "assistant", content: "🎲 2d6 = 7", speaker: "⁣Roll" }] });
  renderCampaign();
  await screen.findByText(/2d6 = 7/);
  expect(screen.queryByRole("button", { name: /reroll/i })).toBeNull();
});

test("Reroll is offered when the last post is merely spoken by a character actually named Roll", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "hello", speaker: "Roll" }] });
  renderCampaign();
  await screen.findByText("hello");
  expect(screen.getByRole("button", { name: /reroll/i })).toBeInTheDocument();
});

test("no Reroll when the last post is the user's", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "assistant", content: "a reply" }, { role: "user", content: "hi" }] });
  renderCampaign();
  await screen.findByText("hi");
  expect(screen.queryByRole("button", { name: /reroll/i })).toBeNull();
});

test("no Reroll on a sole opening post", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "assistant", content: "the greeting" }] });
  renderCampaign();
  await screen.findByText("the greeting");
  expect(screen.queryByRole("button", { name: /reroll/i })).toBeNull();
});

test("only the last assistant post shows Reroll", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "one" }, { role: "assistant", content: "first reply" },
    { role: "user", content: "two" }, { role: "assistant", content: "second reply" }] });
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
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any)
    .mockResolvedValueOnce({ meta: {}, messages: [{ role: "user", content: "hi" }] })
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
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getCast as any).mockResolvedValue([
    { kind: "pcs", id: "elara-vane", role: "player", name: "Elara Vane" },
    { kind: "characters", id: "seraphine", role: "npc", name: "Seraphine Vale" },
  ]);
  (api.getScene as any).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
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
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getCast as any).mockResolvedValue([
    { kind: "pcs", id: "elara-vane", role: "player", name: "Elara Vane" }]);
  (api.getScene as any).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "user", content: "spoken as someone else", speaker: "Old Name" }] });
  renderCampaign();
  const stream = within(await screen.findByTestId("stream"));
  await stream.findByText("Old Name");
  expect(stream.queryByText("Elara Vane")).toBeNull();
});

test("after a stream completes the scene is re-fetched", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any)
    .mockResolvedValueOnce({ meta: {}, messages: [] })
    .mockResolvedValue({ meta: {}, messages: [
      { role: "user", content: "hello" },
      { role: "assistant", content: "Thunder rolls." },
      { role: "assistant", content: "Who goes there?", speaker: "Seraphine Vale" },
    ] });
  (api.chat as any).mockImplementation(async (_c: string, _s: string, _t: string, onEvent: any) => {
    onEvent({ delta: "**Grimoire:** Thunder rolls." });
  });
  renderCampaign();
  await screen.findByRole("heading", { name: /^Old$/ });
  const ta = screen.getByRole("textbox");
  fireEvent.change(ta, { target: { value: "hello" } });
  fireEvent.keyDown(ta, { key: "Enter" });
  await screen.findByText("Who goes there?");
  expect(api.getScene).toHaveBeenCalledTimes(2);
});

test("no Reroll when every message is assistant-side (multi-post opener)", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "assistant", content: "opener one" },
    { role: "assistant", content: "opener two", speaker: "Seraphine Vale" }] });
  renderCampaign();
  await screen.findByText("opener two");
  expect(screen.queryByRole("button", { name: /reroll/i })).toBeNull();
});

test("world name comes from the campaign payload, with no world fetch", async () => {
  (api.getCampaign as any).mockResolvedValue({
    meta: { id: "run", name: "Run One", world: "w", world_name: "Saltmarch" }, body: "" });
  renderCampaign();
  // The column's world-copy link names it — the campaign's own copy, which is
  // the only world the play view ever reaches.
  expect(await screen.findByText(/Saltmarch · this campaign/)).toBeInTheDocument();
  expect(api.getWorld).not.toHaveBeenCalled();
});

test("a first-name speaker matches its cast member (fuzzy, unique prefix)", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getCast as any).mockResolvedValue([
    { kind: "characters", id: "winifred", role: "npc", name: "Winifred Vance" },
    { kind: "pcs", id: "yara", role: "player", name: "Yara Vane" },
  ]);
  (api.getScene as any).mockResolvedValue({
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

const OFFSCREEN_SCENE = [{ id: "s1", title: "Cabal", model: "", created: "", updated: "", pcless: true }];

test("offscreen scene: director composer, Continue button, badges", async () => {
  (api.listScenes as any).mockResolvedValue(OFFSCREEN_SCENE);
  renderCampaign();
  await screen.findByPlaceholderText(/direct the scene/i);
  expect(screen.getByRole("button", { name: /continue ▶/i })).toBeInTheDocument();
  // one "Offscreen" chip beside the scene title. The rail that carried the
  // second one is gone.
  expect(screen.getAllByText("Offscreen")).toHaveLength(1);
});

test("offscreen scene: empty Continue sends an empty note", async () => {
  (api.listScenes as any).mockResolvedValue(OFFSCREEN_SCENE);
  renderCampaign();
  fireEvent.click(await screen.findByRole("button", { name: /continue ▶/i }));
  await waitFor(() => expect(api.chat).toHaveBeenCalledWith("run", "s1", "", expect.any(Function), undefined, expect.any(AbortSignal), expect.any(String)));
});

test("offscreen scene: typed note shows transiently, never lands in messages", async () => {
  (api.listScenes as any).mockResolvedValue(OFFSCREEN_SCENE);
  let release: () => void = () => {};
  (api.chat as any).mockReturnValue(new Promise<void>((r) => { release = () => r(); }));
  renderCampaign();
  const box = await screen.findByPlaceholderText(/direct the scene/i);
  fireEvent.change(box, { target: { value: "the guard grows suspicious" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await screen.findByText(/🎬 the guard grows suspicious/);
  release();
  await waitFor(() => expect(screen.queryByText(/🎬/)).toBeNull());
});

test("normal scene: plain placeholder, Continue on empty input, Send once typed", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  const box = await screen.findByPlaceholderText(/speak your intent/i);
  expect(screen.getByRole("button", { name: /continue ▶/i })).toBeInTheDocument();
  fireEvent.change(box, { target: { value: "I draw my blade." } });
  expect(screen.getByRole("button", { name: /send ▸/i })).toBeInTheDocument();
});

test("normal scene: empty Continue sends an ephemeral round, no user message added", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  fireEvent.click(await screen.findByRole("button", { name: /continue ▶/i }));
  await waitFor(() => expect(api.chat).toHaveBeenCalledWith("run", "s1", "", expect.any(Function), undefined, expect.any(AbortSignal), expect.any(String)));
});

test("Roll dice is disabled on a fresh scene until the opener/cast setup produces a message", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  const rollBtn = await screen.findByRole("button", { name: "Roll dice" });
  expect(rollBtn).toBeDisabled();
});

// Dice are a mechanics affordance: an unbound campaign is freeform play, and
// the popover's Check tab has nothing to offer it either (available_checks
// returns [] with no pack). Accepted consequence: freeform notation rolls go
// with it, since this button is their only entry point.
test("no dice button in a campaign with no mechanics pack bound", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  (api.getCampaignModule as any).mockResolvedValue({ setting: "", resolved: null, source: null });
  renderCampaign();
  await screen.findByText("a reply");
  expect(screen.queryByRole("button", { name: "Roll dice" })).toBeNull();
  // the composer is otherwise intact
  expect(screen.getByRole("textbox")).toBeInTheDocument();
  expect(screen.getByLabelText("Response length")).toBeInTheDocument();
});

// A control that appears and then vanishes a beat later is worse than one that
// arrives late, so an unresolved read renders nothing rather than guessing.
test("the dice button waits for the module read rather than flashing", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  let release: (v: any) => void = () => {};
  (api.getCampaignModule as any).mockReturnValue(new Promise((r) => { release = r; }));
  renderCampaign();
  await screen.findByText("a reply");
  expect(screen.queryByRole("button", { name: "Roll dice" })).toBeNull();
  await act(async () => { release({ setting: "pool-basic", resolved: "pool-basic", source: "campaign" }); });
  expect(await screen.findByRole("button", { name: "Roll dice" })).toBeInTheDocument();
});

// The button is the popover's only way in AND its only way out. Unbinding the
// pack while it is open would otherwise strand a form nothing can dismiss,
// offering a Check whose actor list is now empty.
test("unbinding the pack closes an open roll popover", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(screen.getByRole("button", { name: "Roll dice" }));
  expect(screen.getByLabelText("Dice notation")).toBeInTheDocument();

  // the reader clears the pack in the Mechanics panel, which reports the change
  fireEvent.click(screen.getByRole("button", { name: /^mechanics$/i }));
  const modSelect = await screen.findByLabelText("Mechanics");
  (api.getCampaignModule as any).mockResolvedValue({ setting: "none", resolved: null, source: null });
  fireEvent.change(modSelect, { target: { value: "none" } });
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

  await waitFor(() => expect(screen.queryByRole("button", { name: "Roll dice" })).toBeNull());
  expect(screen.queryByLabelText("Dice notation")).toBeNull();
  // ...and it stays gone once everything has settled, rather than this having
  // caught a transient "not known yet" on the way through (Codex review)
  await act(async () => { await Promise.resolve(); });
  expect(screen.queryByRole("button", { name: "Roll dice" })).toBeNull();
});

// Codex review, finding 4. `readModuleBound` runs on every save, and blanking
// the binding to "not known yet" while it is out used to read as unbound --
// discarding a half-typed roll even when the same pack stayed bound.
test("re-saving the same pack leaves an open roll form alone", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(screen.getByRole("button", { name: "Roll dice" }));
  fireEvent.change(screen.getByLabelText("Dice notation"), { target: { value: "2d6+1" } });

  fireEvent.click(screen.getByRole("button", { name: /^mechanics$/i }));
  fireEvent.click(await screen.findByRole("button", { name: /^save$/i }));   // same pack

  await waitFor(() => expect(api.setCampaignModule).toHaveBeenCalled());
  await act(async () => { await Promise.resolve(); });
  expect(screen.getByLabelText("Dice notation")).toHaveValue("2d6+1");   // typing survived
  expect(screen.getByRole("button", { name: "Roll dice" })).toBeInTheDocument();
});

// Codex review, finding 1. MechanicsConfig holds the `cid` and the `onChanged`
// it was handed, so a save issued in campaign A settles and fires that callback
// after a move to B. If the read it triggers were keyed to the captured `cid`,
// it would ask about A -- which HAS a pack -- and commit that answer into the
// bar for B, which does not: dice appear in a freeform campaign.
test("a mechanics save settling after a campaign switch answers for the campaign on screen", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  let releaseSave: (v: any) => void = () => {};
  (api.setCampaignModule as any).mockReturnValue(new Promise((r) => { releaseSave = r; }));
  render(
    <MemoryRouter initialEntries={["/campaigns/run"]}>
      {withPalette(<>
        <Link to="/campaigns/other">switch campaign</Link>
        {playRoutes()}
      </>)}
    </MemoryRouter>,
  );
  await screen.findByText("a reply");

  // start a save in campaign "run" that will not settle yet
  fireEvent.click(screen.getByRole("button", { name: /^mechanics$/i }));
  fireEvent.click(await screen.findByRole("button", { name: /^save$/i }));

  // the reader moves to a campaign with NO pack, which resolves first
  (api.getCampaignModule as any).mockResolvedValue({ setting: "", resolved: null, source: null });
  fireEvent.click(screen.getByText("switch campaign"));
  await waitFor(() => expect(screen.queryByRole("button", { name: "Roll dice" })).toBeNull());

  // now "run"'s save lands and fires its stale callback. "run" answers "bound",
  // the campaign actually on screen answers "not bound".
  (api.getCampaignModule as any).mockImplementation(async (c: string) =>
    (c === "run" ? { setting: "pool-basic", resolved: "pool-basic", source: "campaign" }
                 : { setting: "", resolved: null, source: null }));
  await act(async () => { releaseSave({ ok: true }); });
  await act(async () => { await Promise.resolve(); });

  // the bar reports the campaign on screen, not the one the save belonged to
  expect(screen.queryByRole("button", { name: "Roll dice" })).toBeNull();
});

// Codex review round 2, finding 3. A failed refresh is not evidence that the
// pack went away, and treating it as such retracted the dice button and threw
// away a half-typed roll.
test("a refresh whose read fails leaves the binding, and the roll form, alone", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(screen.getByRole("button", { name: "Roll dice" }));
  fireEvent.change(screen.getByLabelText("Dice notation"), { target: { value: "2d6+1" } });

  fireEvent.click(screen.getByRole("button", { name: /^mechanics$/i }));
  await screen.findByLabelText("Mechanics");
  // the panel's own re-read succeeds; the refresh the callback triggers does not
  (api.getCampaignModule as any).mockResolvedValueOnce(
    { setting: "pool-basic", resolved: "pool-basic", source: "campaign" })
    .mockRejectedValue(new Error("offline"));
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

  await waitFor(() => expect(api.setCampaignModule).toHaveBeenCalled());
  await act(async () => { await Promise.resolve(); });
  expect(screen.getByRole("button", { name: "Roll dice" })).toBeInTheDocument();
  expect(screen.getByLabelText("Dice notation")).toHaveValue("2d6+1");
});

// Codex review, finding 2. A native select with no option matching its value
// silently displays the FIRST option, so a scene naming a deleted preset would
// have the strip confidently report a preset that is not in effect.
test("a preset the list does not contain is still named, not silently swapped", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({
    meta: { id: "s1", title: "Old", response_preset: "ghost" }, messages: [] });
  (api.listResponsePresets as any).mockResolvedValue(RESPONSE_PRESETS);
  renderCampaign();
  const picker = await screen.findByLabelText("Response length");
  await waitFor(() => expect(picker).toHaveValue("ghost"));
  expect(within(picker as HTMLSelectElement).getByRole("option", { selected: true }))
    .toHaveTextContent("ghost");
});

// Codex review, finding 5. The badge used to sit inside the control and so
// formed part of its accessible name; as a sibling it has to be tied back on.
test("the one-shot badge is announced with the response picker", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({
    meta: { id: "s1", title: "Old", response_preset: "cinematic" }, messages: [] });
  (api.listResponsePresets as any).mockResolvedValue(RESPONSE_PRESETS);
  renderCampaign();
  const picker = await screen.findByLabelText("Response length");
  await screen.findByRole("option", { name: "Terse" });
  expect(picker).not.toHaveAttribute("aria-describedby");
  fireEvent.change(picker, { target: { value: "terse" } });
  expect(picker).toHaveAccessibleDescription(/next reply only/i);
});

test("renders an export menu with a download link per format", async () => {
  renderCampaign();
  const epub = await screen.findByRole("link", { name: /^epub$/i });
  expect(epub).toHaveAttribute("href", "/api/campaigns/run/export.epub");
  expect(epub).toHaveAttribute("download");
  expect(screen.getByRole("link", { name: /markdown/i }))
    .toHaveAttribute("href", "/api/campaigns/run/export.md.zip");
  expect(screen.getByRole("link", { name: /^html$/i }))
    .toHaveAttribute("href", "/api/campaigns/run/export.html");
  expect(screen.getByRole("link", { name: /plain text/i }))
    .toHaveAttribute("href", "/api/campaigns/run/export.txt");
  expect(screen.getByRole("link", { name: /^json$/i }))
    .toHaveAttribute("href", "/api/campaigns/run/export.json");
});

test("rolls dice from the input bar popover and refreshes the scene", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  (api.roll as any).mockResolvedValue({ ok: true, roll: { id: "r1" }, message: "🎲" });
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(screen.getByRole("button", { name: "Roll dice" }));
  fireEvent.change(screen.getByLabelText("Dice notation"), { target: { value: "2d6+1" } });
  fireEvent.change(screen.getByLabelText("Roll label"), { target: { value: "Perception" } });
  fireEvent.click(screen.getByRole("button", { name: "Roll ▸" }));
  await waitFor(() => expect(api.roll).toHaveBeenCalledWith("run", "s1", "2d6+1", "Perception"));
  // popover closes and the scene re-fetches to show the roll line
  await waitFor(() => expect(screen.queryByLabelText("Dice notation")).toBeNull());
  expect((api.getScene as any).mock.calls.length).toBeGreaterThan(1);
});

test("disables roll submission while a roll is in flight, so repeated clicks send only one", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  let resolveRoll: (v: unknown) => void;
  (api.roll as any).mockReturnValue(new Promise((resolve) => { resolveRoll = resolve; }));
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(screen.getByRole("button", { name: "Roll dice" }));
  fireEvent.change(screen.getByLabelText("Dice notation"), { target: { value: "2d6+1" } });
  const rollBtn = screen.getByRole("button", { name: "Roll ▸" });
  fireEvent.click(rollBtn);
  await waitFor(() => expect(rollBtn).toBeDisabled());
  fireEvent.click(rollBtn);
  fireEvent.click(rollBtn);
  expect(api.roll).toHaveBeenCalledTimes(1);
  resolveRoll!({ ok: true, roll: { id: "r1" }, message: "🎲" });
  await waitFor(() => expect(screen.queryByLabelText("Dice notation")).toBeNull());
});

test("shows a roll error and keeps the popover open", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  (api.roll as any).mockRejectedValue({ detail: "can't read dice notation 'garbage'" });
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(screen.getByRole("button", { name: "Roll dice" }));
  fireEvent.change(screen.getByLabelText("Dice notation"), { target: { value: "garbage" } });
  fireEvent.click(screen.getByRole("button", { name: "Roll ▸" }));
  await screen.findByText(/can't read dice notation/);
  expect(screen.getByLabelText("Dice notation")).toBeInTheDocument();
});

test("toggles an in-app dice notation syntax reference from the roll popover", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(screen.getByRole("button", { name: "Roll dice" }));
  expect(screen.queryByText(/exploding dice/i)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Dice notation syntax" }));
  expect(screen.getByText(/exploding dice/i)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Dice notation syntax" }));
  expect(screen.queryByText(/exploding dice/i)).toBeNull();
});

const PROPOSAL_PAYLOAD = {
  id: "pr-1", check: "brawl", check_label: "Vigor + Brawl",
  actor: "characters:mara", actor_label: "Mara", difficulty: 6,
  available: { "characters:mara": [["brawl", "Vigor + Brawl"]] },
  problems: [],
};

test("an SSE proposal event mounts the roll-proposal chip", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  (api.chat as any).mockImplementation(async (_c: string, _s: string, _t: string, onEvent: any) => {
    onEvent({ proposal: PROPOSAL_PAYLOAD });
  });
  // the SSE event mounts the chip immediately; runStream's finally then
  // re-fetches via selectScene — mock the backend as having durably
  // persisted the same pending record by then (its real behavior).
  (api.getRollProposal as any)
    .mockResolvedValueOnce({ record: null }) // initial scene load
    .mockResolvedValue({ record: { id: "pr-1", status: "pending", payload: PROPOSAL_PAYLOAD, resolution: null } });
  renderCampaign();
  await screen.findByText("a reply");
  const ta = screen.getByRole("textbox");
  fireEvent.change(ta, { target: { value: "I punch him" } });
  fireEvent.keyDown(ta, { key: "Enter" });
  expect(await screen.findByRole("button", { name: "Roll it" })).toBeInTheDocument();
  expect(screen.getByText(/Vigor \+ Brawl — Mara/)).toBeInTheDocument();
});

test("resolving a roll-proposal chip calls api.resolveProposal and clears the chip", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  (api.chat as any).mockImplementation(async (_c: string, _s: string, _t: string, onEvent: any) => {
    onEvent({ proposal: PROPOSAL_PAYLOAD });
  });
  (api.getRollProposal as any)
    .mockResolvedValueOnce({ record: null }) // initial scene load
    .mockResolvedValueOnce({ record: { id: "pr-1", status: "pending", payload: PROPOSAL_PAYLOAD, resolution: null } }) // after send()
    .mockResolvedValue({ record: null }); // after resolve — the backend supersedes it
  renderCampaign();
  await screen.findByText("a reply");
  const ta = screen.getByRole("textbox");
  fireEvent.change(ta, { target: { value: "I punch him" } });
  fireEvent.keyDown(ta, { key: "Enter" });
  const rollIt = await screen.findByRole("button", { name: "Roll it" });
  fireEvent.click(rollIt);
  await waitFor(() => expect(api.resolveProposal).toHaveBeenCalledWith(
    "run", "s1",
    { proposal: "pr-1", action: "accept", check: "brawl", actor: "characters:mara", difficulty: 6, modifier: 0 },
    expect.any(Function), expect.any(AbortSignal)));
  await waitFor(() => expect(screen.queryByRole("button", { name: "Roll it" })).toBeNull());
});

test("a declined roll whose narration never landed stays retryable", async () => {
  // Stopping (or an upstream failure on) a declined record's continuation
  // leaves it `declined` with nothing persisted. The backend re-streams that
  // continuation on request, but the chip used to be filtered out of the scene
  // load, so the decline narration had no way back.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "assistant", content: "a reply" }] });
  (api.getRollProposal as any).mockResolvedValue({
    record: { id: "pr-1", status: "declined", payload: PROPOSAL_PAYLOAD, resolution: null } });
  renderCampaign();
  await screen.findByText(/Roll declined, narration pending/);
  fireEvent.click(screen.getByRole("button", { name: "Continue narration" }));
  await waitFor(() => expect(api.resolveProposal).toHaveBeenCalledWith(
    "run", "s1", { proposal: "pr-1", action: "decline" },
    expect.any(Function), expect.any(AbortSignal)));
});

test("selecting a scene re-hydrates a pending roll-proposal record", async () => {
  (api.listScenes as any).mockResolvedValue([
    { id: "001--2024-01-01--one", title: "One", model: "", created: "", updated: "" },
    { id: "002--2024-01-02--two", title: "Two", model: "", created: "", updated: "" },
  ]);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "assistant", content: "a reply" }] });
  (api.getRollProposal as any)
    .mockResolvedValueOnce({ record: null }) // initial select of "one"
    .mockResolvedValue({ record: {
      id: "pr-2", status: "pending", payload: {
        id: "pr-2", check: "stealth", check_label: "Wits + Stealth",
        actor: "characters:mara", actor_label: "Mara", available: {}, problems: [] },
      resolution: null,
    } });
  renderCampaign();
  await screen.findByText("a reply");
  expect(screen.queryByRole("button", { name: "Roll it" })).toBeNull();
  await openScene(/Two/);
  expect(await screen.findByRole("button", { name: "Roll it" })).toBeInTheDocument();
});

test("popover Check mode with difficulty left empty posts rollCheck without difficulty", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  (api.getSceneChecks as any).mockResolvedValue({ actors: [
    { ref: "characters:mara", label: "Mara", sheet_type: "vampire",
      checks: [["brawl", "Vigor + Brawl"], ["stealth", "Wits + Stealth"]] },
  ] });
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(screen.getByRole("button", { name: "Roll dice" }));
  fireEvent.click(screen.getByRole("button", { name: "Check" }));
  await waitFor(() => expect(api.getSceneChecks).toHaveBeenCalledWith("run", "s1"));
  fireEvent.change(await screen.findByLabelText("Check actor"), { target: { value: "characters:mara" } });
  fireEvent.change(screen.getByLabelText("Check"), { target: { value: "brawl" } });
  fireEvent.click(screen.getByRole("button", { name: "Roll ▸" }));
  await waitFor(() => expect(api.rollCheck).toHaveBeenCalledWith("run", "s1",
    { check: "brawl", actor: "characters:mara", modifier: 0 }));
  const [, , rollBody] = (api.rollCheck as any).mock.calls[0];
  expect(rollBody).not.toHaveProperty("difficulty");
  await waitFor(() => expect(screen.queryByLabelText("Check actor")).toBeNull());
});

test("popover Check mode with a typed difficulty posts it", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  (api.getSceneChecks as any).mockResolvedValue({ actors: [
    { ref: "characters:mara", label: "Mara", sheet_type: "vampire",
      checks: [["brawl", "Vigor + Brawl"], ["stealth", "Wits + Stealth"]] },
  ] });
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(screen.getByRole("button", { name: "Roll dice" }));
  fireEvent.click(screen.getByRole("button", { name: "Check" }));
  await waitFor(() => expect(api.getSceneChecks).toHaveBeenCalledWith("run", "s1"));
  fireEvent.change(await screen.findByLabelText("Check actor"), { target: { value: "characters:mara" } });
  fireEvent.change(screen.getByLabelText("Check"), { target: { value: "brawl" } });
  fireEvent.change(screen.getByLabelText("Difficulty"), { target: { value: "7" } });
  fireEvent.click(screen.getByRole("button", { name: "Roll ▸" }));
  await waitFor(() => expect(api.rollCheck).toHaveBeenCalledWith("run", "s1",
    { check: "brawl", actor: "characters:mara", difficulty: 7, modifier: 0 }));
  await waitFor(() => expect(screen.queryByLabelText("Check actor")).toBeNull());
});

test("switching between two scenes that both have pending proposals shows the new scene's chip and rolls its own check, never the previous scene's", async () => {
  (api.listScenes as any).mockResolvedValue([
    { id: "001--2024-01-01--one", title: "One", model: "", created: "", updated: "" },
    { id: "002--2024-01-02--two", title: "Two", model: "", created: "", updated: "" },
  ]);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "assistant", content: "a reply" }] });
  const PROPOSAL_A = {
    id: "pr-a", check: "brawl", check_label: "Vigor + Brawl",
    actor: "characters:mara", actor_label: "Mara", difficulty: 6,
    available: { "characters:mara": [["brawl", "Vigor + Brawl"]] }, problems: [],
  };
  const PROPOSAL_B = {
    id: "pr-b", check: "stealth", check_label: "Wits + Stealth",
    actor: "characters:borys", actor_label: "Borys", difficulty: 4,
    available: { "characters:borys": [["stealth", "Wits + Stealth"]] }, problems: [],
  };
  // scenes each have their own live pending proposal — keyed by scene id, not call order.
  (api.getRollProposal as any).mockImplementation((_c: string, sid: string) => {
    if (sid.endsWith("--one")) return Promise.resolve({ record: { id: "pr-a", status: "pending", payload: PROPOSAL_A, resolution: null } });
    if (sid.endsWith("--two")) return Promise.resolve({ record: { id: "pr-b", status: "pending", payload: PROPOSAL_B, resolution: null } });
    return Promise.resolve({ record: null });
  });
  renderCampaign();
  await screen.findByText("a reply");
  expect(await screen.findByText(/Vigor \+ Brawl — Mara/)).toBeInTheDocument();
  await openScene(/Two/);
  expect(await screen.findByText(/Wits \+ Stealth — Borys/)).toBeInTheDocument();
  expect(screen.queryByText(/Vigor \+ Brawl — Mara/)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Roll it" }));
  await waitFor(() => expect(api.resolveProposal).toHaveBeenCalledWith(
    "run", "002--2024-01-02--two",
    { proposal: "pr-b", action: "accept", check: "stealth", actor: "characters:borys", difficulty: 4, modifier: 0 },
    expect.any(Function), expect.any(AbortSignal)));
  expect(api.resolveProposal).not.toHaveBeenCalledWith(
    "run", expect.anything(),
    expect.objectContaining({ proposal: "pr-a" }),
    expect.anything());
});

test("the inspector is a panel behind the composer's link, not a permanent third column", async () => {
  // It used to be a column that was open by definition. What it answers — what
  // went into the last prompt and what was dropped to fit — is a question about
  // a turn, so it belongs beside the control that takes the next one.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
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
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getCast as any).mockResolvedValue([
    { kind: "characters", id: "aud", role: "npc", name: "Sister Aud" }]);
  (api.sceneBriefing as any).mockResolvedValue({
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

test("the chip names the preset resolved at campaign scope, not a hardcoded Standard", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  // the scene itself names no preset — the campaign does, so nothing in the
  // scene's frontmatter can tell the chip what the reply will actually be
  (api.getScene as any).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [] });
  (api.getSceneResponse as any).mockResolvedValue({
    ...RESPONSE_BUNDLE,
    effective: { ...RESPONSE_BUNDLE.effective, reply_words: 900 },
    provenance: { reply_words: { scope: "campaign", source: "preset" } },
  });
  (api.listResponsePresets as any).mockResolvedValue(RESPONSE_PRESETS);
  renderCampaign();
  const picker = await screen.findByLabelText("Response length");
  // Nothing is selected: the scene names no preset, so the only honest thing to
  // show is the effective budget and where it came from. That lives in the
  // placeholder option, which exists only in this state.
  await waitFor(() => expect(picker).toHaveValue(""));
  const inherited = within(picker as HTMLSelectElement).getByRole("option", { selected: true });
  expect(inherited).toHaveTextContent("900 words");
  expect(inherited).toHaveTextContent("this campaign");
  expect(inherited).not.toHaveTextContent("Standard");
});

test("a pending one-shot pick is badged and can be cancelled without sending", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({
    meta: { id: "s1", title: "Old", response_preset: "cinematic" }, messages: [] });
  (api.listResponsePresets as any).mockResolvedValue(RESPONSE_PRESETS);
  renderCampaign();
  const picker = await screen.findByLabelText("Response length");
  // an inherited/scene setting carries no badge...
  // Waited for, for the reason the picker test above spells out: the <select>
  // exists before its value does, and a flat assert here reads "" whenever a
  // findBy poll lands between the two commits.
  await waitFor(() => expect(picker).toHaveValue("cinematic"));
  expect(screen.queryByText(/next reply only/i)).toBeNull();
  expect(screen.queryByLabelText(/cancel the one-shot/i)).toBeNull();
  // ...a one-shot pick does, and is distinguishable from it
  fireEvent.change(picker, { target: { value: "terse" } });
  expect(picker).toHaveValue("terse");
  expect(screen.getByText(/next reply only/i)).toBeInTheDocument();
  // cancelling reverts to the scene's own setting without sending anything
  fireEvent.click(screen.getByLabelText(/cancel the one-shot/i));
  expect(picker).toHaveValue("cinematic");
  expect(screen.queryByText(/next reply only/i)).toBeNull();
  expect(api.chat).not.toHaveBeenCalled();
});

test("a scene transition renders as unlabelled narration, with no Scene plate", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" },
    { role: "assistant", content: "a reply" },
    { role: "assistant", content: "*Time passes. It is now dusk.*", speaker: "⁣Scene" }] });
  const { container } = renderCampaign();
  await screen.findByText(/Time passes/);
  expect(screen.queryByText(/⁣Scene/)).toBeNull();
  // the tagged transition joins the reply's run instead of opening its own
  // plate — exactly how an untagged transition rendered before the tag existed
  const names = [...container.querySelectorAll(".plate-name")].map((n) => n.textContent);
  expect(names).toEqual(["You", "Grimoire"]);
});

test("Reroll is offered past a trailing scene transition and keeps it", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" },
    { role: "assistant", content: "a reply" },
    { role: "assistant", content: "*Time passes. It is now dusk.*", speaker: "⁣Scene" }] });
  renderCampaign();
  await screen.findByText(/Time passes/);
  fireEvent.click(await screen.findByTitle("Reroll"));
  fireEvent.click(screen.getByRole("button", { name: /reroll ▸/i }));
  // the optimistic trim drops the reply but leaves the transition standing
  expect(screen.queryByText("a reply")).toBeNull();
  expect(screen.getByText(/Time passes/)).toBeInTheDocument();
  await waitFor(() => expect(api.regenerate).toHaveBeenCalled());
});

test("the Ledger is a link to its own route, not a panel over the transcript", async () => {
  // The ledger became a screen (4e): a table read top to bottom, with
  // supersession chains that do not fit in a drawer wedged above the scene.
  (api.campaignProvenance as any).mockResolvedValue({});
  renderCampaign();
  expect(await screen.findByRole("link", { name: "Ledger" }))
    .toHaveAttribute("href", "/campaigns/run/ledger");
  // ...and the play view no longer reads it: that is the ledger route's job now.
  expect(api.campaignLedger).not.toHaveBeenCalled();
});

test("renaming a scene re-reads the continuity column", async () => {
  // Every thread and commitment carries the TITLE of the scene that last moved
  // it, so a rename changes what those reads return — and this path touches
  // none of their other dependencies: same campaign, no absorb saved. Without
  // the revision bump the column keeps the old title until the user selects a
  // scene. (Asserted on the briefing, the continuity read that stayed on this
  // page when the ledger moved to its own route.)
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.campaignProvenance as any).mockResolvedValue({});
  (api.renameScene as any).mockResolvedValue({ id: "s1-renamed", title: "New" });
  renderCampaign();
  await screen.findByRole("button", { name: /rename/i });
  const before = (api.sceneBriefing as any).mock.calls.length;

  fireEvent.click(screen.getByRole("button", { name: /rename/i }));
  const input = screen.getByDisplayValue("Old");
  fireEvent.change(input, { target: { value: "New" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(api.renameScene).toHaveBeenCalled());
  await waitFor(() =>
    expect((api.sceneBriefing as any).mock.calls.length).toBeGreaterThan(before));
});

test("a rename that keeps the scene id still re-reads the continuity column", async () => {
  // A capitalisation or punctuation edit slugs to the same id, so the route
  // returns the scene unchanged — but `meta.title` moved, and the title is
  // exactly what those rows carry. The bump sits ahead of `adoptSceneId`'s
  // same-id guard for that reason: everything else in that function is about
  // the id, and this one thing is not. Nothing else re-reads here, so this is
  // the case that isolates the bump.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.campaignProvenance as any).mockResolvedValue({});
  (api.renameScene as any).mockResolvedValue({ id: "s1", title: "OLD" });
  renderCampaign();
  await screen.findByRole("button", { name: /rename/i });
  const before = (api.sceneBriefing as any).mock.calls.length;

  fireEvent.click(screen.getByRole("button", { name: /rename/i }));
  const input = screen.getByDisplayValue("Old");
  fireEvent.change(input, { target: { value: "OLD" } });
  fireEvent.keyDown(input, { key: "Enter" });
  await waitFor(() => expect(api.renameScene).toHaveBeenCalled());
  await waitFor(() =>
    expect((api.sceneBriefing as any).mock.calls.length).toBeGreaterThan(before));
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
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "assistant", content: "recent" }], offset: 40, total: 41, has_older: true });
  renderCampaign();
  await screen.findByText("recent");
  expect(api.getScene).toHaveBeenCalledWith("run", "s1", { limit: 60 });
});

test("a windowed post is edited by its absolute index, not its position on the page", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "page two" }], offset: 40, total: 41, has_older: true });
  renderCampaign();
  await screen.findByText("page two");
  fireEvent.click(screen.getAllByTitle("Edit message")[0]);
  fireEvent.change(await screen.findByLabelText(/edit message/i), { target: { value: "fixed" } });
  fireEvent.click(screen.getByRole("button", { name: /save/i }));
  await waitFor(() => expect(api.editMessage).toHaveBeenCalledWith("run", "s1", 40, "fixed"));
});

test("scrolling to the top of the stream prepends the previous page", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockImplementation((_c: string, _s: string, w?: any) =>
    Promise.resolve(w?.before === 2
      ? { meta: {}, messages: [{ role: "user", content: "older post" }], offset: 1, total: 3, has_older: true }
      : { meta: {}, messages: [{ role: "user", content: "newer post" }], offset: 2, total: 3, has_older: true }));
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
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockImplementation((_c: string, _s: string, w?: any) =>
    Promise.resolve(w?.before === 2
      ? { meta: {}, messages: [{ role: "user", content: "older post" }], offset: 0, total: 3, has_older: false }
      : { meta: {}, messages: [{ role: "user", content: "newer post" }, { role: "assistant", content: "a reply" }],
          offset: 2, total: 4, has_older: true }));
  const { container } = renderCampaign();
  await screen.findByText("newer post");
  const stream = container.querySelector(".stream") as HTMLElement;
  stubStreamGeometry(stream);
  const scrollTo = vi.fn();
  stream.scrollTo = scrollTo as any;

  fireEvent.scroll(stream); // scrollTop 0 with two posts on screen: at the top
  await screen.findByText("older post");
  // two posts (1000px) with the viewport at the top means 1000px sat below the
  // fold; after the prepend (1500px) the same 1000px must still sit below it
  expect(stream.scrollTop).toBe(500);
  expect(scrollTo).not.toHaveBeenCalled();
});

test("the older-history button loads the previous page and disappears at the top", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockImplementation((_c: string, _s: string, w?: any) =>
    Promise.resolve(w?.before === 1
      ? { meta: {}, messages: [{ role: "user", content: "the opener" }], offset: 0, total: 2, has_older: false }
      : { meta: {}, messages: [{ role: "assistant", content: "newer post" }], offset: 1, total: 2, has_older: true }));
  renderCampaign();
  await screen.findByText("newer post");
  fireEvent.click(screen.getByRole("button", { name: /load 1 older post/i }));
  await screen.findByText("the opener");
  expect(screen.queryByRole("button", { name: /older posts/i })).toBeNull();
});

test("no older-history button when the whole transcript is loaded", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [{ role: "assistant", content: "only post" }], offset: 0, total: 1, has_older: false });
  renderCampaign();
  await screen.findByText("only post");
  expect(screen.queryByRole("button", { name: /older posts/i })).toBeNull();
});

test("Reroll survives the opening user post being off-window", async () => {
  // the run's own user turn is older than the loaded page — unloaded, not
  // absent, so this is still a reply to something and still rerollable
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "assistant", content: "a reply" }],
    offset: 12, total: 13, has_older: true, has_user_message: true });
  renderCampaign();
  await screen.findByText("a reply");
  await screen.findByTitle("Reroll");
});

test("no Reroll on an all-assistant transcript, however much history is above", async () => {
  // an offscreen scene never stores a player turn, so unloaded history above
  // the window is not evidence of one — and regenerate 400s on that transcript
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "assistant", content: "narration" }],
    offset: 12, total: 13, has_older: true, has_user_message: false });
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
  (api.listScenes as any).mockResolvedValue([
    { id: "s1", title: "First", model: "", created: "", updated: "" },
    { id: "s2", title: "Second", model: "", created: "", updated: "" }]);
  let releaseOlder: (() => void) | null = null;
  (api.getScene as any).mockImplementation((_c: string, sid: string, w?: any) => {
    if (sid === "s1" && w?.before === 5) {
      return new Promise((resolve) => {
        releaseOlder = () => resolve({ meta: {}, messages: [{ role: "user", content: "scene one, older" }],
                                       offset: 4, total: 6, has_older: true, has_user_message: true });
      });
    }
    return Promise.resolve(sid === "s2"
      ? { meta: {}, messages: [{ role: "assistant", content: "scene two" }],
          offset: 0, total: 1, has_older: false, has_user_message: false }
      : { meta: {}, messages: [{ role: "assistant", content: "scene one, newest" }],
          offset: 5, total: 6, has_older: true, has_user_message: true });
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
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockImplementation((_c: string, _s: string, w?: any) =>
    Promise.resolve(w?.before === 60
      ? { meta: {}, messages: [{ role: "user", content: "older post" }], offset: 59, total: 61, has_older: true }
      : { meta: {}, messages: [{ role: "assistant", content: "newer post" }], offset: 60, total: 61, has_older: true }));
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
  (api.listScenes as any).mockResolvedValue([
    { id: "s1", title: "Old", model: "", created: "", updated: "" },
    { id: "s2", title: "Later", model: "", created: "", updated: "" },
  ]);
  (api.getScene as any).mockResolvedValue({ meta: {}, total: 0, messages: [] });
  let fail: (() => void) | null = null;
  (api.chat as any).mockImplementation(
    async (_c: string, _s: string, _m: string, onEvent: any) => {
      await new Promise<void>((r) => {
        fail = () => {
          onEvent({ error: { detail: "OpenRouter API key is not set",
                             post_returned: true } });
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
    const composer = (screen.getByRole("textbox") as HTMLTextAreaElement).value;
    if (composer && !/Old/.test(onScreen())) wrong.push(`${onScreen()} | ${composer}`);
  }
  expect(wrong).toEqual([]);

  // And it is not dropped: it comes back with the scene it was written for.
  await waitFor(() => expect(onScreen()).toMatch(/Old/));
  await waitFor(() =>
    expect(screen.getByRole("textbox")).toHaveValue("I draw my blade."));
});

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
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.renameScene as any).mockResolvedValue({ id: "s1-renamed" });
  renderCampaign();
  const ta = await screen.findByRole("textbox");
  fireEvent.change(ta, { target: { value: "I draw my blade." } });

  (api.listScenes as any).mockResolvedValue(
    [{ id: "s1-renamed", title: "Renamed", model: "", created: "", updated: "" }]);
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
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({
    meta: {}, total: 2, messages: [
      { role: "user", content: "and then?" },
      { role: "assistant", content: "The tide turns." }],
  });
  (api.renameScene as any).mockResolvedValue({ id: "s1-renamed" });
  (api.regenerate as any).mockImplementation(
    async (_c: string, _s: string, onEvent: any) => {
      onEvent({ error: { detail: "OpenRouter API key is not set", kind: "missing_key" } });
    });
  renderCampaign();
  await screen.findByText("The tide turns.");
  fireEvent.click(screen.getByRole("button", { name: /reroll/i }));
  fireEvent.change(screen.getByPlaceholderText(/reroll/i),
                   { target: { value: "darker this time" } });
  fireEvent.click(screen.getByRole("button", { name: /reroll ▸/i }));
  await screen.findByText(/OpenRouter API key is not set/);

  // Rename the active scene. The banner stays up, so Retry is still offered.
  (api.listScenes as any).mockResolvedValue(
    [{ id: "s1-renamed", title: "Renamed", model: "", created: "", updated: "" }]);
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
  expect((api.regenerate as any).mock.calls[1][1]).toBe("s1-renamed");
  expect((api.regenerate as any).mock.calls[1][3]).toBe("darker this time");
});

// ------------------------------------------ #110/#112: confidence routing

// ---- the live rolling summary (#85) ----
test("a finished turn asks the server to refold the scene summary", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  fireEvent.change(await screen.findByRole("textbox"), { target: { value: "Go on." } });
  fireEvent.click(screen.getByRole("button", { name: /Send/ }));
  // Without `force`: the server decides whether this turn is the Nth, so an
  // ordinary turn spends nothing. The fourth argument is the turn's transcript
  // boundary, so a fast next send cannot be swallowed by this fold.
  await waitFor(() => expect(api.refreshRollingSummary).toHaveBeenCalledWith(
    "run", "s1", false, expect.anything()));
});

// ---- the scene-break detector (#84) ----
test("a finished turn also asks whether the scene has reached a break", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  fireEvent.change(await screen.findByRole("textbox"), { target: { value: "Go on." } });
  fireEvent.click(screen.getByRole("button", { name: /Send/ }));
  // Without `force` and with the same boundary the fold gets: the heuristic is
  // the server's, so an ordinary turn spends nothing, and a question that took
  // an unanswered next post as the scene's END would be asking about a beat
  // whose reply had not arrived.
  await waitFor(() => expect(api.askSceneBreak).toHaveBeenCalledWith(
    "run", "s1", false, expect.anything()));
});

test("a break question that fails never surfaces an error over the turn", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.askSceneBreak as any).mockRejectedValue(
    new ApiError(409, "OpenRouter key not set", "missing_key"));
  renderCampaign();
  fireEvent.change(await screen.findByRole("textbox"), { target: { value: "Go on." } });
  fireEvent.click(screen.getByRole("button", { name: /Send/ }));
  await waitFor(() => expect(api.askSceneBreak).toHaveBeenCalled());
  expect(screen.queryByText(/OpenRouter key not set/)).toBeNull();
});

test("a failed summary refold does not take the break question down with it", async () => {
  // Fired separately rather than chained, precisely so neither is a
  // precondition for the other.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.refreshRollingSummary as any).mockRejectedValue(
    new ApiError(409, "OpenRouter key not set", "missing_key"));
  renderCampaign();
  fireEvent.change(await screen.findByRole("textbox"), { target: { value: "Go on." } });
  fireEvent.click(screen.getByRole("button", { name: /Send/ }));
  await waitFor(() => expect(api.askSceneBreak).toHaveBeenCalled());
});

test("a manual dice roll also asks whether the scene has reached a break", async () => {
  // Rolls append narrator posts, so a mechanics-heavy stretch of play can cross
  // the cadence with no generated turn to carry the request.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  (api.roll as any).mockResolvedValue({ ok: true, total: 7, message: "" });
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(screen.getByRole("button", { name: "Roll dice" }));
  fireEvent.change(screen.getByLabelText("Dice notation"), { target: { value: "2d6" } });
  fireEvent.click(screen.getByRole("button", { name: "Roll ▸" }));
  await waitFor(() => expect(api.askSceneBreak).toHaveBeenCalledWith(
    "run", "s1", false, expect.anything()));
});

test("a refresh that fails never surfaces an error over the turn", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.refreshRollingSummary as any).mockRejectedValue(
    new ApiError(409, "OpenRouter key not set", "missing_key"));
  renderCampaign();
  fireEvent.change(await screen.findByRole("textbox"), { target: { value: "Go on." } });
  fireEvent.click(screen.getByRole("button", { name: /Send/ }));
  await waitFor(() => expect(api.refreshRollingSummary).toHaveBeenCalled());
  // The summary is a background reading aid; a play session must not be
  // interrupted by one that could not be written.
  expect(screen.queryByText(/OpenRouter key not set/)).toBeNull();
});

test("the turn does not wait on the summary refresh", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  let release: (() => void) | undefined;
  (api.refreshRollingSummary as any).mockImplementation(
    () => new Promise((resolve) => { release = () => resolve({
      summary: "Late.", at: 1, total: 1, stale: false, every: 10, due: false,
      refreshed: true }); }));
  renderCampaign();
  fireEvent.change(await screen.findByRole("textbox"), { target: { value: "Go on." } });
  fireEvent.click(screen.getByRole("button", { name: /Send/ }));
  // The turn is over — the composer is out of its Stop state and takes input
  // again — while the refresh is still unresolved. That is what "non-blocking"
  // has to mean for the player. (The button reads "Continue ▶" here rather than
  // "Send ▸": a landed send clears the composer.)
  await waitFor(() => expect(screen.queryByRole("button", { name: /Stop/ })).toBeNull());
  expect(await screen.findByRole("button", { name: /Continue/ })).not.toBeDisabled();
  expect(release).toBeDefined();      // ...and the refresh really is still in flight
  await act(async () => { release!(); });
});

test("a manual dice roll also asks whether the summary is due", async () => {
  // Rolls append narrator posts, so a mechanics-heavy stretch of play can cross
  // the threshold with no generated turn to carry the request (#85).
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  (api.roll as any).mockResolvedValue({ ok: true, total: 7, message: "" });
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
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] });
  (api.getSceneChecks as any).mockResolvedValue({ actors: [
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

test("a turn whose re-read never landed asks for no fold at all", async () => {
  // The boundary passed to the fold comes from the post-turn re-read. When that
  // read is retired — a newer turn superseded it, or it failed outright — there
  // is no verified boundary, and the earlier code fell back to an UNBOUNDED
  // request. That is backwards: the case with a newer turn in flight is exactly
  // the case where an unbounded fold can cover a player post whose reply has
  // not been written yet, keeping that reply out of the summary until another
  // threshold. Nothing is the right thing to send (#85).
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  await screen.findByRole("textbox");
  (api.refreshRollingSummary as any).mockClear();
  (api.getScene as any).mockRejectedValue(new ApiError(503, "store busy", "busy"));
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Go on." } });
  fireEvent.click(screen.getByRole("button", { name: /Send/ }));
  await waitFor(() => expect(screen.queryByRole("button", { name: /Stop/ })).toBeNull());
  expect(api.refreshRollingSummary).not.toHaveBeenCalled();
});

test("saving an edit asks whether the summary is due", async () => {
  // An edit appends nothing, so it never crosses the threshold by count — but
  // it rewrites text the stored summary already covers, which moves the fold's
  // validity key and makes a from-scratch refold due. Without this the panel
  // could only flag the summary stale and wait for a generated turn (#85).
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: { id: "s1" }, messages: [
    { role: "assistant", content: "hi" }] });
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
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "fresh reply" }] });
  (api.getAlternates as any).mockResolvedValue({
    active: 1, alternates: [ALT("old reply"), ALT("fresh reply")] });
  renderCampaign();
  await screen.findByText("2/2");
  (api.refreshRollingSummary as any).mockClear();

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
  (api.listScenes as any)
    .mockResolvedValueOnce([])                       // initial load
    .mockResolvedValue([{ id: "s9", title: "New", model: "", created: "", updated: "" }]);
  renderCampaign();
  await screen.findByText(/Run One/);
  fireEvent.click(screen.getByRole("button", { name: /\+ new scene/i }));
  (api.refreshRollingSummary as any).mockClear();
  fireEvent.click(await screen.findByText("stub-pick"));
  await waitFor(() => expect(api.refreshRollingSummary).toHaveBeenCalledWith(
    "run", "s9", false, expect.any(Number)));
});

test("adopting a generated opener asks whether the summary is due", async () => {
  // `firstPost` persists the opener as the scene's first post — a transcript
  // write like any other, and the only one the cast panel makes (#85).
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [] });
  renderCampaign();
  await screen.findByTestId("cast-panel");
  (api.refreshRollingSummary as any).mockClear();

  fireEvent.click(screen.getByText("stub-seeded"));

  await waitFor(() => expect(api.refreshRollingSummary).toHaveBeenCalledWith(
    "run", "s1", false, expect.any(Number)));
});

// --- scene navigation (#87) ------------------------------------------------
// The scene on screen lives in the URL, so a reload — or a shared link — lands
// back on the scene the reader was in rather than whichever one was edited last.

// listScenes answers most-recently-updated first, so s1 is what the view
// picks with nothing in the URL to say otherwise. s2 is therefore the scene
// that can only be reached by asking for it.
const TWO_SCENES = [
  { id: "s1", title: "Old", model: "", created: "", updated: "2" },
  { id: "s2", title: "The Saltmarch Gate", model: "", created: "", updated: "1" },
];

// One transcript per scene, so "which scene loaded" is readable off the screen.
function transcriptsPerScene() {
  (api.getScene as any).mockImplementation(async (_c: string, sid: string) => ({
    meta: {}, messages: [{ role: "assistant", content: `transcript of ${sid}` }],
  }));
}

function Back() {
  const navigate = useNavigate();
  return <button onClick={() => navigate(-1)}>go back</button>;
}

test("a scene URL opens that scene, not the most recently updated one", async () => {
  (api.listScenes as any).mockResolvedValue(TWO_SCENES);
  transcriptsPerScene();
  renderCampaign("/campaigns/run/scenes/s2");

  await screen.findByText("transcript of s2");
  expect(screen.queryByText("transcript of s1")).toBeNull();
  // and the rest of the scene's context is hydrated, not just its posts
  expect(api.getCast).toHaveBeenCalledWith("run", "s2");
  expect(api.getSceneDatetime).toHaveBeenCalledWith("run", "s2");
});

test("opening a campaign with no scene in the URL redirects to one", async () => {
  (api.listScenes as any).mockResolvedValue(TWO_SCENES);
  transcriptsPerScene();
  renderCampaign();

  await screen.findByText("transcript of s1");
  expect(here()).toBe("/campaigns/run/scenes/s1");
});

test("clicking a scene in the rail puts it in the URL", async () => {
  (api.listScenes as any).mockResolvedValue(TWO_SCENES);
  transcriptsPerScene();
  renderCampaign();
  await screen.findByText("transcript of s1");

  await openScene(/The Saltmarch Gate/);

  await screen.findByText("transcript of s2");
  expect(here()).toBe("/campaigns/run/scenes/s2");
});

test("a scene id that no longer exists falls back to the first scene", async () => {
  // Scene ids are filenames and change under renames, restamps and repads, so
  // a bookmarked or reloaded id can name nothing. Never render a dead scene —
  // and never leave the dead URL in the history for Back to return to.
  (api.listScenes as any).mockResolvedValue(TWO_SCENES);
  transcriptsPerScene();
  render(
    <MemoryRouter initialEntries={["/worlds", "/campaigns/run/scenes/003--gone"]}>
      {withPalette(<>
        <Here />
        <Back />
        {playRoutes()}
      </>)}
    </MemoryRouter>,
  );

  await screen.findByText("transcript of s1");
  expect(here()).toBe("/campaigns/run/scenes/s1");
  // the fallback never fetched the scene that isn't there
  expect((api.getScene as any).mock.calls.every((c: any[]) => c[1] !== "003--gone")).toBe(true);

  fireEvent.click(screen.getByText("go back"));
  await waitFor(() => expect(here()).toBe("/worlds"));
});

test("a campaign with no scenes at all leaves the URL alone", async () => {
  (api.listScenes as any).mockResolvedValue([]);
  renderCampaign();

  await screen.findByText("Run One");
  expect(here()).toBe("/campaigns/run");
  expect(api.getScene).not.toHaveBeenCalled();
});

test("renaming the active scene carries the URL to its new id, replacing the old", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.renameScene as any).mockResolvedValue({ id: "s1-renamed" });
  render(
    <MemoryRouter initialEntries={["/worlds", "/campaigns/run"]}>
      {withPalette(<>
        <Here />
        <Back />
        {playRoutes()}
      </>)}
    </MemoryRouter>,
  );
  await waitFor(() => expect(here()).toBe("/campaigns/run/scenes/s1"));

  (api.listScenes as any).mockResolvedValue(
    [{ id: "s1-renamed", title: "Renamed", model: "", created: "", updated: "" }]);
  fireEvent.click(screen.getByRole("button", { name: /rename scene/i }));
  const nameInput = screen.getByDisplayValue("Old");
  fireEvent.change(nameInput, { target: { value: "Renamed" } });
  fireEvent.keyDown(nameInput, { key: "Enter" });

  await waitFor(() => expect(here()).toBe("/campaigns/run/scenes/s1-renamed"));
  // A rename is not a place the reader went, so it must not stack an entry —
  // and the entry it replaces names a scene that no longer exists.
  fireEvent.click(screen.getByText("go back"));
  await waitFor(() => expect(here()).toBe("/worlds"));
});

test("renaming a scene that is not first in the rail keeps the reader on it", async () => {
  // The rename has to move the URL itself. Left to the stale-id fallback, the
  // reader lands on whatever the list sorts FIRST — which is the renamed scene
  // only when there is one scene, and some other scene the moment there are two.
  (api.listScenes as any).mockResolvedValueOnce(TWO_SCENES).mockResolvedValue([
    TWO_SCENES[0],
    { id: "s2-renamed", title: "Renamed", model: "", created: "", updated: "1" },
  ]);
  transcriptsPerScene();
  (api.renameScene as any).mockResolvedValue({ id: "s2-renamed" });
  renderCampaign("/campaigns/run/scenes/s2");
  await screen.findByText("transcript of s2");

  fireEvent.click(screen.getByRole("button", { name: /rename scene/i }));
  const box = screen.getByDisplayValue("The Saltmarch Gate");
  fireEvent.change(box, { target: { value: "Renamed" } });
  fireEvent.keyDown(box, { key: "Enter" });

  await waitFor(() => expect(here()).toBe("/campaigns/run/scenes/s2-renamed"));
  expect(screen.queryByText("transcript of s1")).toBeNull();
});

test("a scene renamed by the inspector's first date stamp carries the URL too", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  await waitFor(() => expect(here()).toBe("/campaigns/run/scenes/s1"));

  (api.listScenes as any).mockResolvedValue(
    [{ id: "s10", title: "Old", model: "", created: "", updated: "" }]);
  // the stubbed CastPanel reports a date-stamp rename to s10
  fireEvent.click(screen.getByText("stub-datestamp"));

  await waitFor(() => expect(here()).toBe("/campaigns/run/scenes/s10"));
});

test("deleting the scene on screen moves the URL to what is left", async () => {
  (api.listScenes as any).mockResolvedValue(TWO_SCENES);
  transcriptsPerScene();
  vi.spyOn(window, "confirm").mockReturnValue(true);
  renderCampaign("/campaigns/run/scenes/s2");
  await screen.findByText("transcript of s2");

  (api.listScenes as any).mockResolvedValue([TWO_SCENES[0]]);
  fireEvent.click(screen.getByRole("button", { name: /delete scene/i }));   // s2 is on screen

  await waitFor(() => expect(here()).toBe("/campaigns/run/scenes/s1"));
  await screen.findByText("transcript of s1");
});

test("deleting the last scene drops it from the URL and clears the transcript", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  transcriptsPerScene();
  vi.spyOn(window, "confirm").mockReturnValue(true);
  renderCampaign();
  await screen.findByText("transcript of s1");

  (api.listScenes as any).mockResolvedValue([]);
  fireEvent.click(screen.getByRole("button", { name: /delete scene/i }));

  await waitFor(() => expect(here()).toBe("/campaigns/run"));
  expect(screen.queryByText("transcript of s1")).toBeNull();
});

test("a newly created scene becomes the URL", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  transcriptsPerScene();
  renderCampaign();
  await screen.findByText("transcript of s1");

  (api.listScenes as any).mockResolvedValue(
    [...ONE_SCENE, { id: "s9", title: "New", model: "", created: "", updated: "" }]);
  fireEvent.click(screen.getByRole("button", { name: /\+ new scene/i }));
  fireEvent.click(screen.getByText("stub-pick"));   // the stubbed chooser creates s9

  await waitFor(() => expect(here()).toBe("/campaigns/run/scenes/s9"));
  await screen.findByText("transcript of s9");
});

test("a scene the URL names but the server cannot read says so", async () => {
  // The list has the row, so this is not the stale-id fallback — the read
  // itself failed. It used to be an unhandled rejection: no banner, and an
  // unreadable transcript rendered as a scene with nothing in it.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockRejectedValue(
    Object.assign(new Error("boom"), { detail: "transcript is not valid UTF-8" }));
  renderCampaign();

  expect(await screen.findByText(/transcript is not valid UTF-8/)).toBeInTheDocument();
});

test("the first send into an empty campaign puts its new scene in the URL", async () => {
  (api.listScenes as any).mockResolvedValueOnce([]).mockResolvedValue(
    [{ id: "s1", title: "Untitled", model: "", created: "", updated: "" }]);
  (api.createScene as any).mockResolvedValue({ id: "s1" });
  renderCampaign();
  const ta = await screen.findByRole("textbox");

  fireEvent.change(ta, { target: { value: "we begin" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));

  await waitFor(() => expect(here()).toBe("/campaigns/run/scenes/s1"));
  expect(api.chat).toHaveBeenCalledWith("run", "s1", "we begin",
    expect.anything(), undefined, expect.anything(), expect.any(String));
});

test("a scene list arriving after a campaign switch does not strand the view", async () => {
  // A's list is slow; the reader moves to B, whose list lands first. A's late
  // answer must be dropped, not installed: it is labelled with the campaign it
  // was asked about, so installing it leaves the view holding a list nothing
  // will ever ask about again — the resolver waits for the current campaign's
  // rows and B's scene never opens.
  let landA: (v: any) => void = () => {};
  (api.listScenes as any).mockImplementation((c: string) =>
    c === "run" ? new Promise((res) => { landA = res; })
                : Promise.resolve([{ id: "b1", title: "B one", model: "", created: "", updated: "" }]));
  transcriptsPerScene();
  render(
    <MemoryRouter initialEntries={["/campaigns/run"]}>
      {withPalette(<>
        <Here />
        <Link to="/campaigns/other">switch campaign</Link>
        {playRoutes()}
      </>)}
    </MemoryRouter>,
  );
  await screen.findByText("Run One");

  fireEvent.click(screen.getByText("switch campaign"));
  await waitFor(() => expect(here()).toBe("/campaigns/other/scenes/b1"));
  await screen.findByText("transcript of b1");

  // run's list finally answers, naming a scene "other" does not have
  landA([{ id: "s1", title: "Old", model: "", created: "", updated: "" }]);
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });

  expect(here()).toBe("/campaigns/other/scenes/b1");
  expect(screen.getByText("transcript of b1")).toBeInTheDocument();
  expect(screen.queryByText(/· Old$/)).toBeNull();
});

// --- cross-campaign scoping of the async paths (codex review on #299) -------
// Scene ids repeat freely between campaigns, so every one of these guards has
// to compare the campaign too — an id-only test passes on the wrong scene.

test("a mutation relist landing after a campaign switch does not strand the view", async () => {
  // A rename's relist belongs to the campaign that asked for it. Installed
  // into the campaign the reader moved to, it labels B's rail with A's name —
  // and the resolver, which only acts on a list belonging to the current
  // campaign, then returns early forever: every later rail click and URL
  // change is dead until a reload.
  let landRelist: (v: any) => void = () => {};
  (api.listScenes as any).mockImplementation((c: string) =>
    c === "other"
      ? Promise.resolve([{ id: "b1", title: "B one", model: "", created: "", updated: "" }])
      : (api.renameScene as any).mock.calls.length
        ? new Promise((res) => { landRelist = res; })
        : Promise.resolve(ONE_SCENE));
  transcriptsPerScene();
  (api.renameScene as any).mockResolvedValue({ id: "s1-renamed" });
  render(
    <MemoryRouter initialEntries={["/campaigns/run"]}>
      {withPalette(<>
        <Here />
        <Link to="/campaigns/other">switch campaign</Link>
        {playRoutes()}
      </>)}
    </MemoryRouter>,
  );
  await screen.findByText("transcript of s1");

  fireEvent.click(screen.getByRole("button", { name: /rename scene/i }));
  const box = screen.getByDisplayValue("Old");
  fireEvent.change(box, { target: { value: "Renamed" } });
  fireEvent.keyDown(box, { key: "Enter" });
  await waitFor(() => expect(api.renameScene).toHaveBeenCalled());

  // the reader leaves while run's relist is still open
  fireEvent.click(screen.getByText("switch campaign"));
  await waitFor(() => expect(here()).toBe("/campaigns/other/scenes/b1"));
  await screen.findByText("transcript of b1");

  landRelist([{ id: "s1-renamed", title: "Renamed", model: "", created: "", updated: "" }]);
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });

  // the scene list is still B's, and B is still navigable
  fireEvent.keyDown(window, { key: "k", metaKey: true });
  const rows = await screen.findAllByRole("option");
  const labels = rows.map((r) => r.querySelector(".palette-label")?.textContent);
  expect(labels).not.toContain("Renamed");
  expect(labels).toContain("B one");
});

test("a rename finishing after a campaign switch does not drag the reader back", async () => {
  // Both campaigns have an "s1" — every campaign numbers its scenes from 001 —
  // so the "is the renamed scene still on screen" test passes on B's s1 unless
  // it compares the campaign as well, and replaces B's URL with A's scene.
  let landRename: (v: any) => void = () => {};
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);   // both campaigns: s1
  (api.getScene as any).mockImplementation(async (c: string, sid: string) => ({
    meta: {}, messages: [{ role: "assistant", content: `${c}/${sid}` }],
  }));
  (api.renameScene as any).mockImplementation(() => new Promise((res) => { landRename = res; }));
  render(
    <MemoryRouter initialEntries={["/campaigns/run"]}>
      {withPalette(<>
        <Here />
        <Link to="/campaigns/other">switch campaign</Link>
        {playRoutes()}
      </>)}
    </MemoryRouter>,
  );
  await screen.findByText("run/s1");

  fireEvent.click(screen.getByRole("button", { name: /rename scene/i }));
  const box = screen.getByDisplayValue("Old");
  fireEvent.change(box, { target: { value: "Renamed" } });
  fireEvent.keyDown(box, { key: "Enter" });

  fireEvent.click(screen.getByText("switch campaign"));
  await waitFor(() => expect(here()).toBe("/campaigns/other/scenes/s1"));
  await screen.findByText("other/s1");

  landRename({ id: "s1-renamed" });   // run's rename, answered after the move
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });

  expect(here()).toBe("/campaigns/other/scenes/s1");
  expect(screen.getByText("other/s1")).toBeInTheDocument();
});

test("a scene read that fails after the reader moved on does not raise a banner", async () => {
  // The rejection carries no window token, so an unscoped handler blames
  // whatever is on screen when it lands — and the switch that retired the read
  // has already cleared the errors that belonged to it, so the banner sticks.
  let failFirst: (e: any) => void = () => {};
  (api.listScenes as any).mockResolvedValue(TWO_SCENES);
  (api.getScene as any)
    .mockImplementationOnce(() => new Promise((_res, rej) => { failFirst = rej; }))
    .mockImplementation(async (_c: string, sid: string) => ({
      meta: {}, messages: [{ role: "assistant", content: `transcript of ${sid}` }] }));
  renderCampaign();
  await waitFor(() => expect(api.getScene).toHaveBeenCalled());

  await openScene(/The Saltmarch Gate/);
  await screen.findByText("transcript of s2");

  failFirst(Object.assign(new Error("boom"), { detail: "transcript is not valid UTF-8" }));
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });

  expect(screen.queryByText(/transcript is not valid UTF-8/)).toBeNull();
  expect(screen.getByText("transcript of s2")).toBeInTheDocument();
});

test("a turn finishing after a campaign switch does not install its transcript under the new one", async () => {
  // The most expensive version of the id-collision hazard. `runStream`'s
  // finally refreshes the scene the TURN owns, and every guard on that path
  // compares scene ids — which repeat, since each campaign numbers from 001.
  // So campaign A's refresh for "s1" passes while the reader is on B's "s1",
  // and installs A's posts under B's URL; an edit then addresses B's file with
  // A's message indices.
  //
  // The resolver cannot repair this one: `setActiveId` is handed the value it
  // already holds, React bails out of the render, and the effect never re-runs.
  // The refresh has to refuse to apply itself (codex review, P1).
  let finishTurn: (v: any) => void = () => {};
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);   // both campaigns: s1
  (api.getScene as any).mockImplementation(async (c: string, sid: string) => ({
    meta: {}, total: 1, messages: [{ role: "assistant", content: `${c}/${sid}` }],
  }));
  (api.chat as any).mockImplementation(
    async (_c: string, _s: string, _t: string, onEvent: any) =>
      new Promise<void>((res) => { finishTurn = () => { onEvent({ done: true }); res(undefined); }; }));
  render(
    <MemoryRouter initialEntries={["/campaigns/run"]}>
      {withPalette(<>
        <Here />
        <Link to="/campaigns/other">switch campaign</Link>
        {playRoutes()}
      </>)}
    </MemoryRouter>,
  );
  await screen.findByText("run/s1");

  const ta = screen.getByRole("textbox");
  fireEvent.change(ta, { target: { value: "we begin" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await waitFor(() => expect(api.chat).toHaveBeenCalled());

  fireEvent.click(screen.getByText("switch campaign"));
  await waitFor(() => expect(here()).toBe("/campaigns/other/scenes/s1"));
  await screen.findByText("other/s1");

  finishTurn(undefined);   // run's turn lands after the reader moved on
  await act(async () => { for (let i = 0; i < 6; i++) await Promise.resolve(); });

  expect(screen.getByText("other/s1")).toBeInTheDocument();
  expect(screen.queryByText("run/s1")).toBeNull();
});

test("a scene created just before a campaign switch does not drag the reader back", async () => {
  // The rows are guarded and the navigation was not: `goToScene` carries the
  // captured campaign, so a switch during the relist sent the reader back to
  // the campaign they had just left.
  let landRelist: (v: any) => void = () => {};
  const nth = readCounter();
  (api.listScenes as any).mockImplementation((c: string) =>
    c === "other"
      ? Promise.resolve([{ id: "b1", title: "B one", model: "", created: "", updated: "" }])
      : nth(c) > 1
        ? new Promise((res) => { landRelist = res; })
        : Promise.resolve(ONE_SCENE));
  transcriptsPerScene();
  render(
    <MemoryRouter initialEntries={["/campaigns/run"]}>
      {withPalette(<>
        <Here />
        <Link to="/campaigns/other">switch campaign</Link>
        {playRoutes()}
      </>)}
    </MemoryRouter>,
  );
  await screen.findByText("transcript of s1");

  fireEvent.click(screen.getByRole("button", { name: /\+ new scene/i }));
  fireEvent.click(screen.getByText("stub-pick"));          // creates s9 in "run"
  fireEvent.click(screen.getByText("switch campaign"));    // …then leaves
  await waitFor(() => expect(here()).toBe("/campaigns/other/scenes/b1"));

  landRelist([...ONE_SCENE, { id: "s9", title: "New", model: "", created: "", updated: "" }]);
  await act(async () => { for (let i = 0; i < 4; i++) await Promise.resolve(); });

  expect(here()).toBe("/campaigns/other/scenes/b1");
});

test("an older relist cannot restore a row a newer one removed", async () => {
  // Nothing serializes scene mutations — `renamesInFlight` blocks turns, not
  // deletions — so a rename's relist can still be open when the reader deletes
  // another row. Response order is not request order, and the older answer
  // landing last used to put the deleted row back. The list now decides which
  // sid the URL may name, so that row is a ghost leading to a 404 read.
  let landRenameRelist: (v: any) => void = () => {};
  let relists = 0;
  const nth = readCounter();
  (api.listScenes as any).mockImplementation((c: string) => {
    if (nth(c) === 1) return Promise.resolve(TWO_SCENES);
    relists += 1;
    // the rename's relist (first) hangs; the delete's (second) answers at once
    if (relists === 1) return new Promise((res) => { landRenameRelist = res; });
    return Promise.resolve([{ id: "s1-renamed", title: "Renamed", model: "", created: "", updated: "2" }]);
  });
  transcriptsPerScene();
  (api.renameScene as any).mockResolvedValue({ id: "s1-renamed" });
  vi.spyOn(window, "confirm").mockReturnValue(true);
  renderCampaign("/campaigns/run/scenes/s1");
  await screen.findByText("transcript of s1");

  fireEvent.click(await screen.findByRole("button", { name: /rename scene/i }));
  const box = screen.getByDisplayValue("Old");
  fireEvent.change(box, { target: { value: "Renamed" } });
  fireEvent.keyDown(box, { key: "Enter" });
  await waitFor(() => expect(relists).toBe(1));

  // s2 is deleted while the rename's relist is still open. Delete belongs to
  // the scene on screen now, so this opens it first.
  await openScene(/The Saltmarch Gate/);
  fireEvent.click(screen.getByRole("button", { name: /delete scene/i }));
  await waitFor(() => expect(
    screen.queryByRole("heading", { name: /The Saltmarch Gate/ })).toBeNull());

  // the rename's older answer lands last, still carrying the deleted scene
  landRenameRelist([
    { id: "s1-renamed", title: "Renamed", model: "", created: "", updated: "2" },
    TWO_SCENES[1],
  ]);
  await act(async () => { for (let i = 0; i < 4; i++) await Promise.resolve(); });

  expect(screen.queryByRole("heading", { name: /The Saltmarch Gate/ })).toBeNull();
});

test("the first send's new scene does not follow the reader into another campaign", async () => {
  // Same gap as the chooser's, reached the other way: the campaign check sat
  // ABOVE the relist, so it answered what was true one request ago and the
  // adopt-and-navigate below still ran after a switch.
  let landRelist: (v: any) => void = () => {};
  const nth = readCounter();
  (api.listScenes as any).mockImplementation((c: string) =>
    c === "other"
      ? Promise.resolve([{ id: "b1", title: "B one", model: "", created: "", updated: "" }])
      : nth(c) > 1
        ? new Promise((res) => { landRelist = res; })
        : Promise.resolve([]));            // "run" starts with no scenes at all
  transcriptsPerScene();
  (api.createScene as any).mockResolvedValue({ id: "s1" });
  render(
    <MemoryRouter initialEntries={["/campaigns/run"]}>
      {withPalette(<>
        <Here />
        <Link to="/campaigns/other">switch campaign</Link>
        {playRoutes()}
      </>)}
    </MemoryRouter>,
  );
  const ta = await screen.findByRole("textbox");

  fireEvent.change(ta, { target: { value: "we begin" } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await waitFor(() => expect(api.createScene).toHaveBeenCalled());

  fireEvent.click(screen.getByText("switch campaign"));
  await waitFor(() => expect(here()).toBe("/campaigns/other/scenes/b1"));

  landRelist([{ id: "s1", title: "Untitled", model: "", created: "", updated: "" }]);
  await act(async () => { for (let i = 0; i < 4; i++) await Promise.resolve(); });

  expect(here()).toBe("/campaigns/other/scenes/b1");
  // The turn does not start: everything after the guard writes to the view,
  // none of it campaign-scoped, so running it would render this turn into the
  // campaign the reader moved to. The scene stays behind, created and empty…
  expect(api.chat).not.toHaveBeenCalled();
  // …and the words are still in the composer, not swallowed.
  expect(screen.getByRole("textbox")).toHaveValue("we begin");
});

test("a mutation relist in the old campaign cannot retire the new campaign's list", async () => {
  // The stranding the sequence guard exists to prevent, reachable THROUGH it:
  // a rename started in A resolves — and issues its relist — only after B's
  // mount read is already in flight. A single global counter hands A's later
  // request the newer number, so B's answer is retired by it while A's own is
  // refused for being the wrong campaign. Nothing installs, `sceneListCid`
  // never becomes B, and the resolver is disabled until reload.
  let landRenamePut: (v: any) => void = () => {};
  let landRenameRelist: (v: any) => void = () => {};
  let landMountB: (v: any) => void = () => {};
  const nth = readCounter();
  (api.listScenes as any).mockImplementation((c: string) => {
    if (c === "other") return new Promise((res) => { landMountB = res; });
    if (nth(c) > 1) return new Promise((res) => { landRenameRelist = res; });
    return Promise.resolve(ONE_SCENE);
  });
  transcriptsPerScene();
  // the PUT hangs, so the handler that resumes still carries run's `cid`
  (api.renameScene as any).mockImplementation(() => new Promise((res) => { landRenamePut = res; }));
  render(
    <MemoryRouter initialEntries={["/campaigns/run"]}>
      {withPalette(<>
        <Here />
        <Link to="/campaigns/other">switch campaign</Link>
        {playRoutes()}
      </>)}
    </MemoryRouter>,
  );
  await screen.findByText("transcript of s1");

  // started in run, while run is still the campaign on screen
  fireEvent.click(screen.getByRole("button", { name: /rename scene/i }));
  const box = screen.getByDisplayValue("Old");
  fireEvent.change(box, { target: { value: "Renamed" } });
  fireEvent.keyDown(box, { key: "Enter" });
  await waitFor(() => expect(api.renameScene).toHaveBeenCalledWith("run", "s1", "Renamed"));

  // the reader leaves; B's mount read is issued and still open
  fireEvent.click(screen.getByText("switch campaign"));
  await waitFor(() => expect(api.listScenes).toHaveBeenCalledWith("other"));

  // only NOW does run's rename land, issuing its relist after B's read
  landRenamePut({ id: "s1-renamed" });
  await act(async () => { for (let i = 0; i < 4; i++) await Promise.resolve(); });
  landRenameRelist([{ id: "s1-renamed", title: "Renamed", model: "", created: "", updated: "" }]);
  await act(async () => { for (let i = 0; i < 4; i++) await Promise.resolve(); });

  // B's list finally answers — it must still install, and B still be navigable
  landMountB([{ id: "b1", title: "B one", model: "", created: "", updated: "" }]);
  await waitFor(() => expect(here()).toBe("/campaigns/other/scenes/b1"));
  await screen.findByText("transcript of b1");
});

test("a premise generated in one campaign is not offered to another's scene", async () => {
  // `seedPrompt` is recorded before the relist that follows it, so a reader who
  // switches campaigns during that await leaves it behind — and scene ids
  // repeat, so a sid-only match hands A's premise to B's identically-numbered
  // empty scene.
  let landRelist: (v: any) => void = () => {};
  const nth = readCounter();
  (api.listScenes as any).mockImplementation((c: string) =>
    c === "other"
      ? Promise.resolve([{ id: "s9", title: "B nine", model: "", created: "", updated: "" }])
      : nth(c) > 1
        ? new Promise((res) => { landRelist = res; })
        : Promise.resolve(ONE_SCENE));
  // every scene is empty, so CastPanel (which renders the premise) is shown
  (api.getScene as any).mockResolvedValue({ meta: {}, messages: [] });
  render(
    <MemoryRouter initialEntries={["/campaigns/run"]}>
      {withPalette(<>
        <Here />
        <Link to="/campaigns/other">switch campaign</Link>
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
  landRelist([...ONE_SCENE, { id: "s9", title: "New", model: "", created: "", updated: "" }]);
  await act(async () => { for (let i = 0; i < 4; i++) await Promise.resolve(); });

  // "other" also has an s9, and it must not be handed run's premise
  expect(screen.getByTestId("cast-panel")).not.toHaveTextContent("A premise");
});

// Follow-up to PR #318: a soft failure inside SceneConfirmForm (a failed
// location/date/rename step) still creates the scene -- `salvaged` -- and
// clears `writing`, so Escape and the backdrop can dismiss the chooser from
// there instead of only "Continue to scene". `sceneCreated` is what relists
// after a normal create; dismissing never reached it, so the rail stayed one
// scene short of reality until a manual reload.
test("dismissing the chooser after a soft failure refreshes the scene list", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  await screen.findByTestId("cast-panel");

  fireEvent.click(screen.getByRole("button", { name: /\+ new scene/i }));
  await screen.findByTestId("scene-chooser");
  const before = (api.listScenes as any).mock.calls.length;
  const wasAt = here();
  fireEvent.click(screen.getByText("stub-close-salvaged"));
  await waitFor(() => expect((api.listScenes as any).mock.calls.length).toBeGreaterThan(before));
  // a dismissal, not "Continue to scene" -- the URL does not move to the
  // salvaged scene, unlike a normal `sceneCreated`
  expect(here()).toBe(wasAt);
});

// The other half: a PLAIN dismissal (nothing was salvaged) must not pay for
// a relist it doesn't need -- most Cancels and idle Escapes wrote nothing.
test("a plain dismissal (nothing salvaged) does not refresh the scene list", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  await screen.findByTestId("cast-panel");

  fireEvent.click(screen.getByRole("button", { name: /\+ new scene/i }));
  await screen.findByTestId("scene-chooser");
  const before = (api.listScenes as any).mock.calls.length;
  fireEvent.click(screen.getByText("stub-close"));
  await act(async () => { for (let i = 0; i < 4; i++) await Promise.resolve(); });
  expect((api.listScenes as any).mock.calls.length).toBe(before);
});

test("a created scene is opened against a list that knows it exists", async () => {
  // The rail stays interactive while a creation's relist is open, so a rename
  // can issue a newer read that retires it. If the retired read resolved
  // anyway, `sceneCreated` would navigate to the new row while the installed
  // list still predates the creation — and the resolver would read that
  // brand-new id as stale and redirect straight back to the previous scene.
  let landCreateRelist: (v: any) => void = () => {};
  let landRenameRelist: (v: any) => void = () => {};
  let fresh = 0;
  const WITH_S9 = [...ONE_SCENE, { id: "s9", title: "New", model: "", created: "", updated: "" }];
  const nth = readCounter();
  (api.listScenes as any).mockImplementation((c: string) => {
    if (nth(c) === 1) return Promise.resolve(ONE_SCENE);
    fresh += 1;
    if (fresh === 1) return new Promise((res) => { landCreateRelist = res; });
    return new Promise((res) => { landRenameRelist = res; });
  });
  transcriptsPerScene();
  (api.renameScene as any).mockResolvedValue({ id: "s1-renamed" });
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
  landRenameRelist([{ id: "s1-renamed", title: "Renamed", model: "", created: "", updated: "" },
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
  let failCreateRelist: (e: any) => void = () => {};
  let landRenameRelist: (v: any) => void = () => {};
  let fresh = 0;
  const nth = readCounter();
  (api.listScenes as any).mockImplementation((c: string) => {
    if (nth(c) === 1) return Promise.resolve(ONE_SCENE);
    fresh += 1;
    if (fresh === 1) return new Promise((_res, rej) => { failCreateRelist = rej; });
    return new Promise((res) => { landRenameRelist = res; });
  });
  transcriptsPerScene();
  (api.renameScene as any).mockResolvedValue({ id: "s1-renamed" });
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

  landRenameRelist([{ id: "s1-renamed", title: "Renamed", model: "", created: "", updated: "" },
                    { id: "s9", title: "New", model: "", created: "", updated: "" }]);

  await waitFor(() => expect(here()).toBe("/campaigns/run/scenes/s9"));
  // the superseded failure is not reported: it described a retired request
  expect(screen.queryByText(/list read failed/)).toBeNull();
});

test("a scene created whose only list read fails says so instead of going quiet", async () => {
  // The other side of the same change: when the failure IS the campaign's
  // newest read, the scene exists and the rail does not list it, so silence
  // would strand a real scene behind an unhandled rejection.
  const nth = readCounter();
  (api.listScenes as any).mockImplementation((c: string) =>
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
  (api.listScenes as any).mockResolvedValue(TWO_SCENES);
  (api.getScene as any).mockImplementation(async (_c: string, sid: string) => ({
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
  let landS2: (v: any) => void = () => {};
  (api.getScene as any).mockImplementationOnce(() => new Promise((res) => { landS2 = res; }));
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
  landS2({ meta: {}, total: 2, messages: [
    { role: "user", content: "s2 first" }, { role: "assistant", content: "s2 second" }] });
  await waitFor(() => expect(screen.getByText("s2 first")).toBeInTheDocument());
});

test("a creation's list failure does not raise a banner in another campaign", async () => {
  // The switch to another campaign already cleared that campaign's errors, so
  // a late rejection from the campaign left behind lands as a banner claiming
  // the CURRENT campaign's scene list failed.
  let failRelist: (e: any) => void = () => {};
  const nth = readCounter();
  (api.listScenes as any).mockImplementation((c: string) =>
    c === "other"
      ? Promise.resolve([{ id: "b1", title: "B one", model: "", created: "", updated: "" }])
      : nth(c) > 1
        ? new Promise((_res, rej) => { failRelist = rej; })
        : Promise.resolve(ONE_SCENE));
  transcriptsPerScene();
  render(
    <MemoryRouter initialEntries={["/campaigns/run"]}>
      {withPalette(<>
        <Here />
        <Link to="/campaigns/other">switch campaign</Link>
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
  (api.listScenes as any).mockResolvedValue(TWO_SCENES);
  (api.getScene as any).mockImplementation(async (_c: string, sid: string) => ({
    meta: {}, total: 2, messages: [
      { role: "user", content: `${sid} asked` },
      { role: "assistant", content: `${sid} replied` }],
  }));
  renderCampaign();
  await screen.findByText("s1 replied");
  // the reroll affordance exists while s1's transcript is genuinely s1's
  expect(screen.getByRole("button", { name: /reroll/i })).toBeInTheDocument();

  // hold s2's read open so the divergence window stays observable
  let landS2: (v: any) => void = () => {};
  (api.getScene as any).mockImplementationOnce(() => new Promise((res) => { landS2 = res; }));
  await openScene(/The Saltmarch Gate/);
  await waitFor(() => expect(here()).toBe("/campaigns/run/scenes/s2"));

  // s1's transcript is still rendered while s2 is the active scene
  expect(screen.getByText("s1 replied")).toBeInTheDocument();
  // …and the control that would regenerate s2 off it is gone
  expect(screen.queryByRole("button", { name: /reroll/i })).toBeNull();
  expect(api.regenerate).not.toHaveBeenCalled();

  landS2({ meta: {}, total: 2, messages: [
    { role: "user", content: "s2 asked" }, { role: "assistant", content: "s2 replied" }] });
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
  const held: ((v: any) => void)[] = [];
  (api.listScenes as any).mockResolvedValue(TWO_SCENES);
  (api.getScene as any).mockImplementation(async (_c: string, sid: string) => {
    reads += 1;
    if (reads === 1) {
      return { meta: {}, total: 2, messages: [
        { role: "user", content: `${sid} asked` },
        { role: "assistant", content: `${sid} replied` }] };
    }
    return new Promise((res) => { held.push(res); });
  });
  (api.chat as any).mockImplementation(async (_c: string, _s: string, _t: string, onEvent: any) => {
    onEvent({ error: { detail: "OpenRouter API key is not set", post_returned: true } });
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
  held[held.length - 1]({ meta: {}, total: 2, messages: [
    { role: "user", content: "s2 asked" }, { role: "assistant", content: "s2 replied" }] });
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
  let landS2: (v: any) => void = () => {};
  let reads = 0;
  (api.listScenes as any).mockResolvedValue(TWO_SCENES);
  (api.getScene as any).mockImplementation(async (_c: string, sid: string, w?: any) => {
    // `runStream` takes a one-message baseline read before posting whenever the
    // turn's scene is not the one last read — that has to answer, or the turn
    // never starts and there is nothing to observe.
    if (w?.limit === 1) return { meta: {}, total: 2, messages: [] };
    reads += 1;
    if (reads === 1) {
      return { meta: {}, total: 2, messages: [
        { role: "user", content: `${sid} asked` },
        { role: "assistant", content: `${sid} replied` }] };
    }
    return new Promise((res) => { landS2 = res; });
  });
  (api.chat as any).mockImplementation(async (_c: string, _s: string, _t: string, onEvent: any) => {
    onEvent({ error: { detail: "OpenRouter API key is not set", post_returned: true } });
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

  landS2({ meta: {}, total: 2, messages: [
    { role: "user", content: "s2 asked" }, { role: "assistant", content: "s2 replied" }] });
  await screen.findByText("s2 replied");
  expect(document.querySelector('button[aria-label^="Edit message"]')).not.toBeNull();
});

// ---- provenance in the play view (4a) ----

test("a dossier row carries its citation, and hovering it lights the transcript line", async () => {
  // The claim of the whole screen: every continuity line is something you can
  // check, and checking it costs nothing you were holding.
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getCast as any).mockResolvedValue([
    { kind: "characters", id: "aud", role: "npc", name: "Sister Aud" }]);
  (api.getScene as any).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "assistant", content: "I'd rather the mud than his company.", speaker: "Sister Aud" },
    { role: "assistant", content: "The tide had been going out since before dawn." },
  ] });
  (api.getCasefile as any).mockResolvedValue({
    kind: "characters", id: "aud", name: "Sister Aud", version: "v1", role: "npc",
    scenes: ["s1"], last_seen: "s1",
    standing: "Guarded. Will not be alone with the Reeve.",
    knows: "", suspects: "", dossier: "", tagline: "",
    feels_toward: [], standing_facts: [],
  });
  (api.campaignProvenance as any).mockResolvedValue({
    "characters/aud#current_state": {
      quote: "I'd rather the mud than his company.", speaker: "Sister Aud",
      certainty: 0.92, authority: "self", band: "high", scene: "s1",
      recorded: "2026-08-13T10:00:00Z",
    },
  });
  renderCampaign();
  const column = within(await screen.findByRole("complementary"));
  fireEvent.click(await column.findByText("Sister Aud"));

  const marker = await screen.findByRole("button", { name: /^Standing: Cited/ });
  fireEvent.focus(marker);
  expect(screen.getByText(/CERTAINTY 0.92/)).toBeInTheDocument();

  // the post the quote came from lights up, and only that one
  await waitFor(() => expect(document.querySelectorAll(".msg.cited")).toHaveLength(1));
  expect((document.querySelector(".msg.cited") as HTMLElement).textContent)
    .toMatch(/rather the mud/);

  fireEvent.blur(marker);
  await waitFor(() => expect(document.querySelectorAll(".msg.cited")).toHaveLength(0));
});

test("a campaign whose provenance cannot be read shows uncited rows, not an error", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getCast as any).mockResolvedValue([
    { kind: "characters", id: "aud", role: "npc", name: "Sister Aud" }]);
  (api.campaignProvenance as any).mockRejectedValue(new Error("boom"));
  (api.getCasefile as any).mockResolvedValue({
    kind: "characters", id: "aud", name: "Sister Aud", version: "v1", role: "npc",
    scenes: ["s1"], last_seen: "s1", standing: "Guarded.",
    knows: "", suspects: "", dossier: "", tagline: "",
    feels_toward: [], standing_facts: [],
  });
  renderCampaign();
  const column = within(await screen.findByRole("complementary"));
  fireEvent.click(await column.findByText("Sister Aud"));
  expect(await screen.findByRole("button", { name: /^Standing: No citation/ }))
    .toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

// ---- the absorb review's three panes (4c) ----

// ---- focus mode ----

/** Stands in for the app header's FOCUS button, which is not on this screen. */
function EnterFocus() {
  const { setFocus } = useFocus();
  return <button onClick={() => setFocus(true)}>enter-focus</button>;
}

function renderFocusable() {
  return render(
    <MemoryRouter initialEntries={["/campaigns/run"]}>
      <FocusProvider>
        {withPalette(<><EnterFocus /><Here />{playRoutes()}</>)}
      </FocusProvider>
    </MemoryRouter>,
  );
}

test("focus mode leaves the transcript and the composer, and nothing above them", async () => {
  localStorage.clear();
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({
    meta: { id: "s1", title: "Old" },
    messages: [{ role: "assistant", content: "a reply" }],
  });
  renderFocusable();
  await screen.findByRole("heading", { name: /^Old$/ });
  expect(screen.getByRole("button", { name: /End scene/ })).toBeInTheDocument();

  fireEvent.click(screen.getByText("enter-focus"));

  // The scene bar: eleven controls that wrap into four rows at 375px.
  expect(screen.queryByRole("button", { name: /End scene/ })).toBeNull();
  expect(screen.queryByRole("link", { name: /^Ledger$/ })).toBeNull();
  // The scene head: its title, its turn count and its rename/delete pair.
  expect(screen.queryByRole("heading", { name: /^Old$/ })).toBeNull();
  expect(screen.queryByText(/SCENE 1 ·/i)).toBeNull();
  expect(screen.queryByRole("button", { name: /rename scene/i })).toBeNull();

  // What is left is what the mode is for.
  expect(within(screen.getByTestId("stream")).getByText("a reply")).toBeInTheDocument();
  expect(screen.getByRole("textbox")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /continue ▶|send ▸/i })).toBeInTheDocument();
});

test("a panel the scene bar opened does not outlive the bar that closes it", async () => {
  localStorage.clear();
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({
    meta: { id: "s1", title: "Old" },
    messages: [{ role: "assistant", content: "a reply" }],
  });
  renderFocusable();
  await screen.findByRole("heading", { name: /^Old$/ });

  fireEvent.click(screen.getByRole("button", { name: /^Calendar$/ }));
  expect(await screen.findByTestId("calendar-config")).toBeInTheDocument();

  // Its Close is the same bar button, so leaving it up would strand a panel
  // above the transcript with nothing that could shut it.
  fireEvent.click(screen.getByText("enter-focus"));
  expect(screen.queryByTestId("calendar-config")).toBeNull();
});

test("the inspector stays reachable in focus mode, because its toggle does", async () => {
  localStorage.clear();
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({
    meta: { id: "s1", title: "Old" },
    messages: [{ role: "assistant", content: "a reply" }],
  });
  renderFocusable();
  await screen.findByRole("heading", { name: /^Old$/ });
  fireEvent.click(screen.getByText("enter-focus"));

  // The composer is the input area focus mode exists to keep, and this link is
  // part of it — so unlike the bar's five panels this one can be shut again.
  fireEvent.click(screen.getByRole("button", { name: /what the model saw/i }));
  expect(await screen.findByRole("button", { name: /hide what the model saw/i }))
    .toBeInTheDocument();
});

test("the scene bar opens the incoming-changes review, and closes it again", async () => {
  (api.getIncoming as any).mockResolvedValue([
    { ref: { kind: "locations", id: "saltmarch-harbor" }, status: "conflict",
      world: { name: "Saltmarch Harbor", body: "The harbour is blockaded." },
      mine: { name: "Saltmarch Harbor", body: "A busy port town." } },
  ]);
  renderCampaign();
  await screen.findByRole("button", { name: "World updates" });

  fireEvent.click(screen.getByRole("button", { name: "World updates" }));
  expect(await screen.findByRole("heading", { name: "Incoming world changes" })).toBeInTheDocument();
  expect(await screen.findByRole("button", { name: /Saltmarch Harbor/ })).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  expect(screen.queryByRole("heading", { name: "Incoming world changes" })).toBeNull();
});

// ---- the campaign budget banner (#153) ----
const OVER_BUDGET = {
  limit_usd: 10, period: "monthly", level: "over", warn_fraction: 0.8,
  since: "2026-08-01", until: "2026-08-14", spent_usd: 12.5, estimated_usd: 0,
  unpriced_calls: 0, calls: 40, fraction: 1.25,
};

test("a campaign past its budget says so above the scene", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getCampaignBudget as any).mockResolvedValue(OVER_BUDGET);
  renderCampaign();

  expect(await screen.findByText(/Over budget: \$12\.50 of \$10\.00 spent this month/))
    .toBeInTheDocument();
});

test("a campaign approaching its budget is warned before it is broken", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getCampaignBudget as any).mockResolvedValue(
    { ...OVER_BUDGET, level: "warn", spent_usd: 8.5, fraction: 0.85 });
  renderCampaign();

  expect(await screen.findByText(/Approaching budget: \$8\.50 of \$10\.00/)).toBeInTheDocument();
});

test("a campaign with no budget gets no banner", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  await screen.findByText("Run One");

  expect(screen.queryByText(/budget/i)).not.toBeInTheDocument();
});

test("dismissing the warning does not swallow the one that says it is gone", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getCampaignBudget as any).mockResolvedValue(
    { ...OVER_BUDGET, level: "warn", spent_usd: 8.5, fraction: 0.85 });
  renderCampaign();

  fireEvent.click(await screen.findByRole("button", { name: "Dismiss" }));
  expect(screen.queryByText(/Approaching budget/)).not.toBeInTheDocument();

  // The same campaign, now past the cap after one more turn: a different fact,
  // so it is said again. A turn is what re-reads the budget -- the banner is
  // keyed on the same bump the inspector's panels are, not on a timer.
  (api.getCampaignBudget as any).mockResolvedValue(OVER_BUDGET);
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Go on." } });
  fireEvent.click(screen.getByRole("button", { name: /Send/ }));
  expect(await screen.findByText(/Over budget/)).toBeInTheDocument();
});

test("a budget lookup that fails leaves no banner and no error over the scene", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getCampaignBudget as any).mockRejectedValue(new Error("nope"));
  renderCampaign();
  await screen.findByText("Run One");

  expect(screen.queryByText(/budget/i)).not.toBeInTheDocument();
});

test("the budget banner says where the budget can be changed", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getCampaignBudget as any).mockResolvedValue(OVER_BUDGET);
  renderCampaign();

  expect(await screen.findByText(/Change it in the inspector’s Cost section\./))
    .toBeInTheDocument();
});
