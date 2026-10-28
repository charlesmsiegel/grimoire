import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { api } from "../api/client";
import type {
RollingSummary, RollingSummaryRefresh, SceneBreak
} from "../api/types";
import { NO_SCENE_BREAK, renderInspector, resetSceneInspectorMocks } from "../testkit/sceneInspectorHarness";
import { SceneInspector } from "./SceneInspector";
vi.mock("../api/client", async () => (await import("../testkit/sceneInspectorMocks")).sceneInspectorApiMock());
vi.mock("../api/models", async () => (await import("../testkit/sceneInspectorMocks")).sceneInspectorModelsMock());
vi.mock("./ResponseTargetsPicker", async () => (await import("../testkit/sceneInspectorMocks")).responseTargetsMock());
vi.mock("./AuthorsNotesPanel", async () => (await import("../testkit/sceneInspectorMocks")).authorsNotesMock());

beforeEach(resetSceneInspectorMocks);

// ---- the scene-break detector (#84) ----
const BREAK_YES = {
  ...NO_SCENE_BREAK, verdict: "yes" as const,
  reason: "The ledger changed hands and both sides walked away.",
  title: "The Long Walk Back", posts: 40, score: 3, due: false,
};

test("a confirmed break shows why, and what the next scene might be called", async () => {
  vi.mocked(api.getSceneBreak).mockResolvedValue(BREAK_YES);
  renderInspector();
  await screen.findByText("The ledger changed hands and both sides walked away.");
  await screen.findByText(/The Long Walk Back/);
});

test("the detector never ends the scene itself — the only actions are asking and declining", async () => {
  vi.mocked(api.getSceneBreak).mockResolvedValue(BREAK_YES);
  renderInspector();
  await screen.findByText(/The Long Walk Back/);
  // No control here writes the transcript or the scene's done flag; a reader
  // finding one would be finding an auto-split, which this feature must not do.
  expect(screen.queryByRole("button", { name: /end scene|split|start next/i })).toBeNull();
  expect(screen.getByRole("button", { name: /not here/i })).toBeInTheDocument();
});

test("a model that said no is said differently from nothing having been asked", async () => {
  vi.mocked(api.getSceneBreak).mockResolvedValue({
    ...NO_SCENE_BREAK, verdict: "no", reason: "They are still mid-argument.", posts: 40 });
  renderInspector();
  await screen.findByText(/still mid-beat/i);
  await screen.findByText("They are still mid-argument.");
  // ...and declining is not offered against an answer there is nothing to decline.
  expect(screen.queryByRole("button", { name: /not here/i })).toBeNull();
});

test("with nothing to suggest it says what it can see rather than nothing at all", async () => {
  vi.mocked(api.getSceneBreak).mockResolvedValue({
    ...NO_SCENE_BREAK, posts: 12, score: 1,
    signals: [{ kind: "length", weight: 1, detail: "12 posts since this was last considered" }] });
  renderInspector();
  await screen.findByText(/Nothing to suggest yet/);
  await screen.findByText("12 posts since this was last considered");
});

test("switched off, the panel says so instead of reporting a score of zero", async () => {
  vi.mocked(api.getSceneBreak).mockResolvedValue({ ...NO_SCENE_BREAK, every: 0, posts: 400 });
  renderInspector();
  await screen.findByText(/Turned off/);
});

test("a forced answer is shown even with the automatic cadence switched off", async () => {
  // Ask now works when `every` is 0 — that is the whole point of a button that
  // says now — so an off-notice that outranked the verdict meant the player
  // pressed it, paid for a call, and watched the panel go on saying "Turned off".
  vi.mocked(api.getSceneBreak).mockResolvedValue({ ...NO_SCENE_BREAK, every: 0, posts: 400 });
  vi.mocked(api.askSceneBreak).mockResolvedValue({ ...BREAK_YES, every: 0, asked: true });
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /ask now/i }));
  await screen.findByText("The ledger changed hands and both sides walked away.");
});

test("a proposal about deleted posts is shown, but not as current", async () => {
  vi.mocked(api.getSceneBreak).mockResolvedValue({ ...BREAK_YES, stale: true });
  renderInspector();
  await screen.findByText("The ledger changed hands and both sides walked away.");
  await screen.findByText(/may no longer apply/i);
});

