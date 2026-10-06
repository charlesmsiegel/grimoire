import { StrictMode } from "react";
import { render, screen, fireEvent, waitFor, act, within } from "@testing-library/react";
import { NewSceneChooser } from "./NewSceneChooser";
import type { Driver, DriversSnapshot } from "../api/types";

// The real module first: the suggestion hook tells a stale refusal apart with
// `err instanceof ApiError`, which a factory with no `ApiError` export would
// turn into a throw inside its `.catch`.
vi.mock("../api/client", async () => ({
  ...(await vi.importActual<typeof import("../api/client")>("../api/client")),
  api: {
    availableGreetings: vi.fn(), sceneSuggestions: vi.fn(), sceneIntent: vi.fn(),
    createScene: vi.fn(), startFromGreeting: vi.fn(), addCastBatch: vi.fn(),
    setSceneLocation: vi.fn(), setSceneDatetime: vi.fn(), renameScene: vi.fn(),
    deleteScene: vi.fn(), listEntities: vi.fn(), listCharacters: vi.fn(),
    listCampaignPCs: vi.fn(), listAppearances: vi.fn(),
    listSceneIdeas: vi.fn(), saveSceneIdea: vi.fn(), setSceneIdeaStatus: vi.fn(),
    getCampaignClock: vi.fn(),   // SceneConfirmForm's date-fill button
    sceneImportParse: vi.fn(), sceneImport: vi.fn(),   // the import pane (#92)
    // The pre-notice banner above the mode cards (#106): what is imminent in
    // the campaign, read from the clock since there is no scene yet.
    campaignNotices: vi.fn(), dismissNotices: vi.fn(),
    // The Story Pressure controls' one read (capstone §16.2).
    continuityDrivers: vi.fn(),
  },
}));
vi.mock("./CalendarDatePicker", () => ({
  CalendarDatePicker: ({ value, onChange, ariaLabel }: any) =>
    <input aria-label={ariaLabel} value={value} onChange={(e) => onChange(e.target.value)} />,
}));
import { api, ApiError } from "../api/client";

function driver(ref: string, kind: Driver["kind"], label: string,
                state: Driver["pressure"]["state"], in_days: number | null = null): Driver {
  return { ref, kind, label, summary: "", actors: [], status: "open",
           pressure: { state, in_days, friendly: "" }, time_anchors: [], links: [] };
}

const CORONATION = {
  ref: "event:the-coronation", kind: "event" as const, label: "The coronation",
  native: "2026-05-17", friendly: "17 May 2026", fixed: 110, in_days: 10,
  precision: "exact" as const,
};

function snapshot(over: Partial<DriversSnapshot> = {}): DriversSnapshot {
  return {
    now: "2026-05-07", friendly: "7 May 2026", fixed: 100, matching: "basic",
    drivers: [
      driver("thread:mara-s-map", "thread", "Mara's map", "stale"),
      driver("commitment:mara-s-oath", "commitment", "Mara's oath", "due_soon", 2),
      driver("event:the-coronation", "event", "The coronation", "upcoming", 10),
    ],
    anchors: [CORONATION],
    ...over,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  (api.listAppearances as any).mockResolvedValue([]);
  (api.getCampaignClock as any).mockResolvedValue({ now: "", friendly: "", log: [] });
  (api.availableGreetings as any).mockResolvedValue(
    [{ id: "reck", name: "Reckoning", available: true, reasons: [], unlocked: true }]);
  (api.sceneSuggestions as any).mockResolvedValue(
    { suggestions: [], greeting_picks: [], next_date: "2026-01-01" });
  (api.createScene as any).mockResolvedValue({ id: "s9" });
  (api.startFromGreeting as any).mockResolvedValue({ ok: true, id: "s9" });
  (api.renameScene as any).mockResolvedValue({ id: "s9", title: "Reckoning" });
  (api.setSceneDatetime as any).mockResolvedValue({ ok: true, id: "s9" });
  (api.deleteScene as any).mockResolvedValue({ ok: true });
  (api.listEntities as any).mockResolvedValue([]);
  (api.listCharacters as any).mockResolvedValue([]);
  (api.listCampaignPCs as any).mockResolvedValue([]);
  (api.listSceneIdeas as any).mockResolvedValue([]);
  (api.setSceneIdeaStatus as any).mockResolvedValue({ ok: true });
  (api.sceneImportParse as any).mockResolvedValue(
    { title: "The Long Quay", date: "", location: "", pcless: false,
      messages: [{ role: "user", content: "hi" }], turn_sizes: null,
      cast: [], unmatched: [], warnings: [] });
  (api.sceneImport as any).mockResolvedValue({ id: "s9", messages: 1, cast: 0 });
  (api.campaignNotices as any).mockResolvedValue({ notices: [], now: "", warn_days: 7 });
  (api.dismissNotices as any).mockResolvedValue({ ok: true, marked: [] });
  (api.continuityDrivers as any).mockResolvedValue(snapshot());
});

test("picking a mode asks the ranked question, which is what fills the picker", async () => {
  // The picker's four slots are 2 greetings + 2 ideas, and both halves come
  // out of this one call. #428 put it behind a button; what that cost was the
  // picker itself -- no ideas, and greetings in whatever order the store
  // listed them.
  render(<NewSceneChooser cid="c" afterSid="s1" ready onClose={() => {}} onCreated={() => {}} />);
  fireEvent.click(screen.getByText("With your PC"));

  await screen.findByText(/blank scene/i);
  await waitFor(() => expect(api.sceneSuggestions)
    .toHaveBeenCalledWith("c", expect.objectContaining(
      { after: "s1", offscreen: false, direction: "", rank: true })));
  expect(api.sceneSuggestions).toHaveBeenCalledTimes(1);
  // and the control is the one that REPLACES what the open call produced
  expect(await screen.findByRole("button", { name: /regenerate/i })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /suggest ideas/i })).toBeNull();
});

test("the mode cards themselves spend nothing", async () => {
  // The gate is `ready && playable`: the question is not asked until there is
  // a mode to ask it in, so a reader who came here to import a transcript --
  // or who closes the chooser at the mode step -- pays for nothing.
  render(<NewSceneChooser cid="c" afterSid="s1" ready onClose={() => {}} onCreated={() => {}} />);
  await screen.findByText("With your PC");
  expect(api.sceneSuggestions).not.toHaveBeenCalled();
});

test("Regenerate makes a second, unranked call carrying the typed direction", async () => {
  (api.sceneSuggestions as any).mockResolvedValue(
    { suggestions: [{ title: "At sea", premise: "", cast: [], location: null }],
      greeting_picks: [], next_date: "2026-01-01" });
  render(<NewSceneChooser cid="c" afterSid="s1" ready onClose={() => {}} onCreated={() => {}} />);
  fireEvent.click(screen.getByText("With your PC"));

  fireEvent.change(await screen.findByLabelText("Direction"),
                   { target: { value: "something at sea" } });
  fireEvent.click(screen.getByRole("button", { name: /regenerate/i }));

  expect(await screen.findByText("At sea")).toBeInTheDocument();
  expect(api.sceneSuggestions).toHaveBeenCalledTimes(2);
  // rank=false: the open call already ordered the greeting cards, and
  // re-ranking would reshuffle them under the reader's cursor.
  expect(api.sceneSuggestions)
    .toHaveBeenLastCalledWith("c", expect.objectContaining(
      { after: "s1", offscreen: false, direction: "something at sea", rank: false }));
});

