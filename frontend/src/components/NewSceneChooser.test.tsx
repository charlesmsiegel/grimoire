import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { NewSceneChooser } from "./NewSceneChooser";

vi.mock("../api/client", () => ({
  api: {
    availableGreetings: vi.fn(), sceneSuggestions: vi.fn(), sceneIntent: vi.fn(),
    createScene: vi.fn(), startFromGreeting: vi.fn(), addCastBatch: vi.fn(),
    setSceneLocation: vi.fn(), setSceneDatetime: vi.fn(), renameScene: vi.fn(),
    deleteScene: vi.fn(), listEntities: vi.fn(), listCharacters: vi.fn(),
    listCampaignPCs: vi.fn(), listAppearances: vi.fn(),
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
});

test("mode is chosen first and nothing is fetched before it", () => {
  render(<NewSceneChooser cid="c" afterSid="s1" ready onClose={() => {}} onCreated={() => {}} />);
  expect(screen.getByText("With your PC")).toBeInTheDocument();
  expect(api.availableGreetings).not.toHaveBeenCalled();
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

test("offscreen mode asks for pcless greetings and pcless scenes", async () => {
  (api.availableGreetings as any).mockResolvedValue(
    [{ id: "cabal", name: "Cabal", available: true, reasons: [], unlocked: false, pcless: true }]);
  render(<NewSceneChooser cid="c" afterSid="s1" ready onClose={() => {}} onCreated={() => {}} />);
  fireEvent.click(screen.getByText("Offscreen (NPCs only)"));
  fireEvent.click(await screen.findByText("Cabal"));
  fireEvent.click(await screen.findByRole("button", { name: /create scene/i }));
  await waitFor(() => expect(api.createScene).toHaveBeenCalledWith("c", "Cabal", expect.anything(), true));
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