test("a read that failed is not a detector that found nothing", async () => {
  // "Nothing to suggest yet" is an assertion about the scene. Rendering it out
  // of a failed GET tells the player it looked and found nothing when it never
  // got an answer at all.
  vi.mocked(api.getSceneBreak).mockRejectedValue(new Error("offline"));
  renderInspector();
  await screen.findByText(/could not be read/i);
  expect(screen.queryByText(/Nothing to suggest yet/)).toBeNull();
});

test("Ask now forces a question and installs whatever comes back", async () => {
  vi.mocked(api.askSceneBreak).mockResolvedValue({ ...BREAK_YES, asked: true });
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /ask now/i }));
  await waitFor(() => expect(api.askSceneBreak).toHaveBeenCalledWith("c", "s", true));
  await screen.findByText("The ledger changed hands and both sides walked away.");
});

test("a failed question reports itself and never blanks a standing proposal", async () => {
  vi.mocked(api.getSceneBreak).mockResolvedValue(BREAK_YES);
  vi.mocked(api.askSceneBreak).mockRejectedValue({ detail: "OpenRouter key not set" });
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /ask now/i }));
  await screen.findByText("OpenRouter key not set");
  expect(screen.getByText("The ledger changed hands and both sides walked away."))
    .toBeInTheDocument();
});

test("Not here goes to the server, because the watermark it moves lives there", async () => {
  // A local dismissal would leave the same posts re-earning the same
  // suggestion on the very next turn.
  vi.mocked(api.getSceneBreak).mockResolvedValue(BREAK_YES);
  vi.mocked(api.dismissSceneBreak).mockResolvedValue(NO_SCENE_BREAK);
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /not here/i }));
  await waitFor(() => expect(api.dismissSceneBreak).toHaveBeenCalledWith("c", "s"));
  await screen.findByText(/Nothing to suggest yet/);
});

test("a read issued before Ask now cannot blank the verdict it just landed", async () => {
  // The scene-select effect's GET can be in flight when the button commits, and
  // resolving second would install a pre-write answer over the one the player
  // just paid for — with nothing later scheduled to put it back.
  let releaseRead: ((v: SceneBreak) => void) | undefined;
  vi.mocked(api.getSceneBreak).mockImplementationOnce(
    () => new Promise((resolve) => { releaseRead = resolve; }));
  vi.mocked(api.askSceneBreak).mockResolvedValue({ ...BREAK_YES, asked: true });
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /ask now/i }));
  await screen.findByText("The ledger changed hands and both sides walked away.");
  await act(async () => { releaseRead!(NO_SCENE_BREAK); });
  expect(screen.getByText("The ledger changed hands and both sides walked away."))
    .toBeInTheDocument();
});

test("both break buttons are held while a question is out on this scene", async () => {
  // What makes ordering the panel's own writes against each other unnecessary:
  // they cannot overlap on one record. A write arriving from somewhere else is
  // the server's to refuse, and `_break_commit` does.
  vi.mocked(api.getSceneBreak).mockResolvedValue(BREAK_YES);
  vi.mocked(api.askSceneBreak).mockReturnValueOnce(new Promise(() => {}));
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /ask now/i }));
  await waitFor(() =>
    expect(screen.getByRole("button", { name: /asking/i })).toBeDisabled());
  expect(screen.getByRole("button", { name: /not here/i })).toBeDisabled();
});

test("switching scenes never shows the previous scene's proposal", async () => {
  // A proposal is prose ABOUT a story, so showing one under another scene reads
  // as fact rather than as lag — `rolling`'s rule, for a sharper reason.
  vi.mocked(api.getSceneBreak).mockResolvedValueOnce(BREAK_YES);
  const { rerender } = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  await screen.findByText("The ledger changed hands and both sides walked away.");
  vi.mocked(api.getSceneBreak).mockResolvedValue(NO_SCENE_BREAK);
  rerender(<SceneInspector cid="c" sid="s2" refreshKey={0} onSceneChanged={() => {}} />);
  await waitFor(() => expect(
    screen.queryByText("The ledger changed hands and both sides walked away.")).toBeNull());
});