test("an offscreen chooser asks for offscreen ideas", async () => {
  // The mode is part of the question, not a filter on the answer: an
  // offscreen scene casts nobody the player can be.
  (api.sceneSuggestions as any).mockResolvedValue(
    { suggestions: [], greeting_picks: [], next_date: "" });
  render(<NewSceneChooser cid="c" afterSid="s1" ready onClose={() => {}} onCreated={() => {}} />);
  fireEvent.click(screen.getByText("Offscreen (NPCs only)"));
  await waitFor(() => expect(api.sceneSuggestions)
    .toHaveBeenCalledWith("c", expect.objectContaining(
      { after: "s1", offscreen: true, direction: "", rank: true })));
});

test("a campaign switch drops the ideas the last one earned", async () => {
  (api.sceneSuggestions as any).mockResolvedValue(
    { suggestions: [{ title: "Campaign A's idea", premise: "", cast: [], location: null }],
      greeting_picks: [], next_date: "2026-01-01" });
  const { rerender } = render(
    <NewSceneChooser cid="a" afterSid="s1" ready onClose={() => {}} onCreated={() => {}} />);
  fireEvent.click(screen.getByText("With your PC"));
  await screen.findByText("Campaign A's idea");

  // A ranking that outlived its campaign would be ideas cast from a world the
  // reader has left, so the old answer is dropped and the new one asked for.
  (api.sceneSuggestions as any).mockResolvedValue(
    { suggestions: [{ title: "Campaign B's idea", premise: "", cast: [], location: null }],
      greeting_picks: [], next_date: "" });
  rerender(<NewSceneChooser cid="b" afterSid="s1" ready onClose={() => {}} onCreated={() => {}} />);
  fireEvent.click(screen.getByText("With your PC"));
  await screen.findByText("Campaign B's idea");
  expect(screen.queryByText("Campaign A's idea")).toBeNull();
  expect(api.sceneSuggestions).toHaveBeenLastCalledWith("b", expect.objectContaining(
      { after: "s1", offscreen: false, direction: "", rank: true }));
});

test("the import mode opens the import pane and asks for no suggestions", async () => {
  render(<NewSceneChooser cid="c" afterSid="s1" ready onClose={() => {}} onCreated={() => {}} />);
  fireEvent.click(screen.getByText("Import a transcript"));

  await screen.findByLabelText(/transcript file/i);
  // An imported scene brings its own title, cast and transcript: there is
  // nothing to rank and nothing to open it with.
  expect(api.sceneSuggestions).not.toHaveBeenCalled();
  expect(api.availableGreetings).not.toHaveBeenCalled();
  expect(api.createScene).not.toHaveBeenCalled();
});

test("importing reports the scene it created", async () => {
  const onCreated = vi.fn();
  render(<NewSceneChooser cid="c" afterSid="s1" ready onClose={() => {}} onCreated={onCreated} />);
  fireEvent.click(screen.getByText("Import a transcript"));

  fireEvent.change(await screen.findByLabelText(/transcript file/i),
                   { target: { files: [new File(["**You:** hi\n"], "scene.md")] } });
  fireEvent.click(screen.getByRole("button", { name: /read file/i }));
  await screen.findByDisplayValue("The Long Quay");
  fireEvent.click(screen.getByRole("button", { name: /import scene/i }));

  await waitFor(() => expect(onCreated).toHaveBeenCalledWith("s9"));
  // The import is one request: nothing else creates, dates, places or casts.
  expect(api.createScene).not.toHaveBeenCalled();
  expect(api.addCastBatch).not.toHaveBeenCalled();
});

test("Escape and the backdrop are ignored while an import is in flight", async () => {
  // Unmounting cancels nothing. `SceneImport` correctly skips `onImported`
  // once it is gone, so a dismissal here would leave a real scene that the
  // campaign is never told about -- the same reason the create sequence is
  // gated.
  let release: (v: any) => void = () => {};
  (api.sceneImport as any).mockReturnValue(new Promise((r) => { release = r; }));
  const onClose = vi.fn();
  render(<NewSceneChooser cid="c" afterSid="s1" ready onClose={onClose} onCreated={() => {}} />);
  fireEvent.click(screen.getByText("Import a transcript"));
  fireEvent.change(await screen.findByLabelText(/transcript file/i),
                   { target: { files: [new File(["**You:** hi\n"], "scene.md")] } });
  fireEvent.click(screen.getByRole("button", { name: /read file/i }));
  fireEvent.click(await screen.findByRole("button", { name: /import scene/i }));

  fireEvent.keyDown(window, { key: "Escape" });
  fireEvent.click(screen.getByRole("dialog"));
  expect(onClose).not.toHaveBeenCalled();
  await act(async () => { release({ id: "s9", messages: 1, cast: 0 }); });
});

test("Back from the import pane returns to the mode cards", async () => {
  render(<NewSceneChooser cid="c" afterSid="s1" ready onClose={() => {}} onCreated={() => {}} />);
  fireEvent.click(screen.getByText("Import a transcript"));
  await screen.findByLabelText(/transcript file/i);
  fireEvent.click(screen.getByRole("button", { name: /back/i }));
  expect(screen.getByText("With your PC")).toBeInTheDocument();
});

test("mode is chosen first and nothing a scene needs is fetched before it", async () => {
  render(<NewSceneChooser cid="c" afterSid="s1" ready onClose={() => {}} onCreated={() => {}} />);
  expect(await screen.findByText("With your PC")).toBeInTheDocument();
  expect(api.availableGreetings).not.toHaveBeenCalled();
  expect(api.sceneSuggestions).not.toHaveBeenCalled();
  // The one read that DOES happen before a mode: the pre-notice banner (#106).
  // Deliberate, and not the thing this test guards -- what must not happen
  // before the reader has asked for anything is a *generation*, and this is a
  // plain read of the campaign's own clock and events.
  expect(api.campaignNotices).toHaveBeenCalledWith("c");
});

test("what is imminent is shown while the scene is being planned", async () => {
  // The second surface of #106, and the one the feature is for: "the coronation
  // is in three days" is a thing to know BEFORE deciding what the scene is
  // about, not after.
  (api.campaignNotices as any).mockResolvedValue({
    notices: [{ key: "event:739437:the-coronation", kind: "event",
                name: "The coronation", in_days: 3, friendly: "6 July 2026" }],
    now: "2026-07-03", warn_days: 7 });
  render(<NewSceneChooser cid="c" afterSid="s1" ready onClose={() => {}} onCreated={() => {}} />);
  await screen.findByText("The coronation");
  fireEvent.click(screen.getByLabelText("Dismiss The coronation"));
  await waitFor(() => expect(api.dismissNotices).toHaveBeenCalledWith(
    "c", ["event:739437:the-coronation"], ""));
  await waitFor(() => expect(screen.queryByText("The coronation")).toBeNull());
});

test("a campaign change clears the last campaign's notices before the new read lands", async () => {
  // The effect's request is still in flight when the new campaign first paints,
  // and the banner already holds the new `cid` — so a notice carried over from
  // campaign A would record A's occurrence key in B's ledger, silencing a
  // warning B never showed.
  (api.campaignNotices as any).mockResolvedValue({
    notices: [{ key: "event:739437:the-coronation", kind: "event",
                name: "The coronation", in_days: 3, friendly: "6 July 2026" }],
    now: "2026-07-03", warn_days: 7 });
  const { rerender } = render(
    <NewSceneChooser cid="a" afterSid="s1" ready onClose={() => {}} onCreated={() => {}} />);
  await screen.findByText("The coronation");
  // Campaign b's read never settles, so only the synchronous clear can help.
  (api.campaignNotices as any).mockReturnValue(new Promise(() => {}));
  rerender(
    <NewSceneChooser cid="b" afterSid="s1" ready onClose={() => {}} onCreated={() => {}} />);
  expect(screen.queryByText("The coronation")).toBeNull();
});

