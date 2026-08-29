import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

vi.mock("../api/client", () => ({
  api: { getCampaign: vi.fn(), listScenes: vi.fn(), getShell: vi.fn(),
         deleteScene: vi.fn(),
         // The deferred read: spend arrives after the list is on screen.
         getCampaignSceneCosts: vi.fn() },
}));

// The importer is a three-step walk with its own suite; here it only has to
// be reachable from the header and to report back the way it ends.
vi.mock("../components/SceneImport", () => ({
  SceneImport: ({ onBack, onImported }: any) => (
    <div data-testid="scene-import">
      <button onClick={() => onImported("s8")}>stub-import</button>
      <button onClick={() => onBack()}>stub-import-close</button>
    </div>
  ),
}));

// The chooser is driven end-to-end by its own suite; here it only has to be
// reachable, and to report back the two ways it can end.
vi.mock("../components/NewSceneChooser", () => ({
  NewSceneChooser: ({ onClose, onCreated }: any) => (
    <div data-testid="scene-chooser">
      {/* Two picks, because the premise is the second argument and only some
          drafts carry one: a greeting hands over nothing, and a blank scene
          must not arrive somewhere with a premise it never had. */}
      <button onClick={() => onCreated("s9", "A debt-collector arrives.")}>stub-pick</button>
      <button onClick={() => onCreated("s9")}>stub-pick-blank</button>
      <button onClick={() => onClose()}>stub-close</button>
    </div>
  ),
}));

import { api } from "../api/client";
import ScenesView from "./ScenesView";

const META = { id: "run", name: "Run One", world: "w", created: "", updated: "",
               scenes: 3, last_scene: "", absorbed: 1 };

const scene = (over: Record<string, unknown>) => ({
  id: "001--a", title: "A", model: "", created: "", updated: "", date: "",
  place: "", done: false, ...over,
});

function shell(pending: { sid: string; proposals: number }[] = [],
               open: { sid: string; title: string; turns: number | null }[] = []) {
  return {
    campaigns: 1, todo: null,
    campaign: {
      id: "run", name: "Run One", world_name: "Saltmarch", scenes: 3, open,
      ledger_open: 0, sheets: null, unreviewed: 0, pending,
      images_undescribed: null,
    },
  };
}

/** One row of the deferred per-scene cost read. */
function costRow(scene: string, over: Record<string, unknown> = {}) {
  return {
    scene, title: "", created: "", updated: "", first_ts: "", last_ts: "",
    missing: false, calls: 2, errors: 0, prompt_tokens: 0,
    completion_tokens: 0, total_tokens: 0, cache_read_tokens: 0,
    cache_write_tokens: 0, cost_usd: 0.41, estimated_usd: 0, modelled_usd: 0,
    priced_calls: 2, unpriced_calls: 0, subscription_calls: 0,
    modelled_calls: 0, unmetered_calls: 0, duration_ms: 0, ...over,
  };
}

function costs(scenes: ReturnType<typeof costRow>[]) {
  return { campaign: "run", since: "", until: "", generated_at: "",
           order: "recent", totals: costRow(""),
           scenes, listed: scenes.length, truncated: false };
}

/** Renders whatever premise rode the navigation, so a test can see what the
 *  page the reader lands on will have to seed its opener box from. */
function Landed() {
  const seed = (useLocation().state as { seedPrompt?: string } | null)?.seedPrompt;
  return <div data-testid="play">{seed ? `seed:${seed}` : "seed:none"}</div>;
}

function renderScenes() {
  return render(
    <MemoryRouter initialEntries={["/campaigns/run/scenes"]}>
      <Routes>
        <Route path="/campaigns/:cid/scenes" element={<ScenesView />} />
        <Route path="/campaigns/:cid/scenes/:sid" element={<Landed />} />
      </Routes>
    </MemoryRouter>);
}

beforeEach(() => {
  (api.deleteScene as any).mockReset().mockResolvedValue({ ok: true });
  (api.getCampaign as any).mockResolvedValue({ meta: META, body: "" });
  (api.getShell as any).mockResolvedValue(shell());
  (api.getCampaignSceneCosts as any).mockResolvedValue(costs([]));
  (api.listScenes as any).mockResolvedValue([
    scene({ id: "003--third", title: "The third", date: "Day 41", place: "The weir" }),
    scene({ id: "002--second", title: "The second", done: true }),
    scene({ id: "001--first", title: "The first", done: true }),
  ]);
});

