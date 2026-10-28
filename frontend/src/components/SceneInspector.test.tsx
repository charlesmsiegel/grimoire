import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { api } from "../api/client";
import { fixture } from "../testkit/fixture";
import { EMPTY_BRIEFING, renderInspector, resetSceneInspectorMocks } from "../testkit/sceneInspectorHarness";
import { SceneInspector } from "./SceneInspector";
vi.mock("../api/client", async () => (await import("../testkit/sceneInspectorMocks")).sceneInspectorApiMock());
vi.mock("../api/models", async () => (await import("../testkit/sceneInspectorMocks")).sceneInspectorModelsMock());
vi.mock("./ResponseTargetsPicker", async () => (await import("../testkit/sceneInspectorMocks")).responseTargetsMock());
vi.mock("./AuthorsNotesPanel", async () => (await import("../testkit/sceneInspectorMocks")).authorsNotesMock());

beforeEach(resetSceneInspectorMocks);

test("lists cast names and the location and a context section", async () => {
  renderInspector();
  await screen.findByText("Seraphine");
  await screen.findByText("The Crypt");
  await screen.findByText(/World info/);
});

test("suggested cast: mid-scene mentions can be seated or dismissed from the inspector", async () => {
  // The scan reads the cards of who is already cast, so it only pays off once
  // the scene has a cast — which is exactly where CastPanel has stopped
  // rendering (#7, #96).
  vi.mocked(api.getSuggestions).mockResolvedValue([
    { character: "mara", name: "Mara", mentioned_by: ["seraphine"] },
  ]);
  const onSceneChanged = vi.fn();
  renderInspector(onSceneChanged);
  await screen.findByText("Suggested cast");
  expect(screen.getByText("mentioned by Seraphine")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "Add Mara to the scene" }));
  await waitFor(() => expect(api.addToCast).toHaveBeenCalledWith(
    "c", "s", { kind: "characters", id: "mara", role: "npc" }));
  await waitFor(() => expect(onSceneChanged).toHaveBeenCalled());
});

test("suggested cast: dismissing one drops it from this scene", async () => {
  vi.mocked(api.getSuggestions)
    .mockResolvedValueOnce([{ character: "mara", name: "Mara", mentioned_by: ["seraphine"] }])
    .mockResolvedValue([]);
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: "Dismiss Mara" }));
  await waitFor(() => expect(api.dismissSuggestion).toHaveBeenCalledWith("c", "s", "mara"));
  await waitFor(() => expect(screen.queryByText("Suggested cast")).toBeNull());
});

test("clicking a cast row opens the drawer", async () => {
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /^Seraphine/ }));
  await waitFor(() => expect(api.getCastDetail).toHaveBeenCalledWith("c", "s", "characters", "seraphine"));
  await screen.findByText("keeper");
});

test("cast rows show portraits with roster versions and role chips", async () => {
  vi.mocked(api.getCast).mockResolvedValue([
    { kind: "characters", id: "seraphine", role: "npc", name: "Seraphine" },
    { kind: "pcs", id: "yara", role: "player", name: "Yara" },
  ]);
  vi.mocked(api.listAppearances).mockResolvedValue(fixture([
    { kind: "characters", id: "seraphine", version: "v2", role: "npc", scenes: ["s"] },
  ]));
  vi.mocked(api.listPCs).mockResolvedValue([
    { id: "yara", name: "Yara", tags: [], default_version: "default", versions: [] }]);
  renderInspector();
  await screen.findByText("Seraphine");
  expect(screen.getByAltText("Seraphine portrait")).toBeInTheDocument(); // roster version found
  expect(screen.getByText("Y")).toBeInTheDocument();                     // no roster row: initials
  expect(screen.getByText("player", { selector: ".role-chip" })).toBeInTheDocument();
  expect(screen.getByText("npc", { selector: ".role-chip" })).toBeInTheDocument();
});