test("a failed notice read leaves the chooser usable", async () => {
  // A banner is the least important thing in this modal; it must never be what
  // stops a scene being made.
  (api.campaignNotices as any).mockRejectedValue(new Error("offline"));
  render(<NewSceneChooser cid="c" afterSid="s1" ready onClose={() => {}} onCreated={() => {}} />);
  fireEvent.click(await screen.findByText("With your PC"));
  await screen.findByText("Reckoning");
});

test("picking a card opens the confirm form and creates nothing yet", async () => {
  render(<NewSceneChooser cid="c" afterSid="s1" ready onClose={() => {}} onCreated={() => {}} />);
  fireEvent.click(screen.getByText("With your PC"));
  fireEvent.click(await screen.findByText("Reckoning"));
  await screen.findByRole("button", { name: /create scene/i });
  expect(api.createScene).not.toHaveBeenCalled();
});

test("Back returns to the picker without writing", async () => {
  render(<NewSceneChooser cid="c" afterSid="s1" ready onClose={() => {}} onCreated={() => {}} />);
  fireEvent.click(screen.getByText("With your PC"));
  fireEvent.click(await screen.findByText("Reckoning"));
  fireEvent.click(await screen.findByRole("button", { name: /back/i }));
  await screen.findByText("Reckoning");
  expect(api.createScene).not.toHaveBeenCalled();
});

// `ready` is a required prop, so `tsc` already catches DROPPING it here. What
// it cannot catch is threading the wrong value -- a hardcoded `ready` or
// `ready={true}` typechecks perfectly and leaves the pane claiming it can
// generate in a campaign with no connection. Every test in
// SceneConfirmForm.test.tsx passes the prop directly and so proves nothing
// about this wire; this is the only test that follows the real value across
// the seam.
test("the confirm pane is told whether an LLM is connected", async () => {
  render(<NewSceneChooser cid="c" afterSid="s1" ready={false} onClose={() => {}} onCreated={() => {}} />);
  fireEvent.click(screen.getByText("With your PC"));
  fireEvent.click(await screen.findByText("Reckoning"));
  fireEvent.click(await screen.findByRole("radio", { name: /generate one/i }));
  expect(screen.getByText(/set up an llm connection/i)).toBeInTheDocument();
});

// Issue #319: useSceneSuggestions used to live inside SceneIdeaPicker, which
// unmounts on every Back (its draft is cleared, remounting the picker).
// Remounting re-ran the hook's mount effect at rank=true -- a fresh,
// expensive, re-shufflable LLM call for what the user experiences as "go
// back" -- and threw away whatever direction they had typed, because that
// lived in the unmounted picker's own state too. The fix lifts both up into
// NewSceneChooser, which survives Back untouched.
test("Back preserves the typed direction and the regenerated cards, and issues no further sceneSuggestions call", async () => {
  (api.sceneSuggestions as any).mockResolvedValue(
    { suggestions: [{ title: "Undirected", premise: "", cast: [], location: null }],
      greeting_picks: [], next_date: "2026-01-01" });
  render(<NewSceneChooser cid="c" afterSid="s1" ready onClose={() => {}} onCreated={() => {}} />);
  fireEvent.click(screen.getByText("With your PC"));
  await screen.findByText("Undirected");
  expect(api.sceneSuggestions).toHaveBeenCalledTimes(1);

  fireEvent.change(screen.getByLabelText("Direction"), { target: { value: "something at sea" } });
  (api.sceneSuggestions as any).mockResolvedValue(
    { suggestions: [{ title: "At sea", premise: "", cast: [], location: null }],
      greeting_picks: [], next_date: "2026-01-01" });
  fireEvent.click(screen.getByRole("button", { name: /regenerate/i }));
  await screen.findByText("At sea");
  expect(api.sceneSuggestions).toHaveBeenCalledTimes(2);
  expect(screen.queryByText("Undirected")).toBeNull();

  // pick a card to reach the confirm form, then come back
  fireEvent.click(await screen.findByText("Reckoning"));
  fireEvent.click(await screen.findByRole("button", { name: /back/i }));

  // the typed direction and the regenerated (directed) card both survived --
  // and nothing re-fetched to produce them
  expect(await screen.findByLabelText("Direction")).toHaveValue("something at sea");
  expect(screen.getByText("At sea")).toBeInTheDocument();
  expect(api.sceneSuggestions).toHaveBeenCalledTimes(2);
});

// Follow-up: a `cid` change already discards a stale, UNUSED draft (see
// "changing cid discards the draft" above). This covers the sequence
// already IN FLIGHT when the switch happens: create() closes over the `cid`
// it started with, and without a liveness check its remaining writes (here:
// setSceneDatetime, startFromGreeting, renameScene) would keep firing
// against the campaign the reader just left, and `onCreated` would report a
// scene id into a CampaignView now showing a different one.
test("a cid change mid-create-sequence stops further writes and never reports the scene", async () => {
  let releaseCreate: (v: any) => void = () => {};
  (api.createScene as any).mockReturnValue(new Promise((r) => { releaseCreate = r; }));
  const onCreated = vi.fn();
  const { rerender } = render(
    <NewSceneChooser cid="a" afterSid="s1" ready onClose={() => {}} onCreated={onCreated} />);
  fireEvent.click(screen.getByText("With your PC"));
  fireEvent.click(await screen.findByText("Reckoning"));   // a greeting draft: source "greeting", date set
  fireEvent.click(await screen.findByRole("button", { name: /create scene/i }));
  await waitFor(() => expect(api.createScene).toHaveBeenCalled());

  // the reader switches campaigns while createScene is still in flight
  rerender(<NewSceneChooser cid="b" afterSid="s1" ready onClose={() => {}} onCreated={onCreated} />);

  await act(async () => { releaseCreate({ id: "s9" }); });
  // none of the sequence's later steps fired against campaign "a"
  expect(api.setSceneDatetime).not.toHaveBeenCalled();
  expect(api.startFromGreeting).not.toHaveBeenCalled();
  expect(api.renameScene).not.toHaveBeenCalled();
  expect(onCreated).not.toHaveBeenCalled();
});

// Review (Critical): SceneConfirmForm's own `setWriting(false)` calls are all
// either guarded by `live.current` or sit after an `if (!live.current)
// return;` -- exactly the checks the mid-write test above relies on to stop
// further writes. That means NONE of them run once a switch is detected, so
// `writing` (which lives in NewSceneChooser, not the unmounted form) is never
// reset by that path at all. Without an explicit reset in the `cid`-change
// block, `writing` stays stuck `true` forever, and `dismiss()` refuses
// Escape, the backdrop, and every Cancel button while `writing` is true --
// for the NEW campaign's freshly reset chooser, not just the abandoned one.
test("a cid change mid-write does not leave the new campaign's chooser stuck undismissable", async () => {
  let releaseCreate: (v: any) => void = () => {};
  (api.createScene as any).mockReturnValue(new Promise((r) => { releaseCreate = r; }));
  const onClose = vi.fn();
  const { rerender } = render(
    <NewSceneChooser cid="a" afterSid="s1" ready onClose={onClose} onCreated={() => {}} />);
  fireEvent.click(screen.getByText("With your PC"));
  fireEvent.click(await screen.findByText("Reckoning"));
  fireEvent.click(await screen.findByRole("button", { name: /create scene/i }));
  await waitFor(() => expect(api.createScene).toHaveBeenCalled());

  // the reader switches campaigns while createScene is still in flight
  rerender(<NewSceneChooser cid="b" afterSid="s1" ready onClose={onClose} onCreated={() => {}} />);

  // back at campaign b's mode-select step -- Escape must still dismiss it
  fireEvent.keyDown(window, { key: "Escape" });
  expect(onClose).toHaveBeenCalled();

  await act(async () => { releaseCreate({ id: "s9" }); });
});

