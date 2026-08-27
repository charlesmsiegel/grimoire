import { StrictMode } from "react";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { NewSceneChooser } from "./NewSceneChooser";

vi.mock("../api/client", () => ({
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
  },
}));
vi.mock("./CalendarDatePicker", () => ({
  CalendarDatePicker: ({ value, onChange, ariaLabel }: any) =>
    <input aria-label={ariaLabel} value={value} onChange={(e) => onChange(e.target.value)} />,
}));
import { api } from "../api/client";

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
});

test("picking a mode spends no generation on ideas", async () => {
  // The path taken to start every scene. It used to rank ideas here — an LLM
  // call made before the reader asked for anything and without telling them —
  // and the answer to that is a button, not a faster call (#428).
  render(<NewSceneChooser cid="c" afterSid="s1" ready onClose={() => {}} onCreated={() => {}} />);
  fireEvent.click(screen.getByText("With your PC"));

  // The picker arrives whole: greetings, the saved ledger, a blank scene, and
  // the button that would spend the generation if it were pressed.
  await screen.findByText(/blank scene/i);
  expect(await screen.findByRole("button", { name: /suggest ideas/i })).toBeEnabled();
  expect(api.sceneSuggestions).not.toHaveBeenCalled();
  // ...and it does not sit on "Generating…" waiting for a call nobody started.
  expect(screen.queryByText(/generating/i)).not.toBeInTheDocument();
});

test("pressing Suggest ideas is what makes the call, once, with the typed direction", async () => {
  (api.sceneSuggestions as any).mockResolvedValue(
    { suggestions: [{ title: "At sea", premise: "", cast: [], location: null }],
      greeting_picks: [], next_date: "2026-01-01" });
  render(<NewSceneChooser cid="c" afterSid="s1" ready onClose={() => {}} onCreated={() => {}} />);
  fireEvent.click(screen.getByText("With your PC"));

  fireEvent.change(await screen.findByLabelText("Direction"),
                   { target: { value: "something at sea" } });
  fireEvent.click(screen.getByRole("button", { name: /suggest ideas/i }));

  expect(await screen.findByText("At sea")).toBeInTheDocument();
  // Ranked (the last argument), because this press orders the greeting cards
  // as well as generating the ideas.
  expect(api.sceneSuggestions).toHaveBeenCalledTimes(1);
  expect(api.sceneSuggestions).toHaveBeenCalledWith("c", "s1", false, "something at sea", true);
  // and the control is now the one that replaces what it produced
  expect(screen.getByRole("button", { name: /regenerate/i })).toBeInTheDocument();
});

test("an offscreen chooser asks for offscreen ideas", async () => {
  // The mode is part of the question, not a filter on the answer: an
  // offscreen scene casts nobody the player can be.
  (api.sceneSuggestions as any).mockResolvedValue(
    { suggestions: [], greeting_picks: [], next_date: "" });
  render(<NewSceneChooser cid="c" afterSid="s1" ready onClose={() => {}} onCreated={() => {}} />);
  fireEvent.click(screen.getByText("Offscreen (NPCs only)"));
  fireEvent.click(await screen.findByRole("button", { name: /suggest ideas/i }));
  expect(api.sceneSuggestions).toHaveBeenCalledWith("c", "s1", true, "", true);
});

test("a campaign switch drops the ideas the last one earned", async () => {
  (api.sceneSuggestions as any).mockResolvedValue(
    { suggestions: [{ title: "Campaign A's idea", premise: "", cast: [], location: null }],
      greeting_picks: [], next_date: "2026-01-01" });
  const { rerender } = render(
    <NewSceneChooser cid="a" afterSid="s1" ready onClose={() => {}} onCreated={() => {}} />);
  fireEvent.click(screen.getByText("With your PC"));
  fireEvent.click(await screen.findByRole("button", { name: /suggest ideas/i }));
  await screen.findByText("Campaign A's idea");

  // Nothing re-fetches on its own any more, so a ranking that outlived its
  // campaign would sit in the next one's picker until somebody pressed --
  // ideas cast from a world the reader has left.
  rerender(<NewSceneChooser cid="b" afterSid="s1" ready onClose={() => {}} onCreated={() => {}} />);
  fireEvent.click(screen.getByText("With your PC"));
  expect(await screen.findByRole("button", { name: /suggest ideas/i })).toBeInTheDocument();
  expect(screen.queryByText("Campaign A's idea")).toBeNull();
  expect(api.sceneSuggestions).toHaveBeenCalledTimes(1);
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
  fireEvent.click(await screen.findByRole("button", { name: /suggest ideas/i }));
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
  // No date: the in-world date estimate rides on the suggestions call, and
  // nobody pressed for one. The confirm form's "Last scene's date" button
  // fills it from the campaign clock, which costs nothing (#428).
  await waitFor(() => expect(api.createScene).toHaveBeenCalledWith("c", "Cabal", undefined, true));
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
  await waitFor(() => expect(api.createScene).toHaveBeenCalledWith("b", "Vow of silence", undefined, false));
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
