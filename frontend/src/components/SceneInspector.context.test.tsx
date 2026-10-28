import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { api } from "../api/client";
import { getModels } from "../api/models";
import type {
PromptSnapshot
} from "../api/types";
import { fixture } from "../testkit/fixture";
import { FROZEN, renderInspector, resetSceneInspectorMocks, TURNS } from "../testkit/sceneInspectorHarness";
import { SceneInspector } from "./SceneInspector";
vi.mock("../api/client", async () => (await import("../testkit/sceneInspectorMocks")).sceneInspectorApiMock());
vi.mock("../api/models", async () => (await import("../testkit/sceneInspectorMocks")).sceneInspectorModelsMock());
vi.mock("./ResponseTargetsPicker", async () => (await import("../testkit/sceneInspectorMocks")).responseTargetsMock());
vi.mock("./AuthorsNotesPanel", async () => (await import("../testkit/sceneInspectorMocks")).authorsNotesMock());

beforeEach(resetSceneInspectorMocks);

test("scene focus refreshes only live context, never the parent's transcript reload", async () => {
  const changed = vi.fn();
  vi.mocked(api.setSceneFocus).mockResolvedValue({ text: "Seraphine waits.", status: "ok" });
  renderInspector(changed);
  await waitFor(() => expect(api.getSceneContext).toHaveBeenCalled());
  const reads = vi.mocked(api.getSceneContext).mock.calls.length;
  fireEvent.click(await screen.findByText("Scene focus"));
  await screen.findByText("Let Mara choose.");
  fireEvent.click(screen.getByRole("button", { name: /edit scene focus/i }));
  fireEvent.change(screen.getByRole("textbox", { name: "Scene focus" }), { target: { value: "Seraphine waits." } });
  fireEvent.click(screen.getByRole("button", { name: /^save/i }));
  await waitFor(() => expect(api.getSceneContext).toHaveBeenCalledTimes(reads + 1));
  expect(changed).not.toHaveBeenCalled();
  expect(api.setSceneFocus).toHaveBeenCalledWith("c", "s", "Seraphine waits.");
});

test("focus publication fetches a new composition and rejects the late pre-save transport response", async () => {
  const actual = await vi.importActual<typeof import("../api/client")>("../api/client");
  const context = (text: string) => ({ model: "m", total_tokens: 1, dropped_tokens: 0, budget_tokens: 0,
    sections: [{ id: "scene_focus", label: "Scene focus", text, tokens: 1, tier: "lock-in", dropped: false, trimmed: 0 }] });
  let finishOld: (response: Response) => void = () => {};
  let finishNew: (response: Response) => void = () => {};
  const fetchMock = vi.fn()
    .mockImplementationOnce(() => new Promise<Response>((resolve) => { finishOld = resolve; }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ text: "Seraphine waits.", status: "ok" })))
    .mockImplementationOnce(() => new Promise<Response>((resolve) => { finishNew = resolve; }));
  vi.stubGlobal("fetch", fetchMock);
  vi.mocked(api.getSceneContext).mockImplementation(actual.api.getSceneContext);
  vi.mocked(api.setSceneFocus).mockImplementation(actual.api.setSceneFocus);
  try {
    renderInspector();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: /^Scene focus/ }));
    fireEvent.click(await screen.findByRole("button", { name: /Edit scene focus/ }));
    fireEvent.change(screen.getByRole("textbox", { name: "Scene focus" }), { target: { value: "Seraphine waits." } });
    fireEvent.click(screen.getByRole("button", { name: /^Save$/ }));
    await screen.findByText("Seraphine waits.");
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    await act(async () => { finishNew(new Response(JSON.stringify(context("Current composition.")))); });
    await screen.findByText("Current composition.");
    await act(async () => { finishOld(new Response(JSON.stringify(context("Stale composition.")))); });
    expect(screen.queryByText("Stale composition.")).toBeNull();
    expect(screen.getByText("Current composition.")).toBeInTheDocument();
  } finally {
    finishOld(new Response(JSON.stringify(context("Stale composition."))));
    finishNew(new Response(JSON.stringify(context("Current composition."))));
    vi.unstubAllGlobals();
  }
});

test("context section expands to show the text", async () => {
  renderInspector();
  const summary = await screen.findByText(/World info/);
  fireEvent.click(summary);
  await screen.findByText("lore text");
});