// The same #95 trap CampaignView's `mountedRef` already carries a comment
// about, repeated in SceneConfirmForm: main.tsx renders inside StrictMode, so
// in development React runs setup / cleanup / setup on mount -- for LAYOUT
// effects too. A cleanup-only `live` ref is left `false` by that middle step
// for the whole life of the form, so `create()` takes its first
// `if (!live.current) return;` (the one right after createScene resolves) on
// EVERY create: the scene is made on the server but nothing is cast, dated,
// located or reported, and `busy` -- which only clears on paths that check the
// same flag -- pins the dialog in its "…" state forever. In development, which
// is where the app is run, the New Scene dialog therefore always freezes.
test("StrictMode's mount cycle does not wedge the create sequence", async () => {
  const onCreated = vi.fn();
  render(
    <StrictMode>
      <NewSceneChooser cid="c" afterSid="s1" ready onClose={() => {}} onCreated={onCreated} />
    </StrictMode>,
  );
  fireEvent.click(screen.getByText("With your PC"));
  fireEvent.click(await screen.findByText("Reckoning"));
  fireEvent.click(await screen.findByRole("button", { name: /create scene/i }));

  // the sequence runs past its first liveness check and reports the scene
  await waitFor(() => expect(onCreated).toHaveBeenCalledWith("s9", undefined));
  // ...and the button is back out of its busy state rather than stuck on "…"
  expect(screen.queryByRole("button", { name: "…" })).toBeNull();
});

test("offscreen mode asks for pcless greetings and pcless scenes", async () => {
  (api.availableGreetings as any).mockResolvedValue(
    [{ id: "cabal", name: "Cabal", available: true, reasons: [], unlocked: false, pcless: true }]);
  render(<NewSceneChooser cid="c" afterSid="s1" ready onClose={() => {}} onCreated={() => {}} />);
  fireEvent.click(screen.getByText("Offscreen (NPCs only)"));
  fireEvent.click(await screen.findByText("Cabal"));
  fireEvent.click(await screen.findByRole("button", { name: /create scene/i }));
  // With a date: the in-world estimate rides on the suggestions call, which
  // the mode pick now makes, so the confirm form opens already carrying it.
  await waitFor(() => expect(api.createScene)
    .toHaveBeenCalledWith("c", "Cabal", "2026-01-01", true));
});

test("Cancel from the picker writes nothing", async () => {
  const onClose = vi.fn();
  render(<NewSceneChooser cid="c" afterSid="s1" ready onClose={onClose} onCreated={() => {}} />);
  fireEvent.click(screen.getByText("With your PC"));
  fireEvent.click(await screen.findByRole("button", { name: /^cancel$/i }));
  expect(onClose).toHaveBeenCalled();
  expect(api.createScene).not.toHaveBeenCalled();
});

test("Escape and the backdrop are ignored while the create sequence is writing", async () => {
  let release: (v: any) => void = () => {};
  (api.createScene as any).mockReturnValue(new Promise((r) => { release = r; }));
  const onClose = vi.fn();
  render(<NewSceneChooser cid="c" afterSid="s1" ready onClose={onClose} onCreated={() => {}} />);
  fireEvent.click(screen.getByText("With your PC"));
  fireEvent.click(await screen.findByText("Reckoning"));
  fireEvent.click(await screen.findByRole("button", { name: /create scene/i }));
  fireEvent.keyDown(window, { key: "Escape" });
  fireEvent.click(screen.getByRole("dialog"));
  expect(onClose).not.toHaveBeenCalled();     // unmounting would strand the writes in flight
  await act(async () => { release({ id: "s9" }); });
});

test("creating reports the scene and Escape closes while idle", async () => {
  const onCreated = vi.fn();
  const onClose = vi.fn();
  render(<NewSceneChooser cid="c" afterSid="s1" ready onClose={onClose} onCreated={onCreated} />);
  fireEvent.keyDown(window, { key: "Escape" });
  expect(onClose).toHaveBeenCalled();
  fireEvent.click(screen.getByText("With your PC"));
  fireEvent.click(await screen.findByText("Reckoning"));
  fireEvent.click(await screen.findByRole("button", { name: /create scene/i }));
  await waitFor(() => expect(onCreated).toHaveBeenCalledWith("s9", undefined));
});

// CampaignView reuses this component across a `cid` navigation instead of
// remounting it (Finding 1, PR #318 review): without an explicit reset, a
// draft picked in campaign A would still be showing -- and creatable --
// once the prop moves to campaign B.
test("changing cid discards the draft and returns to the mode step", async () => {
  (api.availableGreetings as any).mockImplementation((cid: string) =>
    Promise.resolve(cid === "a"
      ? [{ id: "reck", name: "Reckoning", available: true, reasons: [], unlocked: true }]
      : [{ id: "vow", name: "Vow of silence", available: true, reasons: [], unlocked: true }]));
  const { rerender } = render(
    <NewSceneChooser cid="a" afterSid="s1" ready onClose={() => {}} onCreated={() => {}} />);
  fireEvent.click(screen.getByText("With your PC"));
  fireEvent.click(await screen.findByText("Reckoning"));
  await screen.findByRole("button", { name: /create scene/i });   // confirm form open on campaign a's draft

  rerender(<NewSceneChooser cid="b" afterSid="s1" ready onClose={() => {}} onCreated={() => {}} />);
  // back at the mode step -- campaign a's draft (and its "Create scene" form) is gone
  expect(screen.getByText("With your PC")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /create scene/i })).toBeNull();

  fireEvent.click(screen.getByText("With your PC"));
  fireEvent.click(await screen.findByText("Vow of silence"));
  fireEvent.click(await screen.findByRole("button", { name: /create scene/i }));
  // the create call carries campaign b's own draft, not a's
  await waitFor(() => expect(api.createScene)
    .toHaveBeenCalledWith("b", "Vow of silence", "2026-01-01", false));
  expect(api.createScene).not.toHaveBeenCalledWith("b", "Reckoning", expect.anything(), expect.anything());
});