test("a PC in the roster gets a portrait like anyone else", async () => {
  // The roster lookup used to be gated on `kind === "characters"`, so a PC fell
  // back to initials even with a locked version to build a URL from (#219).
  vi.mocked(api.getCast).mockResolvedValue([
    { kind: "pcs", id: "yara", role: "player", name: "Yara" }]);
  vi.mocked(api.listAppearances).mockResolvedValue(fixture([
    { kind: "pcs", id: "yara", version: "v3", role: "player", scenes: ["s"] }]));
  vi.mocked(api.listPCs).mockResolvedValue([
    { id: "yara", name: "Yara", tags: [], default_version: "default", versions: [] }]);
  renderInspector();
  const portrait = await screen.findByAltText("yara portrait");
  // A 34px row: the face bucket, which a DPR-3 screen still fills sharply.
  expect(portrait.getAttribute("src")).toBe("/img/pcs/yara/v3?w=256");
});

test("a cast row's portrait carries the roster's avatar token", async () => {
  // Without it the row is served no-cache and revalidated on every open.
  vi.mocked(api.getCast).mockResolvedValue([
    { kind: "characters", id: "seraphine", role: "npc", name: "Seraphine" }]);
  vi.mocked(api.listAppearances).mockResolvedValue(fixture([
    { kind: "characters", id: "seraphine", version: "v2", role: "npc", scenes: ["s"],
      avatar_v: "1a-2b" }]));
  renderInspector();
  const portrait = await screen.findByAltText("Seraphine portrait");
  expect(portrait.getAttribute("src")).toBe("/img/characters/seraphine/v2?w=256&v=1a-2b");
});

test("location with a primary image renders a clickable thumbnail", async () => {
  vi.mocked(api.listEntityImages).mockResolvedValue(fixture([{ name: "avatar", ext: "png" }]));
  renderInspector();
  const thumb = await screen.findByAltText("The Crypt");
  expect(thumb.closest("button")).not.toBeNull();
  // The original: the panel draws this at the main column's full width, past
  // what the largest `?w=` bucket fills, so a downscale here is a blur.
  expect(thumb.getAttribute("src")).toBe("/loc-img");
  expect(thumb.getAttribute("srcset")).toBeNull();
  await waitFor(() => expect(api.listEntityImages).toHaveBeenCalledWith(
    { kind: "campaign", id: "c" }, "locations", "crypt"));
});

test("location without an image keeps the text row", async () => {
  renderInspector();
  const row = await screen.findByRole("button", { name: "The Crypt" });
  expect(row.querySelector("img")).toBeNull();
});

test("shows the story-so-far recap", async () => {
  renderInspector();
  await screen.findByText("Story so far");
  await screen.findByText("They first met.");
});

test("switching scenes refreshes the story recap before the transcript refresh beat", async () => {
  const changed = () => {};
  const view = render(<MemoryRouter><SceneInspector cid="c" sid="s" refreshKey={0}
    onSceneChanged={changed} /></MemoryRouter>);
  await screen.findByText("They first met.");
  expect(api.getChronicle).toHaveBeenCalledTimes(1);

  // Scene selection can precede (or outlive a failed) transcript load, so the
  // refresh counter alone cannot tell this campaign read when to update.
  vi.mocked(api.getChronicle).mockResolvedValue(fixture([
    { id: "s", one_line: "They reached the docks.", summary: "", keywords: [],
      cast: [], location: "", date: "", absorbed: "t" },
  ]));
  view.rerender(<MemoryRouter><SceneInspector cid="c" sid="next" refreshKey={0}
    onSceneChanged={changed} /></MemoryRouter>);
  await screen.findByText("They reached the docks.");
  expect(api.getChronicle).toHaveBeenCalledTimes(2);
  expect(screen.queryByText("They first met.")).toBeNull();
});

test("Move to sets the scene location, reloads it, and refreshes the stream", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([
    { id: "crypt", name: "The Crypt" }, { id: "docks", name: "The Docks" }]);
  const onSceneChanged = vi.fn();
  renderInspector(onSceneChanged);
  await screen.findByText("The Crypt");
  fireEvent.change(await screen.findByLabelText(/move to location/i), { target: { value: "docks" } });
  fireEvent.click(screen.getByRole("button", { name: /move to/i }));
  await waitFor(() => expect(api.setSceneLocation).toHaveBeenCalledWith("c", "s", "docks"));
  await waitFor(() => expect(onSceneChanged).toHaveBeenCalled());
  expect(vi.mocked(api.getSceneLocation).mock.calls.length).toBeGreaterThan(1); // reloaded after the move
});

