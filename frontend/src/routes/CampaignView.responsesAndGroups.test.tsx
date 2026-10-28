import { screen, fireEvent, waitFor, act, within } from "@testing-library/react";
import { api } from "../api/client";
import type { GroupSettings } from "../api/client";
import type { ChatEvent } from "../api/stream";
import { DEFAULT_GROUP, installCampaignMocks, ONE_SCENE, openScene, renderCampaign } from "../testkit/campaignHarness";
import { fixture } from "../testkit/fixture";
import { twoPostScene, PROPOSAL_PAYLOAD, TWO_SCENES } from "../testkit/campaignFixtures";

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




test("Continue sends one empty request", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE.map((scene) => ({ ...scene, date: "" })));
  renderCampaign();
  await screen.findByRole("heading", { name: /^Old$/ });
  fireEvent.click(screen.getByRole("button", { name: /Continue ▶/ }));
  await waitFor(() => expect(api.chat).toHaveBeenCalledOnce());
  expect(vi.mocked(api.chat).mock.calls[0][2]).toBe("");
  expect(vi.mocked(api.chat).mock.calls[0][9]).toBeUndefined();
});

test("a reply chip with an empty composer sends one targeted request", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE.map((scene) => ({ ...scene, date: "" })));
  vi.mocked(api.getCast).mockResolvedValue([
    { kind: "characters", id: "mara", name: "Mara", role: "npc" },
    { kind: "pcs", id: "winifred", name: "Winifred", role: "player" },
  ]);
  renderCampaign();
  await screen.findByRole("heading", { name: /^Old$/ });
  const chips = await screen.findByRole("group", { name: "Reply as" });
  expect(within(chips).queryByRole("button", { name: "Winifred" })).not.toBeInTheDocument();
  fireEvent.click(within(chips).getByRole("button", { name: "Mara" }));
  await waitFor(() => expect(api.chat).toHaveBeenCalledOnce());
  expect(vi.mocked(api.chat).mock.calls[0][2]).toBe("");
  expect(vi.mocked(api.chat).mock.calls[0][9]).toBe("characters:mara");
});

test("a reply chip with text posts it with that speaker leading", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE.map((scene) => ({ ...scene, date: "" })));
  vi.mocked(api.getCast).mockResolvedValue([
    { kind: "characters", id: "mara", name: "Mara", role: "npc" },
  ]);
  renderCampaign();
  await screen.findByRole("heading", { name: /^Old$/ });
  fireEvent.change(screen.getByPlaceholderText("Speak your intent…"), { target: { value: "Hello" } });
  const chips = await screen.findByRole("group", { name: "Reply as" });
  fireEvent.click(within(chips).getByRole("button", { name: "Mara" }));
  await waitFor(() => expect(api.chat).toHaveBeenCalledOnce());
  expect(vi.mocked(api.chat).mock.calls[0][2]).toBe("Hello");
  expect(vi.mocked(api.chat).mock.calls[0][9]).toBe("characters:mara");
});

test("Grimoire chip works with no NPC in the scene", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE.map((scene) => ({ ...scene, date: "" })));
  vi.mocked(api.getCast).mockResolvedValue([]);
  renderCampaign();
  await screen.findByRole("heading", { name: /^Old$/ });
  const chips = await screen.findByRole("group", { name: "Reply as" });
  fireEvent.click(within(chips).getByRole("button", { name: "Grimoire" }));
  await waitFor(() => expect(api.chat).toHaveBeenCalledOnce());
  expect(vi.mocked(api.chat).mock.calls[0][9]).toBe("grimoire");
});

test("reply chips still work when the group settings fail to load", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE.map((scene) => ({ ...scene, date: "" })));
  vi.mocked(api.getCast).mockResolvedValue([
    { kind: "characters", id: "mara", name: "Mara", role: "npc" },
  ]);
  vi.mocked(api.getSceneGroup).mockRejectedValue(new Error("boom"));
  renderCampaign();
  await screen.findByRole("heading", { name: /^Old$/ });
  const chips = await screen.findByRole("group", { name: "Reply as" });
  const mara = within(chips).getByRole("button", { name: "Mara" });
  expect(mara).not.toHaveClass("sitting-out");
  fireEvent.click(mara);
  await waitFor(() => expect(api.chat).toHaveBeenCalledOnce());
});

