import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { api } from "../api/client";
import type {
PromptDiff, PromptEntry, PromptSnapshot
} from "../api/types";
import { fixture } from "../testkit/fixture";
import { FROZEN, renderInspector, resetSceneInspectorMocks, TURNS } from "../testkit/sceneInspectorHarness";
import { SceneInspector } from "./SceneInspector";
vi.mock("../api/client", async () => (await import("../testkit/sceneInspectorMocks")).sceneInspectorApiMock());
vi.mock("../api/models", async () => (await import("../testkit/sceneInspectorMocks")).sceneInspectorModelsMock());
vi.mock("./ResponseTargetsPicker", async () => (await import("../testkit/sceneInspectorMocks")).responseTargetsMock());
vi.mock("./AuthorsNotesPanel", async () => (await import("../testkit/sceneInspectorMocks")).authorsNotesMock());

beforeEach(resetSceneInspectorMocks);

// ---- comparing a captured turn against another composition (#130) ----

const DIFF = fixture<PromptDiff>({
  base: { id: "000001", task: "chat", ts: "2026-08-06T11:00:00Z", model: "m",
          total_tokens: 80, dropped_tokens: 0, budget_tokens: 0 },
  head: { id: "live", task: "live", ts: "", model: "m",
          total_tokens: 120, dropped_tokens: 0, budget_tokens: 0 },
  sections: [{
    id: "world", label: "World info", status: "changed",
    base: { label: "World info", tokens: 80, dropped: false, trimmed: 0, pinned: false },
    head: { label: "World info", tokens: 120, dropped: false, trimmed: 0, pinned: false },
    diff: [{ op: "insert", text: "the pact was signed at dusk" }],
  }],
});

async function openTurnAndCompare(value: string) {
  vi.mocked(api.listScenePrompts).mockResolvedValue(fixture({ entries: TURNS }));
  vi.mocked(api.getScenePrompt).mockResolvedValue(FROZEN);
  vi.mocked(api.getScenePromptDiff).mockResolvedValue(DIFF);
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));
  await screen.findByText("the lore as it stood then");
  fireEvent.change(await screen.findByLabelText("Compare with"),
                   { target: { value } });
}

test("the comparison picker is offered only from a past turn", async () => {
  // The live composition has nothing to be the "before" of.
  vi.mocked(api.listScenePrompts).mockResolvedValue(fixture({ entries: TURNS }));
  vi.mocked(api.getScenePrompt).mockResolvedValue(FROZEN);
  renderInspector();
  await screen.findByText("lore text");
  expect(screen.queryByLabelText("Compare with")).toBeNull();

  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));
  await screen.findByLabelText("Compare with");
});

test("comparing a turn with the live preview replaces the breakdown with the diff", async () => {
  await openTurnAndCompare("live");

  await waitFor(() => expect(api.getScenePromptDiff)
    .toHaveBeenCalledWith("c", "s", "000001", "live"));
  await screen.findByText("the pact was signed at dusk");
  // Neither breakdown is on screen underneath it: the reader asked what moved.
  expect(screen.queryByText("the lore as it stood then")).toBeNull();
  expect(screen.queryByText("lore text")).toBeNull();
});

test("the turn being compared against is offered, and the one being compared is not", async () => {
  vi.mocked(api.listScenePrompts).mockResolvedValue(fixture({ entries: TURNS }));
  vi.mocked(api.getScenePrompt).mockResolvedValue(FROZEN);
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));

  const picker = await screen.findByLabelText<HTMLSelectElement>("Compare with");
  const values = Array.from(picker.options).map((o) => o.value);
  expect(values).toContain("live");
  expect(values).toContain("000002");
  expect(values).not.toContain("000001");     // that is the turn being compared
});

// ---- decisions, listed apart from turns (roadmap 01b) ----

