import { useState } from "react";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { SceneIdeaPicker } from "./SceneIdeaPicker";
import type { SceneSuggestion } from "../api/client";

// `useSceneSuggestions` now lives in NewSceneChooser (issue #319) -- this
// pane only renders what it's handed, so its own tests supply the generated
// half (suggestions/picks/nextDate/busy/error/refresh) and `direction`
// directly as props rather than mocking `api.sceneSuggestions`.
vi.mock("../api/client", () => ({
  api: { availableGreetings: vi.fn(), sceneIntent: vi.fn() },
}));
import { api } from "../api/client";

const GREETINGS = [
  { id: "reck", name: "Reckoning", available: true, reasons: [], unlocked: true },
  { id: "open", name: "Open", available: true, reasons: [], unlocked: false },
];
const SUGGESTION: SceneSuggestion = {
  title: "The creditor", premise: "A debt-collector arrives.", date: "2026-03-04",
  cast: [{ kind: "characters", id: "mara", name: "Mara" }],
  location: { id: "saltmarch", name: "Saltmarch" },
};

beforeEach(() => {
  vi.clearAllMocks();
  (api.availableGreetings as any).mockResolvedValue(GREETINGS);
  (api.sceneIntent as any).mockResolvedValue({
    title: "The morning after", date: "2026-03-04",
    location: { id: "saltmarch", name: "Saltmarch" }, cast: [] });
});

type StateOverrides = Partial<{
  ready: boolean;
  direction: string;
  suggestions: SceneSuggestion[] | null;
  picks: string[] | null;
  nextDate: string;
  busy: boolean;
  error: string | null;
  refresh: (direction: string) => void;
  onPicked: (draft: any, warning?: string) => void;
  onCancel: () => void;
}>;

// A tiny controlled-input harness: SceneIdeaPicker no longer owns `direction`
// itself (it moved up to NewSceneChooser), so a real typed value round-trips
// through this wrapper's own state exactly the way it would through the
// orchestrator's.
function Wrapper(props: StateOverrides) {
  const [direction, setDirection] = useState(props.direction ?? "");
  return (
    <SceneIdeaPicker cid="c" afterSid="s1" ready={props.ready ?? true} pcless={false}
                     direction={direction} onDirectionChange={setDirection}
                     suggestions={"suggestions" in props ? props.suggestions! : [SUGGESTION]}
                     picks={"picks" in props ? props.picks! : []}
                     nextDate={props.nextDate ?? "2026-01-01"}
                     busy={props.busy ?? false}
                     error={props.error ?? null}
                     refresh={props.refresh ?? vi.fn()}
                     onPicked={props.onPicked ?? vi.fn()}
                     onCancel={props.onCancel ?? vi.fn()} />
  );
}

function renderPicker(overrides: StateOverrides = {}) {
  const onPicked = overrides.onPicked ?? vi.fn();
  const refresh = overrides.refresh ?? vi.fn();
  const utils = render(<Wrapper {...overrides} onPicked={onPicked} refresh={refresh} />);
  return { onPicked, refresh, ...utils };
}

test("picking a greeting emits a greeting draft", async () => {
  const { onPicked } = renderPicker();
  await screen.findByText("Reckoning");
  fireEvent.click(screen.getByText("Reckoning"));
  expect(onPicked).toHaveBeenCalledWith(expect.objectContaining({
    source: "greeting", gid: "reck", title: "Reckoning", date: "2026-01-01" }));
});

test("picking a generated card emits its resolved metadata", async () => {
  const { onPicked } = renderPicker();
  fireEvent.click(await screen.findByText("The creditor"));
  expect(onPicked).toHaveBeenCalledWith(expect.objectContaining({
    source: "generated", location: "saltmarch", date: "2026-03-04",
    premise: "A debt-collector arrives." }));
});

