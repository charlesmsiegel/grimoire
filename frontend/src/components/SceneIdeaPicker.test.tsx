import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { SceneIdeaPicker } from "./SceneIdeaPicker";

vi.mock("../api/client", () => ({
  api: { availableGreetings: vi.fn(), sceneSuggestions: vi.fn(), sceneIntent: vi.fn() },
}));
import { api } from "../api/client";

const GREETINGS = [
  { id: "reck", name: "Reckoning", available: true, reasons: [], unlocked: true },
  { id: "open", name: "Open", available: true, reasons: [], unlocked: false },
];
const SUGGESTION = {
  title: "The creditor", premise: "A debt-collector arrives.", date: "2026-03-04",
  cast: [{ kind: "characters", id: "mara", name: "Mara" }],
  location: { id: "saltmarch", name: "Saltmarch" },
};

beforeEach(() => {
  vi.clearAllMocks();
  (api.availableGreetings as any).mockResolvedValue(GREETINGS);
  (api.sceneSuggestions as any).mockResolvedValue({
    suggestions: [SUGGESTION], greeting_picks: [], next_date: "2026-01-01" });
  (api.sceneIntent as any).mockResolvedValue({
    title: "The morning after", date: "2026-03-04",
    location: { id: "saltmarch", name: "Saltmarch" }, cast: [] });
});

function renderPicker(onPicked = vi.fn(), ready = true) {
  render(<SceneIdeaPicker cid="c" afterSid="s1" ready={ready} pcless={false}
                          onPicked={onPicked} onCancel={() => {}} />);
  return onPicked;
}

test("picking a greeting emits a greeting draft", async () => {
  const onPicked = renderPicker();
  fireEvent.click(await screen.findByText("Reckoning"));
  expect(onPicked).toHaveBeenCalledWith(expect.objectContaining({
    source: "greeting", gid: "reck", title: "Reckoning", date: "2026-01-01" }));
});

test("picking a generated card emits its resolved metadata", async () => {
  const onPicked = renderPicker();
  fireEvent.click(await screen.findByText("The creditor"));
  expect(onPicked).toHaveBeenCalledWith(expect.objectContaining({
    source: "generated", location: "saltmarch", date: "2026-03-04",
    premise: "A debt-collector arrives." }));
});

test("Regenerate re-fetches with the direction and does not refetch greetings", async () => {
  renderPicker();
  await screen.findByText("The creditor");
  expect(api.availableGreetings).toHaveBeenCalledTimes(1);
  fireEvent.change(screen.getByLabelText("Direction"), { target: { value: "something at sea" } });
  fireEvent.click(screen.getByRole("button", { name: /regenerate/i }));
  await waitFor(() => expect(api.sceneSuggestions).toHaveBeenLastCalledWith(
    "c", "s1", false, "something at sea", false));
  expect(api.availableGreetings).toHaveBeenCalledTimes(1);
});

test("typed text runs the extraction and emits a custom draft", async () => {
  const onPicked = renderPicker();
  await screen.findByText("The creditor");
  fireEvent.change(screen.getByLabelText("Your own scene"),
                   { target: { value: "back at the marsh house" } });
  fireEvent.click(screen.getByRole("button", { name: /use this/i }));
  await waitFor(() => expect(onPicked).toHaveBeenCalledWith(expect.objectContaining({
    source: "custom", title: "The morning after", location: "saltmarch",
    premise: "back at the marsh house" })));
});

test("empty text creates a blank draft with no LLM call", async () => {
  const onPicked = renderPicker();
  await screen.findByText("The creditor");
  fireEvent.click(screen.getByRole("button", { name: /create blank scene/i }));
  expect(api.sceneIntent).not.toHaveBeenCalled();
  expect(onPicked).toHaveBeenCalledWith(expect.objectContaining({
    source: "custom", title: "New scene", date: "2026-01-01", premise: "" }));
});

test("a failed extraction opens with the typed text AND hands the warning on", async () => {
  (api.sceneIntent as any).mockRejectedValue({ detail: "no key" });
  const onPicked = renderPicker();
  await screen.findByText("The creditor");
  fireEvent.change(screen.getByLabelText("Your own scene"), { target: { value: "a storm" } });
  fireEvent.click(screen.getByRole("button", { name: /use this/i }));
  // the warning is the SECOND argument: this pane unmounts immediately, so a
  // banner of its own would never be seen
  await waitFor(() => expect(onPicked).toHaveBeenCalledWith(
    expect.objectContaining({ source: "custom", title: "New scene", premise: "a storm" }),
    expect.stringContaining("no key")));
});

// This click is purely synchronous — nothing awaits between "the date
// landed" and "the draft is built" — so it does NOT exercise the emit-time
// `latestDate` ref; a plain (non-ref) read of `nextDate` would satisfy this
// test too. It still verifies something real: that a date estimate arriving
// after mount reaches a later-emitted greeting draft at all. The ref
// mechanism itself is covered by "a typed pick that outlasts a slow date
// estimate still carries the fresh date", below, which awaits `api.sceneIntent`
// and lands the date mid-flight.
test("a delayed date estimate reaches an emitted greeting draft", async () => {
  let release: (v: any) => void = () => {};
  (api.sceneSuggestions as any).mockReturnValue(new Promise((r) => { release = r; }));
  const onPicked = renderPicker();
  await screen.findByText("Reckoning");           // greetings render before generation
  await act(async () => { release({ suggestions: [], greeting_picks: [], next_date: "2026-02-02" }); });
  fireEvent.click(screen.getByText("Reckoning"));
  expect(onPicked).toHaveBeenCalledWith(expect.objectContaining({ date: "2026-02-02" }));
});

test("a typed pick that outlasts a slow date estimate still carries the fresh date", async () => {
  let releaseSuggestions: (v: any) => void = () => {};
  let releaseIntent: (v: any) => void = () => {};
  (api.sceneSuggestions as any).mockReturnValue(new Promise((r) => { releaseSuggestions = r; }));
  (api.sceneIntent as any).mockReturnValue(new Promise((r) => { releaseIntent = r; }));
  const onPicked = renderPicker();
  await screen.findByText("Reckoning");           // greetings render before generation
  fireEvent.change(screen.getByLabelText("Your own scene"), { target: { value: "a storm" } });
  fireEvent.click(screen.getByRole("button", { name: /use this/i }));
  // the date estimate lands WHILE the extraction is still in flight
  await act(async () => { releaseSuggestions({ suggestions: [], greeting_picks: [], next_date: "2026-05-05" }); });
  await act(async () => {
    releaseIntent({ title: "The morning after", date: "", location: null, cast: [] });
  });
  await waitFor(() => expect(onPicked).toHaveBeenCalledWith(
    expect.objectContaining({ date: "2026-05-05" })));
});

test("an extraction that returns nothing still hands on a hint that metadata could not be inferred", async () => {
  (api.sceneIntent as any).mockResolvedValue({ title: "", date: "", location: null, cast: [] });
  const onPicked = renderPicker();
  await screen.findByText("The creditor");
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
  await screen.findByText("The creditor");
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
  const onPicked = renderPicker(vi.fn(), false);
  await screen.findByText("Reckoning");
  expect(api.sceneSuggestions).not.toHaveBeenCalled();
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
  render(<SceneIdeaPicker cid="c" afterSid="s1" ready={true} pcless={false}
                          onPicked={onPicked} onCancel={onCancel} />);
  await screen.findByText("Reckoning");
  fireEvent.click(screen.getByRole("button", { name: /cancel/i }));
  expect(onCancel).toHaveBeenCalledTimes(1);
  expect(onPicked).not.toHaveBeenCalled();
});