test("a sitting-out NPC's chip is dimmed", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE.map((scene) => ({ ...scene, date: "" })));
  vi.mocked(api.getCast).mockResolvedValue([
    { kind: "characters", id: "mara", name: "Mara", role: "npc" },
  ]);
  vi.mocked(api.getSceneGroup).mockResolvedValue({ ...DEFAULT_GROUP, sitting_out: ["characters:mara"] });
  renderCampaign();
  await screen.findByRole("heading", { name: /^Old$/ });
  const chips = await screen.findByRole("group", { name: "Reply as" });
  await waitFor(() => expect(within(chips).getByRole("button", { name: "Mara" })).toHaveClass("sitting-out"));
});

const MARA_NPC = { kind: "characters" as const, id: "mara", name: "Mara", role: "npc" as const };

test("group toggle opens the panel with the scene's settings", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE.map((scene) => ({ ...scene, date: "" })));
  vi.mocked(api.getCast).mockResolvedValue([MARA_NPC]);
  vi.mocked(api.getSceneGroup).mockResolvedValue(
    { ...DEFAULT_GROUP, order: "natural", sitting_out: ["characters:mara"] });
  renderCampaign();
  await screen.findByRole("heading", { name: /^Old$/ });
  const toggle = await screen.findByRole("button", { name: "Order: Natural" });
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByLabelText("Speaker order")).not.toBeInTheDocument();
  fireEvent.click(toggle);
  expect(toggle).toHaveAttribute("aria-expanded", "true");
  expect(screen.getByLabelText("Speaker order")).toHaveValue("natural");
  expect(screen.getByLabelText("Mara sits out")).toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  expect(screen.queryByLabelText("Speaker order")).not.toBeInTheDocument();
  expect(toggle).toHaveAttribute("aria-expanded", "false");
});

test("a panel save updates the toggle label and dims the sitting-out chip", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE.map((scene) => ({ ...scene, date: "" })));
  vi.mocked(api.getCast).mockResolvedValue([MARA_NPC]);
  vi.mocked(api.setSceneGroup).mockImplementation(async (_c, _s, settings) => ({ ok: true, settings }));
  renderCampaign();
  await screen.findByRole("heading", { name: /^Old$/ });
  fireEvent.click(await screen.findByRole("button", { name: "Order: Directed" }));
  fireEvent.click(screen.getByLabelText("Mara sits out"));
  const chips = await screen.findByRole("group", { name: "Reply as" });
  await waitFor(() => expect(within(chips).getByRole("button", { name: "Mara" })).toHaveClass("sitting-out"));
  fireEvent.change(screen.getByLabelText("Speaker order"), { target: { value: "list" } });
  await screen.findByRole("button", { name: "Order: List" });
});

test("the group toggle stays enabled while a run is live", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE.map((scene) => ({ ...scene, date: "" })));
  vi.mocked(api.chat).mockImplementation((_c, _s, _t, _e, _r, signal) => new Promise((resolve) => {
    signal?.addEventListener("abort", () => resolve(), { once: true });
  }));
  renderCampaign();
  await screen.findByRole("heading", { name: /^Old$/ });
  const toggle = await screen.findByRole("button", { name: "Order: Directed" });
  fireEvent.click(screen.getByRole("button", { name: /Continue ▶/ }));
  await waitFor(() => expect(api.chat).toHaveBeenCalledOnce());
  expect(toggle).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: /Stop/ }));
});