test("Move to is disabled until a location is chosen", async () => {
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "docks", name: "The Docks" }]);
  renderInspector();
  await screen.findByText("The Crypt");
  expect(await screen.findByRole("button", { name: /move to/i })).toBeDisabled();
});

test("offscreen scene shows the offscreen side-section", async () => {
  render(<SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} pcless />);
  await screen.findByText("Offscreen scene");
  expect(screen.getByText(/no player character/i)).toBeInTheDocument();
});

test("mounts the response preset picker scoped to this scene", async () => {
  renderInspector();
  const picker = await screen.findByTestId("response-preset-picker");
  expect(picker).toHaveAttribute("data-scope", "scene");
  expect(picker).toHaveAttribute("data-cid", "c");
  expect(picker).toHaveAttribute("data-sid", "s");
});

test("the Author's notes section counts the notes applying next turn", async () => {
  vi.mocked(api.getAuthorsNotesNext).mockResolvedValue({
    turn: 3, count: 2,
    notes: [{ level: "campaign", depth: 4, every: 1, applies: true },
            { level: "character", depth: 0, every: 1, applies: true,
              ref: "characters:seraphine", name: "Seraphine" }],
  });
  renderInspector();
  expect(await screen.findByLabelText("Notes applying next turn")).toHaveTextContent("2");
  const header = screen.getByRole("button", { name: /author's notes/i });
  expect(header).toHaveAttribute("aria-expanded", "false");
  fireEvent.click(header);
  const panel = await screen.findByTestId("authors-notes-panel");
  expect(panel).toHaveAttribute("data-cid", "c");
  expect(panel).toHaveAttribute("data-sid", "s");
});

test("no notes applying next turn draws no count", async () => {
  renderInspector();
  await screen.findByRole("button", { name: /author's notes/i });
  expect(screen.queryByLabelText("Notes applying next turn")).not.toBeInTheDocument();
});

test("clicking a section header collapses its body and toggles aria-expanded", async () => {
  renderInspector();
  await screen.findByText("They first met.");
  const header = screen.getByRole("button", { name: /story so far/i });
  expect(header).toHaveAttribute("aria-expanded", "true");
  fireEvent.click(header);
  expect(header).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByText("They first met.")).not.toBeInTheDocument();
  fireEvent.click(header);
  expect(header).toHaveAttribute("aria-expanded", "true");
  await screen.findByText("They first met.");
});

test("section collapse state persists across a remount", async () => {
  const { unmount } = render(<SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  await screen.findByText("They first met.");
  fireEvent.click(screen.getByRole("button", { name: /story so far/i }));
  expect(screen.queryByText("They first met.")).not.toBeInTheDocument();
  expect(JSON.parse(localStorage.getItem("grimoire.inspector.sections")!)).toEqual({ story: true });
  unmount();

  render(<SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  await screen.findByText("Active characters"); // sanity: the inspector rendered
  expect(screen.queryByText("They first met.")).not.toBeInTheDocument(); // stayed collapsed
});

test("the Campaign clock section is shut until asked for, then mounts the panel", async () => {
  // The one campaign-scoped section on a scene-scoped rail (#100): collapsed by
  // default so it does not push the scene's own state down, and — because it is
  // collapsed — the panel must not fetch the clock until a reader opens it.
  renderInspector();
  await screen.findByText("Active characters");
  expect(api.getCampaignClock).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: /campaign clock/i }));
  expect(await screen.findByText(/Now: 1 May 2026/)).toBeInTheDocument();
  expect(api.getCampaignClock).toHaveBeenCalledWith("c");
});

test("the Cost section is shut until asked for, then mounts the breakdown", async () => {
  // Sibling to Context (#153) and collapsed for the same reason the clock is:
  // cost is a question a reader comes to ask, and a shut section must not spend
  // a ledger scan per scene open answering one nobody asked.
  renderInspector();
  await screen.findByText("Active characters");
  expect(api.getSceneUsage).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: /^cost$/i }));
  expect(await screen.findByText("Nothing metered in this scene yet.")).toBeInTheDocument();
  expect(api.getSceneUsage).toHaveBeenCalledWith("c", "s");
});

