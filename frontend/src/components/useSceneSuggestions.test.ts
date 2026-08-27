import { renderHook, waitFor, act } from "@testing-library/react";
import { useSceneSuggestions } from "./useSceneSuggestions";

vi.mock("../api/client", () => ({ api: { sceneSuggestions: vi.fn() } }));
import { api } from "../api/client";

const R = (suggestions: any[], picks: string[] = [], next_date = "") =>
  ({ suggestions, greeting_picks: picks, next_date });

beforeEach(() => vi.clearAllMocks());

test("nothing is fetched until the reader asks", () => {
  (api.sceneSuggestions as any).mockReturnValue(new Promise(() => {}));
  const { result } = renderHook(() => useSceneSuggestions("c", "s1", true, false));
  // Mounted, ready, and idle: a ranking is an LLM call, and mounting a picker
  // is not a request for one.
  expect(api.sceneSuggestions).not.toHaveBeenCalled();
  expect(result.current.asked).toBe(false);
  // `[]`, not `null` -- the picker must not sit on "Generating…" for a call
  // that nobody has started.
  expect(result.current.suggestions).toEqual([]);
  expect(result.current.picks).toEqual([]);
  expect(result.current.busy).toBe(false);
});

test("suggest ranks; a refresh after it does not, and keeps the picks", async () => {
  (api.sceneSuggestions as any).mockResolvedValue(R([{ title: "A" }], ["g1"], "2026-01-01"));
  const { result } = renderHook(() => useSceneSuggestions("c", "s1", true, false));
  act(() => result.current.suggest(""));
  await waitFor(() => expect(result.current.picks).toEqual(["g1"]));
  expect(api.sceneSuggestions).toHaveBeenCalledWith("c", "s1", false, "", true);
  expect(result.current.asked).toBe(true);

  (api.sceneSuggestions as any).mockResolvedValue(R([{ title: "B" }], [], ""));
  act(() => result.current.refresh("something at sea"));
  await waitFor(() => expect(result.current.suggestions).toEqual([{ title: "B" }]));
  expect(api.sceneSuggestions).toHaveBeenLastCalledWith("c", "s1", false, "something at sea", false);
  expect(result.current.picks).toEqual(["g1"]);      // not clobbered by the empty list
  expect(result.current.nextDate).toBe("2026-01-01"); // not cleared by an empty one
});

test("suggest carries the typed direction into the first ranking", () => {
  (api.sceneSuggestions as any).mockReturnValue(new Promise(() => {}));
  const { result } = renderHook(() => useSceneSuggestions("c", "s1", true, false));
  act(() => result.current.suggest("something at sea"));
  expect(api.sceneSuggestions).toHaveBeenCalledWith("c", "s1", false, "something at sea", true);
});

test("suggest reports pending on both lists while the ranking runs", () => {
  (api.sceneSuggestions as any).mockReturnValue(new Promise(() => {}));
  const { result } = renderHook(() => useSceneSuggestions("c", "s1", true, false));
  act(() => result.current.suggest(""));
  // The greeting ranking rides on this same call, so both groups are pending:
  // the picker shows "Generating…" and "Choosing…", not two empty groups it
  // is about to fill.
  expect(result.current.suggestions).toBeNull();
  expect(result.current.picks).toBeNull();
  expect(result.current.busy).toBe(true);
});

test("an offscreen hook asks for offscreen ideas", () => {
  (api.sceneSuggestions as any).mockReturnValue(new Promise(() => {}));
  const { result } = renderHook(() => useSceneSuggestions("c", "s1", true, true));
  act(() => result.current.suggest(""));
  expect(api.sceneSuggestions).toHaveBeenCalledWith("c", "s1", true, "", true);
});

test("a stale response that resolves after a newer one is discarded", async () => {
  let releaseFirst: (v: any) => void = () => {};
  (api.sceneSuggestions as any).mockReturnValueOnce(new Promise((r) => { releaseFirst = r; }));
  const { result } = renderHook(() => useSceneSuggestions("c", "s1", true, false));
  act(() => result.current.suggest(""));

  (api.sceneSuggestions as any).mockResolvedValue(R([{ title: "newest" }]));
  act(() => result.current.refresh("x"));
  await waitFor(() => expect(result.current.suggestions).toEqual([{ title: "newest" }]));

  await act(async () => { releaseFirst(R([{ title: "stale" }], ["g9"])); });
  expect(result.current.suggestions).toEqual([{ title: "newest" }]);
  expect(result.current.picks).toBeNull();     // the stale ranked reply wrote nothing
});

test("a stale response's finally does not clear busy while a newer request is still in flight", async () => {
  let releaseFirst: (v: any) => void = () => {};
  let releaseSecond: (v: any) => void = () => {};
  (api.sceneSuggestions as any).mockReturnValueOnce(new Promise((r) => { releaseFirst = r; }));
  const { result } = renderHook(() => useSceneSuggestions("c", "s1", true, false));
  act(() => result.current.suggest(""));
  expect(result.current.busy).toBe(true);

  (api.sceneSuggestions as any).mockReturnValueOnce(new Promise((r) => { releaseSecond = r; }));
  act(() => result.current.refresh("x"));
  expect(result.current.busy).toBe(true);

  // The stale (first) request resolves while the newer (second) one is still
  // pending: its `finally` must not clear `busy`, or the UI would flash
  // "done" while a request it never saw the result of is still running.
  await act(async () => { releaseFirst(R([{ title: "stale" }])); });
  expect(result.current.busy).toBe(true);

  await act(async () => { releaseSecond(R([{ title: "newest" }])); });
  expect(result.current.busy).toBe(false);
});

