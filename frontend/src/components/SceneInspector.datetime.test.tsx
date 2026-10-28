import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { api } from "../api/client";
import type {
SceneDatetime
} from "../api/types";
import { noticesChanged } from "../appEvents";
import { CLOCK, DIGEST, REV } from "../testkit/clockHarness";
import { fixture, type DeepPartial } from "../testkit/fixture";
import { renderInspector, resetSceneInspectorMocks } from "../testkit/sceneInspectorHarness";
import { SceneInspector } from "./SceneInspector";
vi.mock("../api/client", async () => (await import("../testkit/sceneInspectorMocks")).sceneInspectorApiMock());
vi.mock("../api/models", async () => (await import("../testkit/sceneInspectorMocks")).sceneInspectorModelsMock());
vi.mock("./ResponseTargetsPicker", async () => (await import("../testkit/sceneInspectorMocks")).responseTargetsMock());
vi.mock("./AuthorsNotesPanel", async () => (await import("../testkit/sceneInspectorMocks")).authorsNotesMock());

beforeEach(resetSceneInspectorMocks);

function pending<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

const CLOCK_MOVE = {
  ok: true, moved: true, now: "2026-12-27", friendly: "27 December 2026",
  digest: DIGEST, fired: DIGEST.events,
};
const DATED_SCENE = fixture<SceneDatetime>({
  current: { native: "2026-07-04", friendly: "4 July 2026", weekday: "Saturday",
    secondary_friendly: null, holidays_today: [], upcoming: null, cast: [],
    notices: [{ key: "event:739437:the-envoy-arrives", kind: "event",
      name: "The envoy arrives", in_days: 2, friendly: "6 July 2026" }] },
  history: ["2026-07-04"], suggested: null,
});

test("a retained clock advance from the previous scene preserves the pending scene date and draft", async () => {
  const advance = pending<Awaited<ReturnType<typeof api.advanceTime>>>();
  const readback = pending<Awaited<ReturnType<typeof api.getCampaignClock>>>();
  const sceneB = pending<SceneDatetime>();
  vi.mocked(api.getSceneDatetime).mockImplementation((_cid, sid) =>
    sid === "s2" ? sceneB.promise : Promise.resolve({ current: null, history: [], suggested: null }));
  vi.mocked(api.getCampaignClock).mockResolvedValue(CLOCK);
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: REV, digest: DIGEST });
  vi.mocked(api.advanceTime).mockReturnValue(advance.promise);
  const { rerender } = render(
    <MemoryRouter><SceneInspector cid="c" sid="s" refreshKey={0}
      onSceneChanged={() => {}} /></MemoryRouter>);
  fireEvent.click(await screen.findByRole("button", { name: /campaign clock/i }));
  await screen.findByText(/Now: 24 December 2026/);
  fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "three days of rain" } });
  fireEvent.click(screen.getByRole("button", { name: "Advance time" }));
  await waitFor(() => expect(api.advanceTime).toHaveBeenCalled());

  // Keep the campaign clock mounted across a same-campaign scene switch. Its
  // in-flight handler still holds scene A's onAdvanced, while B owns this read.
  rerender(
    <MemoryRouter><SceneInspector cid="c" sid="s2" refreshKey={0}
      onSceneChanged={() => {}} /></MemoryRouter>);
  await waitFor(() => expect(api.getSceneDatetime).toHaveBeenCalledWith("c", "s2", undefined));
  fireEvent.change(screen.getByLabelText("Scene date year"), { target: { value: "2027" } });
  await waitFor(() => expect(screen.getByLabelText("Scene date month")).toBeEnabled());
  fireEvent.change(screen.getByLabelText("Scene date month"), { target: { value: "07" } });
  fireEvent.change(screen.getByLabelText("Scene date day"), { target: { value: "9" } });
  vi.mocked(api.getCampaignClock).mockReturnValueOnce(readback.promise);
  await act(async () => { advance.resolve(CLOCK_MOVE); });
  await screen.findByText(/^Advanced/);
  // Completing the real readback lets runAdvance invoke its retained callback.
  await act(async () => { readback.resolve({ ...CLOCK, now: "2026-12-27", friendly: "27 December 2026" }); });
  await act(async () => { sceneB.resolve(DATED_SCENE); });

  expect(await screen.findByText("The envoy arrives")).toBeInTheDocument();
  expect(screen.getByText("4 July 2026 (Saturday)")).toBeInTheDocument();
  expect(screen.getByLabelText("Scene date year")).toHaveValue(2027);
  expect(screen.getByLabelText("Scene date month")).toHaveValue("07");
  expect(screen.getByLabelText("Scene date day")).toHaveValue("9");
});

