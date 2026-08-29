import { renderHook, waitFor, act } from "@testing-library/react";
import { useSceneSuggestions } from "./useSceneSuggestions";

vi.mock("../api/client", () => ({ api: { sceneSuggestions: vi.fn() } }));
import { api } from "../api/client";

const R = (suggestions: any[], picks: string[] = [], next_date = "") =>
  ({ suggestions, greeting_picks: picks, next_date });

/** A call that never settles: the hook stays in its pending state. */
const pending = () => new Promise(() => {});

beforeEach(() => vi.clearAllMocks());

// ---- the call the picker opens on ----

test("opening asks the ranked question, with no direction", () => {
  (api.sceneSuggestions as any).mockReturnValue(pending());
  const { result } = renderHook(() => useSceneSuggestions("c", "s1", true, false));
  // rank=true: the picker's four slots are 2 greetings + 2 ideas, and the
  // greeting half is an ordering only this call returns.
  expect(api.sceneSuggestions).toHaveBeenCalledWith("c", "s1", false, "", true);
  expect(result.current.asked).toBe(true);
  // Both groups pending, so the picker draws "Generating…" and "Choosing…"
  // rather than two empty groups it is about to fill.
  expect(result.current.suggestions).toBeNull();
  expect(result.current.picks).toBeNull();
  expect(result.current.busy).toBe(true);
});

test("opening asks once, however often the hook re-renders", () => {
  (api.sceneSuggestions as any).mockReturnValue(pending());
  const { rerender } = renderHook(() => useSceneSuggestions("c", "s1", true, false));
  rerender();
  rerender();
  expect(api.sceneSuggestions).toHaveBeenCalledTimes(1);
});

test("an offscreen hook asks the offscreen question", () => {
  (api.sceneSuggestions as any).mockReturnValue(pending());
  renderHook(() => useSceneSuggestions("c", "s1", true, true));
  expect(api.sceneSuggestions).toHaveBeenCalledWith("c", "s1", true, "", true);
});

test("without a connection nothing is asked, and suggest stays a no-op", () => {
  const { result } = renderHook(() => useSceneSuggestions("c", "s1", false, false));
  act(() => result.current.suggest("x"));
  expect(api.sceneSuggestions).not.toHaveBeenCalled();
  // The pending state must NOT be set around a call that never happened, or
  // the picker sits on "Generating…" for a ranking nobody is running.
  expect(result.current.asked).toBe(false);
  expect(result.current.suggestions).toEqual([]);
  expect(result.current.picks).toEqual([]);
});

test("ready arriving late asks then, and only once", () => {
  (api.sceneSuggestions as any).mockReturnValue(pending());
  const { rerender } = renderHook(
    ({ ready }) => useSceneSuggestions("c", "s1", ready, false),
    { initialProps: { ready: false } },
  );
  expect(api.sceneSuggestions).not.toHaveBeenCalled();
  // App resolves its config fetch asynchronously, so `ready` genuinely does
  // flip on a mounted hook -- and a picker that opened before it landed would
  // otherwise sit on the fallback view for the life of the modal.
  rerender({ ready: true });
  expect(api.sceneSuggestions).toHaveBeenCalledTimes(1);
  rerender({ ready: true });
  expect(api.sceneSuggestions).toHaveBeenCalledTimes(1);
});

// ---- the button, which regenerates what the open call already ranked ----

test("a press after the ranking lands regenerates without re-ranking", async () => {
  (api.sceneSuggestions as any).mockResolvedValue(R([{ title: "A" }], ["g1"], "2026-01-01"));
  const { result } = renderHook(() => useSceneSuggestions("c", "s1", true, false));
  await waitFor(() => expect(result.current.picks).toEqual(["g1"]));

  (api.sceneSuggestions as any).mockResolvedValue(R([{ title: "B" }], [], ""));
  act(() => result.current.suggest("something at sea"));
  await waitFor(() => expect(result.current.suggestions).toEqual([{ title: "B" }]));
  // rank=false: the greeting order is earned, and re-ranking would reshuffle
  // the cards under the reader's cursor.
  expect(api.sceneSuggestions).toHaveBeenLastCalledWith("c", "s1", false, "something at sea", false);
  expect(result.current.picks).toEqual(["g1"]);       // not clobbered by the empty list
  expect(result.current.nextDate).toBe("2026-01-01");  // not cleared by an empty one
});

test("a press does not reset existing suggestions to pending while it is in flight", async () => {
  (api.sceneSuggestions as any).mockResolvedValue(R([{ title: "A" }], ["g1"]));
  const { result } = renderHook(() => useSceneSuggestions("c", "s1", true, false));
  await waitFor(() => expect(result.current.suggestions).toEqual([{ title: "A" }]));

  (api.sceneSuggestions as any).mockReturnValue(pending());
  act(() => result.current.suggest(""));
  // What is on screen stays there while the new ideas load: only the FIRST
  // (ranking) call reports pending, because only it has nothing to replace.
  expect(result.current.suggestions).toEqual([{ title: "A" }]);
  expect(result.current.picks).toEqual(["g1"]);
  expect(result.current.busy).toBe(true);
});

// ---- failures ----

test("a failure empties the suggestions and reports the error", async () => {
  (api.sceneSuggestions as any).mockRejectedValue(new Error("nope"));
  const { result } = renderHook(() => useSceneSuggestions("c", "s1", true, false));
  await waitFor(() => expect(result.current.error).toBeTruthy());
  expect(result.current.suggestions).toEqual([]);
  expect(result.current.picks).toEqual([]);
  expect(result.current.busy).toBe(false);
});