const DECISIONS: PromptEntry[] = [
  { id: "000005", scene: "s", ts: "2026-08-06T12:40:00Z", task: "scene-break",
    operation: "decide", model: "m", total_tokens: 40, dropped_tokens: 0, budget_tokens: 0 },
  { id: "000004", scene: "s", ts: "2026-08-06T12:30:00Z", task: "scene-break",
    operation: "decide", model: "m", total_tokens: 40, dropped_tokens: 0, budget_tokens: 0 },
  // A speaker pick filed before decisions had an operation: still a decision.
  { id: "000003", scene: "s", ts: "2026-08-06T11:30:00Z", task: "response-selector",
    model: "m", total_tokens: 30, dropped_tokens: 0, budget_tokens: 0 },
];

const FROZEN_DECISION: PromptSnapshot = {
  id: "000005", ts: "2026-08-06T12:40:00Z", task: "scene-break", operation: "decide",
  model: "m", total_tokens: 40, dropped_tokens: 0, budget_tokens: 0,
  sections: [
    { id: "message_0", label: "user", text: "Is the scene over yet?", tokens: 40,
      tier: "lock-in", dropped: false, trimmed: 0 },
    { id: "decision", label: "decision", text: '{"task": "scene-break", "calls": []}',
      tokens: 0, tier: "lock-in", dropped: false, trimmed: 0 },
  ],
};

test("turn history hides decisions, which a collapsed section lists apart", async () => {
  vi.mocked(api.listScenePrompts).mockResolvedValue(fixture({ entries: [...DECISIONS, ...TURNS] }));
  renderInspector();
  await screen.findByText("Regenerate");
  expect(screen.queryByText("Scene-break check")).toBeNull();
  expect(screen.queryByText("Speaker pick")).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: /^Decisions/ }));
  expect(await screen.findAllByText("Scene-break check")).toHaveLength(2);
  await screen.findByText("Speaker pick");
});

test("a scene with only decisions has no captured turns", async () => {
  vi.mocked(api.listScenePrompts).mockResolvedValue(fixture({ entries: DECISIONS }));
  renderInspector();
  await screen.findByText("No captured turns yet.");
});

test("a decision opens read-only and compares only with its own task", async () => {
  vi.mocked(api.listScenePrompts).mockResolvedValue(fixture({ entries: [...DECISIONS, ...TURNS] }));
  vi.mocked(api.getScenePrompt).mockResolvedValue(FROZEN_DECISION);
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /^Decisions/ }));
  fireEvent.click((await screen.findAllByRole("button", { name: /^Scene-break check/ }))[0]);

  await waitFor(() => expect(api.getScenePrompt).toHaveBeenCalledWith("c", "s", "000005"));
  await screen.findByText("outcome · not sent");
  expect(screen.queryByRole("textbox")).toBeNull();
  const picker = await screen.findByLabelText<HTMLSelectElement>("Compare with");
  const values = Array.from(picker.options).map((o) => o.value);
  expect(values).toEqual(["", "000004"]);
});

test("two captured turns compare against each other, oldest end first", async () => {
  // The rail is newest-first, so a reader picking a row means "what changed
  // since then" — and a diff whose insertions are what the OLDER prompt had is
  // one running backwards through time.
  await openTurnAndCompare("000002");
  await waitFor(() => expect(api.getScenePromptDiff)
    .toHaveBeenCalledWith("c", "s", "000001", "000002"));
  await screen.findByText("the pact was signed at dusk");
});

test("picking an OLDER turn still asks for the diff oldest end first", async () => {
  vi.mocked(api.listScenePrompts).mockResolvedValue(fixture({ entries: TURNS }));
  vi.mocked(api.getScenePrompt).mockResolvedValue(fixture({ ...FROZEN, id: "000002", task: "regenerate" }));
  vi.mocked(api.getScenePromptDiff).mockResolvedValue(DIFF);
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /^Regenerate/ }));
  await screen.findByText("the lore as it stood then");

  fireEvent.change(await screen.findByLabelText("Compare with"),
                   { target: { value: "000001" } });
  await waitFor(() => expect(api.getScenePromptDiff)
    .toHaveBeenCalledWith("c", "s", "000001", "000002"));
});