test("removing a cast member calls removeFromCast, reloads cast, and notifies the scene changed", async () => {
  const onSceneChanged = vi.fn();
  render(<SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={onSceneChanged} />);
  await screen.findByText("Seraphine");
  fireEvent.click(screen.getByRole("button", { name: /remove seraphine from scene/i }));
  await waitFor(() => expect(api.removeFromCast).toHaveBeenCalledWith("c", "s", "characters", "seraphine"));
  await waitFor(() => expect(onSceneChanged).toHaveBeenCalled());
  expect(api.getCast).toHaveBeenCalledTimes(2); // initial load + reload after remove
});

test("adding a character posts kind + id + role, reloads cast, and notifies the scene changed", async () => {
  vi.mocked(api.listCharacters).mockResolvedValue([
    { id: "seraphine", name: "Seraphine", default_version: "default", versions: [] },
    { id: "mara", name: "Mara", default_version: "default", versions: [] },
  ]);
  const onSceneChanged = vi.fn();
  render(<SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={onSceneChanged} />);
  await screen.findByRole("option", { name: "Mara" });
  fireEvent.change(screen.getByLabelText("Character or PC to add"), { target: { value: "mara" } });
  fireEvent.change(screen.getByLabelText("Role for new cast member"), { target: { value: "player" } });
  fireEvent.click(screen.getByRole("button", { name: "+ Add" }));
  await waitFor(() => expect(api.addToCast).toHaveBeenCalledWith(
    "c", "s", { kind: "characters", id: "mara", role: "player" }));
  await waitFor(() => expect(onSceneChanged).toHaveBeenCalled());
});

test("adding a PC omits the role picker and forces role=player", async () => {
  vi.mocked(api.listCampaignPCs).mockResolvedValue([
    { id: "elara", name: "Elara", tags: [], default_version: "default", versions: [] }]);
  render(<SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  fireEvent.change(await screen.findByLabelText("Cast kind to add"), { target: { value: "pcs" } });
  await screen.findByRole("option", { name: "Elara" });
  expect(screen.queryByLabelText("Role for new cast member")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Character or PC to add"), { target: { value: "elara" } });
  fireEvent.click(screen.getByRole("button", { name: "+ Add" }));
  await waitFor(() => expect(api.addToCast).toHaveBeenCalledWith(
    "c", "s", { kind: "pcs", id: "elara", role: "player" }));
});

test("offscreen scene hides the kind and role pickers, forcing npc characters only", async () => {
  vi.mocked(api.getCast).mockResolvedValue([]);
  render(<SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} pcless />);
  await screen.findByLabelText("Character or PC to add");
  expect(screen.queryByLabelText("Cast kind to add")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Role for new cast member")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Character or PC to add"), { target: { value: "seraphine" } });
  fireEvent.click(screen.getByRole("button", { name: "+ Add" }));
  await waitFor(() => expect(api.addToCast).toHaveBeenCalledWith(
    "c", "s", { kind: "characters", id: "seraphine", role: "npc" }));
});

test("a failed add surfaces the error banner instead of silently failing", async () => {
  vi.mocked(api.addToCast).mockRejectedValue({ detail: "already cast" });
  vi.mocked(api.listCharacters).mockResolvedValue([
    { id: "mara", name: "Mara", default_version: "default", versions: [] }]);
  render(<SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  await screen.findByRole("option", { name: "Mara" });
  fireEvent.change(screen.getByLabelText("Character or PC to add"), { target: { value: "mara" } });
  fireEvent.click(screen.getByRole("button", { name: "+ Add" }));
  await screen.findByText("already cast");
});

test("a failed remove surfaces the error banner instead of silently failing", async () => {
  vi.mocked(api.removeFromCast).mockRejectedValue({ detail: "actor kind not found" });
  renderInspector();
  await screen.findByText("Seraphine");
  fireEvent.click(screen.getByRole("button", { name: /remove seraphine from scene/i }));
  await screen.findByText("actor kind not found");
});

// ---- the pre-scene briefing (#118) ----------------------------------------

const BRIEFING = {
  focus: ["Winifred Vance"],
  plot: [{ id: "the-ledger", title: "Find the ledger", status: "open",
           last_scene: "s0", latest_beat: "She named it aloud.",
           involves: ["Winifred Vance"] },
         { id: "the-tide", title: "The tide turns", status: "advanced",
           last_scene: "s0", latest_beat: "", involves: [] }],
  commitments: [{ id: "the-deadline", title: "Seraphine's midnight deadline",
                  kind: "threat", status: "open", due: "midnight",
                  last_scene: "s0", latest_beat: "Sworn in front of her.",
                  involves: ["Winifred Vance"] }],
  relationships: ["Winifred Vance distrusts Seraphine."],
  last_time: { id: "s0", one_line: "They argued.", title: "First Night",
               date: "5 Harvestmoon" },
};

function renderWithBriefing(posts?: number) {
  vi.mocked(api.sceneBriefing).mockResolvedValue(BRIEFING);
  render(<SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}}
                         posts={posts} />);
}