test("a current-scene clock advance refreshes its date and notices while preserving typing", async () => {
  vi.mocked(api.getSceneDatetime)
    .mockResolvedValueOnce({ current: null, history: [], suggested: null })
    .mockResolvedValue(DATED_SCENE);
  vi.mocked(api.getCampaignClock).mockResolvedValue(CLOCK);
  vi.mocked(api.previewAdvance).mockResolvedValue({ revision: REV, digest: DIGEST });
  vi.mocked(api.advanceTime).mockResolvedValue(CLOCK_MOVE);
  renderInspector();
  fireEvent.change(await screen.findByLabelText("Scene date year"), { target: { value: "2027" } });
  await waitFor(() => expect(screen.getByLabelText("Scene date month")).toBeEnabled());
  fireEvent.change(screen.getByLabelText("Scene date month"), { target: { value: "07" } });
  fireEvent.change(screen.getByLabelText("Scene date day"), { target: { value: "9" } });
  fireEvent.click(screen.getByRole("button", { name: /campaign clock/i }));
  await screen.findByText(/Now: 24 December 2026/);
  fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "three days of rain" } });
  fireEvent.click(screen.getByRole("button", { name: "Advance time" }));

  expect(await screen.findByText("The envoy arrives")).toBeInTheDocument();
  expect(screen.getByText("4 July 2026 (Saturday)")).toBeInTheDocument();
  expect(screen.getByLabelText("Scene date year")).toHaveValue(2027);
  expect(screen.getByLabelText("Scene date month")).toHaveValue("07");
  expect(screen.getByLabelText("Scene date day")).toHaveValue("9");
});

test("no calendar selected: choosing one confirms the calendar", async () => {
  vi.mocked(api.getCalendarConfig).mockResolvedValue(fixture({
    primary: { provider: "gregorian", region: "US", custom_holidays: [], anchor: null },
    secondary: null, confirmed: false }));
  renderInspector();
  fireEvent.click(await screen.findByRole("button", { name: /use this calendar/i }));
  await waitFor(() => expect(api.setCalendarConfig).toHaveBeenCalledWith(
    { kind: "campaign", id: "c" }, expect.objectContaining({ confirmed: true })));
});

test("calendar but no date: setting a date calls setSceneDatetime and notifies", async () => {
  const onChanged = vi.fn();
  renderInspector(onChanged);
  fireEvent.change(await screen.findByLabelText("Scene date year"), { target: { value: "2026" } });
  const monthSelect = await screen.findByLabelText("Scene date month");
  await waitFor(() => expect(monthSelect).not.toBeDisabled());
  fireEvent.change(monthSelect, { target: { value: "07" } });
  const daySelect = screen.getByLabelText("Scene date day");
  await waitFor(() => expect(daySelect).not.toBeDisabled());
  fireEvent.change(daySelect, { target: { value: "4" } });
  fireEvent.click(screen.getByRole("button", { name: /set date/i }));
  await waitFor(() => expect(api.setSceneDatetime).toHaveBeenCalledWith("c", "s", "2026-07-04"));
  await waitFor(() => expect(onChanged).toHaveBeenCalled());
});