// ---- the live rolling summary (#85) ----
test("shows the running summary and what it covers", async () => {
  vi.mocked(api.getRollingSummary).mockResolvedValue({
    summary: "Mara reaches the salt gate; the ledger is still missing.",
    at: 12, total: 14, stale: false, every: 10, due: false });
  renderInspector();
  await screen.findByText("Mara reaches the salt gate; the ledger is still missing.");
  await screen.findByText(/12 of 14 posts/);
});

test("says so when nothing has been summarized yet, rather than showing an empty box", async () => {
  renderInspector();
  await screen.findByText(/No summary yet/);
});

test("flags a summary whose posts have since been rerolled or edited", async () => {
  vi.mocked(api.getRollingSummary).mockResolvedValue({
    summary: "An older account of the scene.", at: 12, total: 12,
    stale: true, every: 10, due: false });
  renderInspector();
  await screen.findByText(/out of date/i);
});

test("Refresh now forces a refold and shows the result", async () => {
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /refresh now/i }));
  await waitFor(() => expect(api.refreshRollingSummary).toHaveBeenCalledWith("c", "s", true));
  await screen.findByText("Refolded.");
});

test("a refresh that spends nothing leaves the panel as it was", async () => {
  vi.mocked(api.getRollingSummary).mockResolvedValue({
    summary: "Standing summary.", at: 4, total: 4, stale: false, every: 10, due: false });
  vi.mocked(api.refreshRollingSummary).mockResolvedValue({
    summary: "Standing summary.", at: 4, total: 4, stale: false, every: 10, due: false,
    refreshed: false });
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /refresh now/i }));
  await waitFor(() => expect(api.refreshRollingSummary).toHaveBeenCalled());
  await screen.findByText("Standing summary.");
});

test("a failed refresh reports itself and never blanks the summary", async () => {
  vi.mocked(api.getRollingSummary).mockResolvedValue({
    summary: "Standing summary.", at: 4, total: 4, stale: false, every: 10, due: false });
  vi.mocked(api.refreshRollingSummary).mockRejectedValue({ detail: "OpenRouter key not set" });
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /refresh now/i }));
  await screen.findByText("OpenRouter key not set");
  expect(screen.getByText("Standing summary.")).toBeInTheDocument();
});

test("a refold the model could not be reached for offers the recovery", async () => {
  vi.mocked(api.getRollingSummary).mockResolvedValue({
    summary: "Standing summary.", at: 4, total: 4, stale: false, every: 10, due: false });
  vi.mocked(api.refreshRollingSummary).mockRejectedValue(
    { detail: "connection refused", kind: "network" });
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /refresh now/i }));
  await screen.findByText(/Couldn.t reach the model provider/);
  expect(screen.getByRole("link", { name: /Providers/ })).toHaveAttribute("href", "/providers");
  // still never destructive
  expect(screen.getByText("Standing summary.")).toBeInTheDocument();
});

test("a summary that cannot be read is not an empty summary", async () => {
  // The panel reads on every scene select, and a failed GET must not be
  // indistinguishable from a scene nobody has summarized.
  vi.mocked(api.getRollingSummary).mockRejectedValue(new Error("offline"));
  renderInspector();
  await screen.findByText(/could not be read/i);
});

test("switching scenes never shows the previous scene's summary", async () => {
  // Prose under the wrong scene reads as fact, where a stale token count reads
  // as lag — and two selects in a row can answer out of order.
  vi.mocked(api.getRollingSummary).mockResolvedValueOnce({
    summary: "Scene one: Mara reaches the salt gate.", at: 4, total: 4,
    stale: false, every: 10, due: false });
  const view = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  await screen.findByText("Scene one: Mara reaches the salt gate.");

  vi.mocked(api.getRollingSummary).mockReturnValueOnce(new Promise(() => {}));  // never answers
  view.rerender(
    <SceneInspector cid="c" sid="s2" refreshKey={0} onSceneChanged={() => {}} />);
  await waitFor(() =>
    expect(screen.queryByText("Scene one: Mara reaches the salt gate.")).toBeNull());
});