test("the briefing lists what is open, what came before, and who it involves", async () => {
  renderWithBriefing();
  await screen.findByText("Find the ledger");
  expect(api.sceneBriefing).toHaveBeenCalledWith("c", "s");
  expect(screen.getByText("Seraphine's midnight deadline")).toBeInTheDocument();
  expect(screen.getByText("due midnight")).toBeInTheDocument();
  expect(screen.getByText("They argued.")).toBeInTheDocument();
  expect(screen.getByText(/First Night/)).toBeInTheDocument();
  expect(screen.getByText("Winifred Vance distrusts Seraphine.")).toBeInTheDocument();
  // The flag names who, so a scene with two players can tell whose thread it is.
  expect(screen.getAllByText("involves Winifred Vance")).toHaveLength(2);
});

test("an unflagged row still lists — narrowing is ordering, not filtering", async () => {
  renderWithBriefing();
  await screen.findByText("The tide turns");
});

test("the briefing section is absent when there is nothing to brief", async () => {
  render(<SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  await screen.findByText("Seraphine");                  // the rail has loaded...
  expect(screen.queryByText("Briefing")).not.toBeInTheDocument();   // ...without it
});

test("the briefing survives a failed load as the empty state", async () => {
  vi.mocked(api.sceneBriefing).mockRejectedValue(new Error("nope"));
  render(<SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  await screen.findByText("Seraphine");
  expect(screen.queryByText("Briefing")).not.toBeInTheDocument();
});

test("the briefing opens itself on a fresh scene and not on a long one", async () => {
  renderWithBriefing(0);
  await screen.findByText("Find the ledger");
  expect(screen.getByRole("button", { name: /Briefing/ })).toHaveAttribute("aria-expanded", "true");
});

test("a scene already several posts in gets the briefing collapsed", async () => {
  renderWithBriefing(20);
  const head = await screen.findByRole("button", { name: /Briefing/ });
  expect(head).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByText("Find the ledger")).not.toBeInTheDocument();
});

test("an explicit toggle outlives the post count that set the default", async () => {
  renderWithBriefing(20);
  fireEvent.click(await screen.findByRole("button", { name: /Briefing/ }));
  await screen.findByText("Find the ledger");           // opened by hand...
  expect(JSON.parse(localStorage.getItem("grimoire.inspector.sections")!).briefing)
    .toBe(false);                                       // ...and remembered as open
});

test("a briefing never renders under a different campaign's scene of the same id", async () => {
  // The route is /campaigns/:cid with no `key`, so React Router reuses
  // CampaignView across campaigns, and scene ids are per-campaign — so two
  // campaigns sitting on "s" would have matched on sid alone, showing one
  // game's commitments under the other's name (Codex review).
  vi.mocked(api.sceneBriefing).mockResolvedValue(BRIEFING);
  const { rerender } = render(
    <SceneInspector cid="a" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  await screen.findByText("Find the ledger");

  // Campaign b's request never settles, so anything on screen is a's.
  vi.mocked(api.sceneBriefing).mockReturnValue(new Promise(() => {}));
  rerender(<SceneInspector cid="b" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  await waitFor(() =>
    expect(screen.queryByText("Find the ledger")).not.toBeInTheDocument());
});

// Persisted preferences are user-controlled browser data, including old shapes.
test.each(["null", "[]", '"closed"', "42", "true", "{broken", '{"cast":"yes","models":true}'])(
  "malformed section preferences %s keep the cast usable", async (stored) => {
    localStorage.setItem("grimoire.inspector.sections", stored);
    renderInspector();
    await screen.findByRole("button", { name: "Remove Seraphine from scene" });
    fireEvent.click(screen.getByRole("button", { name: /^Active characters/ }));
    expect(screen.queryByRole("button", { name: "Remove Seraphine from scene" })).toBeNull();
  },
);

test("failed actor name lookup falls back to ids without an unhandled rejection", async () => {
  vi.mocked(api.listCharacters).mockRejectedValue(new Error("names offline"));
  vi.mocked(api.listCampaignPCs).mockResolvedValue([
    { id: "mara", name: "Mara", tags: [], default_version: "default", versions: [] },
  ]);
  vi.mocked(api.getCast).mockResolvedValue(fixture([
    { kind: "characters", id: "seraphine", role: "npc" },
    { kind: "pcs", id: "mara", role: "player" },
  ]));
  renderInspector();
  await screen.findByRole("button", { name: "Remove seraphine from scene" });
  expect(screen.getByRole("button", { name: "Pin seraphine in the prompt" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "Pin Mara in the prompt" })).toBeEnabled();
});

test("late previous-campaign character catalog cannot replace the current picker", async () => {
  let finish: (rows: Awaited<ReturnType<typeof api.listCharacters>>) => void = () => {};
  vi.mocked(api.listCharacters).mockImplementation(({ id }) => id === "c"
    ? new Promise((resolve) => { finish = resolve; })
    : Promise.resolve([{ id: "mara", name: "Mara", default_version: "default", versions: [] }]));
  const view = render(<MemoryRouter><SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} /></MemoryRouter>);
  await screen.findByText("The Crypt");
  view.rerender(<MemoryRouter><SceneInspector cid="other" sid="s" refreshKey={0} onSceneChanged={() => {}} /></MemoryRouter>);
  await screen.findByRole("option", { name: "Mara" });
  await act(async () => { finish([{ id: "old", name: "Old choice", default_version: "default", versions: [] }]); });
  expect(screen.getByRole("option", { name: "Mara" })).toBeInTheDocument();
  expect(screen.queryByRole("option", { name: "Old choice" })).toBeNull();
});

test("relationship display keeps duplicate lines and their order after refresh", async () => {
  vi.mocked(api.sceneBriefing).mockResolvedValue({ ...EMPTY_BRIEFING, relationships: ["Mara trusts Seraphine", "Mara trusts Seraphine"] });
  const view = render(<MemoryRouter><SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} /></MemoryRouter>);
  await screen.findAllByText("Mara trusts Seraphine");
  vi.mocked(api.sceneBriefing).mockResolvedValue({ ...EMPTY_BRIEFING, relationships: ["A new bond", "Mara trusts Seraphine", "Mara trusts Seraphine"] });
  view.rerender(<MemoryRouter><SceneInspector cid="c" sid="s" refreshKey={1} onSceneChanged={() => {}} /></MemoryRouter>);
  const inserted = await screen.findByText("A new bond");
  expect(inserted.parentElement?.textContent).toBe("Between themA new bondMara trusts SeraphineMara trusts Seraphine");
});

test("cast picker draft survives collapse and routine refresh", async () => {
  vi.mocked(api.listCharacters).mockResolvedValue([{ id: "mara", name: "Mara", default_version: "default", versions: [] }]);
  const view = render(<MemoryRouter><SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} /></MemoryRouter>);
  await screen.findByRole("option", { name: "Mara" });
  fireEvent.change(screen.getByLabelText("Character or PC to add"), { target: { value: "mara" } });
  fireEvent.click(screen.getByRole("button", { name: /^Active characters/ }));
  view.rerender(<MemoryRouter><SceneInspector cid="c" sid="s" refreshKey={1} onSceneChanged={() => {}} /></MemoryRouter>);
  await waitFor(() => expect(api.getCast).toHaveBeenCalledTimes(2));
  fireEvent.click(screen.getByRole("button", { name: /^Active characters/ }));
  expect(screen.getByLabelText("Character or PC to add")).toHaveValue("mara");
});
