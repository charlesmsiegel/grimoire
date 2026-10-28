import { screen, fireEvent, waitFor, within } from "@testing-library/react";
import { api } from "../api/client";
import { here, installCampaignMocks, ONE_SCENE, renderCampaign } from "../testkit/campaignHarness";
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



test("⌘K numbers a scene by its id's own number, not by list position", async () => {
  // listScenes is sorted by `updated` descending — an earlier scene edited
  // most recently sorts first, which must not desync the displayed number
  // from the scene's actual story position (its id's leading number).
  vi.mocked(api.listScenes).mockResolvedValue([
    { id: "003--2024-09-10--day-two", title: "Day Two", model: "", created: "", updated: "2026-07-07T00:49:35Z", date: "" },
    { id: "036--2024-09-24--froot-loops", title: "Froot Loops", model: "", created: "", updated: "2026-07-06T23:26:21Z", date: "" },
  ]);
  renderCampaign();
  await screen.findByRole("heading", { name: /Day Two/ });
  fireEvent.keyDown(window, { key: "k", metaKey: true });
  expect(await screen.findByRole("option", { name: /Day Two.*scene 3/i })).toBeInTheDocument();
  expect(screen.getByRole("option", { name: /Froot Loops.*scene 36/i })).toBeInTheDocument();
});

test("the palette's campaign-world entry lands directly on the cast, not the redirect root", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  await screen.findByRole("heading", { name: /Old/ });
  fireEvent.keyDown(window, { key: "k", metaKey: true });
  fireEvent.click(await screen.findByRole("option", { name: /This campaign's world/i }));
  await waitFor(() => expect(here()).toBe("/campaigns/run/world/characters"));
});

test("plates mark PC speakers and show avatars from the roster", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getCast).mockResolvedValue([
    { kind: "characters", id: "seraphine", role: "npc", name: "Seraphine Vale" },
    { kind: "pcs", id: "yara", role: "player", name: "Yara" },
  ]);
  vi.mocked(api.listAppearances).mockResolvedValue(fixture([
    { kind: "characters", id: "seraphine", version: "v1", role: "npc", scenes: ["s1"] },
  ]));
  vi.mocked(api.getScene).mockResolvedValue({
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
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getCast).mockResolvedValue([
    { kind: "pcs", id: "yara", role: "player", name: "Yara" },
  ]);
  vi.mocked(api.listAppearances).mockResolvedValue(fixture([
    { kind: "pcs", id: "yara", version: "v2", role: "player", scenes: ["s1"] },
  ]));
  vi.mocked(api.getScene).mockResolvedValue({
    meta: { id: "s1", title: "Old" },
    messages: [{ role: "user", content: "Hello.", speaker: "Yara" }],
  });
  renderCampaign();
  const stream = within(await screen.findByTestId("stream"));
  const portrait = await stream.findByAltText("Yara portrait");
  // A 26px plate asks for the smallest bucket, never the original.
  expect(portrait.getAttribute("src")).toBe("/img/pcs/yara/v2/avatar?w=128");
});

test("a plate's portrait carries the roster's avatar token, so it is cached immutable", async () => {
  // Every plate of one speaker is the same URL; without `?v=` it is served
  // no-cache, so reopening the scene revalidated it for every actor who spoke.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getCast).mockResolvedValue([
    { kind: "pcs", id: "yara", role: "player", name: "Yara" },
  ]);
  vi.mocked(api.listAppearances).mockResolvedValue(fixture([
    { kind: "pcs", id: "yara", version: "v2", role: "player", scenes: ["s1"], avatar_v: "1a-2b" },
  ]));
  vi.mocked(api.getScene).mockResolvedValue({
    meta: { id: "s1", title: "Old" },
    messages: [{ role: "user", content: "Hello.", speaker: "Yara" }],
  });
  renderCampaign();
  const stream = within(await screen.findByTestId("stream"));
  const portrait = await stream.findByAltText("Yara portrait");
  expect(portrait.getAttribute("src")).toBe("/img/pcs/yara/v2/avatar?w=128&v=1a-2b");
});

/** A scene whose transcript has one post, spoken by a cast member. */
function speakingScene() {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getCast).mockResolvedValue([
    { kind: "characters", id: "seraphine", role: "npc", name: "Seraphine Vale" },
  ]);
  vi.mocked(api.getScene).mockResolvedValue({
    meta: { id: "s1", title: "Old" },
    messages: [{ role: "assistant", content: "She waits.", speaker: "Seraphine Vale" }],
  });
}