test("a late read for a scene the reader has left does not evict the current one", async () => {
  // Stamping the response keeps A's prose off B's panel, but on its own it does
  // not stop A's *later* answer from replacing B's — after which the stamp
  // rejects it and the panel says "No summary yet" about a scene that has one.
  let answerA: ((v: RollingSummary) => void) | undefined;
  vi.mocked(api.getRollingSummary).mockImplementationOnce(
    () => new Promise((resolve) => { answerA = resolve; }));
  const view = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);

  vi.mocked(api.getRollingSummary).mockResolvedValueOnce({
    summary: "Scene two: the ledger surfaces.", at: 2, total: 2,
    stale: false, every: 10, due: false });
  view.rerender(
    <SceneInspector cid="c" sid="s2" refreshKey={0} onSceneChanged={() => {}} />);
  await screen.findByText("Scene two: the ledger surfaces.");

  // A answers last, and must be dropped rather than overwrite B's state
  await act(async () => {
    answerA!({ summary: "Scene one: Mara reaches the salt gate.", at: 4, total: 4,
               stale: false, every: 10, due: false });
  });
  expect(screen.getByText("Scene two: the ledger surfaces.")).toBeInTheDocument();
  expect(screen.queryByText(/No summary yet/)).toBeNull();
});

test("the same scene id in another campaign does not inherit the summary", async () => {
  // Scene ids are campaign-local and collide freely — `001--saltmarch` is what
  // every campaign's first scene by that title is called. `App.tsx` renders
  // CampaignView without a key, so navigating between campaigns reuses this
  // inspector rather than remounting it.
  vi.mocked(api.getRollingSummary).mockResolvedValueOnce({
    summary: "Campaign one: Mara reaches the salt gate.", at: 4, total: 4,
    stale: false, every: 10, due: false });
  const view = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  await screen.findByText("Campaign one: Mara reaches the salt gate.");

  vi.mocked(api.getRollingSummary).mockReturnValueOnce(new Promise(() => {}));
  view.rerender(
    <SceneInspector cid="c2" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  await waitFor(() =>
    expect(screen.queryByText("Campaign one: Mara reaches the salt gate.")).toBeNull());
});

test("an older read of the same scene cannot overwrite a newer one", async () => {
  // This effect re-runs on `refreshKey` for the SAME scene — twice per turn, in
  // fact — so two reads are routinely in flight together. Scene identity cannot
  // order them.
  let answerFirst: ((v: RollingSummary) => void) | undefined;
  vi.mocked(api.getRollingSummary).mockImplementationOnce(
    () => new Promise((resolve) => { answerFirst = resolve; }));
  const view = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);

  vi.mocked(api.getRollingSummary).mockResolvedValueOnce({
    summary: "The refreshed summary.", at: 12, total: 12,
    stale: false, every: 10, due: false });
  view.rerender(
    <SceneInspector cid="c" sid="s" refreshKey={1} onSceneChanged={() => {}} />);
  await screen.findByText("The refreshed summary.");

  await act(async () => {
    answerFirst!({ summary: "The summary as it was before the refresh.", at: 4,
                   total: 4, stale: false, every: 10, due: false });
  });
  expect(screen.getByText("The refreshed summary.")).toBeInTheDocument();
});

test("a refresh that fails after the reader moves on does not banner the new scene", async () => {
  // `error` is shared by every action in this panel and the scene-change effect
  // does not clear it, so an error installed for the scene the reader left
  // sits over the scene they are on until something else happens to clear it.
  let rejectA: ((e: any) => void) | undefined;
  vi.mocked(api.refreshRollingSummary).mockImplementationOnce(
    () => new Promise((_resolve, reject) => { rejectA = reject; }));
  const view = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: /refresh now/i }));

  view.rerender(
    <SceneInspector cid="c" sid="s2" refreshKey={0} onSceneChanged={() => {}} />);
  await act(async () => { rejectA!({ detail: "OpenRouter key not set" }); });

  expect(screen.queryByText("OpenRouter key not set")).toBeNull();
});

test("switching scenes frees the new scene's Refresh button", async () => {
  // `rollingBusy` was a bare boolean, so scene A's in-flight refold left B's
  // button disabled and reading "Summarizing…" for as long as A's provider call
  // took — for a result B can no longer use.
  vi.mocked(api.refreshRollingSummary).mockReturnValueOnce(new Promise(() => {}));
  const view = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: /refresh now/i }));
  await screen.findByRole("button", { name: /summarizing/i });

  view.rerender(
    <SceneInspector cid="c" sid="s2" refreshKey={0} onSceneChanged={() => {}} />);
  const button = await screen.findByRole("button", { name: /refresh now/i });
  expect(button).not.toBeDisabled();
});

