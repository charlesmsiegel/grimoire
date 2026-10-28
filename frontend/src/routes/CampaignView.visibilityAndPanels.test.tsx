import { screen, fireEvent, waitFor, act, within } from "@testing-library/react";
import { api } from "../api/client";
import type { TrackerField } from "../api/client";
import { installCampaignMocks, ONE_SCENE, renderCampaign } from "../testkit/campaignHarness";
import { fixture } from "../testkit/fixture";
import { trackerCallsFor } from "../testkit/campaignFixtures";

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




test("a turn that kept generating while the tab was away is read back on return", async () => {
  // THE feature. A send goes out, the socket dies mid-generation, the phone
  // locks. The backend keeps going and buffers every frame. When the tab comes
  // back this has to read the frames produced while nobody was listening --
  // an earlier revision detected the live run and returned, under a comment
  // claiming it attached, so the turn finished perfectly on the server and the
  // screen showed nothing until a manual reload.
  //
  // The asserted text is the narration generated WHILE AWAY, never the player's
  // own post: the post comes back with the scene refetch whether or not a
  // single buffered frame was applied, so asserting on it would pass against an
  // implementation that requests the right offset and drops everything it gets.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  // The socket DIES -- it is not stopped. A confirmed Stop is an established
  // outcome and settles; this is the case where nobody ever learned anything,
  // which is the only one recovery is for.
  vi.mocked(api.chat).mockImplementation(async (
    _c, _s, _t, onEvent) => {
    onEvent({ run: { id: "run-9", attempt_id: "a", state: "running", next_index: 1 } });
    throw new Error("network");
  });
  vi.mocked(api.findRun).mockResolvedValueOnce({ run: null }).mockResolvedValue({
    run: { id: "run-9", attempt_id: "a", state: "running", next_index: 4 } });
  // The transcript grows ONLY once the reattach has actually run, which is
  // what makes this discriminate. Returning the narration unconditionally
  // would let the refetch that follows Stop show it, and the test would pass
  // against an implementation that detects the live run and returns.
  let attached = false;
  let resume: () => void = () => {};
  vi.mocked(api.getScene).mockImplementation(async () => fixture({
    meta: {},
    messages: attached
      ? [{ role: "assistant", content: "The lamps are already lit." }]
      : [],
  }));
  vi.mocked(api.attachRun).mockImplementation(async (
    _c, _s, _r, _from, onEvent) => {
    onEvent({ response_start: { id: "a", speaker: "Mara", actor_ref: "characters:mara" } });
    await new Promise<void>((resolve) => { resume = resolve; });
    attached = true;
    onEvent({ delta: "The lamps are already lit." });
    onEvent({ response_end: { id: "a", status: "complete" } });
    onEvent({ done: true });
  });

  renderCampaign();
  fireEvent.change(await screen.findByRole("textbox"), { target: { value: "Mara waits." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await waitFor(() => expect(api.chat).toHaveBeenCalled());

  act(() => { document.dispatchEvent(new Event("visibilitychange")); });

  await waitFor(() => expect(api.attachRun).toHaveBeenCalled());
  expect(await screen.findByRole("status", { name: "Mara is responding" })).toBeVisible();
  expect(screen.getByRole("button", { name: /Continue/ })).toBeDisabled();
  await act(async () => resume());
  expect(await screen.findByText(/The lamps are already lit\./)).toBeInTheDocument();
  expect(screen.queryByRole("status", { name: "Mara is responding" })).not.toBeInTheDocument();
});

test("the reattach asks from one past what was read, not from the live tail", async () => {
  // `next_index` is where the run is NOW. Resuming from it drops everything
  // generated while the client was away -- which, in the case this exists for,
  // is the entire reply. The cursor has to be one past the last frame actually
  // read, and the run frame this send received was index 0.
  vi.mocked(api.listScenes).mockResolvedValue(ONE_SCENE);
  vi.mocked(api.chat).mockImplementation(async (
    _c, _s, _t, onEvent, _r,
    _signal, _a, onIndex) => {
    // The wire order the backend actually produces: `tail_response` yields the
    // lead run frame with NO `id:` prefix, and the buffered frames follow it
    // starting at index 0. So the run is named before any index arrives.
    onEvent({ run: { id: "run-9", attempt_id: "a", state: "running", next_index: 1 } });
    onIndex?.(0);
    onEvent({ delta: "The lamps " });
    throw new Error("network");           // the socket dies after one frame
  });
  // Null for the mount pass; see the note in the reattach test above.
  vi.mocked(api.findRun)
    .mockResolvedValueOnce({ run: null })
    .mockResolvedValue({
      run: { id: "run-9", attempt_id: "a", state: "running", next_index: 12 } });

  renderCampaign();
  fireEvent.change(await screen.findByRole("textbox"), { target: { value: "Mara waits." } });
  fireEvent.click(screen.getByRole("button", { name: /send ▸/i }));
  await waitFor(() => expect(api.chat).toHaveBeenCalled());
  await waitFor(() => expect(screen.getByRole("button", { name: /(send ▸|continue ▶)/i })).toBeEnabled());

  act(() => { document.dispatchEvent(new Event("visibilitychange")); });

  await waitFor(() => expect(api.findRun).toHaveBeenCalled());
  await waitFor(() => expect(api.attachRun).toHaveBeenCalled());
  expect(vi.mocked(api.attachRun).mock.calls[0][3]).toBe(1);   // not 12
});


// ---- the scene bar's overflow ----

test("the eight panels are behind one control, and End scene is not", async () => {
  // The mobile-clutter half of the brief. The bar's own focus-mode comment
  // conceded the cost of not having this: "eleven controls at the 44px touch
  // target this width mandates wrap into four rows".
  renderCampaign();
  await screen.findByText("Scene ⋯");

  // What the bar is FOR stays on the bar.
  expect(screen.getByRole("button", { name: "End scene" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /back to the campaign/i }))
    .toBeInTheDocument();

  // Everything that opens a panel is one control now.
  for (const label of ["Changes", "World updates", "Composition", "Mechanics",
                       "Calendar", "Response", "Cover", "Tracker", "+ New scene"]) {
    expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
  }
  // ...and they are inside the disclosure rather than beside it.
  const menu = screen.getByText("Scene ⋯").closest("details")!;
  expect(within(menu).getByRole("button", { name: "Calendar" })).toBeInTheDocument();
});

test("the Tracker panel edits this scene's fields, and Close shuts it", async () => {
  vi.mocked(api.listScenes).mockResolvedValue([
    { id: "s1", title: "One", model: "", created: "", updated: "", date: "" },
  ]);
  const clothing: TrackerField = { key: "clothing", label: "Clothing", type: "text", aware: "present", hint: "" };
  vi.mocked(api.getTrackerFields).mockResolvedValue(
    { layer: {}, effective: [clothing], inherited: [clothing] });
  renderCampaign();
  await screen.findByText("Scene ⋯");

  fireEvent.click(screen.getByRole("button", { name: "Tracker" }));

  expect(await screen.findByRole("checkbox", { name: "Clothing" })).toBeChecked();
  expect(api.getTrackerFields).toHaveBeenCalledWith({ kind: "scene", cid: "run", sid: "s1" });
  fireEvent.click(screen.getByRole("button", { name: "Close Tracker" }));
  expect(screen.queryByRole("checkbox", { name: "Clothing" })).not.toBeInTheDocument();
});

test("switching a field off in the scene's Tracker panel re-reads the tracker summary", async () => {
  // The summary carries the cast tiles' moods and the fields' labels, which
  // the scene's layer decides -- a save there must not leave them stale until
  // the next turn lands.
  vi.mocked(api.listScenes).mockResolvedValue([
    { id: "s1", title: "One", model: "", created: "", updated: "", date: "" },
  ]);
  const mood: TrackerField = { key: "visible_mood", label: "Visible mood", type: "text", aware: "present", hint: "" };
  vi.mocked(api.getTrackerFields).mockResolvedValue(
    { layer: {}, effective: [mood], inherited: [mood] });
  vi.mocked(api.setTrackerFields).mockResolvedValue(
    { layer: { off: ["visible_mood"] }, effective: [{ ...mood, off: true }], inherited: [mood] });
  renderCampaign();
  await screen.findByText("Scene ⋯");
  fireEvent.click(screen.getByRole("button", { name: "Tracker" }));
  const box = await screen.findByRole("checkbox", { name: "Visible mood" });
  const before = trackerCallsFor("s1");
  fireEvent.click(box);
  await waitFor(() => expect(api.setTrackerFields).toHaveBeenCalledWith(
    { kind: "scene", cid: "run", sid: "s1" }, { fields: [], off: ["visible_mood"] }));
  await waitFor(() => expect(trackerCallsFor("s1")).toBeGreaterThan(before));
});

test("picking from the menu shuts the menu it was picked from", async () => {
  // A menu still standing over the panel it just opened is in the way of the
  // thing that was asked for.
  renderCampaign();
  const summary = await screen.findByText("Scene ⋯");
  const menu = summary.closest("details") as HTMLDetailsElement;
  menu.open = true;

  fireEvent.click(screen.getByRole("button", { name: "Calendar" }));

  expect(menu.open).toBe(false);
});

test("with nothing open the bar grows no Close", async () => {
  // It renders only while something is above the transcript, which is exactly
  // when its width is worth spending.
  renderCampaign();
  await screen.findByText("Scene ⋯");

  expect(screen.queryByRole("button", { name: /^Close / })).not.toBeInTheDocument();
});