test("world info lists each entry with its reason", async () => {
  vi.mocked(api.getSceneContext).mockResolvedValue({
    model: "m", total_tokens: 100, dropped_tokens: 0, budget_tokens: 0,
    sections: [{
      id: "world_info", label: "World info", text: "lore text", tokens: 100,
      tier: "spotlight", dropped: false, trimmed: 0,
      names: { "lore:realm-charter": "Realm charter" },
      entries: [
        { ref: "lore:saltmarch", name: "Saltmarch", kind: "lore", secrecy: "public", priority: 100,
          keep: false, level: 0, shed: false,
          reason: { type: "key", key: "Saltmarch", secondary: null, post: 41, seed: false } },
        { ref: "lore:tide-tables", name: "Tide tables", kind: "lore", secrecy: "public", priority: 100,
          keep: false, level: 1, shed: true,
          reason: { type: "recursion", via: "lore:realm-charter", key: "tide" } },
      ],
      held_back: [{ ref: "lore:winifred-rumour", name: "Winifred's rumour",
                    reason: { type: "cooldown", remaining: 3 } }],
    }],
  });
  renderInspector();
  fireEvent.click(await screen.findByText(/World info/));
  const kept = (await screen.findByText("Saltmarch")).closest("li")!;
  expect(kept.textContent).toContain("key 'Saltmarch' in post #41");
  expect(kept.className).not.toContain("shed");
  const shed = screen.getByText("Tide tables").closest("li")!;
  expect(shed.className).toContain("shed");
  expect(shed.textContent).toContain("pulled in by Realm charter");
  expect(shed.textContent).toContain("shed: budget");
  expect(screen.getByText("Held back")).toBeInTheDocument();
  expect(screen.getByText("Winifred's rumour").closest("li")!.textContent)
    .toContain("on cooldown — 3 posts");
});

test("a World info that sent nothing still lists what cooldown held back", async () => {
  vi.mocked(api.getSceneContext).mockResolvedValue({
    model: "m", total_tokens: 0, dropped_tokens: 0, budget_tokens: 0,
    sections: [{
      id: "world_info", label: "World info", text: "", tokens: 0,
      tier: "spotlight", dropped: false, trimmed: 0, names: {}, entries: [],
      held_back: [{ ref: "lore:winifred-rumour", name: "Winifred's rumour",
                    reason: { type: "cooldown", remaining: 2 } }],
    }],
  });
  renderInspector();
  fireEvent.click(await screen.findByText(/World info/));
  expect(screen.getByText("Winifred's rumour").closest("li")!.textContent)
    .toContain("on cooldown — 2 posts");
  expect(document.querySelector(".ctx-text")).toBeNull();
});

test("a capture without entries still renders", async () => {
  // A prompt recorded before rows carried entries: the section expands to its
  // text and draws no list and no "Held back" heading.
  renderInspector();
  fireEvent.click(await screen.findByText(/World info/));
  await screen.findByText("lore text");
  expect(document.querySelector(".ctx-entries")).toBeNull();
  expect(screen.queryByText("Held back")).not.toBeInTheDocument();
});

test("the Context section header still shows the percentage badge and collapses as a whole", async () => {
  renderInspector();
  await screen.findByText(/World info/);
  await screen.findByText("≈10%");
  const header = screen.getByRole("button", { name: /context/i });
  fireEvent.click(header);
  expect(screen.queryByText(/World info/)).not.toBeInTheDocument();
});

test("a shut Context section does not recompose the prompt after a turn; opening it does", async () => {
  // GET /context has the server assemble the whole next prompt. Read on every
  // `refreshKey` bump it cost that after every turn, for a section the reader
  // had closed.
  localStorage.setItem("grimoire.inspector.sections", JSON.stringify({ context: true }));
  const view = (key: number) => (
    <MemoryRouter><SceneInspector cid="c" sid="s" refreshKey={key}
                                  onSceneChanged={() => {}} /></MemoryRouter>);
  const { rerender } = render(view(0));
  await screen.findByText("Active characters");
  rerender(view(1));                                   // a turn landed
  await screen.findByText("Active characters");
  expect(api.getSceneContext).not.toHaveBeenCalled();
  expect(screen.queryByText("≈10%")).toBeNull();       // no figure nobody read

  fireEvent.click(screen.getByRole("button", { name: /^context/i }));
  expect(await screen.findByText(/World info/)).toBeInTheDocument();
  expect(api.getSceneContext).toHaveBeenCalledTimes(1);
  // Shut and reopened within the same turn: the answer on hand is still the
  // current one, so it is not asked for again.
  fireEvent.click(screen.getByRole("button", { name: /^context/i }));
  fireEvent.click(screen.getByRole("button", { name: /^context/i }));
  await screen.findByText(/World info/);
  expect(api.getSceneContext).toHaveBeenCalledTimes(1);
});

