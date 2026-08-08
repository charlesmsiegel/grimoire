import { renderHook, waitFor, act } from "@testing-library/react";
import { useSceneSuggestions } from "./useSceneSuggestions";

vi.mock("../api/client", () => ({ api: { sceneSuggestions: vi.fn() } }));
import { api } from "../api/client";

const R = (suggestions: any[], picks: string[] = [], next_date = "") =>
  ({ suggestions, greeting_picks: picks, next_date });

beforeEach(() => vi.clearAllMocks());

test("the first fetch ranks; a refresh does not, and keeps the picks", async () => {
  (api.sceneSuggestions as any).mockResolvedValue(R([{ title: "A" }], ["g1"], "2026-01-01"));
  const { result } = renderHook(() => useSceneSuggestions("c", "s1", true, false));
  await waitFor(() => expect(result.current.picks).toEqual(["g1"]));
  expect(api.sceneSuggestions).toHaveBeenCalledWith("c", "s1", false, "", true);

  (api.sceneSuggestions as any).mockResolvedValue(R([{ title: "B" }], [], ""));
  act(() => result.current.refresh("something at sea"));
  await waitFor(() => expect(result.current.suggestions).toEqual([{ title: "B" }]));
  expect(api.sceneSuggestions).toHaveBeenLastCalledWith("c", "s1", false, "something at sea", false);
  expect(result.current.picks).toEqual(["g1"]);      // not clobbered by the empty list
  expect(result.current.nextDate).toBe("2026-01-01"); // not cleared by an empty one
});

test("a stale response that resolves after a newer one is discarded", async () => {
  let releaseFirst: (v: any) => void = () => {};
  (api.sceneSuggestions as any).mockReturnValueOnce(new Promise((r) => { releaseFirst = r; }));
  const { result } = renderHook(() => useSceneSuggestions("c", "s1", true, false));

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

test("without a connection nothing is fetched and the lists are empty, not pending", () => {
  const { result } = renderHook(() => useSceneSuggestions("c", "s1", false, false));
  expect(api.sceneSuggestions).not.toHaveBeenCalled();
  expect(result.current.suggestions).toEqual([]);
  expect(result.current.picks).toEqual([]);
});

test("refresh is a no-op while not ready", () => {
  const { result } = renderHook(() => useSceneSuggestions("c", "s1", false, false));
  act(() => result.current.refresh("x"));
  expect(api.sceneSuggestions).not.toHaveBeenCalled();
});

test("ready flipping true on a mounted hook restores the pending state and fetches", () => {
  // Never resolves -- this test inspects the state right after the flip,
  // before any reply could land.
  (api.sceneSuggestions as any).mockReturnValue(new Promise(() => {}));
  const { result, rerender } = renderHook(
    ({ ready }) => useSceneSuggestions("c", "s1", ready, false),
    { initialProps: { ready: false } },
  );
  expect(result.current.suggestions).toEqual([]);
  expect(result.current.picks).toEqual([]);
  expect(api.sceneSuggestions).not.toHaveBeenCalled();

  rerender({ ready: true });
  // Pending again, not left at "nothing to offer" -- a fetch is genuinely
  // running now, and the picker must show "Generating...", not a blank list.
  expect(result.current.suggestions).toBeNull();
  expect(result.current.picks).toBeNull();
  expect(api.sceneSuggestions).toHaveBeenCalledTimes(1);
});

test("mounting with ready already true is unaffected by the ready-reset effect", () => {
  (api.sceneSuggestions as any).mockReturnValue(new Promise(() => {}));
  const { result } = renderHook(() => useSceneSuggestions("c", "s1", true, false));
  expect(result.current.suggestions).toBeNull();
  expect(result.current.picks).toBeNull();
  expect(api.sceneSuggestions).toHaveBeenCalledTimes(1);
});

test("a refresh does not reset existing suggestions to pending while it is in flight", async () => {
  (api.sceneSuggestions as any).mockResolvedValueOnce(R([{ title: "A" }], ["g1"]));
  const { result } = renderHook(() => useSceneSuggestions("c", "s1", true, false));
  await waitFor(() => expect(result.current.suggestions).toEqual([{ title: "A" }]));

  let releaseRefresh: (v: any) => void = () => {};
  (api.sceneSuggestions as any).mockReturnValueOnce(new Promise((r) => { releaseRefresh = r; }));
  act(() => result.current.refresh("x"));
  // `ready` never changes across a refresh, so the ready-reset effect must not
  // fire here: the existing cards should stay on screen while the new ones load.
  expect(result.current.suggestions).toEqual([{ title: "A" }]);

  await act(async () => { releaseRefresh(R([{ title: "B" }])); });
  expect(result.current.suggestions).toEqual([{ title: "B" }]);
});

test("a failure empties the suggestions and reports the error", async () => {
  (api.sceneSuggestions as any).mockRejectedValue({ detail: "no key" });
  const { result } = renderHook(() => useSceneSuggestions("c", "s1", true, false));
  await waitFor(() => expect(result.current.error).toBe("no key"));
  expect(result.current.suggestions).toEqual([]);
});
