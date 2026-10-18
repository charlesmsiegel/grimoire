import { fixture } from "../testkit/fixture";
import type { ComponentProps } from "react";
import type { ReactNode } from "react";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { Link, MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { useShellPayload } from "../shell/useShellPayload";
import { ShellPayloadProvider } from "../shell/ShellPayloadContext";

vi.mock("../api/client", () => ({
  api: { getCampaign: vi.fn(), listScenes: vi.fn(), getShell: vi.fn(),
         deleteScene: vi.fn(),
         // The deferred reads: spend, place names and month names arrive after
         // the list is on screen.
         getCampaignSceneCosts: vi.fn(), listEntities: vi.fn(), getCalendarMonths: vi.fn() },
}));

// The importer is a three-step walk with its own suite; here it only has to
// be reachable from the header and to report back the way it ends.
vi.mock("../components/SceneImport", () => ({
  SceneImport: ({ onBack, onImported }:
                  ComponentProps<typeof import("../components/SceneImport").SceneImport>) => (
    <div data-testid="scene-import">
      <button onClick={() => onImported("s8")}>stub-import</button>
      <button onClick={() => onBack()}>stub-import-close</button>
    </div>
  ),
}));

// The chooser is driven end-to-end by its own suite; here it only has to be
// reachable, and to report back the two ways it can end.
vi.mock("../components/NewSceneChooser", () => ({
  NewSceneChooser: ({ onClose, onCreated, seed, afterSid }:
                      ComponentProps<typeof import("../components/NewSceneChooser").NewSceneChooser>) => (
    <div data-testid="scene-chooser">
      {/* What the chooser was opened with: a Story Graph handoff's seed, and
          the ranking reference a seeded open has to wait for. */}
      <pre data-testid="seed">{JSON.stringify(seed ?? null)}</pre>
      <pre data-testid="after">{JSON.stringify(afterSid ?? null)}</pre>
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
import type { SceneMeta } from "../api/types";

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

const noop = () => {};

/** The rail's shell read, provided the way `App.Shell` provides it: the list
 *  draws its turn counts and waiting reviews from here rather than reading
 *  them itself, so the `getShell` mock still decides what a row says. */
function WithShell({ children }: { children: ReactNode }) {
  const shell = useShellPayload("/store", "run", noop);
  return <ShellPayloadProvider value={{ ...shell, cid: "run" }}>{children}</ShellPayloadProvider>;
}

/** The history entry's state as the page leaves it, so a test can see that a
 *  handoff was cleared off the entry rather than only ignored. */
function StateProbe() {
  return <pre data-testid="history-state">{JSON.stringify(useLocation().state ?? null)}</pre>;
}

function renderScenes(opts: { state?: unknown } = {}) {
  const entry = "state" in opts
    ? { pathname: "/campaigns/run/scenes", state: opts.state }
    : "/campaigns/run/scenes";
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <WithShell>
        {"state" in opts && <StateProbe />}
        <Routes>
          <Route path="/campaigns/:cid/scenes" element={<ScenesView />} />
          <Route path="/campaigns/:cid/scenes/:sid" element={<Landed />} />
        </Routes>
      </WithShell>
    </MemoryRouter>);
}

beforeEach(() => {
  vi.mocked(api.deleteScene).mockReset().mockResolvedValue({ ok: true });
  vi.mocked(api.getCampaign).mockResolvedValue({ meta: META, body: "" });
  vi.mocked(api.getShell).mockResolvedValue(shell());
  vi.mocked(api.getCampaignSceneCosts).mockResolvedValue(costs([]));
  vi.mocked(api.listEntities).mockResolvedValue(fixture([{ id: "the-weir", name: "The weir" }]));
  vi.mocked(api.getCalendarMonths).mockResolvedValue({ months: [] });
  vi.mocked(api.listScenes).mockResolvedValue([
    scene({ id: "003--third", title: "The third", date: "Day 41", place: "the-weir" }),
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
  expect(screen.getByText("Run One · last played first · 1 open"))
    .toBeInTheDocument();
});

test("the eyebrow drops the open count rather than claiming zero", async () => {
  // Joining with a filter means a part with nothing to say disappears
  // instead of printing "0 open" -- matching how the hub's eyebrow behaves.
  vi.mocked(api.listScenes).mockResolvedValue([
    scene({ id: "001--first", title: "The first", done: true }),
  ]);
  renderScenes();
  await screen.findByText("The first");
  expect(screen.getByText("Run One · last played first")).toBeInTheDocument();
});

test("the action names what to do, and it is not the same verb for every state", async () => {
  vi.mocked(api.getShell).mockResolvedValue(shell([{ sid: "002--second", proposals: 4 }]));
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
  vi.mocked(api.getShell).mockResolvedValue(shell([{ sid: "002--second", proposals: 4 }]));
  renderScenes();
  await screen.findByText("The second");
  const row = screen.getAllByRole("listitem")[1];
  expect(within(row).getByText("4 unreviewed")).toBeInTheDocument();
});

test("the chip carries the proposal count, not just that some are waiting", async () => {
  // The shell payload already has the count per pending scene; a bare
  // "unreviewed" would make the reader open the scene to learn whether it's
  // one proposal or thirty.
  vi.mocked(api.getShell).mockResolvedValue(shell([{ sid: "002--second", proposals: 12 }]));
  renderScenes();
  await screen.findByText("The second");
  const row = screen.getAllByRole("listitem")[1];
  expect(within(row).getByText("12 unreviewed")).toBeInTheDocument();
});

test("where is the location's name, not its id", async () => {
  // A scene stores its place as a location id, and the list printed it as
  // stored: "THE-WEIR". The name comes from the campaign's locations, read
  // once after the list.
  renderScenes();
  await screen.findByText("The third");
  expect(await screen.findByText(/Day 41 · The weir/)).toBeInTheDocument();
  expect(screen.queryByText(/the-weir/)).toBeNull();
  expect(api.listEntities).toHaveBeenCalledWith({ kind: "campaign", id: "run" }, "locations");
});

test("a place nobody can name is left out rather than shown as an id", async () => {
  vi.mocked(api.listEntities).mockRejectedValue(new Error("offline"));
  renderScenes();
  await screen.findByText("The third");
  expect(screen.getByText("Day 41")).toBeInTheDocument();
  expect(screen.queryByText(/the-weir/)).toBeNull();
});

test("a date reads in its calendar's own month names, one read per year", async () => {
  vi.mocked(api.listScenes).mockResolvedValue([
    scene({ id: "002--b", title: "B", date: "2026-01-02T18:00" }),
    scene({ id: "001--a", title: "A", date: "2026-02-14" }),
  ]);
  vi.mocked(api.getCalendarMonths).mockResolvedValue({ months: [
    { key: "01", name: "January", days: 31 }, { key: "02", name: "February", days: 28 }] });
  renderScenes();
  expect(await screen.findByText("January 2, 2026")).toBeInTheDocument();
  expect(screen.getByText("February 14, 2026")).toBeInTheDocument();
  expect(api.getCalendarMonths).toHaveBeenCalledTimes(1);
  expect(api.getCalendarMonths).toHaveBeenCalledWith({ kind: "campaign", id: "run" }, 2026);
});

test("until the months are known a date reads as written", async () => {
  vi.mocked(api.listScenes).mockResolvedValue([scene({ id: "001--a", title: "A", date: "2026-01-02" })]);
  vi.mocked(api.getCalendarMonths).mockReturnValue(new Promise(() => {}));
  renderScenes();
  expect(await screen.findByText("2026-01-02")).toBeInTheDocument();
});

test("a scene's state is an outlined status, not a filled button", async () => {
  // A filled "OPEN" outweighed the row's real action at its end.
  renderScenes();
  await screen.findByText("The third");
  const chip = screen.getByText("in play");
  expect(chip).toHaveClass("chip", "status", "live");
  expect(chip).not.toHaveClass("on");
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
  vi.mocked(api.listScenes).mockResolvedValue([]);
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
  const readsBefore = vi.mocked(api.listScenes).mock.calls.length;
  fireEvent.click(screen.getByRole("button", { name: /\+ new scene/i }));
  fireEvent.click(await screen.findByText("stub-close"));
  expect(screen.queryByTestId("scene-chooser")).toBeNull();
  expect(screen.queryByTestId("play")).toBeNull();
  expect(vi.mocked(api.listScenes).mock.calls.length).toBe(readsBefore);
});

test("a failed read is not an empty campaign", async () => {
  vi.mocked(api.listScenes).mockRejectedValue(new Error("offline"));
  renderScenes();
  expect(await screen.findByText(/the scenes could not be read/i)).toBeInTheDocument();
  expect(screen.queryByText(/no scenes yet/i)).not.toBeInTheDocument();
});

// ---- the shell read is the rail's, and the list does not wait on it ----

test("the list does not wait on the shell read", async () => {
  // The list's own read answers in tens of milliseconds; the shell read is the
  // slowest in the chrome, and the page used to hold every row until its own
  // copy of it answered.
  vi.mocked(api.getShell).mockReturnValue(new Promise(() => {}));
  renderScenes();
  expect(await screen.findByText("The third")).toBeInTheDocument();
  expect(screen.getAllByRole("listitem")).toHaveLength(3);
  // Until the counts arrive a row says only what its frontmatter knows: an
  // open scene reads "Open", the wording that never claims there is something
  // to come back to, and it draws no turn count rather than zero.
  const rows = screen.getAllByRole("listitem");
  expect(within(rows[0]).getByText("Open →")).toBeInTheDocument();
  expect(within(rows[0]).queryByText(/\dt$/)).not.toBeInTheDocument();
});

test("a failed shell read costs the counts, not the list", async () => {
  vi.mocked(api.getShell).mockRejectedValue(new Error("offline"));
  renderScenes();
  expect(await screen.findByText(/waiting reviews and turn counts could not be read/i))
    .toBeInTheDocument();
  expect(screen.getByText("The third")).toBeInTheDocument();
  expect(screen.queryByText(/the scenes could not be read/i)).not.toBeInTheDocument();

  // ...and its retry asks again, and the rows take what it answers.
  vi.mocked(api.getShell).mockResolvedValue(shell([{ sid: "002--second", proposals: 4 }]));
  fireEvent.click(screen.getByRole("button", { name: /try again/i }));
  expect(await screen.findByText("4 unreviewed")).toBeInTheDocument();
  expect(screen.queryByText(/turn counts could not be read/i)).not.toBeInTheDocument();
});

test("a payload about another campaign draws nothing on these rows", async () => {
  vi.mocked(api.getShell).mockResolvedValue({
    ...shell([{ sid: "002--second", proposals: 4 }]),
    campaign: { ...shell([{ sid: "002--second", proposals: 4 }]).campaign, id: "elsewhere" },
  });
  renderScenes();
  await screen.findByText("The second");
  expect(screen.queryByText("4 unreviewed")).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Wrap up →" })).not.toBeInTheDocument();
});


// ---- the two columns that are not frontmatter ----

test("an open scene with turns says Resume, one with none says Open", async () => {
  // The same click either way. What the reader gets is knowing which of the
  // two they are about to do: a conversation to come back to, or a start.
  vi.mocked(api.listScenes).mockResolvedValue([
    scene({ id: "002--b", title: "Underway", done: false }),
    scene({ id: "001--a", title: "Not started", done: false }),
  ]);
  vi.mocked(api.getShell).mockResolvedValue(shell([], [
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
  vi.mocked(api.listScenes).mockResolvedValue([
    scene({ id: "001--a", title: "Unreadable", done: false }),
  ]);
  vi.mocked(api.getShell).mockResolvedValue(shell([], [
    { sid: "001--a", title: "Unreadable", turns: null },
  ]));
  renderScenes();

  await screen.findByText("Unreadable");
  expect(screen.getByRole("link", { name: "Open →" })).toBeInTheDocument();
  expect(screen.queryByText(/turns$/)).not.toBeInTheDocument();
});

test("spend lands after the list and never before it", async () => {
  vi.mocked(api.listScenes).mockResolvedValue([
    scene({ id: "001--a", title: "A scene", done: true }),
  ]);
  vi.mocked(api.getCampaignSceneCosts).mockResolvedValue(
    costs([costRow("001--a", { cost_usd: 0.41 })]));
  renderScenes();

  // The title does not wait on the ledger.
  await screen.findByText("A scene");
  expect(await screen.findByText("$0.41")).toBeInTheDocument();
});

test("a scene the ledger cannot price says so rather than showing $0.00", async () => {
  vi.mocked(api.listScenes).mockResolvedValue([
    scene({ id: "001--a", title: "A scene", done: true }),
  ]);
  vi.mocked(api.getCampaignSceneCosts).mockResolvedValue(
    costs([costRow("001--a", { cost_usd: 0, priced_calls: 0, unpriced_calls: 2 })]));
  renderScenes();

  expect(await screen.findByText("not reported")).toBeInTheDocument();
  expect(screen.queryByText("$0.00")).not.toBeInTheDocument();
});

test("a zero estimate beside calls nobody priced says not reported, not ≈ $0.00", async () => {
  // A zero-rated call next to ten unpriced ones: `≈ $0.00` would read as a
  // complete total, and the scene list has no footnote to say otherwise.
  vi.mocked(api.listScenes).mockResolvedValue([
    scene({ id: "001--a", title: "A scene", done: true }),
  ]);
  vi.mocked(api.getCampaignSceneCosts).mockResolvedValue(
    costs([costRow("001--a", { calls: 11, cost_usd: 0, priced_calls: 0,
                               modelled_calls: 1, modelled_usd: 0,
                               unpriced_calls: 10 })]));
  renderScenes();

  expect(await screen.findByText("not reported")).toBeInTheDocument();
  expect(screen.queryByText(/\$0\.00/)).not.toBeInTheDocument();
});

test("a scene's price says when its token counts were estimated here", async () => {
  vi.mocked(api.listScenes).mockResolvedValue([
    scene({ id: "001--a", title: "A scene", done: true }),
  ]);
  vi.mocked(api.getCampaignSceneCosts).mockResolvedValue(
    costs([costRow("001--a", { calls: 2, cost_usd: 0, priced_calls: 0, modelled_calls: 2,
                               modelled_usd: 0.37, estimated_token_calls: 2 })]));
  renderScenes();

  expect(await screen.findByText("≈ $0.37"))
    .toHaveAttribute("title", expect.stringMatching(/2 calls with tokens estimated/));
});

test("a ledger read that failed costs the figures and nothing else", async () => {
  vi.mocked(api.listScenes).mockResolvedValue([
    scene({ id: "001--a", title: "A scene", done: true }),
  ]);
  vi.mocked(api.getCampaignSceneCosts).mockRejectedValue(new Error("busy"));
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
  vi.mocked(api.listScenes).mockResolvedValue([
    scene({ id: "001--a", title: "Absorbed", done: true }),
  ]);
  vi.mocked(api.getShell).mockResolvedValue(shell([{ sid: "001--a", proposals: 8 }]));
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
  vi.mocked(api.listScenes).mockResolvedValue([
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
  vi.mocked(api.deleteScene).mockRejectedValue({
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

test("branches carry a branch chip, and a closed one says so in place of open", async () => {
  vi.mocked(api.listScenes).mockResolvedValue([
    scene({ id: "002--other", title: "Other" }),
    scene({ id: "001--alt", title: "Alt", branch_group: "g",
            closed_by: { sid: "001--first", title: "First" } }),
    scene({ id: "001--first", title: "First", done: true, branch_group: "g" }),
  ]);
  renderScenes();
  await screen.findByText("Other");
  const rows = screen.getAllByRole("listitem");
  const row = (title: string) => rows.find((r) => within(r).queryByText(title))!;
  expect(screen.getAllByText("branch")).toHaveLength(2);
  expect(within(row("Other")).queryByText("branch")).toBeNull();
  const closed = within(row("Alt")).getByText("closed");
  expect(closed).toHaveAttribute("title", "A sibling branch was absorbed");
  expect(within(row("Alt")).queryByText("open")).toBeNull();
  expect(within(row("Alt")).getByText("Read →")).toBeInTheDocument();
  expect(within(row("Other")).queryByText("closed")).toBeNull();
});

test("a group of one shows no branch chip", async () => {
  vi.mocked(api.listScenes).mockResolvedValue([
    scene({ id: "001--first", title: "First", branch_group: "g" }),
  ]);
  renderScenes();
  await screen.findByText("First");
  expect(screen.queryByText("branch")).toBeNull();
});

test("a closed branch is not counted or listed as open", async () => {
  vi.mocked(api.listScenes).mockResolvedValue([
    scene({ id: "002--other", title: "Other" }),
    scene({ id: "001--alt", title: "Alt", branch_group: "g",
            closed_by: { sid: "001--first", title: "First" } }),
    scene({ id: "001--first", title: "First", done: true, branch_group: "g" }),
  ]);
  renderScenes();
  await screen.findByText("Other");
  expect(screen.getByText("Run One · last played first · 1 open")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /^Open/ }));
  await screen.findByText("Other");
  expect(screen.queryByText("Alt")).toBeNull();
});

// The Story Graph's handoff (§16.5): a driver or an anchor arrives as history
// state and opens the chooser already steered. Adopted once and cleared off
// the entry, like `CampaignView`'s seedPrompt -- left in place, Back into this
// entry or a reload would reopen a chooser the reader had already closed.
test("a chooser handoff opens the chooser seeded once", async () => {
  const { rerender } = renderScenes(
    { state: { chooser: { drivers: { "thread:mara-s-map": "focus" } } } });
  const chooser = await screen.findByTestId("scene-chooser");
  expect(within(chooser).getByTestId("seed"))
    .toHaveTextContent(JSON.stringify({ drivers: { "thread:mara-s-map": "focus" } }));
  expect(screen.getByTestId("history-state")).toHaveTextContent("null");

  fireEvent.click(screen.getByText("stub-close"));
  await waitFor(() => expect(screen.queryByTestId("scene-chooser")).toBeNull());
  rerender(
    <MemoryRouter initialEntries={["/campaigns/run/scenes"]}>
      <WithShell>
        <StateProbe />
        <Routes>
          <Route path="/campaigns/:cid/scenes" element={<ScenesView />} />
        </Routes>
      </WithShell>
    </MemoryRouter>);
  await screen.findByText("The third");
  expect(screen.queryByTestId("scene-chooser")).toBeNull();

  // The ordinary entry point is unchanged: it carries no seed, not the one
  // the reader just closed.
  fireEvent.click(screen.getByRole("button", { name: /\+ new scene/i }));
  expect(within(await screen.findByTestId("scene-chooser")).getByTestId("seed"))
    .toHaveTextContent("null");
});

test("an anchor handoff seeds the anchor", async () => {
  renderScenes(
    { state: { chooser: { anchor: { ref: "event:the-coronation", relation: "on" } } } });
  const chooser = await screen.findByTestId("scene-chooser");
  expect(within(chooser).getByTestId("seed")).toHaveTextContent(
    JSON.stringify({ anchor: { ref: "event:the-coronation", relation: "on" } }));
});

test("a malformed chooser state is ignored and cleared", async () => {
  renderScenes({ state: { chooser: { drivers: 3 } } });
  await screen.findByText("The third");
  expect(screen.queryByTestId("scene-chooser")).toBeNull();
  expect(screen.getByTestId("history-state")).toHaveTextContent("null");
});

test("a seeded open waits for the scene list", async () => {
  // Opening before the list lands would rank against no scene, then re-ask
  // when `afterSid` arrives -- a second paid ranked call (Decision 20).
  let resolve!: (v: SceneMeta[]) => void;
  vi.mocked(api.listScenes).mockReturnValue(new Promise((r) => { resolve = r; }));
  renderScenes({ state: { chooser: { drivers: { "thread:mara-s-map": "focus" } } } });
  await waitFor(() =>
    expect(screen.getByTestId("history-state")).toHaveTextContent("null"));
  expect(screen.queryByTestId("scene-chooser")).toBeNull();

  resolve([scene({ id: "003--third", title: "The third" })]);
  const chooser = await screen.findByTestId("scene-chooser");
  expect(within(chooser).getByTestId("after")).toHaveTextContent('"003--third"');
  expect(within(chooser).getByTestId("seed")).toHaveTextContent("thread:mara-s-map");
});

test("a seeded open after a failed list survives Try again, and + New scene still opens", async () => {
  // A failed list opens a seeded chooser anyway, with no reference
  // (Decision 20). The release is one-way: clearing the error banner must not
  // re-hold a chooser the reader is already using, and a seed left over from
  // the handoff must never hide an ordinary open.
  vi.mocked(api.listScenes).mockRejectedValueOnce(new Error("down"));
  renderScenes({ state: { chooser: { drivers: { "thread:mara-s-map": "focus" } } } });
  const chooser = await screen.findByTestId("scene-chooser");
  expect(within(chooser).getByTestId("after")).toHaveTextContent("null");
  expect(within(chooser).getByTestId("seed")).toHaveTextContent("thread:mara-s-map");

  fireEvent.click(screen.getByRole("button", { name: /try again/i }));
  await waitFor(() =>
    expect(screen.queryByText(/could not be read/i)).toBeNull());
  expect(screen.getByTestId("scene-chooser")).toBeInTheDocument();

  fireEvent.click(screen.getByText("stub-close"));
  await waitFor(() => expect(screen.queryByTestId("scene-chooser")).toBeNull());
  fireEvent.click(screen.getByRole("button", { name: /\+ new scene/i }));
  expect(within(await screen.findByTestId("scene-chooser")).getByTestId("seed"))
    .toHaveTextContent("null");
});

test("+ New scene during a held handoff opens the chooser unseeded", async () => {
  // While a handoff waits for the list, the ordinary entry point is not held
  // behind it: it opens at once, and with no seed.
  vi.mocked(api.listScenes).mockReturnValue(new Promise(() => {}));
  renderScenes({ state: { chooser: { drivers: { "thread:mara-s-map": "focus" } } } });
  await waitFor(() =>
    expect(screen.getByTestId("history-state")).toHaveTextContent("null"));
  expect(screen.queryByTestId("scene-chooser")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /\+ new scene/i }));
  expect(within(await screen.findByTestId("scene-chooser")).getByTestId("seed"))
    .toHaveTextContent("null");
});

test("the list's Try again reads the list again", async () => {
  // The banner says "look again", so its button has to: clearing the banner
  // alone left the search tools over an empty list, with no read under way.
  vi.mocked(api.listScenes).mockClear().mockRejectedValueOnce(new Error("offline"));
  renderScenes();
  expect(await screen.findByText("The scenes could not be read.", { exact: false }))
    .toBeInTheDocument();
  expect(api.listScenes).toHaveBeenCalledTimes(1);

  vi.mocked(api.listScenes).mockResolvedValue([
    scene({ id: "003--third", title: "The third", date: "Day 41", place: "The weir" }),
  ]);
  fireEvent.click(screen.getByRole("button", { name: /try again/i }));
  expect(await screen.findByText("The third")).toBeInTheDocument();
  expect(api.listScenes).toHaveBeenCalledTimes(2);
  expect(screen.queryByText(/the scenes could not be read/i)).not.toBeInTheDocument();
});

test("scene rows name locations and calendar months, reading each year once", async () => {
  vi.mocked(api.listScenes).mockResolvedValue([
    scene({ id: "003--third", title: "The third", date: "2026-01-02T10:30", place: "saltmarch" }),
    scene({ id: "002--second", title: "The second", date: "2026-01-03", place: "saltmarch" }),
  ]);
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "saltmarch", name: "Saltmarch" }]);
  vi.mocked(api.getCalendarMonths).mockResolvedValue({
    months: [{ key: "01", name: "January", days: 31 }],
  });
  vi.mocked(api.getCalendarMonths).mockClear();
  renderScenes();

  expect(await screen.findByText("January 2, 2026 · Saltmarch")).toBeInTheDocument();
  expect(screen.getByText("January 3, 2026 · Saltmarch")).toBeInTheDocument();
  expect(api.getCalendarMonths).toHaveBeenCalledTimes(1);
  expect(api.getCalendarMonths).toHaveBeenCalledWith({ kind: "campaign", id: "run" }, 2026);
});

test("unreadable date and location metadata leaves the scene reachable", async () => {
  vi.mocked(api.listScenes).mockResolvedValue([
    scene({ id: "003--third", title: "The third", date: "2026-01-02", place: "saltmarch" }),
  ]);
  vi.mocked(api.listEntities).mockRejectedValue(new Error("offline"));
  vi.mocked(api.getCalendarMonths).mockRejectedValue(new Error("offline"));
  renderScenes();

  expect(await screen.findByText("2026-01-02")).toBeInTheDocument();
  expect(screen.queryByText("saltmarch")).not.toBeInTheDocument();
  expect(screen.queryByText(/the scenes could not be read/i)).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Open →" }))
    .toHaveAttribute("href", "/campaigns/run/scenes/003--third");
});

test("switching campaigns clears the previous calendar while the new months are pending", async () => {
  vi.mocked(api.listScenes).mockResolvedValue([
    scene({ id: "003--third", title: "The third", date: "2026-01-02" }),
  ]);
  let finishMonths: (value: Awaited<ReturnType<typeof api.getCalendarMonths>>) => void = () => {
    throw new Error("the month request was not started");
  };
  const nextMonths = new Promise<Awaited<ReturnType<typeof api.getCalendarMonths>>>(
    (resolve) => { finishMonths = resolve; });
  vi.mocked(api.getCalendarMonths).mockImplementation((scope) => scope.id === "run"
    ? Promise.resolve({ months: [{ key: "01", name: "January", days: 31 }] })
    : nextMonths);
  render(<MemoryRouter initialEntries={["/campaigns/run/scenes"]}>
    <Link to="/campaigns/realm/scenes">Switch campaign</Link>
    <Routes><Route path="/campaigns/:cid/scenes" element={<ScenesView />} /></Routes>
  </MemoryRouter>);
  expect(await screen.findByText("January 2, 2026")).toBeInTheDocument();

  fireEvent.click(screen.getByRole("link", { name: "Switch campaign" }));
  await waitFor(() => expect(api.getCalendarMonths)
    .toHaveBeenCalledWith({ kind: "campaign", id: "realm" }, 2026));
  expect(screen.queryByText("January 2, 2026")).not.toBeInTheDocument();
  expect(screen.getByText("2026-01-02")).toBeInTheDocument();

  finishMonths({ months: [{ key: "01", name: "February", days: 31 }] });
  expect(await screen.findByText("February 2, 2026")).toBeInTheDocument();
});
