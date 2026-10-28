import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { MemoryRouter, Link } from "react-router-dom";
import { api } from "../api/client";
import type { CampaignModule } from "../api/client";
import type { RollProposalPayload } from "../api/stream";
import { installCampaignMocks, ONE_SCENE, openScene, playRoutes, renderCampaign, withPalette } from "../testkit/campaignHarness";
import { fixture } from "../testkit/fixture";
import { PROPOSAL_PAYLOAD } from "../testkit/campaignFixtures";

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



test("Roll dice is disabled on a fresh scene until the opener/cast setup produces a message", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  const rollBtn = await screen.findByRole("button", { name: "Roll dice" });
  expect(rollBtn).toBeDisabled();
});

test("the composer's glyphs are drawn in the ink, never as colour emoji", async () => {
  // 🎲 and "Direct 🎬" were full-colour pictures among the composer's
  // hairline controls.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  const roll = await screen.findByRole("button", { name: "Roll dice" });
  expect(roll.textContent).not.toMatch(/\p{Emoji}(?!\uFE0E)/u);
  fireEvent.click(screen.getByRole("button", { name: "Direct" }));
  fireEvent.change(screen.getByPlaceholderText(/direct the scene/i), { target: { value: "rain" } });
  expect(screen.getByRole("button", { name: "Direct ▸" })).toBeInTheDocument();
});

// Dice are a mechanics affordance: an unbound campaign is freeform play, and
// the popover's Check tab has nothing to offer it either (available_checks
// returns [] with no pack). Accepted consequence: freeform notation rolls go
// with it, since this button is their only entry point.
test("no dice button in a campaign with no mechanics pack bound", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  vi.mocked(api.getCampaignModule).mockResolvedValue({ setting: "", resolved: null, source: null });
  renderCampaign();
  await screen.findByText("a reply");
  expect(screen.queryByRole("button", { name: "Roll dice" })).toBeNull();
  // the composer is otherwise intact
  expect(screen.getByRole("textbox")).toBeInTheDocument();
  expect(screen.getByLabelText("Next reply words")).toBeInTheDocument();
});