test("clearing the picker returns to the frozen turn", async () => {
  await openTurnAndCompare("live");
  await screen.findByText("the pact was signed at dusk");

  fireEvent.change(screen.getByLabelText("Compare with"), { target: { value: "" } });
  await screen.findByText("the lore as it stood then");
  expect(screen.queryByText("the pact was signed at dusk")).toBeNull();
});

test("comparing against the live preview survives moving to another turn", async () => {
  // "At which turn did this section change?" is walked by clicking down the
  // rail; re-picking the comparison at every step would make that unusable.
  await openTurnAndCompare("live");
  await screen.findByText("the pact was signed at dusk");
  // Each row answers with its own snapshot, or the second click would land the
  // first turn again and the assertion below would pass without moving.
  vi.mocked(api.getScenePrompt).mockImplementation((_c, _s, eid) =>
    Promise.resolve(fixture({ ...FROZEN, id: eid, task: eid === "000002" ? "regenerate" : "chat" })));

  fireEvent.click(screen.getByRole("button", { name: /^Regenerate/ }));
  await waitFor(() => expect(api.getScenePromptDiff)
    .toHaveBeenCalledWith("c", "s", "000002", "live"));
  expect(screen.getByLabelText<HTMLSelectElement>("Compare with").value).toBe("live");
});

test("comparing against a specific turn does NOT survive moving to another", async () => {
  // The turn just clicked can be the one being compared against, and an entry
  // diffed with itself is a panel that has quietly stopped answering.
  await openTurnAndCompare("000002");
  await screen.findByText("the pact was signed at dusk");

  fireEvent.click(screen.getByRole("button", { name: /^Regenerate/ }));
  await screen.findByText("the lore as it stood then");
  expect(screen.getByLabelText<HTMLSelectElement>("Compare with").value).toBe("");
});

test("going back to live context ends the comparison", async () => {
  await openTurnAndCompare("live");
  await screen.findByText("the pact was signed at dusk");

  fireEvent.click(screen.getByRole("button", { name: /Back to live context/ }));
  await screen.findByText("lore text");
  expect(screen.queryByText("the pact was signed at dusk")).toBeNull();
});

test.each(["live", "000002"])("focus save refreshes only the live end of a %s comparison", async (against) => {
  await openTurnAndCompare(against);
  await screen.findByText("the pact was signed at dusk");
  const reads = vi.mocked(api.getScenePromptDiff).mock.calls.length;
  const snapshotReads = vi.mocked(api.getScenePrompt).mock.calls.length;
  // The field publishes its canonical metadata; the selected capture stays
  // selected. Only a comparison whose OTHER end is live can change here.
  fireEvent.click(screen.getByRole("button", { name: /^Scene focus/ }));
  fireEvent.click(await screen.findByRole("button", { name: /Edit scene focus/ }));
  fireEvent.change(screen.getByRole("textbox", { name: "Scene focus" }), { target: { value: "Seraphine waits." } });
  vi.mocked(api.setSceneFocus).mockResolvedValueOnce({ text: "Seraphine waits.", status: "ok" });
  fireEvent.click(screen.getByRole("button", { name: /^Save$/ }));
  await screen.findByText("Seraphine waits.");
  if (against === "live") await waitFor(() => expect(api.getScenePromptDiff).toHaveBeenCalledTimes(reads + 1));
  else expect(api.getScenePromptDiff).toHaveBeenCalledTimes(reads);
  expect(api.getScenePrompt).toHaveBeenCalledTimes(snapshotReads);
  expect(screen.getByLabelText<HTMLSelectElement>("Compare with").value).toBe(against);
});