test("a failed reread is reported even when older prose is on screen", async () => {
  // Same scene, `refreshKey` bumped: the cached value still matches the key, so
  // the failure used to be suppressed entirely and the panel kept presenting
  // pre-turn coverage as current with nothing to say otherwise.
  vi.mocked(api.getRollingSummary).mockResolvedValueOnce({
    summary: "Mara reaches the salt gate.", at: 10, total: 10,
    stale: false, every: 10, due: false });
  const view = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  await screen.findByText("Mara reaches the salt gate.");

  vi.mocked(api.getRollingSummary).mockRejectedValueOnce(new Error("offline"));
  view.rerender(
    <SceneInspector cid="c" sid="s" refreshKey={1} onSceneChanged={() => {}} />);

  // A failed read beside cached prose reads differently from a failed read with
  // nothing to show: the prose is not gone, it just cannot claim to be current.
  await screen.findByText(/latest read failed/i);
  // the prose is still the best thing anyone has, so it stays
  expect(screen.getByText("Mara reaches the salt gate.")).toBeInTheDocument();
  // ...but its coverage must not be presented as current
  expect(screen.queryByText(/Covers 10 of 10 posts/)).toBeNull();
});

test("a read issued before a manual refresh cannot undo it", async () => {
  // The GET and the POST shared one token, so a read issued while the refold was
  // in flight bumped it, and the POST's authoritative answer was then thrown
  // away as superseded — leaving the panel on the old summary although the new
  // one is durable on the server.
  let answerRead: ((v: RollingSummary) => void) | undefined;
  let answerPost: ((v: RollingSummaryRefresh) => void) | undefined;
  vi.mocked(api.getRollingSummary).mockResolvedValueOnce({
    summary: "The old summary.", at: 4, total: 4, stale: false, every: 10, due: false });
  vi.mocked(api.refreshRollingSummary).mockImplementationOnce(
    () => new Promise((resolve) => { answerPost = resolve; }));
  const view = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  await screen.findByText("The old summary.");
  fireEvent.click(screen.getByRole("button", { name: /refresh now/i }));

  // a routine reread starts while the refold is still out, and answers first
  vi.mocked(api.getRollingSummary).mockImplementationOnce(
    () => new Promise((resolve) => { answerRead = resolve; }));
  view.rerender(
    <SceneInspector cid="c" sid="s" refreshKey={1} onSceneChanged={() => {}} />);
  await waitFor(() => expect(answerRead).toBeDefined());
  await act(async () => {
    answerRead!({ summary: "The old summary.", at: 4, total: 4,
                  stale: false, every: 10, due: false });
  });
  await act(async () => {
    answerPost!({ summary: "The refolded summary.", at: 9, total: 9,
                  stale: false, every: 10, due: false, refreshed: true });
  });

  await screen.findByText("The refolded summary.");
});

test("Refresh is held while a turn is streaming into the scene", async () => {
  // A chat appends the player post before streaming and the reply only when it
  // lands, so a refold in between covers an unanswered post — and the reply is
  // an append, which does not invalidate the digest, so it can stay out of the
  // "current" summary until the next threshold.
  render(<SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}}
                         sceneLocked />);
  expect(await screen.findByRole("button", { name: /refresh now/i })).toBeDisabled();
});

test("a refresh error does not follow the reader to the next scene", async () => {
  // The token guard only retires a rejection that arrives AFTER navigation. One
  // that lands while the scene is still selected is stored, and nothing on the
  // scene-change path clears the panel's shared banner.
  vi.mocked(api.refreshRollingSummary).mockRejectedValueOnce({ detail: "OpenRouter key not set" });
  const view = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: /refresh now/i }));
  await screen.findByText("OpenRouter key not set");

  view.rerender(
    <SceneInspector cid="c" sid="s2" refreshKey={0} onSceneChanged={() => {}} />);
  await waitFor(() =>
    expect(screen.queryByText("OpenRouter key not set")).toBeNull());
});