test("a slow group read for the previous scene cannot overwrite the current scene's", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(TWO_SCENES.map((scene) => ({ ...scene, date: "" })));
  let releaseS1: (g: GroupSettings) => void = () => {};
  vi.mocked(api.getSceneGroup).mockImplementation((_c, sid) =>
    sid === "s1"
      ? new Promise((resolve) => { releaseS1 = resolve; })
      : Promise.resolve({ ...DEFAULT_GROUP, order: "list" as const }));
  renderCampaign();
  await openScene(/The Saltmarch Gate/);
  await screen.findByRole("button", { name: "Order: List" });
  await act(async () => releaseS1({ ...DEFAULT_GROUP, order: "natural" }));
  expect(screen.getByRole("button", { name: "Order: List" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Order: Natural" })).not.toBeInTheDocument();
});

test("round_start frames show Round n/total while responding", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE.map((scene) => ({ ...scene, date: "" })));
  let emit: Parameters<typeof api.chat>[3] = () => {};
  let finish: () => void = () => {};
  vi.mocked(api.chat).mockImplementation((_cid, _sid, _text, onEvent) => new Promise((resolve) => {
    emit = onEvent;
    finish = () => { onEvent({ done: true }); resolve(); };
  }));
  renderCampaign();
  await screen.findByRole("heading", { name: /^Old$/ });
  fireEvent.click(screen.getByRole("button", { name: /Continue ▶/ }));
  await waitFor(() => expect(api.chat).toHaveBeenCalledOnce());
  act(() => {
    emit({ round_start: { index: 2, of: 3 } });
    emit({ response_start: { id: "a", speaker: "Mara", actor_ref: "characters:mara" } });
  });
  const status = await screen.findByRole("status", { name: /Mara is responding/ });
  expect(status).toHaveTextContent("Round 2/3 · Mara is responding…");
  await act(async () => finish());
  await waitFor(() => expect(screen.queryByText(/Round 2\/3/)).not.toBeInTheDocument());
});

test("a first-round response shows no round prefix", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE.map((scene) => ({ ...scene, date: "" })));
  vi.mocked(api.chat).mockImplementation((_cid, _sid, _text, onEvent, _r, signal) => new Promise((resolve) => {
    onEvent({ response_start: { id: "a", speaker: "Mara", actor_ref: "characters:mara" } });
    signal?.addEventListener("abort", () => resolve(), { once: true });
  }));
  renderCampaign();
  await screen.findByRole("heading", { name: /^Old$/ });
  fireEvent.click(screen.getByRole("button", { name: /Continue ▶/ }));
  const status = await screen.findByRole("status", { name: "Mara is responding" });
  expect(status).not.toHaveTextContent("Round");
  fireEvent.click(screen.getByRole("button", { name: /Stop/ }));
});

test("individual response deletion uses its stable id and keeps cut-from-here separate", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE.map((scene) => ({ ...scene, date: "" })));
  const messages = [{ role: "assistant" as const, content: "A response.", speaker: "Mara",
    response_id: "response-a", response_status: "complete" as const, response_can_reroll: true }];
  vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages });
  vi.mocked(api.deleteResponse).mockResolvedValue({ meta: { id: "s1", title: "Old" }, messages: [] });
  vi.spyOn(window, "confirm").mockReturnValue(true);
  renderCampaign();
  await screen.findByText("A response.");
  fireEvent.click(screen.getByText("Response actions"));
  fireEvent.click(screen.getByRole("button", { name: "Delete response" }));
  await waitFor(() => expect(api.deleteResponse).toHaveBeenCalledWith("run", "s1", "response-a"));
  expect(api.deleteMessagesFrom).not.toHaveBeenCalled();
});


// A ledger reroll streams the frozen snapshot, so the server ignores a
// `response` override: the client neither sends the pending chip nor spends it.
test("individual reroll keeps the old reply until accepted and leaves its one-shot override pending, without transcript growth", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE.map((scene) => ({ ...scene, date: "" })));
  const message = { role: "assistant" as const, content: "The accepted response.", speaker: "Mara",
    response_id: "response-a", response_status: "complete" as const, response_can_reroll: true };
  vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old", response_preset: "cinematic" }, messages: [message] });
  let finish: (() => void) | undefined;
  vi.mocked(api.regenerateResponse).mockImplementation((_cid, _sid, _rid, onEvent) => new Promise((resolve) => {
    finish = () => {
      vi.mocked(api.getScene).mockResolvedValue({ meta: { id: "s1", title: "Old", response_preset: "cinematic" },
        messages: [{ ...message, content: "The revised response." }] });
      onEvent({ done: true }); resolve();
    };
  }));
  renderCampaign();
  await screen.findByText("The accepted response.");
  const picker = screen.getByLabelText("Next reply words");
  await waitFor(() => expect(picker).toHaveValue(null));
  fireEvent.change(picker, { target: { value: "120" } });
  fireEvent.click(screen.getByText("Response actions"));
  fireEvent.change(screen.getByLabelText("Response steer"), { target: { value: "Calmer" } });
  fireEvent.click(screen.getByRole("button", { name: "Reroll response" }));
  await waitFor(() => expect(api.regenerateResponse).toHaveBeenCalledOnce());
  expect(vi.mocked(api.regenerateResponse).mock.calls[0][2]).toBe("response-a");
  expect(vi.mocked(api.regenerateResponse).mock.calls[0][4]).toEqual({ guidance: "Calmer" });
  expect(screen.getByText("The accepted response.")).toBeInTheDocument();
  expect(api.regenerate).not.toHaveBeenCalled();
  await act(async () => finish?.());
  await screen.findByText("The revised response.");
  expect(picker).toHaveValue(120);
});