test("a landed turn re-reads the comparison, because the live end has moved", async () => {
  // A diff against a preview that has since changed describes a prompt nobody
  // would send. `refreshKey` is bumped by the very turn that moved it.
  vi.mocked(api.listScenePrompts).mockResolvedValue(fixture({ entries: TURNS }));
  vi.mocked(api.getScenePrompt).mockResolvedValue(FROZEN);
  vi.mocked(api.getScenePromptDiff).mockResolvedValue(DIFF);
  const { rerender } = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));
  await screen.findByText("the lore as it stood then");
  fireEvent.change(await screen.findByLabelText("Compare with"),
                   { target: { value: "live" } });
  await waitFor(() => expect(api.getScenePromptDiff).toHaveBeenCalledTimes(1));

  rerender(<SceneInspector cid="c" sid="s" refreshKey={1} onSceneChanged={() => {}} />);
  await waitFor(() => expect(api.getScenePromptDiff).toHaveBeenCalledTimes(2));
  // ...and the reader is still where they were, not yanked back to live.
  await screen.findByText("the pact was signed at dusk");
});

test("a turn landing does not re-read a comparison of two frozen turns", async () => {
  // Both ends are frozen, so a completed turn cannot change the answer and the
  // request could only return what is already on screen.
  vi.mocked(api.listScenePrompts).mockResolvedValue(fixture({ entries: TURNS }));
  vi.mocked(api.getScenePrompt).mockResolvedValue(FROZEN);
  vi.mocked(api.getScenePromptDiff).mockResolvedValue(DIFF);
  const { rerender } = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));
  await screen.findByText("the lore as it stood then");
  fireEvent.change(await screen.findByLabelText("Compare with"), { target: { value: "000002" } });
  await waitFor(() => expect(api.getScenePromptDiff).toHaveBeenCalledTimes(1));

  rerender(<SceneInspector cid="c" sid="s" refreshKey={1} onSceneChanged={() => {}} />);
  await screen.findByText("the pact was signed at dusk");
  expect(api.getScenePromptDiff).toHaveBeenCalledTimes(1);
});

test("a live comparison marks itself stale while the replacement is in flight", async () => {
  // Blanking it would flip the panel to this turn's breakdown — a different
  // kind of content, and one that is refetching too.
  vi.mocked(api.listScenePrompts).mockResolvedValue(fixture({ entries: TURNS }));
  vi.mocked(api.getScenePrompt).mockResolvedValue(FROZEN);
  vi.mocked(api.getScenePromptDiff).mockResolvedValue(DIFF);
  const { rerender } = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));
  await screen.findByText("the lore as it stood then");
  fireEvent.change(await screen.findByLabelText("Compare with"), { target: { value: "live" } });
  await screen.findByText("the pact was signed at dusk");

  let release: (v: PromptDiff) => void = () => {};
  vi.mocked(api.getScenePromptDiff).mockReturnValue(new Promise((r) => { release = r; }));
  rerender(<SceneInspector cid="c" sid="s" refreshKey={1} onSceneChanged={() => {}} />);

  await screen.findByText(/A turn has landed since this was computed/);
  await screen.findByText("the pact was signed at dusk");   // still readable, not blanked
  release(DIFF);
  await waitFor(() =>
    expect(screen.queryByText(/A turn has landed since this was computed/)).toBeNull());
});

