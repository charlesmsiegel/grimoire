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

test("a failure empties the suggestions and reports the error", async () => {
  (api.sceneSuggestions as any).mockRejectedValue({ detail: "no key" });
  const { result } = renderHook(() => useSceneSuggestions("c", "s1", true, false));
  await waitFor(() => expect(result.current.error).toBe("no key"));
  expect(result.current.suggestions).toEqual([]);
});