test("Regenerate calls refresh with the typed direction and does not refetch greetings", async () => {
  const { refresh } = renderPicker();
  await screen.findByText("Reckoning");
  expect(api.availableGreetings).toHaveBeenCalledTimes(1);
  fireEvent.change(screen.getByLabelText("Direction"), { target: { value: "something at sea" } });
  expect(screen.getByLabelText("Direction")).toHaveValue("something at sea");
  fireEvent.click(screen.getByRole("button", { name: /regenerate/i }));
  expect(refresh).toHaveBeenCalledWith("something at sea");
  expect(api.availableGreetings).toHaveBeenCalledTimes(1);
});

test("typed text runs the extraction and emits a custom draft", async () => {
  const { onPicked } = renderPicker();
  await screen.findByText("Reckoning");
  fireEvent.change(screen.getByLabelText("Your own scene"),
                   { target: { value: "back at the marsh house" } });
  fireEvent.click(screen.getByRole("button", { name: /use this/i }));
  await waitFor(() => expect(onPicked).toHaveBeenCalledWith(expect.objectContaining({
    source: "custom", title: "The morning after", location: "saltmarch",
    premise: "back at the marsh house" })));
});

test("empty text creates a blank draft with no LLM call", async () => {
  const { onPicked } = renderPicker();
  await screen.findByText("Reckoning");
  fireEvent.click(screen.getByRole("button", { name: /create blank scene/i }));
  expect(api.sceneIntent).not.toHaveBeenCalled();
  expect(onPicked).toHaveBeenCalledWith(expect.objectContaining({
    source: "custom", title: "New scene", date: "2026-01-01", premise: "" }));
});

test("a failed extraction opens with the typed text AND hands the warning on", async () => {
  (api.sceneIntent as any).mockRejectedValue({ detail: "no key" });
  const { onPicked } = renderPicker();
  await screen.findByText("Reckoning");
  fireEvent.change(screen.getByLabelText("Your own scene"), { target: { value: "a storm" } });
  fireEvent.click(screen.getByRole("button", { name: /use this/i }));
  // the warning is the SECOND argument: this pane unmounts immediately, so a
  // banner of its own would never be seen
  await waitFor(() => expect(onPicked).toHaveBeenCalledWith(
    expect.objectContaining({ source: "custom", title: "New scene", premise: "a storm" }),
    expect.stringContaining("no key")));
});

// `nextDate` is a prop now (it used to land via an internal async fetch);
// this still proves a value arriving AFTER mount (via a re-render, exactly
// as NewSceneChooser would deliver it once useSceneSuggestions resolves)
// reaches a click emitted later, through the emit-time `latestDate` ref.
test("a delayed date estimate reaches an emitted greeting draft", async () => {
  const onPicked = vi.fn();
  const { rerender } = render(
    <SceneIdeaPicker cid="c" afterSid="s1" ready pcless={false}
                     direction="" onDirectionChange={() => {}}
                     suggestions={[]} picks={[]} nextDate="" busy={false} error={null}
                     refresh={() => {}} onPicked={onPicked} onCancel={() => {}} />);
  await screen.findByText("Reckoning");
  rerender(
    <SceneIdeaPicker cid="c" afterSid="s1" ready pcless={false}
                     direction="" onDirectionChange={() => {}}
                     suggestions={[]} picks={[]} nextDate="2026-02-02" busy={false} error={null}
                     refresh={() => {}} onPicked={onPicked} onCancel={() => {}} />);
  fireEvent.click(screen.getByText("Reckoning"));
  expect(onPicked).toHaveBeenCalledWith(expect.objectContaining({ date: "2026-02-02" }));
});