test("an open Context section is re-read once per turn", async () => {
  const view = (key: number) => (
    <MemoryRouter><SceneInspector cid="c" sid="s" refreshKey={key}
                                  onSceneChanged={() => {}} /></MemoryRouter>);
  const { rerender } = render(view(0));
  await screen.findByText(/World info/);
  expect(api.getSceneContext).toHaveBeenCalledTimes(1);
  rerender(view(1));
  await screen.findByText(/World info/);
  expect(api.getSceneContext).toHaveBeenCalledTimes(2);
});

test("a section the budget dropped is shown as dropped, not hidden", async () => {
  // The whole point of reporting drops is that the user can see them; a
  // dropped section that simply vanished would be the silent truncation the
  // packer replaced.
  vi.mocked(api.getSceneContext).mockResolvedValue({
    model: "m", total_tokens: 100, dropped_tokens: 40, budget_tokens: 120,
    sections: [
      { label: "World info", text: "lore text", tokens: 100, tier: "spotlight", dropped: false, trimmed: 0 },
      { label: "Earlier scenes", text: "old scene", tokens: 40, tier: "archive", dropped: true, trimmed: 0 },
    ],
  });
  renderInspector();
  const dropped = await screen.findByText("Earlier scenes");
  expect(dropped.closest("details")!.className).toContain("dropped");
  await screen.findByText("dropped");
  await screen.findByText(/40 tok dropped to fit the budget/i);
});

test("the two off-scene cast tiers read as separate rows with their own numbers", async () => {
  // The backend splits the directory into a section per tier (#2) precisely so
  // the unbounded one can be priced on its own; the panel has to show them as
  // two readouts rather than collapsing them back into a single row.
  vi.mocked(api.getSceneContext).mockResolvedValue({
    model: "m", total_tokens: 100, dropped_tokens: 0, budget_tokens: 200,
    sections: [
      { label: "Off-scene cast · active elsewhere", text: "Winifred: she counts the tide.",
        tokens: 20, tier: "background", dropped: false, trimmed: 0 },
      { label: "Off-scene cast · known to exist", text: "Mara: a courier with cold hands.",
        tokens: 80, tier: "background", dropped: false, trimmed: 0 },
    ],
  });
  renderInspector();
  const active = await screen.findByText("Off-scene cast · active elsewhere");
  const known = await screen.findByText("Off-scene cast · known to exist");
  expect(active.closest("details")).not.toBe(known.closest("details"));
  await screen.findByText("20 · 10%");
  await screen.findByText("80 · 40%");
});

test("percentages measure against the configured budget, not the model window", async () => {
  vi.mocked(api.getSceneContext).mockResolvedValue({
    model: "m", total_tokens: 100, dropped_tokens: 0, budget_tokens: 200,
    sections: [{ label: "World info", text: "lore text", tokens: 100,
                 tier: "spotlight", dropped: false, trimmed: 0 }],
  });
  renderInspector();
  // 100 of a 200-token budget is 50%, not 10% of the model's 1000-token window
  await screen.findByText("≈50%");
  await screen.findByText(/100 \/ 200 tok/);
});

test("a trimmed history says how many turns went", async () => {
  vi.mocked(api.getSceneContext).mockResolvedValue({
    model: "m", total_tokens: 100, dropped_tokens: 0, budget_tokens: 200,
    sections: [{ label: "Conversation history", text: "turns", tokens: 100,
                 tier: "history", dropped: false, trimmed: 3 }],
  });
  renderInspector();
  await screen.findByText("3 trimmed");
});

test("percentages use the smaller of the budget and the model window", async () => {
  // A 32k budget left over from a bigger model would otherwise report a full
  // 8k window as a quarter used — hiding the overflow this panel exists to show.
  vi.mocked(getModels).mockResolvedValue([
    { id: "m", name: "M", context: 200, prompt: "0", completion: "0" }]);
  vi.mocked(api.getSceneContext).mockResolvedValue({
    model: "m", total_tokens: 100, dropped_tokens: 0, budget_tokens: 1000,
    sections: [{ label: "World info", text: "lore text", tokens: 100,
                 tier: "spotlight", dropped: false, trimmed: 0 }],
  });
  renderInspector();
  await screen.findByText("≈50%");              // 100 of the model's 200, not of 1000
  await screen.findByText(/100 \/ 200 tok/);
});

// ---- turn history: what the model saw for a PAST turn (#157) ----

test("with no captured turns the history section says so", async () => {
  renderInspector();
  await screen.findByText("No captured turns yet.");
});

test("captured turns are listed by what kind of turn they were", async () => {
  vi.mocked(api.listScenePrompts).mockResolvedValue(fixture({ entries: TURNS }));
  renderInspector();
  await screen.findByText("Regenerate");
  await screen.findByText("Send");
});