test("moving to another turn with a live comparison selected stays in compare mode", async () => {
  // The picker keeps saying "The live preview" across the move, so the panel
  // must not drop to the ordinary breakdown underneath it while the
  // replacement diff is in flight — two controls contradicting each other, and
  // indefinitely so if that request never answers.
  vi.mocked(api.listScenePrompts).mockResolvedValue(fixture({ entries: TURNS }));
  vi.mocked(api.getScenePrompt).mockImplementation((_c, _s, eid) =>
    Promise.resolve(fixture({ ...FROZEN, id: eid, task: eid === "000002" ? "regenerate" : "chat" })));
  vi.mocked(api.getScenePromptDiff).mockResolvedValue(DIFF);
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));
  await screen.findByText("the lore as it stood then");
  fireEvent.change(await screen.findByLabelText("Compare with"), { target: { value: "live" } });
  await screen.findByText("the pact was signed at dusk");

  let release: (v: PromptDiff) => void = () => {};
  vi.mocked(api.getScenePromptDiff).mockReturnValue(new Promise((r) => { release = r; }));
  fireEvent.click(screen.getByRole("button", { name: /^Regenerate/ }));

  await screen.findByText("Comparing…");
  expect(screen.getByLabelText<HTMLSelectElement>("Compare with").value).toBe("live");
  // ...and specifically NOT the frozen breakdown the fall-through used to show
  expect(screen.queryByText("the lore as it stood then")).toBeNull();

  release(DIFF);
  await screen.findByText("the pact was signed at dusk");
});

test("the very first comparison also waits rather than showing the breakdown", async () => {
  vi.mocked(api.listScenePrompts).mockResolvedValue(fixture({ entries: TURNS }));
  vi.mocked(api.getScenePrompt).mockResolvedValue(FROZEN);
  let release: (v: PromptDiff) => void = () => {};
  vi.mocked(api.getScenePromptDiff).mockReturnValue(new Promise((r) => { release = r; }));
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));
  await screen.findByText("the lore as it stood then");

  fireEvent.change(await screen.findByLabelText("Compare with"), { target: { value: "live" } });
  await screen.findByText("Comparing…");
  release(DIFF);
  await screen.findByText("the pact was signed at dusk");
});

test("a failed comparison falls back to the turn rather than waiting forever", async () => {
  // The pending state must not outlive the request: the catch clears the
  // picker, so the panel has a comparison to fall back FROM.
  vi.mocked(api.listScenePrompts).mockResolvedValue(fixture({ entries: TURNS }));
  vi.mocked(api.getScenePrompt).mockResolvedValue(FROZEN);
  vi.mocked(api.getScenePromptDiff).mockRejectedValue({ status: 404, detail: "gone" });
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));
  await screen.findByText("the lore as it stood then");

  fireEvent.change(await screen.findByLabelText("Compare with"), { target: { value: "live" } });
  await screen.findByText(/aged out of the log/);
  await screen.findByText("the lore as it stood then");
  expect(screen.queryByText("Comparing…")).toBeNull();
});

test("an evicted turn on either end says so rather than blanking", async () => {
  vi.mocked(api.listScenePrompts).mockResolvedValue(fixture({ entries: TURNS }));
  vi.mocked(api.getScenePrompt).mockResolvedValue(FROZEN);
  vi.mocked(api.getScenePromptDiff).mockRejectedValue({ status: 404, detail: "not found" });
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));
  await screen.findByText("the lore as it stood then");

  fireEvent.change(await screen.findByLabelText("Compare with"),
                   { target: { value: "live" } });
  await screen.findByText(/aged out of the log/);
  await screen.findByText("the lore as it stood then");   // still on the frozen view
});

test("a failed comparison's banner clears when the reader tries again", async () => {
  // Otherwise the retry renders its diff underneath "those turns could not be
  // compared", which reads as a diff that failed.
  vi.mocked(api.listScenePrompts).mockResolvedValue(fixture({ entries: TURNS }));
  vi.mocked(api.getScenePrompt).mockResolvedValue(FROZEN);
  vi.mocked(api.getScenePromptDiff).mockRejectedValueOnce({ status: 404, detail: "gone" });
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));
  await screen.findByText("the lore as it stood then");

  fireEvent.change(await screen.findByLabelText("Compare with"), { target: { value: "live" } });
  await screen.findByText(/aged out of the log/);

  vi.mocked(api.getScenePromptDiff).mockResolvedValue(DIFF);
  fireEvent.change(screen.getByLabelText("Compare with"), { target: { value: "live" } });
  await screen.findByText("the pact was signed at dusk");
  expect(screen.queryByText(/aged out of the log/)).toBeNull();
});