test("streaming response boundaries display separate speakers and Stop remains the parent-run control", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE.map((scene) => ({ ...scene, date: "" })));
  vi.mocked(api.chat).mockImplementation((_cid, _sid, _text, onEvent, _response, signal) => new Promise((resolve) => {
    onEvent({ response_start: { id: "a", speaker: "Mara", actor_ref: "characters:mara" } });
    onEvent({ delta: "First reply." });
    onEvent({ response_end: { id: "a", status: "complete" } });
    onEvent({ response_start: { id: "b", speaker: "Winifred", actor_ref: "characters:winifred" } });
    onEvent({ delta: "Second reply." });
    signal?.addEventListener("abort", () => resolve(), { once: true });
  }));
  renderCampaign();
  await screen.findByRole("heading", { name: /^Old$/ });
  fireEvent.click(screen.getByRole("button", { name: /Continue ▶/ }));
  await screen.findByText("Second reply.");
  const first = screen.getByText("First reply.").closest(".streaming-response");
  const second = screen.getByText("Second reply.").closest(".streaming-response");
  expect(first).toHaveTextContent("Mara");
  expect(second).toHaveTextContent("Winifred");
  expect(first).not.toBe(second);
  fireEvent.click(screen.getByRole("button", { name: /Stop/ }));
});


test("speaker progress appears before prose and moves to the next NPC on handoff", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE.map((scene) => ({ ...scene, date: "" })));
  let emit: Parameters<typeof api.chat>[3] = () => {};
  let finish: () => void = () => {};
  vi.mocked(api.chat).mockImplementation((_cid, _sid, _text, onEvent) => new Promise((resolve) => {
    emit = onEvent;
    finish = () => { onEvent({ done: true }); resolve(); };
  }));
  renderCampaign();
  await screen.findByRole("heading", { name: /^Old$/ });
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "I wait." } });
  fireEvent.click(screen.getByRole("button", { name: /Send/ }));
  await waitFor(() => expect(screen.getByRole("button", { name: /Continue/ })).toBeDisabled());
  // Selection has not named anyone yet, but a second click must not launch a run.
  fireEvent.click(screen.getByRole("button", { name: /Continue/ }));
  expect(api.chat).toHaveBeenCalledOnce();
  act(() => emit({ response_start: { id: "a", speaker: "Mara", actor_ref: "characters:mara" } }));
  expect(await screen.findByRole("status", { name: "Mara is responding" })).toBeVisible();
  expect(screen.getByRole("button", { name: /Stop/ })).toBeEnabled();
  act(() => {
    emit({ delta: 'She closes the book. "Ready."' });
    emit({ response_end: { id: "a", status: "complete" } });
    emit({ response_start: { id: "b", speaker: "Winifred", actor_ref: "characters:winifred" } });
  });
  expect(await screen.findByRole("status", { name: "Winifred is responding" })).toBeVisible();
  expect(screen.queryByRole("status", { name: "Mara is responding" })).not.toBeInTheDocument();
  expect(screen.getByText('"Ready."').closest(".streaming-response"))
    .toHaveTextContent('She closes the book. "Ready."');
  expect(screen.getByRole("button", { name: /Continue/ })).toBeDisabled();
  act(() => {
    emit({ delta: '"So am I."' });
    emit({ response_end: { id: "b", status: "complete" } });
  });
  expect(screen.queryByRole("status", { name: "Winifred is responding" })).not.toBeInTheDocument();
  expect(screen.getByText('"So am I."').closest(".streaming-response")).toHaveTextContent("Winifred");
  expect(screen.getByRole("button", { name: /Continue/ })).toBeDisabled();
  await act(async () => finish());
  await waitFor(() => expect(screen.getByRole("button", { name: /Continue/ })).toBeEnabled());
  expect(screen.queryByText('"So am I."')).not.toBeInTheDocument();
  expect(screen.queryByRole("status", { name: /is responding/ })).not.toBeInTheDocument();
});