// A late `onPicked` (the picker's extraction resolves after another draft has
// already replaced it) must remount SceneConfirmForm with fresh state rather
// than mutate the mounted instance -- otherwise the pane mixes controls from
// the stale draft with state seeded from the new one (Important 2). The real
// UI now also disables the picker's own cards mid-extraction (see
// SceneIdeaPicker.test.tsx), which closes off the only way this race reaches
// the app today -- so this test drives the two `onPicked` calls directly
// through a stand-in picker to prove the `key`-based remount holds regardless.
test("a late-arriving draft remounts the confirm form instead of mutating it in place", async () => {
  vi.resetModules();
  let capturedOnPicked: ((d: any, w?: string) => void) | null = null;
  vi.doMock("./SceneIdeaPicker", () => ({
    SceneIdeaPicker: ({ onPicked }: any) => {
      capturedOnPicked = onPicked;
      return (
        <button onClick={() => onPicked({
          source: "greeting", gid: "reck", title: "Reckoning", defaultTitle: "Reckoning",
          date: "2026-01-01", location: "", pcless: false,
        })}>Pick greeting</button>
      );
    },
  }));
  const { NewSceneChooser: FreshChooser } = await import("./NewSceneChooser");
  render(<FreshChooser cid="c" afterSid="s1" ready onClose={() => {}} onCreated={() => {}} />);
  fireEvent.click(screen.getByText("With your PC"));
  fireEvent.click(await screen.findByText("Pick greeting"));
  // now on the confirm form, seeded from the greeting draft
  expect(await screen.findByLabelText("Title")).toHaveValue("Reckoning");
  // simulate the extraction resolving late and calling onPicked a second time,
  // exactly as SceneIdeaPicker's useTyped does after the picker has already
  // handed off once
  act(() => {
    capturedOnPicked!({
      source: "custom", title: "Fresh title", defaultTitle: "Fresh title",
      date: "2026-02-02", location: "", pcless: false, premise: "fresh premise", cast: [],
    });
  });
  // remounted with the SECOND draft's own state, not the first draft's state
  // surviving underneath the second draft's (now custom) controls
  await waitFor(() => expect(screen.getByLabelText("Title")).toHaveValue("Fresh title"));
  expect(screen.getByLabelText("Premise")).toHaveValue("fresh premise");
  vi.doUnmock("./SceneIdeaPicker");
});


test.each([false, true])("Regenerate shows progress over existing ideas and clears it after failure=%s", async (fails) => {
  const previous = { suggestions: [{ title: "At sea", premise: "", cast: [], location: null }],
    greeting_picks: [], next_date: "2026-01-01" };
  let finish: () => void = () => { throw new Error("generation did not start"); };
  vi.mocked(api.sceneSuggestions).mockResolvedValueOnce(previous).mockImplementationOnce(() =>
    new Promise((resolve, reject) => {
      finish = () => fails ? reject(new Error("Scene generation failed")) : resolve({ ...previous,
        suggestions: [{ title: "Back in Saltmarch", premise: "", cast: [], location: null }] });
    }));
  render(<NewSceneChooser cid="c" afterSid="s1" ready onClose={() => {}} onCreated={() => {}} />);
  fireEvent.click(screen.getByText("With your PC"));
  await screen.findByText("At sea");
  fireEvent.click(screen.getByRole("button", { name: /regenerate/i }));
  expect(await screen.findByRole("status")).toHaveTextContent("Generating scene ideas");
  expect(screen.getByRole("button", { name: /regenerating/i })).toBeDisabled();
  expect(screen.getByText("At sea")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: /regenerating/i }));
  expect(api.sceneSuggestions).toHaveBeenCalledTimes(2);
  await act(async () => finish());
  await waitFor(() => expect(screen.getByRole("button", { name: /regenerate/i })).toBeEnabled());
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
  expect(await screen.findByText(fails ? /Scene generation failed/ : "Back in Saltmarch")).toBeVisible();
});

// ---- Story pressure (capstone §16.2-§16.5, plan Decision 19) ----

const CARD = { title: "At sea", premise: "", cast: [], location: null };
const MONTH_BIRTHDAY = {
  ref: "birthday:characters:mara:month:2026-06", kind: "birthday" as const,
  label: "Mara's birthday", native: "", friendly: "June 2026", fixed: null, in_days: null,
  precision: "month" as const,
};

function renderChooser(props: Partial<Parameters<typeof NewSceneChooser>[0]> = {}) {
  return render(<NewSceneChooser cid="c" afterSid="s1" ready onClose={() => {}}
                                 onCreated={() => {}} {...props} />);
}

/** The disclosure, once the drivers read has settled and the picker shows it. */
async function pressureDetails(container: HTMLElement): Promise<HTMLDetailsElement> {
  return waitFor(() => {
    const d = container.querySelector<HTMLDetailsElement>("details.story-pressure");
    expect(d).not.toBeNull();
    return d!;
  });
}

async function openPressure(container: HTMLElement): Promise<HTMLDetailsElement> {
  const details = await pressureDetails(container);
  if (!details.hasAttribute("open")) fireEvent.click(details.querySelector("summary")!);
  await waitFor(() => expect(details).toHaveAttribute("open"));
  return details;
}

function row(label: string) {
  return screen.getByRole("radiogroup", { name: `${label}: story pressure` });
}

function choose(label: string, control: "Normal" | "Focus" | "Avoid" | "Must") {
  fireEvent.click(within(row(label)).getByRole("radio", { name: control }));
}

function chooseTime(name: string) {
  fireEvent.click(within(screen.getByRole("radiogroup", { name: "Time" }))
    .getByRole("radio", { name }));
}

function lastOptions() {
  const calls = (api.sceneSuggestions as any).mock.calls as unknown[][];
  return calls[calls.length - 1][1] as Record<string, unknown>;
}

async function regenerate() {
  const before = (api.sceneSuggestions as any).mock.calls.length as number;
  fireEvent.click(await screen.findByRole("button", { name: /regenerate/i }));
  await waitFor(() => expect(api.sceneSuggestions).toHaveBeenCalledTimes(before + 1));
}

/** Mode picked and the open call settled with one card on screen. */
async function pickerReady(container: HTMLElement, mode = "With your PC") {
  (api.sceneSuggestions as any).mockResolvedValue(
    { suggestions: [CARD], greeting_picks: [], next_date: "2026-01-01" });
  fireEvent.click(screen.getByText(mode));
  await screen.findByText("At sea");
  return openPressure(container);
}

test("the drivers read happens once on open", async () => {
  (api.sceneSuggestions as any).mockResolvedValue(
    { suggestions: [CARD], greeting_picks: [], next_date: "2026-01-01" });
  renderChooser();
  fireEvent.click(screen.getByText("Import a transcript"));
  await screen.findByLabelText(/transcript file/i);
  fireEvent.click(screen.getByRole("button", { name: /back/i }));
  fireEvent.click(screen.getByText("With your PC"));
  fireEvent.click(await screen.findByText("At sea"));
  fireEvent.click(await screen.findByRole("button", { name: /back/i }));
  await screen.findByText("At sea");
  // Once per open and per campaign, without `offscreen`: the mode is picked
  // after opening, and the ref set does not depend on it.
  expect(api.continuityDrivers).toHaveBeenCalledTimes(1);
  expect(api.continuityDrivers).toHaveBeenCalledWith("c");
});

test("story pressure is a collapsed disclosure under Direction", async () => {
  const { container, rerender } = renderChooser();
  fireEvent.click(screen.getByText("With your PC"));
  const details = await pressureDetails(container);
  expect(details).not.toHaveAttribute("open");
  // Inside the Generated group, which is a run of siblings rather than one
  // element: after its role header and the Direction row, before "Your own".
  const FOLLOWS = Node.DOCUMENT_POSITION_FOLLOWING;
  const generated = screen.getByText("Generated", { selector: ".role" });
  const direction = container.querySelector(".idea-direction")!;
  const yourOwn = screen.getByText("Your own", { selector: ".role" });
  expect(generated.compareDocumentPosition(details) & FOLLOWS).toBeTruthy();
  expect(direction.compareDocumentPosition(details) & FOLLOWS).toBeTruthy();
  expect(details.compareDocumentPosition(yourOwn) & FOLLOWS).toBeTruthy();
  expect(within(details).getAllByRole("radio", { name: "Focus", hidden: true })[0])
    .not.toBeVisible();

  fireEvent.click(details.querySelector("summary")!);
  await waitFor(() => expect(details).toHaveAttribute("open"));
  expect(row("Mara's map")).toBeVisible();
  expect(row("Mara's oath")).toBeVisible();
  expect(screen.getByRole("radiogroup", { name: "Time" })).toBeVisible();

  rerender(<NewSceneChooser cid="d" afterSid="s1" ready onClose={() => {}} onCreated={() => {}} />);
  rerender(<NewSceneChooser cid="c" afterSid="s1" ready onClose={() => {}} onCreated={() => {}} />);
  fireEvent.click(screen.getByText("With your PC"));
  expect(await pressureDetails(container)).not.toHaveAttribute("open");
});