test("first date set renames the scene: adopts the new id via onSceneRenamed", async () => {
  vi.mocked(api.setSceneDatetime).mockResolvedValue(
    { ok: true, advanced: false, friendly: "4 July 2026", id: "001--2026-07-04--s" });
  const onRenamed = vi.fn();
  render(<SceneInspector cid="c" sid="s" refreshKey={0}
                         onSceneChanged={() => {}} onSceneRenamed={onRenamed} />);
  fireEvent.change(await screen.findByLabelText("Scene date year"), { target: { value: "2026" } });
  const monthSelect = await screen.findByLabelText("Scene date month");
  await waitFor(() => expect(monthSelect).not.toBeDisabled());
  fireEvent.change(monthSelect, { target: { value: "07" } });
  const daySelect = screen.getByLabelText("Scene date day");
  await waitFor(() => expect(daySelect).not.toBeDisabled());
  fireEvent.change(daySelect, { target: { value: "4" } });
  fireEvent.click(screen.getByRole("button", { name: /set date/i }));
  await waitFor(() => expect(onRenamed).toHaveBeenCalledWith("001--2026-07-04--s"));
});

test("shows the current date when one is set", async () => {
  vi.mocked(api.getSceneDatetime).mockResolvedValue(fixture({
    current: { native: "2026-07-04", friendly: "4 July 2026", weekday: "Saturday",
               secondary_friendly: null, holidays_today: [], upcoming: null, cast: [] },
    history: ["2026-07-04"] }));
  renderInspector();
  await screen.findByText(/4 July 2026/);
});

test("an imminent event is warned about once, and dismissing it marks the key", async () => {
  // The reader-facing half of #106. The model is told what is upcoming every
  // turn; this is the channel that says it once and then stops.
  vi.mocked(api.getSceneDatetime).mockResolvedValue(fixture({
    current: { native: "2026-07-04", friendly: "4 July 2026", weekday: "Saturday",
               secondary_friendly: null, holidays_today: [], upcoming: null, cast: [],
               notices: [{ key: "event:739437:the-envoy-arrives", kind: "event",
                           name: "The envoy arrives", in_days: 2,
                           friendly: "6 July 2026" }] },
    history: ["2026-07-04"] }));
  renderInspector();
  await screen.findByText("The envoy arrives");
  fireEvent.click(screen.getByLabelText("Dismiss The envoy arrives"));
  await waitFor(() => expect(api.dismissNotices).toHaveBeenCalledWith(
    "c", ["event:739437:the-envoy-arrives"], "s"));
  await waitFor(() => expect(screen.queryByText("The envoy arrives")).toBeNull());
});

test("a notice dismissed elsewhere refreshes this panel's copy of the payload", async () => {
  // CampaignView mounts the inspector and the new-scene chooser as independent
  // siblings, so the chooser can be overlaid on a live inspector. A dismissal
  // in either left the other holding a payload from before it, showing the
  // reader the warning they just closed (#106). The api mutators emit on the
  // `notices` channel and both surfaces listen.
  vi.mocked(api.getSceneDatetime).mockResolvedValue(fixture({
    current: { native: "2026-07-04", friendly: "4 July 2026", weekday: "Saturday",
               secondary_friendly: null, holidays_today: [], upcoming: null, cast: [],
               notices: [] },
    history: ["2026-07-04"] }));
  renderInspector();
  await screen.findByText(/4 July 2026/);
  const before = vi.mocked(api.getSceneDatetime).mock.calls.length;
  act(() => { noticesChanged(); });
  await waitFor(() =>
    expect(vi.mocked(api.getSceneDatetime).mock.calls.length).toBeGreaterThan(before));
  // `fresh`, so the read cannot join a GET issued before the write it follows.
  const last = vi.mocked(api.getSceneDatetime).mock.calls.slice(-1)[0];
  expect(last[2]).toEqual({ fresh: true });
});