test("a later successful read clears a settled refresh failure", async () => {
  // A manual refold fails; a later automatic one succeeds and bumps refreshKey.
  // The banner must not go on reporting a failure the panel recovered from.
  vi.mocked(api.refreshRollingSummary).mockRejectedValueOnce({ detail: "OpenRouter key not set" });
  const view = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: /refresh now/i }));
  await screen.findByText("OpenRouter key not set");

  vi.mocked(api.getRollingSummary).mockResolvedValueOnce({
    summary: "Recovered, and current.", at: 12, total: 12,
    stale: false, every: 10, due: false });
  view.rerender(
    <SceneInspector cid="c" sid="s" refreshKey={1} onSceneChanged={() => {}} />);

  await screen.findByText("Recovered, and current.");
  expect(screen.queryByText("OpenRouter key not set")).toBeNull();
});

test("moving the scene asks whether the summary is due", async () => {
  // A location move appends a transition post, which counts toward the
  // threshold like any other — and `onSceneChanged` only re-reads (#85).
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "dock", name: "The Night Dock" }]);
  renderInspector();
  fireEvent.change(await screen.findByLabelText("Move to location"),
                   { target: { value: "dock" } });
  fireEvent.click(screen.getByRole("button", { name: "Move to" }));
  await waitFor(() => expect(api.setSceneLocation).toHaveBeenCalled());
  // Without `force` — the server still decides whether to spend a call — and
  // bounded by the transcript length the panel just read, so the fold cannot
  // swallow a post the transition did not include.
  await waitFor(() => expect(api.refreshRollingSummary).toHaveBeenCalledWith(
    "c", "s", false, 0));
});

test("the first date set asks for a refold under the scene's NEW id", async () => {
  // Setting a date for the first time renames the scene file, and this branch
  // returns early to let the sid prop change re-run the load effects. Those
  // effects only READ, and a read never evaluates the gate — while the write
  // itself changed the scene's date FACT, which is part of the fold's validity
  // key. So the ask belongs here, against the id the rename produced: the old
  // one no longer names a file (#85).
  vi.mocked(api.setSceneDatetime).mockResolvedValue(
    { ok: true, advanced: false, friendly: "4 July 2026", id: "001--2026-07-04--s" });
  render(<SceneInspector cid="c" sid="s" refreshKey={0}
                         onSceneChanged={() => {}} onSceneRenamed={() => {}} />);
  fireEvent.change(await screen.findByLabelText("Scene date year"), { target: { value: "2026" } });
  const monthSelect = await screen.findByLabelText("Scene date month");
  await waitFor(() => expect(monthSelect).not.toBeDisabled());
  fireEvent.change(monthSelect, { target: { value: "07" } });
  const daySelect = screen.getByLabelText("Scene date day");
  await waitFor(() => expect(daySelect).not.toBeDisabled());
  fireEvent.change(daySelect, { target: { value: "4" } });
  fireEvent.click(screen.getByRole("button", { name: /set date/i }));

  await waitFor(() => expect(api.getRollingSummary).toHaveBeenCalledWith(
    "c", "001--2026-07-04--s"));
  await waitFor(() => expect(api.refreshRollingSummary).toHaveBeenCalledWith(
    "c", "001--2026-07-04--s", false, expect.any(Number)));
});

test("an automatic refold that answers late cannot undo a newer one", async () => {
  // Two transitions in quick succession each ask. The first fold finishes on the
  // server and releases its claim, so the second is not coalesced and really
  // does refold — and if the FIRST one's response is the slower of the two it
  // lands second, passes the scene-key check, and overwrites the newer summary
  // and coverage with its own older ones. Nothing is scheduled behind it to put
  // that right (#85).
  // Two places that are not where the scene already is: the picker offers
  // somewhere to GO, so the current location is not among its options.
  vi.mocked(api.listEntities).mockResolvedValue([
    { id: "dock", name: "The Night Dock" }, { id: "gate", name: "The Salt Gate" }]);
  let landFirst: ((v: RollingSummaryRefresh) => void) | undefined;
  vi.mocked(api.refreshRollingSummary)
    .mockImplementationOnce(() => new Promise((resolve) => { landFirst = resolve; }))
    .mockResolvedValueOnce({ summary: "The newer fold.", at: 12, total: 12,
                             stale: false, every: 10, due: false, refreshed: true });
  render(<SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);

  const picker = await screen.findByLabelText("Move to location");
  fireEvent.change(picker, { target: { value: "dock" } });
  fireEvent.click(screen.getByRole("button", { name: "Move to" }));
  await waitFor(() => expect(api.refreshRollingSummary).toHaveBeenCalledTimes(1));
  fireEvent.change(picker, { target: { value: "gate" } });
  fireEvent.click(screen.getByRole("button", { name: "Move to" }));

  await screen.findByText("The newer fold.");
  // ...and only now does the FIRST ask answer, describing less of the scene.
  await act(async () => {
    landFirst!({ summary: "The older fold.", at: 10, total: 10, stale: false,
                 every: 10, due: false, refreshed: true });
  });

  expect(screen.getByText("The newer fold.")).toBeInTheDocument();
  expect(screen.queryByText("The older fold.")).toBeNull();
});