test("clicking a turn shows that turn's frozen prompt instead of the live one", async () => {
  vi.mocked(api.listScenePrompts).mockResolvedValue(fixture({ entries: TURNS }));
  vi.mocked(api.getScenePrompt).mockResolvedValue(FROZEN);
  renderInspector();

  // the live composition is what shows first
  await screen.findByText("lore text");

  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));

  await waitFor(() => expect(api.getScenePrompt).toHaveBeenCalledWith("c", "s", "000001"));
  await screen.findByText("the lore as it stood then");
  await screen.findByText(/What the model saw/);
  // and the live text is gone — showing both would be the confusion this fixes
  expect(screen.queryByText("lore text")).toBeNull();
});

test("going back restores the live context", async () => {
  vi.mocked(api.listScenePrompts).mockResolvedValue(fixture({ entries: TURNS }));
  vi.mocked(api.getScenePrompt).mockResolvedValue(FROZEN);
  renderInspector();

  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));
  await screen.findByText("the lore as it stood then");

  fireEvent.click(screen.getByRole("button", { name: /Back to live context/ }));
  await screen.findByText("lore text");
  expect(screen.queryByText(/What the model saw/)).toBeNull();
});

test("a turn that has aged out of the log says so rather than blanking", async () => {
  vi.mocked(api.listScenePrompts).mockResolvedValue(fixture({ entries: TURNS }));
  vi.mocked(api.getScenePrompt).mockRejectedValue({ status: 404, detail: "not found" });
  renderInspector();

  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));
  await screen.findByText(/aged out of the log/);
  await screen.findByText("lore text");        // still on the live view
});

test("a frozen turn is measured against the budget it was captured under", async () => {
  // Not today's: the live budget has since been raised, and reporting the past
  // turn against it would show a prompt that overran as comfortably inside.
  vi.mocked(api.listScenePrompts).mockResolvedValue(fixture({ entries: TURNS }));
  vi.mocked(api.getScenePrompt).mockResolvedValue(fixture({ ...FROZEN, budget_tokens: 100 }));
  vi.mocked(api.getSceneContext).mockResolvedValue({
    model: "m", total_tokens: 100, dropped_tokens: 0, budget_tokens: 10000,
    sections: [{ label: "World info", text: "lore text", tokens: 100,
                 tier: "spotlight", dropped: false, trimmed: 0 }],
  });
  renderInspector();

  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));
  await screen.findByText(/80 \/ 100 tok/);    // the frozen budget, not 10000
  await screen.findByText("≈80%");
});

test("a snapshot arriving after a scene change is dropped, not shown", async () => {
  // The guard is worth a test because the obvious version of it — comparing the
  // callback's own captured sid against itself — is always satisfied and looks
  // right on the page.
  vi.mocked(api.listScenePrompts).mockResolvedValue(fixture({ entries: TURNS }));
  let release: (v: PromptSnapshot) => void = () => {};
  vi.mocked(api.getScenePrompt).mockReturnValue(new Promise((r) => { release = r; }));

  const { rerender } = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));

  // the reader moves to another scene while the fetch is still in flight
  rerender(<SceneInspector cid="c" sid="s2" refreshKey={0} onSceneChanged={() => {}} />);
  release(FROZEN);

  await screen.findByText("lore text");                       // still the live view
  expect(screen.queryByText("the lore as it stood then")).toBeNull();
  expect(screen.queryByText(/What the model saw/)).toBeNull();
});

test("a turn list arriving after a scene change is dropped, not listed", async () => {
  // Worse than a stale label: entry ids are per-campaign counters, so they
  // collide across campaigns and clicking a stale row would fetch a different
  // campaign's prompt under it.
  vi.mocked(api.listScenePrompts).mockResolvedValue(fixture({ entries: TURNS }));
  const { rerender } = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  await screen.findByText("Regenerate");

  let release: (v: Awaited<ReturnType<typeof api.listScenePrompts>>) => void = () => {};
  vi.mocked(api.listScenePrompts).mockReturnValue(new Promise((r) => { release = r; }));
  rerender(<SceneInspector cid="c2" sid="s" refreshKey={0} onSceneChanged={() => {}} />);

  // the previous campaign's rows are gone the moment the scene changes, not
  // when the replacement request settles
  await screen.findByText("No captured turns yet.");
  release(fixture({ entries: TURNS }));
  await screen.findByText("Regenerate");   // c2's own rows, once they arrive
});