test("a failed drivers read hides Story pressure and Suggest still works", async () => {
  (api.continuityDrivers as any).mockRejectedValue(new Error("offline"));
  (api.sceneSuggestions as any).mockResolvedValue(
    { suggestions: [CARD], greeting_picks: [], next_date: "2026-01-01" });
  const { container } = renderChooser();
  fireEvent.click(screen.getByText("With your PC"));
  await screen.findByText("At sea");
  await waitFor(() => expect(api.continuityDrivers).toHaveBeenCalled());
  expect(container.querySelector("details.story-pressure")).toBeNull();
  expect(screen.queryByText(/story pressure/i)).toBeNull();
  await regenerate();
  expect(lastOptions()).toEqual(expect.objectContaining(
    { focus_refs: [], avoid_refs: [], must_refs: [], time_mode: "auto" }));
});

test("driver controls alter the request body", async () => {
  const { container } = renderChooser();
  await pickerReady(container);
  choose("Mara's map", "Focus");
  choose("Mara's oath", "Must");
  await regenerate();
  expect(lastOptions()).toEqual(expect.objectContaining({
    focus_refs: ["thread:mara-s-map"], must_refs: ["commitment:mara-s-oath"],
    avoid_refs: [], time_mode: "auto",
  }));
});

test("changing a control starts no generation", async () => {
  const { container } = renderChooser();
  await pickerReady(container);
  expect(api.sceneSuggestions).toHaveBeenCalledTimes(1);
  choose("Mara's map", "Focus");
  choose("Mara's oath", "Avoid");
  chooseTime("Stay near current date");
  chooseTime("Choose anchor…");
  await screen.findByRole("combobox", { name: "Anchor" });
  expect(api.sceneSuggestions).toHaveBeenCalledTimes(1);
});

test("Must is disabled for temporal drivers and after three", async () => {
  (api.continuityDrivers as any).mockResolvedValue(snapshot({
    drivers: [
      driver("thread:mara-s-map", "thread", "Mara's map", "stale"),
      driver("thread:find-the-ledger", "thread", "Find the ledger", "ok"),
      driver("commitment:mara-s-oath", "commitment", "Mara's oath", "due_soon", 2),
      driver("commitment:salt-owed", "commitment", "Salt owed", "ok"),
      driver("event:the-coronation", "event", "The coronation", "upcoming", 10),
    ],
  }));
  const { container } = renderChooser();
  await pickerReady(container);
  const must = (label: string) => within(row(label)).getByRole("radio", { name: "Must" });
  expect(must("The coronation")).toBeDisabled();
  expect(must("Salt owed")).toBeEnabled();
  choose("Mara's map", "Must");
  choose("Find the ledger", "Must");
  choose("Mara's oath", "Must");
  expect(must("Salt owed")).toBeDisabled();
  // A held must stays changeable, and the reason is visible text.
  expect(must("Mara's map")).toBeEnabled();
  expect(screen.getByText(/up to 3 threads or commitments/)).toBeVisible();
});

test("with no anchors, Choose anchor is disabled and says why", async () => {
  (api.continuityDrivers as any).mockResolvedValue(snapshot({ anchors: [] }));
  const { container } = renderChooser();
  await pickerReady(container);
  expect(within(screen.getByRole("radiogroup", { name: "Time" }))
    .getByRole("radio", { name: "Choose anchor…" })).toBeDisabled();
  expect(screen.getByText(/No upcoming dated events/)).toBeVisible();
});

const UNDATED_HINT = /no current date/;

test("in an undated campaign, Stay near and Let time move are disabled and say why", async () => {
  (api.continuityDrivers as any).mockResolvedValue(
    snapshot({ now: "", friendly: "", fixed: null }));
  const { container } = renderChooser();
  await pickerReady(container);
  const time = screen.getByRole("radiogroup", { name: "Time" });
  expect(within(time).getByRole("radio", { name: "Stay near current date" })).toBeDisabled();
  expect(within(time).getByRole("radio", { name: "Let time move" })).toBeDisabled();
  expect(within(time).getByRole("radio", { name: "Any date" })).toBeEnabled();
  expect(screen.getByText(UNDATED_HINT)).toBeVisible();
});

test("in an undated campaign, a dated anchor shows its day and never reads undated", async () => {
  (api.continuityDrivers as any).mockResolvedValue(
    snapshot({ now: "", friendly: "", fixed: null,
               anchors: [{ ...CORONATION, in_days: null }] }));
  const { container } = renderChooser();
  await pickerReady(container);
  chooseTime("Choose anchor…");
  await screen.findByRole("combobox", { name: "Anchor" });
  expect(screen.getByRole("option", { name: "The coronation — 17 May 2026 (no current date)" }))
    .toBeInTheDocument();
  expect(screen.queryByRole("option", { name: /undated/ })).toBeNull();
});

test("in a dated campaign, Stay near and Let time move are offered", async () => {
  const { container } = renderChooser();
  await pickerReady(container);
  const time = screen.getByRole("radiogroup", { name: "Time" });
  expect(within(time).getByRole("radio", { name: "Stay near current date" })).toBeEnabled();
  expect(within(time).getByRole("radio", { name: "Let time move" })).toBeEnabled();
  expect(screen.queryByText(UNDATED_HINT)).toBeNull();
});