test("saving focus during an NPC stream preserves its speaker, thinking and accumulated prose", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE.map((scene) => ({ ...scene, date: "" })));
  let emit: Parameters<typeof api.chat>[3] = () => {};
  let finish: () => void = () => {};
  vi.mocked(api.chat).mockImplementation((_cid, _sid, _text, onEvent) => new Promise((resolve) => {
    emit = onEvent; finish = () => { onEvent({ done: true }); resolve(); };
  }));
  renderCampaign();
  await screen.findByRole("heading", { name: /^Old$/ });
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Mara waits." } });
  fireEvent.click(screen.getByRole("button", { name: /Send/ }));
  await waitFor(() => expect(api.chat).toHaveBeenCalledOnce());
  act(() => {
    emit({ response_start: { id: "a", speaker: "Mara", actor_ref: "characters:mara" } });
    emit({ thinking_delta: "First thought" });
    emit({ delta: "She waits." });
  });
  expect(await screen.findByRole("status", { name: "Mara is responding" })).toBeVisible();
  fireEvent.click(await screen.findByText("Thinking"));
  fireEvent.click(screen.getByRole("button", { name: /What the model saw/ }));
  fireEvent.click(await screen.findByRole("button", { name: /^Scene focus/ }));
  fireEvent.click(await screen.findByRole("button", { name: /Edit scene focus/ }));
  fireEvent.change(screen.getByRole("textbox", { name: "Scene focus" }), { target: { value: "Let Seraphine choose." } });
  const sceneReads = vi.mocked(api.getScene).mock.calls.length;
  const contextReads = vi.mocked(api.getSceneContext).mock.calls.length;
  fireEvent.click(screen.getByRole("button", { name: /^Save$/ }));
  await waitFor(() => expect(api.setSceneFocus).toHaveBeenCalledWith("run", "s1", "Let Seraphine choose."));
  await screen.findByText("Let Seraphine choose.");
  await waitFor(() => expect(api.getSceneContext).toHaveBeenCalledTimes(contextReads + 1));
  expect(api.getScene).toHaveBeenCalledTimes(sceneReads);
  act(() => { emit({ thinking_delta: " continues" }); emit({ delta: " She nods." }); });
  expect(screen.getByRole("status", { name: "Mara is responding" })).toBeVisible();
  expect(screen.getByText(/She waits\. She nods\./).closest(".streaming-response")).toHaveTextContent("Mara");
  expect(await screen.findByText(/First thought continues/)).toBeInTheDocument();
  await act(async () => finish());
});

test("streamed thinking is collapsed and follows its NPC across a handoff", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE.map((scene) => ({ ...scene, date: "" })));
  let emit: Parameters<typeof api.chat>[3] = () => {};
  let finish: () => void = () => {};
  vi.mocked(api.chat).mockImplementation((_cid, _sid, _text, onEvent) => new Promise((resolve) => {
    emit = onEvent; finish = () => { onEvent({ done: true }); resolve(); };
  }));
  renderCampaign();
  await screen.findByRole("heading", { name: /^Old$/ });
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "I wait." } });
  fireEvent.click(screen.getByRole("button", { name: /Send/ }));
  await waitFor(() => expect(api.chat).toHaveBeenCalledOnce());
  act(() => {
    emit({ response_start: { id: "a", speaker: "Mara", actor_ref: "characters:mara" } });
    emit({ thinking_delta: "First thought" });
  });
  const thinking = await screen.findByText("Thinking");
  expect(thinking.closest("details")).not.toHaveAttribute("open");
  fireEvent.click(thinking);
  act(() => emit({ thinking_delta: " continues" }));
  expect(screen.getByText(/First thought continues/).closest("details")).toHaveAttribute("open");
  act(() => {
    emit({ thinking_reset: true });
    emit({ thinking_delta: "Retry thought" });
    emit({ delta: '"Ready."' });
    emit({ response_end: { id: "a", status: "complete" } });
    emit({ response_start: { id: "b", speaker: "Winifred", actor_ref: "characters:winifred" } });
    emit({ thinking_delta: "Second thought" });
  });
  expect(screen.queryByText(/First thought/)).not.toBeInTheDocument();
  expect(screen.getByText(/Retry thought/).closest(".streaming-response")).toHaveTextContent("Mara");
  expect(screen.getByText(/Second thought/).closest(".streaming-response")).toHaveTextContent("Winifred");
  await act(async () => finish());
});