test("every scene is listed with its number from its own id", async () => {
  renderScenes();
  await screen.findByText("The third");
  // Story order comes off the id, never the list position: `list_scenes` sorts
  // by `updated`, so editing an early scene would renumber the whole campaign.
  const rows = screen.getAllByRole("listitem");
  expect(within(rows[0]).getByText("3")).toBeInTheDocument();
  expect(within(rows[2]).getByText("1")).toBeInTheDocument();
});

test("the eyebrow names the campaign, what this list is, and how much is open", async () => {
  // The design's eyebrow is richer than the bare campaign name: it also says
  // what kind of list this is and how much of it still needs playing.
  renderScenes();
  await screen.findByText("The third");
  expect(screen.getByText("Run One · every scene, newest first · 1 open"))
    .toBeInTheDocument();
});

test("the eyebrow drops the open count rather than claiming zero", async () => {
  // Joining with a filter means a part with nothing to say disappears
  // instead of printing "0 open" -- matching how the hub's eyebrow behaves.
  (api.listScenes as any).mockResolvedValue([
    scene({ id: "001--first", title: "The first", done: true }),
  ]);
  renderScenes();
  await screen.findByText("The first");
  expect(screen.getByText("Run One · every scene, newest first")).toBeInTheDocument();
});

test("the action names what to do, and it is not the same verb for every state", async () => {
  (api.getShell as any).mockResolvedValue(shell([{ sid: "002--second", proposals: 4 }]));
  renderScenes();
  await screen.findByText("The third");
  const rows = screen.getAllByRole("listitem");
  // Open, unreviewed and finished are three different things to do next.
  expect(within(rows[0]).getByText("Open →")).toBeInTheDocument();
  expect(within(rows[1]).getByText("Wrap up →")).toBeInTheDocument();
  expect(within(rows[2]).getByText("Read →")).toBeInTheDocument();
});

test("an absorbed scene holding a review is not filed away as finished", async () => {
  // "Absorbed" and "absorbed but nobody decided its proposals" are different
  // states, and calling both of them done loses the one that needs doing.
  (api.getShell as any).mockResolvedValue(shell([{ sid: "002--second", proposals: 4 }]));
  renderScenes();
  await screen.findByText("The second");
  const row = screen.getAllByRole("listitem")[1];
  expect(within(row).getByText("4 unreviewed")).toBeInTheDocument();
});

test("the chip carries the proposal count, not just that some are waiting", async () => {
  // The shell payload already has the count per pending scene; a bare
  // "unreviewed" would make the reader open the scene to learn whether it's
  // one proposal or thirty.
  (api.getShell as any).mockResolvedValue(shell([{ sid: "002--second", proposals: 12 }]));
  renderScenes();
  await screen.findByText("The second");
  const row = screen.getAllByRole("listitem")[1];
  expect(within(row).getByText("12 unreviewed")).toBeInTheDocument();
});

test("when and where come off the row without a second read", async () => {
  renderScenes();
  await screen.findByText("The third");
  expect(screen.getByText(/Day 41 · The weir/)).toBeInTheDocument();
});

test("filtering narrows the list and says how much of it is showing", async () => {
  renderScenes();
  await screen.findByText("The third");
  fireEvent.change(screen.getByLabelText(/filter scenes by title/i),
                   { target: { value: "third" } });
  expect(screen.getByText("1 of 3")).toBeInTheDocument();
  expect(screen.queryByText("The second")).not.toBeInTheDocument();
});

test("a filter matching nothing is not an empty campaign", async () => {
  renderScenes();
  await screen.findByText("The third");
  fireEvent.change(screen.getByLabelText(/filter scenes by title/i),
                   { target: { value: "zzz" } });
  expect(screen.getByText(/no scene here matches that/i)).toBeInTheDocument();
  expect(screen.queryByText(/no scenes yet/i)).not.toBeInTheDocument();
});

test("an empty campaign says so and offers the first scene", async () => {
  // This page is where a campaign starts now: the play view mounts on a scene,
  // so there is no composer to type the first one into.
  (api.listScenes as any).mockResolvedValue([]);
  renderScenes();
  expect(await screen.findByText(/no scenes yet/i)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /\+ new scene/i })).toBeInTheDocument();
});

test("+ New scene opens the chooser without creating anything", async () => {
  renderScenes();
  await screen.findByText("The third");
  fireEvent.click(screen.getByRole("button", { name: /\+ new scene/i }));
  expect(await screen.findByTestId("scene-chooser")).toBeInTheDocument();
});

