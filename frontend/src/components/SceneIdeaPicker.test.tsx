import { useState } from "react";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { SceneIdeaPicker } from "./SceneIdeaPicker";
import type { SceneIdea, SceneSuggestion } from "../api/client";

// `useSceneSuggestions` now lives in NewSceneChooser (issue #319) -- this
// pane only renders what it's handed, so its own tests supply the generated
// half (suggestions/picks/nextDate/busy/error/refresh) and `direction`
// directly as props rather than mocking `api.sceneSuggestions`.
vi.mock("../api/client", () => ({
  api: {
    availableGreetings: vi.fn(), sceneIntent: vi.fn(),
    listSceneIdeas: vi.fn(), saveSceneIdea: vi.fn(), setSceneIdeaStatus: vi.fn(),
  },
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
  (api.listSceneIdeas as any).mockResolvedValue([]);
  (api.saveSceneIdea as any).mockResolvedValue({ id: "the-creditor" });
  (api.setSceneIdeaStatus as any).mockResolvedValue({ ok: true });
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
  error: unknown;
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
  // Routed: an unreachable model puts a Connections link in the banner (#210).
  const utils = render(
    <MemoryRouter><Wrapper {...overrides} onPicked={onPicked} refresh={refresh} /></MemoryRouter>);
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

test("a generation the model could not be reached for offers the recovery", async () => {
  // The hook hands the rejection up whole; this is the surface that turns a
  // `network` kind into somewhere to go (#210).
  renderPicker({ error: { detail: "connection refused", kind: "network" }, suggestions: [] });
  await screen.findByText(/Couldn.t reach the model provider/);
  expect(screen.getByRole("link", { name: /Connections/ })).toHaveAttribute("href", "/connections");
  expect(screen.getByText(/connection refused/)).toBeInTheDocument();
});

test("a generation refused for any other reason still shows that reason", async () => {
  renderPicker({ error: { detail: "No LLM connection selected", kind: "missing_key" },
                 suggestions: [] });
  await screen.findByText("No LLM connection selected");
  expect(screen.queryByRole("link")).toBeNull();
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

// ---- the saved half of the ledger (#88) ----
// deliberately NOT the generated card's title: the two groups render the same
// shape, and a shared name would make every query below ambiguous
const SAVED: SceneIdea = {
  id: "the-tide-book", title: "The tide-book", premise: "A ledger nobody signed.",
  date: "2026-03-04", cast: [{ kind: "characters", id: "mara", name: "Mara" }],
  location: { id: "saltmarch", name: "Saltmarch" }, pcless: false,
  source: "llm", status: "active", created: "2026-03-01T00:00:00Z", used_scene: "",
};

test("the picker asks for the saved half alone", async () => {
  // composing the greeting half parses every greeting's frontmatter, and this
  // pane renders greetings from its own ranked read and drops every composed
  // row -- so asking for them is a second full sweep to render nothing
  renderPicker();
  await waitFor(() => expect(api.listSceneIdeas).toHaveBeenCalledWith("c", false));
});

test("a save in flight cannot be fired twice", async () => {
  let release: (v: any) => void = () => {};
  (api.saveSceneIdea as any).mockReturnValue(new Promise((r) => { release = r; }));
  renderPicker();
  const b = await screen.findByRole("button", { name: "Save The creditor" });
  fireEvent.click(b);
  fireEvent.click(b);          // impatient reader, request still open
  expect(api.saveSceneIdea).toHaveBeenCalledTimes(1);
  await act(async () => { release({ id: "x" }); });
});

test("a saved idea is pickable and emits a draft carrying its ledger id", async () => {
  (api.listSceneIdeas as any).mockResolvedValue([SAVED]);
  const { onPicked } = renderPicker();
  fireEvent.click(await screen.findByText("The tide-book"));
  expect(onPicked).toHaveBeenCalledWith(expect.objectContaining({
    source: "saved", lid: "the-tide-book", location: "saltmarch",
    premise: "A ledger nobody signed." }));
});

test("a saved idea's fossil date does not beat this minute's estimate", async () => {
  // SAVED carries 2026-03-04, saved whenever it was saved; the campaign has
  // been played since. set_datetime accepts a date before the campaign's
  // current moment without complaint, so pre-filling the confirm form with the
  // stored one would date the new scene behind the one it follows.
  (api.listSceneIdeas as any).mockResolvedValue([SAVED]);
  const { onPicked } = renderPicker({ nextDate: "2026-09-09" });
  fireEvent.click(await screen.findByText("The tide-book"));
  expect(onPicked).toHaveBeenCalledWith(expect.objectContaining({ date: "2026-09-09" }));
});

test("...but the stored date is still the fallback when nothing was estimated", async () => {
  (api.listSceneIdeas as any).mockResolvedValue([SAVED]);
  const { onPicked } = renderPicker({ nextDate: "" });
  fireEvent.click(await screen.findByText("The tide-book"));
  expect(onPicked).toHaveBeenCalledWith(expect.objectContaining({ date: "2026-03-04" }));
});

test("saved ideas for the other mode, and ones already used, are not offered", async () => {
  // an offscreen idea casts nobody the player can be; a used one is a scene
  // that already exists in the rail behind this modal
  (api.listSceneIdeas as any).mockResolvedValue([
    { ...SAVED, id: "offscreen", title: "While she sleeps", pcless: true },
    { ...SAVED, id: "spent", title: "Already played", status: "used" },
    { ...SAVED, id: "greet", title: "Reckoning greeting", source: "greeting" },
    SAVED,
  ]);
  renderPicker();
  expect(await screen.findByText("The tide-book")).toBeInTheDocument();
  expect(screen.queryByText("While she sleeps")).toBeNull();
  expect(screen.queryByText("Already played")).toBeNull();
  // the greeting rows have their own group, ranked and chipped -- showing them
  // here as well would be strictly worse than once
  expect(screen.queryByText("Reckoning greeting")).toBeNull();
});

test("Save keeps a generated card, sending ids rather than the names it rendered", async () => {
  renderPicker();
  fireEvent.click(await screen.findByRole("button", { name: "Save The creditor" }));
  await waitFor(() => expect(api.saveSceneIdea).toHaveBeenCalledWith("c", {
    pcless: false, title: "The creditor", premise: "A debt-collector arrives.",
    date: "2026-03-04", cast: ["characters:mara"], location: "saltmarch", source: "llm" }));
  // the list is re-read, and the card cannot be filed a second time
  await waitFor(() => expect(api.listSceneIdeas).toHaveBeenCalledTimes(2));
  expect(screen.getByRole("button", { name: "Saved The creditor" })).toBeDisabled();
});

test("Regenerate clears the Saved labels, which point at cards that are gone", async () => {
  const OTHER: SceneSuggestion = { ...SUGGESTION, title: "The tide turns", premise: "Q" };
  const { rerender } = render(
    <SceneIdeaPicker cid="c" afterSid="s1" ready pcless={false}
                     direction="" onDirectionChange={() => {}}
                     suggestions={[SUGGESTION]} picks={[]} nextDate="" busy={false} error={null}
                     refresh={() => {}} onPicked={vi.fn()} onCancel={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: "Save The creditor" }));
  await screen.findByRole("button", { name: "Saved The creditor" });
  // the regenerate lands: index 0 is a different idea now, and must be savable
  rerender(
    <SceneIdeaPicker cid="c" afterSid="s1" ready pcless={false}
                     direction="" onDirectionChange={() => {}}
                     suggestions={[OTHER]} picks={[]} nextDate="" busy={false} error={null}
                     refresh={() => {}} onPicked={vi.fn()} onCancel={() => {}} />);
  expect(screen.getByRole("button", { name: "Save The tide turns" })).toBeEnabled();
});

test("Save for later files the typed text without an extraction call", async () => {
  const { onPicked } = renderPicker();
  await screen.findByText("Reckoning");
  fireEvent.change(screen.getByLabelText("Your own scene"), { target: { value: "a storm" } });
  fireEvent.click(screen.getByRole("button", { name: /save for later/i }));
  await waitFor(() => expect(api.saveSceneIdea).toHaveBeenCalledWith(
    "c", { pcless: false, premise: "a storm", source: "user" }));
  expect(api.sceneIntent).not.toHaveBeenCalled();   // nothing is going to the confirm form
  expect(onPicked).not.toHaveBeenCalled();
  await waitFor(() => expect(screen.getByLabelText("Your own scene")).toHaveValue(""));
});

test("Save for later is disabled with nothing typed", async () => {
  renderPicker();
  await screen.findByText("Reckoning");
  expect(screen.getByRole("button", { name: /save for later/i })).toBeDisabled();
});

test("dismissing a saved idea moves it behind the toggle, and Restore brings it back", async () => {
  (api.listSceneIdeas as any).mockResolvedValue([SAVED]);
  renderPicker();
  await screen.findByText("The tide-book");

  (api.listSceneIdeas as any).mockResolvedValue([{ ...SAVED, status: "dismissed" }]);
  fireEvent.click(screen.getByRole("button", { name: "Dismiss The tide-book" }));
  expect(api.setSceneIdeaStatus).toHaveBeenCalledWith("c", "the-tide-book", "dismissed");

  const toggle = await screen.findByRole("button", { name: /show dismissed \(1\)/i });
  // dismissed ideas are out of the way but not gone -- restore is the only
  // route back, and there is no management surface yet
  fireEvent.click(toggle);
  fireEvent.click(screen.getByRole("button", { name: "Restore The tide-book" }));
  expect(api.setSceneIdeaStatus).toHaveBeenCalledWith("c", "the-tide-book", "active");
});

test("each Restore names the idea it restores", async () => {
  // several stack, and "Restore, Restore, Restore" is what a screen reader
  // would otherwise read out
  (api.listSceneIdeas as any).mockResolvedValue([
    { ...SAVED, id: "a", title: "Idea A", status: "dismissed" },
    { ...SAVED, id: "b", title: "Idea B", status: "dismissed" }]);
  renderPicker();
  fireEvent.click(await screen.findByRole("button", { name: /show dismissed \(2\)/i }));
  fireEvent.click(screen.getByRole("button", { name: "Restore Idea B" }));
  expect(api.setSceneIdeaStatus).toHaveBeenCalledWith("c", "b", "active");
});

test("the saved group has a slot budget, with everything behind a toggle", async () => {
  // nothing prunes the ledger, so an unbounded group would push the greeting
  // and generated cards off the bottom of the modal
  (api.listSceneIdeas as any).mockResolvedValue(
    [1, 2, 3, 4, 5].map((n) => ({ ...SAVED, id: `idea-${n}`, title: `Idea ${n}` })));
  renderPicker();
  await screen.findByText("Idea 1");
  expect(screen.queryByText("Idea 5")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /show all 5 saved/i }));
  expect(screen.getByText("Idea 5")).toBeInTheDocument();
});

test("a ledger that will not load costs its own group and nothing else", async () => {
  (api.listSceneIdeas as any).mockRejectedValue({ detail: "ledger unreachable" });
  const { onPicked } = renderPicker();
  await screen.findByText(/ledger unreachable/i);
  // greetings, generated cards and the typed path all still work
  fireEvent.click(screen.getByText("Reckoning"));
  expect(onPicked).toHaveBeenCalledWith(expect.objectContaining({ source: "greeting" }));
});