// ---- regex output processing: the play view's half (spec 5.3, 6.2) ----

test("a display frame replaces streamed text", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE.map((scene) => ({ ...scene, date: "" })));
  let emit: Parameters<typeof api.chat>[3] = () => {};
  let finish: () => void = () => {};
  vi.mocked(api.chat).mockImplementation((_cid, _sid, _text, onEvent) => new Promise((resolve) => {
    emit = onEvent; finish = () => { onEvent({ done: true }); resolve(); };
  }));
  renderCampaign();
  await screen.findByRole("heading", { name: /^Old$/ });
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "I wait." } });
  fireEvent.click(screen.getByRole("button", { name: /Send/ }));
  await waitFor(() => expect(api.chat).toHaveBeenCalledOnce());
  act(() => {
    emit({ response_start: { id: "a", speaker: "Mara", actor_ref: "characters:mara" } });
    emit({ display: { keep: 0, tail: "She waits. Secret plan" } });
  });
  expect(await screen.findByText(/Secret plan/)).toBeInTheDocument();
  // The thinking block closed: the server takes back what it already showed.
  act(() => emit({ display: { keep: 10, tail: " She nods." } }));
  expect(screen.queryByText(/Secret plan/)).not.toBeInTheDocument();
  expect(screen.getByText(/She waits\. She nods\./)).toBeInTheDocument();
  // The next speaker's `keep` counts from their own response_start, so a
  // frame that keeps nothing leaves Mara's part whole.
  act(() => {
    emit({ response_end: { id: "a", status: "complete" } });
    emit({ response_start: { id: "b", speaker: "Winifred", actor_ref: "characters:winifred" } });
    emit({ display: { keep: 0, tail: "Draft" } });
    emit({ display: { keep: 0, tail: '"Ready."' } });
  });
  expect(screen.getByText(/She waits\. She nods\./).closest(".streaming-response"))
    .toHaveTextContent("Mara");
  expect(screen.getByText('"Ready."').closest(".streaming-response")).toHaveTextContent("Winifred");
  expect(screen.queryByText(/Draft/)).not.toBeInTheDocument();
  await act(async () => finish());
});

test("a roll continuation's display frames count from the continuation's own text", async () => {
  // The legacy path's continuation is its own stream with its own display
  // pass, and sends no response_start: its first frame keeps from 0.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "I punch him" }, { role: "assistant", content: "a reply" }] }));
  vi.mocked(api.getRollProposal).mockResolvedValue(
    { record: { id: "pr-1", status: "pending", payload: PROPOSAL_PAYLOAD, resolution: null } });
  let emit: (e: ChatEvent) => void = () => {};
  let finish: () => void = () => {};
  vi.mocked(api.resolveProposal).mockImplementation(
    (_c, _s, _b, onEvent) => new Promise<void>((resolve) => {
      emit = onEvent; finish = () => { onEvent({ done: true }); resolve(); };
    }));
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(await screen.findByRole("button", { name: "Roll it" }));
  await waitFor(() => expect(api.resolveProposal).toHaveBeenCalled());
  act(() => {
    emit({ display: { keep: 0, tail: "The door opens (aside" } });
    emit({ display: { keep: 14, tail: " slowly." } });
  });
  expect(await screen.findByText("The door opens slowly.")).toBeInTheDocument();
  expect(screen.queryByText(/aside/)).not.toBeInTheDocument();
  await act(async () => finish());
});

test("renders shown over content", async () => {
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hello" },
    { role: "assistant", content: "Raw words [[hidden]] here", shown: "Clean words here" }] }));
  renderCampaign();
  expect(await screen.findByText("Clean words here")).toBeInTheDocument();
  expect(screen.queryByText(/hidden/)).not.toBeInTheDocument();
});