test("a chooser pick opens the scene it made", async () => {
  renderScenes();
  await screen.findByText("The third");
  fireEvent.click(screen.getByRole("button", { name: /\+ new scene/i }));
  fireEvent.click(await screen.findByText("stub-pick"));
  await waitFor(() => expect(screen.getByTestId("play")).toBeInTheDocument());
});

test("closing the chooser leaves the list alone", async () => {
  renderScenes();
  await screen.findByText("The third");
  const readsBefore = (api.listScenes as any).mock.calls.length;
  fireEvent.click(screen.getByRole("button", { name: /\+ new scene/i }));
  fireEvent.click(await screen.findByText("stub-close"));
  expect(screen.queryByTestId("scene-chooser")).toBeNull();
  expect(screen.queryByTestId("play")).toBeNull();
  expect((api.listScenes as any).mock.calls.length).toBe(readsBefore);
});

test("a failed read is not an empty campaign", async () => {
  (api.listScenes as any).mockRejectedValue(new Error("offline"));
  renderScenes();
  expect(await screen.findByText(/could not be read/i)).toBeInTheDocument();
  expect(screen.queryByText(/no scenes yet/i)).not.toBeInTheDocument();
});


// ---- the two columns that are not frontmatter ----

test("an open scene with turns says Resume, one with none says Open", async () => {
  // The same click either way. What the reader gets is knowing which of the
  // two they are about to do: a conversation to come back to, or a start.
  (api.listScenes as any).mockResolvedValue([
    scene({ id: "002--b", title: "Underway", done: false }),
    scene({ id: "001--a", title: "Not started", done: false }),
  ]);
  (api.getShell as any).mockResolvedValue(shell([], [
    { sid: "002--b", title: "Underway", turns: 5 },
    { sid: "001--a", title: "Not started", turns: 0 },
  ]));
  renderScenes();

  await screen.findByText("Underway");
  expect(screen.getByRole("link", { name: "Resume →" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Open →" })).toBeInTheDocument();
});

test("a turn count nobody could read is not zero turns", async () => {
  // `turns: null` is "the transcript could not be read". Rendering that as a
  // scene with no turns would offer Open over a scene mid-conversation.
  (api.listScenes as any).mockResolvedValue([
    scene({ id: "001--a", title: "Unreadable", done: false }),
  ]);
  (api.getShell as any).mockResolvedValue(shell([], [
    { sid: "001--a", title: "Unreadable", turns: null },
  ]));
  renderScenes();

  await screen.findByText("Unreadable");
  expect(screen.getByRole("link", { name: "Open →" })).toBeInTheDocument();
  expect(screen.queryByText(/turns$/)).not.toBeInTheDocument();
});

test("spend lands after the list and never before it", async () => {
  (api.listScenes as any).mockResolvedValue([
    scene({ id: "001--a", title: "A scene", done: true }),
  ]);
  (api.getCampaignSceneCosts as any).mockResolvedValue(
    costs([costRow("001--a", { cost_usd: 0.41 })]));
  renderScenes();

  // The title does not wait on the ledger.
  await screen.findByText("A scene");
  expect(await screen.findByText("$0.41")).toBeInTheDocument();
});

test("a scene the ledger cannot price says so rather than showing $0.00", async () => {
  (api.listScenes as any).mockResolvedValue([
    scene({ id: "001--a", title: "A scene", done: true }),
  ]);
  (api.getCampaignSceneCosts as any).mockResolvedValue(
    costs([costRow("001--a", { cost_usd: 0, priced_calls: 0, unpriced_calls: 2 })]));
  renderScenes();

  expect(await screen.findByText("not reported")).toBeInTheDocument();
  expect(screen.queryByText("$0.00")).not.toBeInTheDocument();
});

test("a ledger read that failed costs the figures and nothing else", async () => {
  (api.listScenes as any).mockResolvedValue([
    scene({ id: "001--a", title: "A scene", done: true }),
  ]);
  (api.getCampaignSceneCosts as any).mockRejectedValue(new Error("busy"));
  renderScenes();

  // The list is the page; the money is a column on it.
  expect(await screen.findByText("A scene")).toBeInTheDocument();
  expect(screen.queryByText(/could not/i)).not.toBeInTheDocument();
});

// ---- importing a transcript ----

test("Import a transcript is a header action, not a click deeper in", async () => {
  renderScenes();
  fireEvent.click(await screen.findByRole("button", { name: /import a transcript/i }));

  expect(screen.getByTestId("scene-import")).toBeInTheDocument();
});

test("an imported transcript opens the scene it made", async () => {
  renderScenes();
  fireEvent.click(await screen.findByRole("button", { name: /import a transcript/i }));
  fireEvent.click(screen.getByText("stub-import"));

  expect(await screen.findByTestId("play")).toBeInTheDocument();
});

// ---- the wrap-up address ----

test("an unreviewed scene's row goes to the wrap-up, not the transcript", async () => {
  // The action says "Wrap up →", so it must land on the wrap-up.
  (api.listScenes as any).mockResolvedValue([
    scene({ id: "001--a", title: "Absorbed", done: true }),
  ]);
  (api.getShell as any).mockResolvedValue(shell([{ sid: "001--a", proposals: 8 }]));
  renderScenes();

  const act = await screen.findByRole("link", { name: "Wrap up →" });
  expect(act).toHaveAttribute("href", "/campaigns/run/scenes/001--a/wrap-up");
});

test("every row carries a delete naming its own scene", async () => {
  // Named per row rather than a bare "Delete": three ✕ that read the same are
  // three buttons a screen reader cannot tell apart, and this is the one
  // control on the page you do not get to press twice.
  renderScenes();
  await screen.findByText("The third");
  const rows = screen.getAllByRole("listitem");
  expect(within(rows[0]).getByRole("button", { name: "Delete The third" }))
    .toBeInTheDocument();
  expect(within(rows[2]).getByRole("button", { name: "Delete The first" }))
    .toBeInTheDocument();
});

test("deleting a scene confirms, deletes, and re-reads the list", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  renderScenes();
  await screen.findByText("The third");
  (api.listScenes as any).mockResolvedValue([
    scene({ id: "002--second", title: "The second", done: true }),
    scene({ id: "001--first", title: "The first", done: true }),
  ]);

  fireEvent.click(screen.getByRole("button", { name: "Delete The third" }));

  await waitFor(() => expect(api.deleteScene).toHaveBeenCalledWith("run", "003--third"));
  // Re-read rather than spliced out of local state: the delete cascades into
  // records this list shows (the absorbed chip, the count in the eyebrow), so
  // the server's answer is the only one worth drawing.
  await waitFor(() => expect(screen.queryByText("The third")).toBeNull());
});

test("a declined confirm deletes nothing", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(false);
  renderScenes();
  await screen.findByText("The third");
  fireEvent.click(screen.getByRole("button", { name: "Delete The third" }));
  expect(api.deleteScene).not.toHaveBeenCalled();
  expect(screen.getByText("The third")).toBeInTheDocument();
});

test("a scene a turn is holding keeps its row and says why", async () => {
  // 409 `scene_busy`. This list cannot know a run is live -- the play page
  // disables its own delete from state this page does not have -- so the
  // refusal has to arrive as an answer and be shown, not swallowed.
  vi.spyOn(window, "confirm").mockReturnValue(true);
  (api.deleteScene as any).mockRejectedValue({
    kind: "scene_busy", run_id: "r1",
    detail: "a turn or review is running on this scene; stop it first" });
  renderScenes();
  await screen.findByText("The third");

  fireEvent.click(screen.getByRole("button", { name: "Delete The third" }));

  expect(await screen.findByText(/a turn or review is running on this scene/i))
    .toBeInTheDocument();
  expect(screen.getByText("The third")).toBeInTheDocument();
});

// The bug behind "picking a suggestion still leaves the opener box empty":
// this page created the scene and navigated away without the premise, so the
// page that owns the opener box had nothing to seed it with. The in-campaign
// chooser had been wired for this since #90; this entry point never was.
test("a premise picked here rides the navigation to the scene", async () => {
  renderScenes();
  fireEvent.click(await screen.findByRole("button", { name: /\+ new scene/i }));
  fireEvent.click(screen.getByText("stub-pick"));
  const landed = await screen.findByTestId("play");
  expect(landed).toHaveTextContent("seed:A debt-collector arrives.");
});

test("a draft that carries no premise arrives without one", async () => {
  renderScenes();
  fireEvent.click(await screen.findByRole("button", { name: /\+ new scene/i }));
  fireEvent.click(screen.getByText("stub-pick-blank"));
  const landed = await screen.findByTestId("play");
  expect(landed).toHaveTextContent("seed:none");
});