test("without a connection suggest is a no-op and leaves nothing pending", () => {
  const { result } = renderHook(() => useSceneSuggestions("c", "s1", false, false));
  act(() => result.current.suggest("x"));
  expect(api.sceneSuggestions).not.toHaveBeenCalled();
  // The pending state must NOT be set around a call that never happened, or
  // the picker sits on "Generating…" for a ranking nobody is running.
  expect(result.current.asked).toBe(false);
  expect(result.current.suggestions).toEqual([]);
  expect(result.current.picks).toEqual([]);
});

test("refresh is a no-op while not ready", () => {
  const { result } = renderHook(() => useSceneSuggestions("c", "s1", false, false));
  act(() => result.current.refresh("x"));
  expect(api.sceneSuggestions).not.toHaveBeenCalled();
});

test("ready flipping true on a mounted hook fetches nothing on its own", () => {
  (api.sceneSuggestions as any).mockReturnValue(new Promise(() => {}));
  const { result, rerender } = renderHook(
    ({ ready }) => useSceneSuggestions("c", "s1", ready, false),
    { initialProps: { ready: false } },
  );
  rerender({ ready: true });
  // App resolves its config fetch asynchronously, so `ready` genuinely does
  // flip on a mounted hook. It enables the button; it does not press it.
  expect(api.sceneSuggestions).not.toHaveBeenCalled();
  expect(result.current.suggestions).toEqual([]);

  act(() => result.current.suggest(""));
  expect(api.sceneSuggestions).toHaveBeenCalledTimes(1);
});

test("a refresh does not reset existing suggestions to pending while it is in flight", async () => {
  (api.sceneSuggestions as any).mockResolvedValueOnce(R([{ title: "A" }], ["g1"]));
  const { result } = renderHook(() => useSceneSuggestions("c", "s1", true, false));
  act(() => result.current.suggest(""));
  await waitFor(() => expect(result.current.suggestions).toEqual([{ title: "A" }]));

  let releaseRefresh: (v: any) => void = () => {};
  (api.sceneSuggestions as any).mockReturnValueOnce(new Promise((r) => { releaseRefresh = r; }));
  act(() => result.current.refresh("x"));
  // the existing cards stay on screen while the new ones load
  expect(result.current.suggestions).toEqual([{ title: "A" }]);

  await act(async () => { releaseRefresh(R([{ title: "B" }])); });
  expect(result.current.suggestions).toEqual([{ title: "B" }]);
});

test("a failure empties the suggestions and reports the error", async () => {
  // The rejection is reported whole, not as text: the picker renders it, and
  // `kind` is what lets it tell an unreachable model from a missing key (#210).
  const refused = { detail: "no key", kind: "missing_key" };
  (api.sceneSuggestions as any).mockRejectedValue(refused);
  const { result } = renderHook(() => useSceneSuggestions("c", "s1", true, false));
  act(() => result.current.suggest(""));
  await waitFor(() => expect(result.current.error).toBe(refused));
  expect(result.current.suggestions).toEqual([]);
  // Still asked: the button says Regenerate, because pressing again is the
  // recovery.
  expect(result.current.asked).toBe(true);
});

// ---- the ranking is remembered per question, and only per question ----

test("a different reference scene drops the ranking rather than answering the old question", async () => {
  (api.sceneSuggestions as any).mockResolvedValue(R([{ title: "A" }], ["g1"], "2026-01-01"));
  const { result, rerender } = renderHook(
    ({ after }) => useSceneSuggestions("c", after, true, false),
    { initialProps: { after: "s1" } },
  );
  act(() => result.current.suggest(""));
  await waitFor(() => expect(result.current.suggestions).toEqual([{ title: "A" }]));

  rerender({ after: "s2" });
  // Nothing re-fetches on its own any more, so cards ranked against one scene
  // would simply sit there answering the wrong question.
  expect(result.current.asked).toBe(false);
  expect(result.current.suggestions).toEqual([]);
  expect(result.current.picks).toEqual([]);
  expect(result.current.nextDate).toBe("");
  expect(api.sceneSuggestions).toHaveBeenCalledTimes(1);
});

test("switching between PC and offscreen drops the ranking too", async () => {
  (api.sceneSuggestions as any).mockResolvedValue(R([{ title: "A" }]));
  const { result, rerender } = renderHook(
    ({ off }) => useSceneSuggestions("c", "s1", true, off),
    { initialProps: { off: false } },
  );
  act(() => result.current.suggest(""));
  await waitFor(() => expect(result.current.suggestions).toEqual([{ title: "A" }]));

  // An offscreen scene casts nobody the player can be: a PC ranking is the
  // wrong answer, not a stale one.
  rerender({ off: true });
  expect(result.current.suggestions).toEqual([]);
  expect(result.current.asked).toBe(false);
});

test("a reply to the old question cannot land on the new one", async () => {
  let releaseFirst: (v: any) => void = () => {};
  (api.sceneSuggestions as any).mockReturnValue(new Promise((r) => { releaseFirst = r; }));
  const { result, rerender } = renderHook(
    ({ cid }) => useSceneSuggestions(cid, "s1", true, false),
    { initialProps: { cid: "a" } },
  );
  act(() => result.current.suggest(""));
  expect(result.current.busy).toBe(true);

  rerender({ cid: "b" });
  // The discarded request's `finally` no longer clears `busy`, so the reset
  // has to -- otherwise campaign B's button is disabled by campaign A's call.
  expect(result.current.busy).toBe(false);

  await act(async () => { releaseFirst(R([{ title: "campaign A" }], ["g1"])); });
  expect(result.current.suggestions).toEqual([]);
  expect(result.current.picks).toEqual([]);
});