test("a held near falls back to any date when a re-read has no current date", async () => {
  const { container } = renderChooser();
  await pickerReady(container);
  choose("Mara's map", "Focus");
  chooseTime("Stay near current date");
  (api.sceneSuggestions as any).mockRejectedValueOnce(
    new ApiError(409, "stale", "stale_drivers", { refs: ["thread:mara-s-map"] }));
  (api.continuityDrivers as any).mockResolvedValue(
    snapshot({ now: "", friendly: "", fixed: null }));
  await regenerate();
  await waitFor(() => expect(api.continuityDrivers).toHaveBeenCalledTimes(2));
  expect(await screen.findByText(/were reset — press Regenerate\./))
    .toHaveTextContent(/Mara's map, near date/);
  await waitFor(() => expect(within(screen.getByRole("radiogroup", { name: "Time" }))
    .getByRole("radio", { name: "Any date" })).toBeChecked());
  (api.sceneSuggestions as any).mockResolvedValue(
    { suggestions: [CARD], greeting_picks: [], next_date: "" });
  await regenerate();
  expect(lastOptions()).toEqual(expect.objectContaining({ time_mode: "auto" }));
});

test("anchor mode sends the anchor and relation", async () => {
  (api.continuityDrivers as any).mockResolvedValue(
    snapshot({ anchors: [CORONATION, MONTH_BIRTHDAY] }));
  const { container } = renderChooser();
  await pickerReady(container);
  // Nothing else touched: an anchor is always selected.
  chooseTime("Choose anchor…");
  await regenerate();
  expect(lastOptions()).toEqual(expect.objectContaining({
    time_mode: "anchor", time_anchor_ref: "event:the-coronation", time_anchor_relation: "on",
  }));

  fireEvent.change(screen.getByRole("combobox", { name: "Relation" }),
                   { target: { value: "before" } });
  await regenerate();
  expect(lastOptions()).toEqual(expect.objectContaining({
    time_anchor_ref: "event:the-coronation", time_anchor_relation: "before",
  }));

  // A month-only birthday has no day to be before: it takes `on`, and the
  // Relation select offers nothing else.
  fireEvent.change(screen.getByRole("combobox", { name: "Anchor" }),
                   { target: { value: MONTH_BIRTHDAY.ref } });
  const relation = screen.getByRole("combobox", { name: "Relation" });
  expect(within(relation).getAllByRole("option").map((o) => o.textContent)).toEqual(["on"]);
  expect(screen.getByRole("option", { name: "Mara's birthday — June 2026 (day unknown)" }))
    .toBeInTheDocument();
  await regenerate();
  expect(lastOptions()).toEqual(expect.objectContaining({
    time_mode: "anchor", time_anchor_ref: MONTH_BIRTHDAY.ref, time_anchor_relation: "on",
  }));
});

test("the anchored row cannot be avoided", async () => {
  const { container } = renderChooser();
  await pickerReady(container);
  choose("The coronation", "Avoid");
  chooseTime("Choose anchor…");
  // The anchor beats avoid (plan Decision 26): the row is back to Normal and
  // cannot be set to Avoid while it is the anchor.
  expect(within(row("The coronation")).getByRole("radio", { name: "Normal" })).toBeChecked();
  expect(within(row("The coronation")).getByRole("radio", { name: "Avoid" })).toBeDisabled();
  await regenerate();
  expect(lastOptions()).toEqual(expect.objectContaining({
    avoid_refs: [], time_anchor_ref: "event:the-coronation",
  }));
});

test("the summary counts drivers and names the time separately", async () => {
  const { container } = renderChooser();
  const details = await pickerReady(container);
  const summary = () => details.querySelector("summary")!.textContent;
  expect(summary()).toBe("Story pressure");
  choose("Mara's map", "Focus");
  chooseTime("Choose anchor…");
  expect(summary()).toBe("Story pressure (1 not Normal · anchored)");
  choose("Mara's map", "Normal");
  expect(summary()).toBe("Story pressure (anchored)");
});

test("driver chips read naturally", async () => {
  (api.continuityDrivers as any).mockResolvedValue(snapshot({
    drivers: [
      driver("commitment:mara-s-oath", "commitment", "Mara's oath", "overdue", -3),
      driver("event:the-coronation", "event", "The coronation", "today", 0),
      driver("thread:find-the-ledger", "thread", "Find the ledger", "due_soon", 1),
      driver("thread:mara-s-map", "thread", "Mara's map", "ok"),
    ],
  }));
  const { container } = renderChooser();
  const details = await pickerReady(container);
  expect(within(details).getByText("overdue · 3 days ago")).toHaveClass("chip");
  expect(within(details).getByText("today")).toHaveClass("chip");
  expect(within(details).getByText("due soon · in 1 day")).toHaveClass("chip");
  expect(details.textContent).not.toMatch(/in -\d|in 0 days|in 1 days/);
  // An `ok` driver carries no chip at all.
  expect(row("Mara's map").parentElement!.querySelector(".chip")).toBeNull();
});

test("basic matching keeps Suggest enabled and says nothing about embeddings", async () => {
  const { container } = renderChooser();
  await pickerReady(container);
  expect(screen.queryByText(/embedding/i)).toBeNull();
  expect(screen.getByRole("button", { name: /regenerate/i })).toBeEnabled();
});

test("story pressure survives Back and resets on a campaign switch", async () => {
  const { container, rerender } = renderChooser();
  await pickerReady(container);
  choose("Mara's map", "Focus");
  fireEvent.click(screen.getByText("At sea"));
  fireEvent.click(await screen.findByRole("button", { name: /back/i }));
  await screen.findByText("At sea");
  expect(within(row("Mara's map")).getByRole("radio", { name: "Focus" })).toBeChecked();

  rerender(<NewSceneChooser cid="b" afterSid="s1" ready onClose={() => {}} onCreated={() => {}} />);
  fireEvent.click(screen.getByText("With your PC"));
  const details = await openPressure(container);
  expect(within(row("Mara's map")).getByRole("radio", { name: "Normal" })).toBeChecked();
  expect(details.querySelector("summary")!.textContent).toBe("Story pressure");
  expect(api.continuityDrivers).toHaveBeenLastCalledWith("b");
});

test("a seed focuses its drivers and the open call carries them", async () => {
  const { container } = renderChooser({ seed: { drivers: { "thread:mara-s-map": "focus" } } });
  fireEvent.click(screen.getByText("With your PC"));
  await waitFor(() => expect(api.sceneSuggestions).toHaveBeenCalledTimes(1));
  expect(lastOptions()).toEqual(expect.objectContaining(
    { rank: true, focus_refs: ["thread:mara-s-map"] }));
  // A seeded disclosure opens expanded, so the reader sees what was set.
  expect(await pressureDetails(container)).toHaveAttribute("open");
  expect(within(row("Mara's map")).getByRole("radio", { name: "Focus" })).toBeChecked();
});

test("an anchor seed sends anchor mode on the open call", async () => {
  renderChooser({ seed: { anchor: { ref: "event:the-coronation", relation: "on" } } });
  fireEvent.click(screen.getByText("With your PC"));
  await waitFor(() => expect(api.sceneSuggestions).toHaveBeenCalledTimes(1));
  expect(lastOptions()).toEqual(expect.objectContaining({
    rank: true, time_mode: "anchor", time_anchor_ref: "event:the-coronation",
    time_anchor_relation: "on",
  }));
});

test("a seed waits for the drivers read before the open call", async () => {
  let release: (v: DriversSnapshot) => void = () => {};
  (api.continuityDrivers as any).mockReturnValue(new Promise((r) => { release = r; }));
  renderChooser({ seed: { drivers: { "thread:mara-s-map": "focus" } } });
  fireEvent.click(screen.getByText("With your PC"));
  await screen.findByText(/blank scene/i);
  expect(api.sceneSuggestions).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: /suggest ideas/i })).toBeDisabled();
  expect(screen.getByText("Reading story pressure…")).toBeInTheDocument();

  await act(async () => { release(snapshot()); });
  await waitFor(() => expect(api.sceneSuggestions).toHaveBeenCalledTimes(1));
  expect(lastOptions()).toEqual(expect.objectContaining({ focus_refs: ["thread:mara-s-map"] }));
  expect(screen.queryByText("Reading story pressure…")).toBeNull();
  expect(api.sceneSuggestions).toHaveBeenCalledTimes(1);
});

test("a seed naming a missing driver is dropped with a note", async () => {
  renderChooser({ seed: { drivers: { "thread:ghost": "focus", "thread:mara-s-map": "focus" } } });
  fireEvent.click(screen.getByText("With your PC"));
  await waitFor(() => expect(api.sceneSuggestions).toHaveBeenCalledTimes(1));
  expect(lastOptions()).toEqual(expect.objectContaining({ focus_refs: ["thread:mara-s-map"] }));
  expect(await screen.findByText("Not current any more, so not applied: thread:ghost"))
    .toBeInTheDocument();
});

test("a seed after a failed read is dropped with a note and the open call goes unsteered", async () => {
  (api.continuityDrivers as any).mockRejectedValue(new Error("offline"));
  const { container } = renderChooser({ seed: { drivers: { "thread:mara-s-map": "focus" } } });
  fireEvent.click(screen.getByText("With your PC"));
  await waitFor(() => expect(api.sceneSuggestions).toHaveBeenCalledTimes(1));
  expect(lastOptions()).toEqual(expect.objectContaining(
    { focus_refs: [], time_mode: "auto", rank: true }));
  expect(await screen.findByText(
    "Story pressure could not be read; the Story Graph selection was not applied."))
    .toBeInTheDocument();
  expect(container.querySelector("details.story-pressure")).toBeNull();
});