// A control that appears and then vanishes a beat later is worse than one that
// arrives late, so an unresolved read renders nothing rather than guessing.
test("the dice button waits for the module read rather than flashing", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  let release: (v: CampaignModule) => void = () => {};
  vi.mocked(api.getCampaignModule).mockReturnValue(new Promise((r) => { release = r; }));
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
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(screen.getByRole("button", { name: "Roll dice" }));
  expect(screen.getByLabelText("Dice notation")).toBeInTheDocument();

  // the reader clears the pack in the Mechanics panel, which reports the change
  fireEvent.click(screen.getByRole("button", { name: /^mechanics$/i }));
  const modSelect = await screen.findByLabelText("Mechanics");
  vi.mocked(api.getCampaignModule).mockResolvedValue({ setting: "none", resolved: null, source: null });
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
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
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
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  let releaseSave: (v: Awaited<ReturnType<typeof api.setCampaignModule>>) => void = () => {};
  vi.mocked(api.setCampaignModule).mockReturnValue(new Promise((r) => { releaseSave = r; }));
  render(
    <MemoryRouter initialEntries={["/campaigns/run/scenes/s1"]}>
      {withPalette(<>
        <Link to="/campaigns/other/scenes/whichever">switch campaign</Link>
        {playRoutes()}
      </>)}
    </MemoryRouter>,
  );
  await screen.findByText("a reply");

  // start a save in campaign "run" that will not settle yet
  fireEvent.click(screen.getByRole("button", { name: /^mechanics$/i }));
  fireEvent.click(await screen.findByRole("button", { name: /^save$/i }));

  // the reader moves to a campaign with NO pack, which resolves first
  vi.mocked(api.getCampaignModule).mockResolvedValue({ setting: "", resolved: null, source: null });
  fireEvent.click(screen.getByText("switch campaign"));
  await waitFor(() => expect(screen.queryByRole("button", { name: "Roll dice" })).toBeNull());

  // now "run"'s save lands and fires its stale callback. "run" answers "bound",
  // the campaign actually on screen answers "not bound".
  vi.mocked(api.getCampaignModule).mockImplementation(async (c) =>
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
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(screen.getByRole("button", { name: "Roll dice" }));
  fireEvent.change(screen.getByLabelText("Dice notation"), { target: { value: "2d6+1" } });

  fireEvent.click(screen.getByRole("button", { name: /^mechanics$/i }));
  await screen.findByLabelText("Mechanics");
  // the panel's own re-read succeeds; the refresh the callback triggers does not
  vi.mocked(api.getCampaignModule).mockResolvedValueOnce(
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

// Codex review, finding 5. The badge used to sit inside the control and so
// formed part of its accessible name; as a sibling it has to be tied back on.

// The export menu and the Ledger link moved to the campaign hub, and their
// coverage went with them (`CampaignHub.test.tsx`). They were campaign-level
// destinations sitting on a scene's toolbar, which is what put the same four
// places in the chrome, on the page and in the palette at once.

test("rolls dice from the input bar popover and refreshes the scene", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  vi.mocked(api.roll).mockResolvedValue(fixture({ ok: true, roll: { id: "r1" }, message: "🎲" }));
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(screen.getByRole("button", { name: "Roll dice" }));
  fireEvent.change(screen.getByLabelText("Dice notation"), { target: { value: "2d6+1" } });
  fireEvent.change(screen.getByLabelText("Roll label"), { target: { value: "Perception" } });
  fireEvent.click(screen.getByRole("button", { name: "Roll ▸" }));
  await waitFor(() => expect(api.roll).toHaveBeenCalledWith("run", "s1", "2d6+1", "Perception"));
  // popover closes and the scene re-fetches to show the roll line
  await waitFor(() => expect(screen.queryByLabelText("Dice notation")).toBeNull());
  expect(vi.mocked(api.getScene).mock.calls.length).toBeGreaterThan(1);
});

test("disables roll submission while a roll is in flight, so repeated clicks send only one", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  let resolveRoll: (v: Awaited<ReturnType<typeof api.roll>>) => void = () => {};
  vi.mocked(api.roll).mockReturnValue(new Promise((resolve) => { resolveRoll = resolve; }));
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
  resolveRoll(fixture({ ok: true, roll: { id: "r1" }, message: "🎲" }));
  await waitFor(() => expect(screen.queryByLabelText("Dice notation")).toBeNull());
});

test("shows a roll error and keeps the popover open", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  vi.mocked(api.roll).mockRejectedValue({ detail: "can't read dice notation 'garbage'" });
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(screen.getByRole("button", { name: "Roll dice" }));
  fireEvent.change(screen.getByLabelText("Dice notation"), { target: { value: "garbage" } });
  fireEvent.click(screen.getByRole("button", { name: "Roll ▸" }));
  await screen.findByText(/can't read dice notation/);
  expect(screen.getByLabelText("Dice notation")).toBeInTheDocument();
});

test("toggles an in-app dice notation syntax reference from the roll popover", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  renderCampaign();
  await screen.findByText("a reply");
  fireEvent.click(screen.getByRole("button", { name: "Roll dice" }));
  expect(screen.queryByText(/exploding dice/i)).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Dice notation syntax" }));
  expect(screen.getByText(/exploding dice/i)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Dice notation syntax" }));
  expect(screen.queryByText(/exploding dice/i)).toBeNull();
});

test("an SSE proposal event mounts the roll-proposal chip", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  vi.mocked(api.chat).mockImplementation(async (_c, _s, _t, onEvent) => {
    onEvent({ proposal: PROPOSAL_PAYLOAD });
  });
  // the SSE event mounts the chip immediately; runStream's finally then
  // re-fetches via selectScene — mock the backend as having durably
  // persisted the same pending record by then (its real behavior).
  vi.mocked(api.getRollProposal)
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
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  vi.mocked(api.chat).mockImplementation(async (_c, _s, _t, onEvent) => {
    onEvent({ proposal: PROPOSAL_PAYLOAD });
  });
  vi.mocked(api.getRollProposal)
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
    expect.any(Function), expect.any(AbortSignal),
    expect.any(String), expect.any(Function)));
  await waitFor(() => expect(screen.queryByRole("button", { name: "Roll it" })).toBeNull());
});