test("a successful automatic refold retires the warnings it just disproved", async () => {
  // The panel has two ways of saying "what you are reading may be behind", and a
  // fold the server reconciled makes both false. Leaving them up left an earlier
  // provider failure sitting beside prose the POST had just made current, with
  // nothing later scheduled to retire it (#85).
  vi.mocked(api.listEntities).mockResolvedValue([{ id: "dock", name: "The Night Dock" }]);
  vi.mocked(api.refreshRollingSummary).mockRejectedValueOnce(
    { detail: "OpenRouter key not set" });
  render(<SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: /refresh now/i }));
  await screen.findByText("OpenRouter key not set");

  vi.mocked(api.refreshRollingSummary).mockResolvedValueOnce({
    summary: "Mara reaches the salt gate.", at: 12, total: 12, stale: false,
    every: 10, due: false, refreshed: true });
  fireEvent.change(await screen.findByLabelText("Move to location"),
                   { target: { value: "dock" } });
  fireEvent.click(screen.getByRole("button", { name: "Move to" }));

  await screen.findByText("Mara reaches the salt gate.");
  expect(screen.queryByText("OpenRouter key not set")).toBeNull();
  expect(screen.queryByText(/latest read failed/i)).toBeNull();
});

// --- pins & excludes (#129) --------------------------------------------------

// ---- quick replies share the two manual tasks ----

test("a task the strip is running disables its manual button", async () => {
  const { rerender } = render(<MemoryRouter><SceneInspector cid="c" sid="s" refreshKey={0}
    onSceneChanged={() => {}} stripTasks={{ rolling_summary: true }} /></MemoryRouter>);
  expect(await screen.findByRole("button", { name: /refresh now/i })).toBeDisabled();
  expect(screen.getByRole("button", { name: /ask now/i })).toBeEnabled();
  rerender(<MemoryRouter><SceneInspector cid="c" sid="s" refreshKey={0}
    onSceneChanged={() => {}} stripTasks={{ scene_break: true }} /></MemoryRouter>);
  expect(await screen.findByRole("button", { name: /ask now/i })).toBeDisabled();
  expect(screen.getByRole("button", { name: /refresh now/i })).toBeEnabled();
});

test("Not here is held while the strip has a scene-break question out", async () => {
  // Dismissing under a strip-started question would let that question land
  // afterwards and raise the prompt the player just turned down.
  vi.mocked(api.getSceneBreak).mockResolvedValue(BREAK_YES);
  render(<MemoryRouter><SceneInspector cid="c" sid="s" refreshKey={0}
    onSceneChanged={() => {}} stripTasks={{ scene_break: true }} /></MemoryRouter>);
  expect(await screen.findByRole("button", { name: /not here/i })).toBeDisabled();
});

test("its manual buttons report themselves busy", async () => {
  const onTaskBusy = vi.fn();
  render(<MemoryRouter><SceneInspector cid="c" sid="s" refreshKey={0}
    onSceneChanged={() => {}} onTaskBusy={onTaskBusy} /></MemoryRouter>);
  fireEvent.click(await screen.findByRole("button", { name: /refresh now/i }));
  await waitFor(() => expect(onTaskBusy).toHaveBeenLastCalledWith("rolling_summary", false));
  expect(onTaskBusy.mock.calls[0]).toEqual(["rolling_summary", true]);
  onTaskBusy.mockClear();
  fireEvent.click(screen.getByRole("button", { name: /ask now/i }));
  await waitFor(() => expect(onTaskBusy).toHaveBeenLastCalledWith("scene_break", false));
  expect(onTaskBusy.mock.calls[0]).toEqual(["scene_break", true]);
});