test("a stale_drivers refusal re-reads drivers and says what dropped", async () => {
  const { container } = renderChooser();
  await pickerReady(container);
  choose("Mara's map", "Focus");
  (api.sceneSuggestions as any).mockRejectedValue(
    new ApiError(409, "stale", "stale_drivers", { refs: ["thread:mara-s-map"] }));
  (api.continuityDrivers as any).mockResolvedValue(snapshot({
    drivers: [driver("commitment:mara-s-oath", "commitment", "Mara's oath", "due_soon", 2)],
  }));
  await regenerate();
  await waitFor(() => expect(api.continuityDrivers).toHaveBeenCalledTimes(2));
  expect(await screen.findByText(
    /Some selections are no longer current and were reset — press Regenerate\./))
    .toHaveTextContent("Mara's map");
  expect(screen.queryByText(/No ideas came back/)).toBeNull();
  // Not an error either: nothing failed, the campaign moved.
  expect(container.querySelector(".banner")).toBeNull();
  await waitFor(() => expect(screen.queryByRole("radiogroup",
    { name: "Mara's map: story pressure" })).toBeNull());
});

test("a refused ref spelled differently is still cleared", async () => {
  const { container } = renderChooser();
  await pickerReady(container);
  choose("Mara's map", "Focus");
  // The server names its canonical spelling, which is not what the chooser
  // holds; the re-read's prune is what clears the held one.
  (api.sceneSuggestions as any).mockRejectedValueOnce(
    new ApiError(409, "stale", "stale_drivers", { refs: ["thread:find-the-ledger"] }));
  (api.continuityDrivers as any).mockResolvedValue(snapshot({
    drivers: [driver("commitment:mara-s-oath", "commitment", "Mara's oath", "due_soon", 2)],
  }));
  await regenerate();
  await waitFor(() => expect(api.continuityDrivers).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(screen.queryByRole("radiogroup",
    { name: "Mara's map: story pressure" })).toBeNull());
  (api.sceneSuggestions as any).mockResolvedValue(
    { suggestions: [CARD], greeting_picks: [], next_date: "" });
  await regenerate();
  expect(JSON.stringify(lastOptions())).not.toContain("thread:mara-s-map");
});

test("a failed re-read after a stale refusal resets what it can no longer show", async () => {
  const { container } = renderChooser();
  await pickerReady(container);
  choose("Mara's map", "Focus");
  choose("Mara's oath", "Avoid");
  chooseTime("Stay near current date");
  // A refusal naming a spelling the chooser does not hold clears nothing, and
  // the re-read that would have pruned the held refs fails.
  (api.sceneSuggestions as any).mockRejectedValueOnce(
    new ApiError(409, "stale", "stale_drivers", { refs: ["thread:find-the-ledger"] }));
  (api.continuityDrivers as any).mockRejectedValue(new Error("offline"));
  await regenerate();
  await waitFor(() => expect(api.continuityDrivers).toHaveBeenCalledTimes(2));
  await waitFor(() => expect(container.querySelector("details.story-pressure")).toBeNull());
  // The reader is told what was reset...
  expect(await screen.findByText(/Story pressure could not be read/))
    .toHaveTextContent(/Mara's map.*Mara's oath/);
  // ...and hidden controls send nothing.
  (api.sceneSuggestions as any).mockResolvedValue(
    { suggestions: [CARD], greeting_picks: [], next_date: "" });
  await regenerate();
  expect(lastOptions()).toEqual(expect.objectContaining({
    focus_refs: [], avoid_refs: [], must_refs: [], time_mode: "auto",
    time_anchor_ref: "", time_anchor_relation: "",
  }));
  // The note told the reader what to do before the next request; that
  // request has gone out, so it is not left under the cards it brought.
  await screen.findByText("At sea");
  expect(screen.queryByText(/Story pressure could not be read/)).toBeNull();
});

test("a stale note clears once the next request goes out", async () => {
  const { container } = renderChooser();
  await pickerReady(container);
  choose("Mara's map", "Focus");
  (api.sceneSuggestions as any).mockRejectedValueOnce(
    new ApiError(409, "stale", "stale_drivers", { refs: ["thread:mara-s-map"] }));
  (api.continuityDrivers as any).mockResolvedValue(snapshot({
    drivers: [driver("commitment:mara-s-oath", "commitment", "Mara's oath", "due_soon", 2)],
  }));
  await regenerate();
  expect(await screen.findByText(/press Regenerate\./)).toHaveTextContent("Mara's map");
  // Regenerate works this time: the cards land, and "press Regenerate" is
  // not left under them.
  (api.sceneSuggestions as any).mockResolvedValue(
    { suggestions: [{ ...CARD, title: "Ashore" }], greeting_picks: [], next_date: "" });
  await regenerate();
  expect(await screen.findByText("Ashore")).toBeInTheDocument();
  expect(screen.queryByText(/press Regenerate/)).toBeNull();
  expect(screen.queryByText(/Reset:/)).toBeNull();
});

test("a second stale cycle names only what it reset", async () => {
  const { container } = renderChooser();
  await pickerReady(container);
  choose("Mara's map", "Focus");
  choose("Mara's oath", "Avoid");
  // The first refusal names Mara's map, and the re-read drops it.
  (api.sceneSuggestions as any).mockRejectedValueOnce(
    new ApiError(409, "stale", "stale_drivers", { refs: ["thread:mara-s-map"] }));
  (api.continuityDrivers as any).mockResolvedValue(snapshot({
    drivers: [driver("commitment:mara-s-oath", "commitment", "Mara's oath", "due_soon", 2),
              driver("event:the-coronation", "event", "The coronation", "upcoming", 10)],
  }));
  await regenerate();
  expect(await screen.findByText(/press Regenerate\./)).toHaveTextContent("Mara's map");
  // The second names a spelling the chooser does not hold; the re-read's
  // prune is what drops Mara's oath, and the note names that alone.
  (api.sceneSuggestions as any).mockRejectedValueOnce(
    new ApiError(409, "stale", "stale_drivers", { refs: ["commitment:the-oath"] }));
  (api.continuityDrivers as any).mockResolvedValue(snapshot({
    drivers: [driver("event:the-coronation", "event", "The coronation", "upcoming", 10)],
  }));
  await regenerate();
  await waitFor(() => expect(api.continuityDrivers).toHaveBeenCalledTimes(3));
  const note = await screen.findByText(/Reset:.*Mara's oath/);
  expect(note).not.toHaveTextContent("Mara's map");
});

test("a seed's dropped-ref note outlives the open call and clears on a control change", async () => {
  const { container } = renderChooser(
    { seed: { drivers: { "thread:ghost": "focus", "thread:mara-s-map": "focus" } } });
  (api.sceneSuggestions as any).mockResolvedValue(
    { suggestions: [CARD], greeting_picks: [], next_date: "" });
  fireEvent.click(screen.getByText("With your PC"));
  await screen.findByText("At sea");
  // The open call went out with the seed; the note is about the seed, not
  // about that request, so the request does not clear it.
  expect(screen.getByText("Not current any more, so not applied: thread:ghost"))
    .toBeInTheDocument();
  await openPressure(container);
  choose("Mara's oath", "Avoid");
  await waitFor(() => expect(screen.queryByText(/Not current any more/)).toBeNull());
});