test("a declined roll whose narration never landed stays retryable", async () => {
  // Stopping (or an upstream failure on) a declined record's continuation
  // leaves it `declined` with nothing persisted. The backend re-streams that
  // continuation on request, but the chip used to be filtered out of the scene
  // load, so the decline narration had no way back.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "assistant", content: "a reply" }] }));
  vi.mocked(api.getRollProposal).mockResolvedValue({
    record: { id: "pr-1", status: "declined", payload: PROPOSAL_PAYLOAD, resolution: null } });
  renderCampaign();
  await screen.findByText(/Roll declined, narration pending/);
  fireEvent.click(screen.getByRole("button", { name: "Continue narration" }));
  await waitFor(() => expect(api.resolveProposal).toHaveBeenCalledWith(
    "run", "s1", { proposal: "pr-1", action: "decline" },
    expect.any(Function), expect.any(AbortSignal),
    expect.any(String), expect.any(Function)));
});

test("selecting a scene re-hydrates a pending roll-proposal record", async () => {
  vi.mocked(api.listScenes).mockResolvedValue([
    { id: "001--2024-01-01--one", title: "One", model: "", created: "", updated: "", date: "" },
    { id: "002--2024-01-02--two", title: "Two", model: "", created: "", updated: "", date: "" },
  ]);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [{ role: "assistant", content: "a reply" }] }));
  vi.mocked(api.getRollProposal)
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
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  vi.mocked(api.getSceneChecks).mockResolvedValue({ actors: [
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
  const [, , rollBody] = vi.mocked(api.rollCheck).mock.calls[0];
  expect(rollBody).not.toHaveProperty("difficulty");
  await waitFor(() => expect(screen.queryByLabelText("Check actor")).toBeNull());
});

test("popover Check mode with a typed difficulty posts it", async () => {
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [
    { role: "user", content: "hi" }, { role: "assistant", content: "a reply" }] }));
  vi.mocked(api.getSceneChecks).mockResolvedValue({ actors: [
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
  vi.mocked(api.listScenes).mockResolvedValue([
    { id: "001--2024-01-01--one", title: "One", model: "", created: "", updated: "", date: "" },
    { id: "002--2024-01-02--two", title: "Two", model: "", created: "", updated: "", date: "" },
  ]);
  vi.mocked(api.getScene).mockResolvedValue(fixture({ meta: {}, messages: [{ role: "assistant", content: "a reply" }] }));
  const PROPOSAL_A: RollProposalPayload = {
    id: "pr-a", check: "brawl", check_label: "Vigor + Brawl",
    actor: "characters:mara", actor_label: "Mara", difficulty: 6,
    available: { "characters:mara": [["brawl", "Vigor + Brawl"]] }, problems: [],
  };
  const PROPOSAL_B: RollProposalPayload = {
    id: "pr-b", check: "stealth", check_label: "Wits + Stealth",
    actor: "characters:borys", actor_label: "Borys", difficulty: 4,
    available: { "characters:borys": [["stealth", "Wits + Stealth"]] }, problems: [],
  };
  // scenes each have their own live pending proposal — keyed by scene id, not call order.
  vi.mocked(api.getRollProposal).mockImplementation((_c, sid) => {
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
    expect.any(Function), expect.any(AbortSignal),
    expect.any(String), expect.any(Function)));
  expect(api.resolveProposal).not.toHaveBeenCalledWith(
    "run", expect.anything(),
    expect.objectContaining({ proposal: "pr-a" }),
    expect.anything());
});