test("clicking a speaker in the transcript opens them in the column, like the cast grid", async () => {
  // A drawer over the transcript to read about someone standing in it was a
  // modal answering the question the column beside it already answers.
  speakingScene();
  vi.mocked(api.getCasefile).mockResolvedValue({
    kind: "characters", id: "seraphine", name: "Seraphine Vale", version: "v1", role: "npc",
    scenes: [{ id: "s1", title: "Old" }], last_seen: "Old", standing: "Keeps the tide gate.",
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
  vi.mocked(api.getCast).mockResolvedValue([]);
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
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  fireEvent.click(await screen.findByRole("button", { name: /what the model saw/i }));
  await screen.findByText(/Active characters/i);
  await screen.findByText(/^Context/);
});

test("hides Cast & scene setup once the scene has messages", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [{ role: "assistant", content: "hi" }] });
  renderCampaign();
  await screen.findByText("hi");
  expect(screen.queryByTestId("cast-panel")).toBeNull();
});

test("shows Cast & scene setup for an empty scene", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [] });
  renderCampaign();
  await screen.findByTestId("cast-panel");
});

test("the setup panel sits under the scene's title, between it and the composer", async () => {
  // Above the title at its full height it pushed the composer below the fold
  // of a new scene. The reading order keeps setup before the composer.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [] });
  renderCampaign();
  const panel = await screen.findByTestId("cast-panel");
  const head = screen.getByRole("heading", { name: /^Old$/ });
  const box = screen.getByRole("textbox");
  expect(head.compareDocumentPosition(panel) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(panel.compareDocumentPosition(box) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

});

// #91: an empty scene opened for an adapted greeting carries the greeting on
// its stamp, and the scene load is what hands it to the opener block -- so a
// reload, not just the chooser's handoff, still offers the adaptation.
test("an empty scene started from a greeting hands that greeting to the setup panel", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(
    { meta: { id: "s1", title: "Old", greeting: "reck" }, messages: [] });
  renderCampaign();
  expect(await screen.findByTestId("cast-panel")).toHaveAttribute("data-greeting", "reck");
});

test("an empty scene with no greeting hands none to the setup panel", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [] });
  renderCampaign();
  expect(await screen.findByTestId("cast-panel")).toHaveAttribute("data-greeting", "");
});

test("editing a message saves and reloads", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [{ role: "assistant", content: "hi" }] });
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
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old" },
    messages: [{ role: "assistant", content: "The room is cold." }] });
  renderCampaign();
  await screen.findByText("The room is cold.");
  fireEvent.click(screen.getAllByTitle("Insert an image")[0]);
  const picker = await screen.findByTestId("image-picker");
  expect(JSON.parse(picker.getAttribute("data-target")!))
    .toEqual({ kind: "campaign", name: "Grimoire" });
});

test("an actor post's picker names the actor and the version the roster locked", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getCast).mockResolvedValue([
    { kind: "characters", id: "seraphine", role: "npc", name: "Seraphine Vale" },
  ]);
  vi.mocked(api.listAppearances).mockResolvedValue(fixture([
    { kind: "characters", id: "seraphine", version: "v1", role: "npc", scenes: ["s1"] },
  ]));
  vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old" },
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
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getCast).mockResolvedValue([
    { kind: "pcs", id: "yara", role: "player", name: "Yara" },
  ]);
  vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old" },
    messages: [{ role: "user", content: "Hello.", speaker: "Yara" }] });
  renderCampaign();
  await screen.findByText("Hello.");
  fireEvent.click(screen.getAllByTitle("Insert an image")[0]);
  expect(JSON.parse((await screen.findByTestId("image-picker")).getAttribute("data-target")!))
    .toEqual({ kind: "campaign", name: "Yara" });
});

test("picking an image opens the post for editing with the reference appended", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old" },
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
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "assistant",
      content: "The coast.\n\n![coastline](/api/campaigns/run/images/coastline)" }] });
  renderCampaign();
  const img = await within(await screen.findByTestId("stream")).findByAltText("coastline");
  expect(img.getAttribute("src")).toBe("/api/campaigns/run/images/coastline");
});

test("a dice-roll line is offered no image button, for the reason it is offered no Edit", async () => {
  // Its text has to stay in lockstep with an immutable rolls.json entry, and an
  // insert rewrites the post.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [
    { role: "user", content: "hi" },
    { role: "assistant", content: "🎲 2d6 = 7", speaker: "⁣Roll" }] });
  renderCampaign();
  await screen.findByText("🎲 2d6 = 7");
  expect(screen.getAllByTitle("Insert an image")).toHaveLength(1);   // the user post only
  expect(screen.getAllByTitle("Edit message")).toHaveLength(1);
});