test("a typed pick that outlasts a slow date estimate still carries the fresh date", async () => {
  let releaseIntent: (v: any) => void = () => {};
  (api.sceneIntent as any).mockReturnValue(new Promise((r) => { releaseIntent = r; }));
  const onPicked = vi.fn();
  const { rerender } = render(
    <SceneIdeaPicker cid="c" afterSid="s1" ready pcless={false}
                     direction="" onDirectionChange={() => {}}
                     suggestions={[]} picks={[]} nextDate="" busy={false} error={null}
                     refresh={() => {}} onPicked={onPicked} onCancel={() => {}} />);
  await screen.findByText("Reckoning");
  fireEvent.change(screen.getByLabelText("Your own scene"), { target: { value: "a storm" } });
  fireEvent.click(screen.getByRole("button", { name: /use this/i }));
  // the date estimate lands WHILE the extraction is still in flight
  rerender(
    <SceneIdeaPicker cid="c" afterSid="s1" ready pcless={false}
                     direction="" onDirectionChange={() => {}}
                     suggestions={[]} picks={[]} nextDate="2026-05-05" busy={false} error={null}
                     refresh={() => {}} onPicked={onPicked} onCancel={() => {}} />);
  await act(async () => {
    releaseIntent({ title: "The morning after", date: "", location: null, cast: [] });
  });
  await waitFor(() => expect(onPicked).toHaveBeenCalledWith(
    expect.objectContaining({ date: "2026-05-05" })));
});

test("an extraction that returns nothing still hands on a hint that metadata could not be inferred", async () => {
  (api.sceneIntent as any).mockResolvedValue({ title: "", date: "", location: null, cast: [] });
  const { onPicked } = renderPicker();
  await screen.findByText("Reckoning");
  fireEvent.change(screen.getByLabelText("Your own scene"), { target: { value: "a storm" } });
  fireEvent.click(screen.getByRole("button", { name: /use this/i }));
  await waitFor(() => expect(onPicked).toHaveBeenCalledWith(
    expect.objectContaining({ source: "custom", title: "New scene", premise: "a storm" }),
    expect.stringMatching(/could not be inferred|nothing could be inferred/i)));
});

test("greeting and generated cards are disabled while an extraction is in flight", async () => {
  let releaseIntent: (v: any) => void = () => {};
  (api.sceneIntent as any).mockReturnValue(new Promise((r) => { releaseIntent = r; }));
  renderPicker();
  await screen.findByText("Reckoning");
  fireEvent.change(screen.getByLabelText("Your own scene"), { target: { value: "a storm" } });
  fireEvent.click(screen.getByRole("button", { name: /use this/i }));
  // a card click here would otherwise emit a SECOND onPicked once the
  // extraction resolves, racing the first draft into the confirm form (#Important2)
  expect(screen.getByText("Reckoning").closest("button")).toBeDisabled();
  expect(screen.getByText("The creditor").closest("button")).toBeDisabled();
  await act(async () => { releaseIntent({ title: "x", date: "", location: null, cast: [] }); });
});

test("a stale greetings-fetch error banner is cleared when Regenerate starts", async () => {
  (api.availableGreetings as any).mockRejectedValue({ detail: "greetings unreachable" });
  renderPicker();
  await screen.findByText(/greetings unreachable/i);
  fireEvent.click(screen.getByRole("button", { name: /regenerate/i }));
  await waitFor(() => expect(screen.queryByText(/greetings unreachable/i)).toBeNull());
});

test("without a connection the direction row is disabled but typing still works", async () => {
  const { onPicked } = renderPicker({ ready: false });
  await screen.findByText("Reckoning");
  expect(screen.getByRole("button", { name: /regenerate/i })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Your own scene"), { target: { value: "a storm" } });
  fireEvent.click(screen.getByRole("button", { name: /use this/i }));
  await waitFor(() => expect(onPicked).toHaveBeenCalledWith(expect.objectContaining({
    premise: "a storm", title: "New scene" })));
  expect(api.sceneIntent).not.toHaveBeenCalled();
});

test("Cancel calls onCancel and emits nothing", async () => {
  const onPicked = vi.fn();
  const onCancel = vi.fn();
  renderPicker({ onPicked, onCancel });
  await screen.findByText("Reckoning");
  fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
  expect(onCancel).toHaveBeenCalledTimes(1);
  expect(onPicked).not.toHaveBeenCalled();
});

test("Regenerate is disabled while busy, even if ready", async () => {
  renderPicker({ busy: true });
  await screen.findByText("Reckoning");
  expect(screen.getByRole("button", { name: /regenerate/i })).toBeDisabled();
});