test("a diff arriving after the reader moved on is dropped, not shown", async () => {
  vi.mocked(api.listScenePrompts).mockResolvedValue(fixture({ entries: TURNS }));
  vi.mocked(api.getScenePrompt).mockResolvedValue(FROZEN);
  let release: (v: PromptDiff) => void = () => {};
  vi.mocked(api.getScenePromptDiff).mockReturnValue(new Promise((r) => { release = r; }));

  const { rerender } = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));
  await screen.findByText("the lore as it stood then");
  fireEvent.change(await screen.findByLabelText("Compare with"),
                   { target: { value: "live" } });

  rerender(<SceneInspector cid="c" sid="s2" refreshKey={0} onSceneChanged={() => {}} />);
  release(DIFF);

  await screen.findByText("lore text");
  expect(screen.queryByText("the pact was signed at dusk")).toBeNull();
});

test("a turn that fails to load ends the comparison rather than hiding it", async () => {
  // Clearing `frozen` returns the panel to live context, which is where "Back
  // to live context" lands — and that clears `compare`. Leaving it set made the
  // two exits differ: the choice would be invisible, and the NEXT turn clicked
  // would open straight into a comparison the reader never saw themselves ask
  // for. Sticky across turns, not across leaving.
  vi.mocked(api.listScenePrompts).mockResolvedValue(fixture({ entries: TURNS }));
  vi.mocked(api.getScenePrompt).mockResolvedValue(FROZEN);
  vi.mocked(api.getScenePromptDiff).mockResolvedValue(DIFF);
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));
  await screen.findByText("the lore as it stood then");
  fireEvent.change(await screen.findByLabelText("Compare with"), { target: { value: "live" } });
  await screen.findByText("the pact was signed at dusk");

  // the next turn clicked has aged out
  vi.mocked(api.getScenePrompt).mockRejectedValue({ status: 404, detail: "gone" });
  fireEvent.click(screen.getByRole("button", { name: /^Regenerate/ }));
  await screen.findByText(/aged out of the log/);

  // ...and a later turn that DOES load opens as itself, not as a comparison
  vi.mocked(api.getScenePrompt).mockResolvedValue(FROZEN);
  fireEvent.click(screen.getByRole("button", { name: /^Send/ }));
  await screen.findByText("the lore as it stood then");
  expect(screen.getByLabelText<HTMLSelectElement>("Compare with").value).toBe("");
  expect(screen.queryByText("the pact was signed at dusk")).toBeNull();
});

test("a comparison whose turn ages out keeps its option in the picker", async () => {
  // A turn-against-turn diff is frozen at both ends and so is not re-read, but
  // retention can still evict the entry it names. Without an option the browser
  // falls back to the first one and the picker contradicts the panel below it;
  // clearing would throw away a comparison the reader is in the middle of.
  vi.mocked(api.listScenePrompts).mockResolvedValue(fixture({ entries: TURNS }));
  vi.mocked(api.getScenePrompt).mockResolvedValue(FROZEN);
  vi.mocked(api.getScenePromptDiff).mockResolvedValue(DIFF);
  const { rerender } = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));
  await screen.findByText("the lore as it stood then");
  fireEvent.change(await screen.findByLabelText("Compare with"), { target: { value: "000002" } });
  await screen.findByText("the pact was signed at dusk");

  // the compared turn falls out of the retention window
  vi.mocked(api.listScenePrompts).mockResolvedValue(fixture({ entries: [TURNS[1]] }));
  rerender(<SceneInspector cid="c" sid="s" refreshKey={1} onSceneChanged={() => {}} />);

  await screen.findByText(/aged out of the log/);
  expect(screen.getByLabelText<HTMLSelectElement>("Compare with").value).toBe("000002");
  await screen.findByText("the pact was signed at dusk");   // still on screen
});