test("an open call that failed leaves the next press ranked", async () => {
  (api.sceneSuggestions as any).mockRejectedValue(new Error("nope"));
  const { result } = renderHook(() => useSceneSuggestions("c", "s1", true, false));
  await waitFor(() => expect(result.current.error).toBeTruthy());

  // The greeting ordering is half of what the call buys and only a rank=true
  // request fetches it, so a failed open must not promote the button to an
  // unranked regenerate -- a picker with more than two greetings would then
  // never get the ordering it is waiting on.
  (api.sceneSuggestions as any).mockResolvedValue(R([{ title: "A" }], ["g1"]));
  act(() => result.current.suggest(""));
  await waitFor(() => expect(result.current.picks).toEqual(["g1"]));
  expect(api.sceneSuggestions).toHaveBeenLastCalledWith("c", "s1", false, "", true);
});

test("a press after a failure is pending again rather than showing the failure's empty list", async () => {
  (api.sceneSuggestions as any).mockRejectedValue(new Error("nope"));
  const { result } = renderHook(() => useSceneSuggestions("c", "s1", true, false));
  await waitFor(() => expect(result.current.suggestions).toEqual([]));

  (api.sceneSuggestions as any).mockReturnValue(pending());
  act(() => result.current.suggest(""));
  expect(result.current.suggestions).toBeNull();
  expect(result.current.picks).toBeNull();
});

// ---- races ----

test("a stale response that resolves after a newer one is discarded", async () => {
  let releaseFirst: (v: any) => void = () => {};
  (api.sceneSuggestions as any).mockReturnValueOnce(new Promise((r) => { releaseFirst = r; }));
  const { result } = renderHook(() => useSceneSuggestions("c", "s1", true, false));

  (api.sceneSuggestions as any).mockResolvedValue(R([{ title: "newest" }]));
  act(() => result.current.suggest("x"));
  await waitFor(() => expect(result.current.suggestions).toEqual([{ title: "newest" }]));

  await act(async () => { releaseFirst(R([{ title: "stale" }], ["g9"])); });
  expect(result.current.suggestions).toEqual([{ title: "newest" }]);
  // The stale reply's "g9" never reached the ranking.
  expect(result.current.picks).toEqual([]);
});

test("a stale response's finally does not clear busy while a newer request is still in flight", async () => {
  let releaseFirst: (v: any) => void = () => {};
  let releaseSecond: (v: any) => void = () => {};
  (api.sceneSuggestions as any).mockReturnValueOnce(new Promise((r) => { releaseFirst = r; }));
  const { result } = renderHook(() => useSceneSuggestions("c", "s1", true, false));
  expect(result.current.busy).toBe(true);

  (api.sceneSuggestions as any).mockReturnValueOnce(new Promise((r) => { releaseSecond = r; }));
  act(() => result.current.suggest("x"));
  expect(result.current.busy).toBe(true);

  // The stale (first) request resolves while the newer (second) one is still
  // pending: its `finally` must not clear `busy`, or the UI would flash
  // "done" while a request it never saw the result of is still running.
  await act(async () => { releaseFirst(R([{ title: "stale" }])); });
  expect(result.current.busy).toBe(true);

  await act(async () => { releaseSecond(R([{ title: "newest" }])); });
  expect(result.current.busy).toBe(false);
});

// ---- one question, one answer ----

test("a different reference scene asks the new question rather than keeping the old answer", async () => {
  (api.sceneSuggestions as any).mockResolvedValue(R([{ title: "A" }], ["g1"], "2026-01-01"));
  const { result, rerender } = renderHook(
    ({ after }) => useSceneSuggestions("c", after, true, false),
    { initialProps: { after: "s1" } },
  );
  await waitFor(() => expect(result.current.suggestions).toEqual([{ title: "A" }]));

  (api.sceneSuggestions as any).mockReturnValue(pending());
  rerender({ after: "s2" });
  // Cards ranked against one scene answer the wrong question about another, so
  // the old answer is cleared and the new one asked for.
  expect(api.sceneSuggestions).toHaveBeenLastCalledWith("c", "s2", false, "", true);
  expect(result.current.suggestions).toBeNull();
  expect(result.current.picks).toBeNull();
});

test("switching between PC and offscreen asks again too", async () => {
  (api.sceneSuggestions as any).mockResolvedValue(R([{ title: "A" }]));
  const { result, rerender } = renderHook(
    ({ off }) => useSceneSuggestions("c", "s1", true, off),
    { initialProps: { off: false } },
  );
  await waitFor(() => expect(result.current.suggestions).toEqual([{ title: "A" }]));

  // An offscreen scene casts nobody the player can be: a PC ranking is the
  // wrong answer, not a stale one.
  (api.sceneSuggestions as any).mockReturnValue(pending());
  rerender({ off: true });
  expect(api.sceneSuggestions).toHaveBeenLastCalledWith("c", "s1", true, "", true);
});

test("a reply to the old question cannot land on the new one", async () => {
  let releaseFirst: (v: any) => void = () => {};
  (api.sceneSuggestions as any).mockReturnValueOnce(new Promise((r) => { releaseFirst = r; }));
  const { result, rerender } = renderHook(
    ({ cid }) => useSceneSuggestions(cid, "s1", true, false),
    { initialProps: { cid: "a" } },
  );
  expect(result.current.busy).toBe(true);

  (api.sceneSuggestions as any).mockReturnValue(pending());
  rerender({ cid: "b" });

  await act(async () => { releaseFirst(R([{ title: "campaign A" }], ["g1"])); });
  // Campaign A's reply is discarded rather than landing on B's pending state.
  expect(result.current.suggestions).toBeNull();
  expect(result.current.picks).toBeNull();
});