test("an older datetime read cannot replace a newer one", async () => {
  // `fresh` stops a new read JOINING an in-flight GET; it does not cancel the
  // older promise, whose `.then` is already attached. Landing late, that stale
  // payload would put the acknowledged notice back (#106).
  const withNotice = fixture<SceneDatetime>({
    current: { native: "2026-07-04", friendly: "4 July 2026", weekday: "Saturday",
               secondary_friendly: null, holidays_today: [], upcoming: null, cast: [],
               notices: [{ key: "event:739437:the-envoy-arrives", kind: "event",
                           name: "The envoy arrives", in_days: 2,
                           friendly: "6 July 2026" }] },
    history: ["2026-07-04"] });
  const withoutNotice = {
    ...withNotice,
    current: { ...withNotice.current, notices: [] } } satisfies DeepPartial<SceneDatetime>;

  // The first (stale) read is held open; the second resolves immediately.
  let landStale: (v: SceneDatetime) => void = () => {};
  vi.mocked(api.getSceneDatetime)
    .mockReturnValueOnce(new Promise((r: any) => { landStale = r; }))
    .mockResolvedValue(fixture(withoutNotice));
  renderInspector();
  await waitFor(() => expect(api.getSceneDatetime).toHaveBeenCalled());

  // A dismissal elsewhere triggers the fresh read, which settles first.
  act(() => { noticesChanged(); });
  await waitFor(() => expect(screen.getByText(/4 July 2026/)).toBeTruthy());

  // Now the stale one lands, still carrying the notice. It must be ignored.
  await act(async () => { landStale(fixture(withNotice)); });
  expect(screen.queryByText("The envoy arrives")).toBeNull();
});

test("a scene change drops the previous scene's notices before the reload lands", async () => {
  // Until the reload settles, `when` holds the PREVIOUS scene's payload — and
  // the banner would render its notices beside the new ids, so a dismissal in
  // that window writes the old campaign's occurrence key into the new
  // campaign's ledger (#106).
  vi.mocked(api.getSceneDatetime).mockResolvedValue(fixture({
    current: { native: "2026-07-04", friendly: "4 July 2026", weekday: "Saturday",
               secondary_friendly: null, holidays_today: [], upcoming: null, cast: [],
               notices: [{ key: "event:739437:the-envoy-arrives", kind: "event",
                           name: "The envoy arrives", in_days: 2,
                           friendly: "6 July 2026" }] },
    history: ["2026-07-04"] }));
  const { rerender } = render(
    <MemoryRouter><SceneInspector cid="c" sid="s" refreshKey={0}
                                  onSceneChanged={() => {}} /></MemoryRouter>);
  await screen.findByText("The envoy arrives");
  // The next scene's read never settles, so only the synchronous clear can help.
  vi.mocked(api.getSceneDatetime).mockReturnValue(new Promise(() => {}));
  rerender(
    <MemoryRouter><SceneInspector cid="c" sid="s2" refreshKey={0}
                                  onSceneChanged={() => {}} /></MemoryRouter>);
  expect(screen.queryByText("The envoy arrives")).toBeNull();
});

test("a read in flight when the scene changes cannot restore the old payload", async () => {
  // The invariant: a read issued for the previous scene never restores its
  // payload under the new ids (#106).
  //
  // Honest about what this does and does not pin. TWO things enforce it — the
  // reset advances the generation, and so does the reload that follows it — so
  // this passes with either one alone and cannot isolate the first. The window
  // the reset closes is between the render-time clear and the effect flush,
  // which a test cannot interleave. The bump is kept because it is free and
  // correct, not because this proves it.
  const withNotice = fixture<SceneDatetime>({
    current: { native: "2026-07-04", friendly: "4 July 2026", weekday: "Saturday",
               secondary_friendly: null, holidays_today: [], upcoming: null, cast: [],
               notices: [{ key: "event:739437:the-envoy-arrives", kind: "event",
                           name: "The envoy arrives", in_days: 2,
                           friendly: "6 July 2026" }] },
    history: ["2026-07-04"] });
  let landOld: (v: SceneDatetime) => void = () => {};
  vi.mocked(api.getSceneDatetime).mockReturnValueOnce(
    new Promise((r: any) => { landOld = r; }));
  const { rerender } = render(
    <MemoryRouter><SceneInspector cid="c" sid="s" refreshKey={0}
                                  onSceneChanged={() => {}} /></MemoryRouter>);
  await waitFor(() => expect(api.getSceneDatetime).toHaveBeenCalled());
  // The next scene has nothing imminent, and its read settles first.
  vi.mocked(api.getSceneDatetime).mockResolvedValue(fixture({
    current: { ...withNotice.current, notices: [] }, history: ["2026-07-04"] }));
  rerender(
    <MemoryRouter><SceneInspector cid="c" sid="s2" refreshKey={0}
                                  onSceneChanged={() => {}} /></MemoryRouter>);
  await screen.findByText(/4 July 2026/);
  await act(async () => { landOld(fixture(withNotice)); });
  expect(screen.queryByText("The envoy arrives")).toBeNull();
});