test("the last turn clicked wins even if an earlier request resolves after it", async () => {
  // The scene guard does not cover this: both requests are for the same scene,
  // so without a per-request check the slower first click overwrites the second.
  vi.mocked(api.listScenePrompts).mockResolvedValue(fixture({ entries: TURNS }));
  const pending: Record<string, (v: any) => void> = {};
  vi.mocked(api.getScenePrompt).mockImplementation((_c, _s, eid) =>
    new Promise((r) => { pending[eid] = r; }));

  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));        // 000001
  fireEvent.click(await screen.findByRole("button", { name: /^Regenerate/ }));  // 000002

  pending["000002"]({ ...FROZEN, id: "000002", task: "regenerate",
                      sections: [{ label: "World info", text: "the regenerate prompt",
                                   tokens: 90, tier: "spotlight", dropped: false, trimmed: 0 }] });
  await screen.findByText("the regenerate prompt");

  pending["000001"](FROZEN);          // the superseded click lands late
  await new Promise((r) => setTimeout(r, 0));
  await screen.findByText("the regenerate prompt");
  expect(screen.queryByText("the lore as it stood then")).toBeNull();
});

test("a superseded turn-list response does not overwrite a newer one", async () => {
  // `fresh: true` stops the refresh JOINING a pre-turn request; it does not
  // order two independent ones. If the newer answers first, the older `.then`
  // would put the pre-turn rows back.
  const pending: ((v: any) => void)[] = [];
  vi.mocked(api.listScenePrompts).mockImplementation(
    () => new Promise((r) => { pending.push(r); }));

  const { rerender } = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  rerender(<SceneInspector cid="c" sid="s" refreshKey={1} onSceneChanged={() => {}} />);
  await waitFor(() => expect(pending.length).toBe(2));

  pending[1]({ entries: TURNS });                    // the post-generation read
  await screen.findByText("Regenerate");
  pending[0]({ entries: [] });                       // the stale one, late
  await new Promise((r) => setTimeout(r, 0));

  await screen.findByText("Regenerate");             // still there
  expect(screen.queryByText("No captured turns yet.")).toBeNull();
});

test("going back to live discards a detail fetch still in flight", async () => {
  // Click B, change your mind, go back — B must not reopen the panel over an
  // explicit choice to leave it.
  vi.mocked(api.listScenePrompts).mockResolvedValue(fixture({ entries: TURNS }));
  vi.mocked(api.getScenePrompt).mockResolvedValueOnce(FROZEN);
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));
  await screen.findByText("the lore as it stood then");

  let release: (v: PromptSnapshot) => void = () => {};
  vi.mocked(api.getScenePrompt).mockReturnValue(new Promise((r) => { release = r; }));
  fireEvent.click(screen.getByRole("button", { name: /^Regenerate/ }));
  fireEvent.click(screen.getByRole("button", { name: /Back to live context/ }));
  await screen.findByText("lore text");

  release(fixture({ ...FROZEN, id: "000002",
            sections: [{ label: "World info", text: "the regenerate prompt", tokens: 90,
                         tier: "spotlight", dropped: false, trimmed: 0 }] }));
  await new Promise((r) => setTimeout(r, 0));

  await screen.findByText("lore text");              // still live
  expect(screen.queryByText("the regenerate prompt")).toBeNull();
  expect(screen.queryByText(/What the model saw/)).toBeNull();
});

test("a frozen turn is not painted under a scene it does not belong to", async () => {
  // The clearing effect runs AFTER render, so scoping has to happen during it —
  // otherwise the first paint under the new scene still shows the old prompt.
  vi.mocked(api.listScenePrompts).mockResolvedValue(fixture({ entries: TURNS }));
  vi.mocked(api.getScenePrompt).mockResolvedValue(FROZEN);
  const { rerender } = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));
  await screen.findByText("the lore as it stood then");

  rerender(<SceneInspector cid="c" sid="s2" refreshKey={0} onSceneChanged={() => {}} />);
  // synchronously after the re-render, before effects have settled
  expect(screen.queryByText("the lore as it stood then")).toBeNull();
  expect(screen.queryByText(/What the model saw/)).toBeNull();
});

test("switching campaigns does not paint the other campaign's frozen turn", async () => {
  // Scene ids repeat across campaigns, so `sid` alone would match here.
  vi.mocked(api.listScenePrompts).mockResolvedValue(fixture({ entries: TURNS }));
  vi.mocked(api.getScenePrompt).mockResolvedValue(FROZEN);
  const { rerender } = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: /^Send/ }));
  await screen.findByText("the lore as it stood then");

  rerender(<SceneInspector cid="c2" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  expect(screen.queryByText("the lore as it stood then")).toBeNull();
});