test("Test rules on this post opens the test pane filled from the raw message", async () => {
  vi.mocked(api.listConnections).mockResolvedValue(fixture([{ id: "conn-a", name: "Connection A" }]));
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "assistant", content: "Raw words [[hidden]]", shown: "Raw words",
      connection: "conn-a" },
    { role: "user", content: "hello" }] }));
  renderCampaign();
  await screen.findByText("Raw words");
  fireEvent.click(screen.getByRole("button", { name: "Test rules on message 1" }));
  const dialog = await screen.findByRole("dialog", { name: "Test rules on this post" });
  // The STORED text, not what the display rules made of it: the pane runs the
  // rules over it, so it starts where they start.
  expect(within(dialog).getByLabelText("Text to test")).toHaveValue("Raw words [[hidden]]");
  expect(within(dialog).getByLabelText("Role")).toHaveValue("model");
  // One post below it, so it is one from the end.
  expect(within(dialog).getByLabelText("Depth")).toHaveValue(1);
  expect(within(dialog).getByLabelText("Connection")).toHaveValue("conn-a");
  fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
  expect(screen.queryByRole("dialog", { name: "Test rules on this post" })).not.toBeInTheDocument();
});

test("restoring a rewritten post from Turn history re-reads the scene and asks as an edit does", async () => {
  twoPostScene();
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" },
    { role: "assistant", content: "a reply", response_id: "resp-1", rewritten: true }] }));
  vi.mocked(api.getSceneRewrites).mockResolvedValue(
    { "resp-1": { original: "a reply [[aside]]", rules: ["r-1"], at: "" } });
  vi.mocked(api.getRegex).mockResolvedValue(
    fixture({ layer: { rules: [{ id: "r-1", name: "Strip asides" }], off: [] }, inherited: [], warnings: {} }));
  vi.mocked(api.editMessage).mockResolvedValue({ ok: true });
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(screen.getByRole("button", { name: /What the model saw/ }));
  fireEvent.click(await screen.findByRole("button", { name: /post 2 Rewritten/ }));
  const detail = await screen.findByRole("region", { name: "Rewrite of post 2" });
  const reads = vi.mocked(api.getScene).mock.calls.length;
  fireEvent.click(within(detail).getByRole("button", { name: "Restore original" }));
  await waitFor(() => expect(api.editMessage).toHaveBeenCalledWith(
    "run", "s1", 1, "a reply [[aside]]", { restore: true }));
  await waitFor(() => expect(vi.mocked(api.getScene).mock.calls.length).toBeGreaterThan(reads));
  await waitFor(() => expect(api.refreshRollingSummary).toHaveBeenCalled());
});

test("a reattached run applies display frames within each part", async () => {
  // The reattach path renders buffered frames the way the live path does, so a
  // display frame's `keep` counts from the part's own response_start there too.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.chat).mockImplementation(async (
    _c, _s, _t, onEvent) => {
    onEvent({ run: { id: "run-9", attempt_id: "a", state: "running", next_index: 1 } });
    throw new Error("network");
  });
  vi.mocked(api.findRun).mockResolvedValueOnce({ run: null }).mockResolvedValue({
    run: { id: "run-9", attempt_id: "a", state: "running", next_index: 4 } });
  let resume: () => void = () => {};
  vi.mocked(api.attachRun).mockImplementation(async (
    _c, _s, _r, _from, onEvent) => {
    onEvent({ response_start: { id: "a", speaker: "Mara", actor_ref: "characters:mara" } });
    onEvent({ display: { keep: 0, tail: "The lamps. Secret plan" } });
    onEvent({ display: { keep: 10, tail: " Lit." } });
    onEvent({ response_end: { id: "a", status: "complete" } });
    onEvent({ response_start: { id: "b", speaker: "Winifred", actor_ref: "characters:winifred" } });
    onEvent({ display: { keep: 0, tail: "Draft" } });
    onEvent({ display: { keep: 0, tail: "Good." } });
    await new Promise<void>((resolve) => { resume = resolve; });
    onEvent({ done: true });
  });
  renderCampaign();
  fireEvent.change(await screen.findByRole("textbox"), { target: { value: "Mara waits." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await waitFor(() => expect(api.chat).toHaveBeenCalled());
  act(() => { document.dispatchEvent(new Event("visibilitychange")); });
  await waitFor(() => expect(api.attachRun).toHaveBeenCalled());
  expect(await screen.findByText("Good.")).toBeInTheDocument();
  expect(screen.getByText(/The lamps\. Lit\./).closest(".streaming-response")).toHaveTextContent("Mara");
  expect(screen.queryByText(/Secret plan|Draft/)).not.toBeInTheDocument();
  await act(async () => resume());
});