test("a scene with nothing imminent shows no warning at all", async () => {
  vi.mocked(api.getSceneDatetime).mockResolvedValue(fixture({
    current: { native: "2026-07-04", friendly: "4 July 2026", weekday: "Saturday",
               secondary_friendly: null, holidays_today: [], upcoming: null, cast: [],
               notices: [] },
    history: ["2026-07-04"] }));
  renderInspector();
  await screen.findByText(/4 July 2026/);
  expect(screen.queryByLabelText("Coming up")).toBeNull();
});

test("a dateless scene with a suggestion pre-fills the date input", async () => {
  vi.mocked(api.getSceneDatetime).mockResolvedValue(
    { current: null, history: [], suggested: "2026-07-06" });
  renderInspector();
  await screen.findByLabelText("Scene date year");
  // the picker's visible fields show the prefill once it arrives...
  await waitFor(() =>
    expect(screen.getByLabelText("Scene date year")).toHaveValue(2026));
  await waitFor(() =>
    expect(screen.getByLabelText("Scene date month")).toHaveValue("07"));
  expect(screen.getByLabelText("Scene date day")).toHaveValue("6");
  // ...and "Set date" is immediately enabled and submits the suggestion
  const button = await screen.findByRole("button", { name: /set date/i });
  await waitFor(() => expect(button).not.toBeDisabled());
  fireEvent.click(button);
  await waitFor(() => expect(api.setSceneDatetime).toHaveBeenCalledWith("c", "s", "2026-07-06"));
});

test("the date actions are locked while a turn streams into this scene", async () => {
  // Setting a date for the first time re-slugs the scene file, and a scene's id
  // is its filename — so this is a rename control, and moving the file mid-turn
  // strands `finalize`, `_persist_reply` and the abort write on the old id.
  // Review caught the rail and the cast panel being locked while this one, the
  // always-mounted surface, was not (#95).
  vi.mocked(api.getSceneDatetime).mockResolvedValue(
    { current: null, history: [], suggested: "2026-07-06" });
  render(<SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} sceneLocked />);
  const button = await screen.findByRole("button", { name: /set date/i });
  await waitFor(() => expect(screen.getByLabelText("Scene date year")).toHaveValue(2026));
  expect(button).toBeDisabled();          // even with a date filled in
  expect(button).toHaveAttribute("title", "Not while this scene is generating");
  fireEvent.click(button);
  expect(api.setSceneDatetime).not.toHaveBeenCalled();
});

test("Advance to is locked for the same reason", async () => {
  // The dated branch renders a different button through the same handler.
  vi.mocked(api.getSceneDatetime).mockResolvedValue(fixture({
    current: { friendly: "6 July 2026", weekday: "Monday", holidays_today: [] },
    history: [], suggested: "2026-07-07",
  }));
  const { rerender } = render(
    <SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} sceneLocked />);
  const button = await screen.findByRole("button", { name: /advance to/i });
  expect(button).toBeDisabled();
  // On the title, not on `disabled` alone: a dated scene does not prefill the
  // picker, so this button is disabled for want of a date either way and
  // `toBeDisabled` would pass without the lock existing. The title is set only
  // when locked, so it is the one assertion that distinguishes the two.
  expect(button).toHaveAttribute("title", "Not while this scene is generating");
  rerender(<SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  await waitFor(() => expect(
    screen.getByRole("button", { name: /advance to/i })).not.toHaveAttribute("title"));
});

