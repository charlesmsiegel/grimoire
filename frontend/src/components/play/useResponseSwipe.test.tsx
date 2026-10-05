import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import type { ResponseSwipe } from "../../api/types";
import { useResponseSwipe } from "./useResponseSwipe";

function mk(ids: string[], active: number | null): ResponseSwipe {
  return {
    active, variants: ids.map((id) => ({ id, status: id === "half" ? "incomplete" : "complete" })),
    settings: null, resume_settings: null, can_reroll: true, editable: true, round_open: false,
  };
}
const props = (over: Partial<{ rid: string | null; content: string | null; windowToken: unknown }> = {}) =>
  ({ cid: "realm", sid: "saltmarch", rid: "r1", content: "Mara nods.", windowToken: 1, ...over });

// A read the test settles by hand, so "outstanding" can be observed.
function deferred() {
  let resolve!: (v: ResponseSwipe | undefined) => void;
  const promise = new Promise<ResponseSwipe | undefined>((r) => { resolve = r; });
  return { promise, resolve };
}

describe("useResponseSwipe", () => {
  afterEach(() => vi.restoreAllMocks());

  it("reads complete variants and the active position", async () => {
    vi.spyOn(api, "getResponseSwipe").mockResolvedValue(mk(["a", "half", "b"], 2));
    const { result } = renderHook((p) => useResponseSwipe(p), { initialProps: props() });
    await waitFor(() => expect(result.current.swipe).not.toBeNull());
    expect(result.current.complete).toEqual(["a", "b"]);
    expect(result.current.position).toBe(1);
    expect(result.current.pending).toBe(false);
  });

  it("is pending after a content change until the refetch lands, keeping the old swipe", async () => {
    const spy = vi.spyOn(api, "getResponseSwipe").mockResolvedValueOnce(mk(["a", "b"], 1));
    const { result, rerender } = renderHook((p) => useResponseSwipe(p), { initialProps: props() });
    await waitFor(() => expect(result.current.swipe).not.toBeNull());
    expect(result.current.pending).toBe(false);
    const before = result.current.swipe;

    const d = deferred();
    spy.mockReturnValueOnce(d.promise as Promise<ResponseSwipe>);
    rerender(props({ content: "Mara shrugs." }));
    expect(result.current.pending).toBe(true);
    expect(result.current.swipe).toBe(before);

    await act(async () => { d.resolve(mk(["a", "b", "c"], 2)); });
    expect(result.current.pending).toBe(false);
    expect(result.current.complete).toEqual(["a", "b", "c"]);
  });

  it("is pending after refresh() and after a windowToken change", async () => {
    const spy = vi.spyOn(api, "getResponseSwipe").mockResolvedValue(mk(["a", "b"], 0));
    const { result, rerender } = renderHook((p) => useResponseSwipe(p), { initialProps: props() });
    await waitFor(() => expect(result.current.swipe).not.toBeNull());

    let d = deferred();
    spy.mockReturnValueOnce(d.promise as Promise<ResponseSwipe>);
    act(() => result.current.refresh());
    expect(result.current.pending).toBe(true);
    await act(async () => { d.resolve(mk(["a", "b"], 0)); });
    expect(result.current.pending).toBe(false);

    d = deferred();
    spy.mockReturnValueOnce(d.promise as Promise<ResponseSwipe>);
    rerender(props({ windowToken: 2 }));
    expect(result.current.pending).toBe(true);
    await act(async () => { d.resolve(mk(["a", "b"], 0)); });
    expect(result.current.pending).toBe(false);
  });

  it("keeps the swipe object (and the returned state) across an identical refetch", async () => {
    const spy = vi.spyOn(api, "getResponseSwipe").mockImplementation(async () => mk(["a", "b"], 1));
    const { result, rerender } = renderHook((p) => useResponseSwipe(p), { initialProps: props() });
    await waitFor(() => expect(result.current.swipe).not.toBeNull());
    const first = result.current;

    rerender(props({ content: "Mara shrugs." }));
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(result.current.pending).toBe(false));
    expect(result.current.swipe).toBe(first.swipe);
    expect(result.current.complete).toBe(first.complete);
    expect(result.current.refresh).toBe(first.refresh);
  });

  it("treats an undefined resolution as an error: swipe null, not pending", async () => {
    const spy = vi.spyOn(api, "getResponseSwipe").mockResolvedValueOnce(mk(["a", "b"], 0));
    const { result, rerender } = renderHook((p) => useResponseSwipe(p), { initialProps: props() });
    await waitFor(() => expect(result.current.swipe).not.toBeNull());
    spy.mockResolvedValueOnce(undefined as never);
    rerender(props({ content: "Mara shrugs." }));
    await waitFor(() => expect(result.current.swipe).toBeNull());
    expect(result.current.pending).toBe(false);
    expect(result.current.complete).toEqual([]);
    expect(result.current.position).toBeNull();
  });

  it("treats a rejection as an error", async () => {
    vi.spyOn(api, "getResponseSwipe").mockRejectedValue(new Error("boom"));
    const { result } = renderHook((p) => useResponseSwipe(p), { initialProps: props() });
    await waitFor(() => expect(result.current.pending).toBe(false));
    expect(result.current.swipe).toBeNull();
  });

  it("makes no request for a null rid or sid, and is never pending", async () => {
    const spy = vi.spyOn(api, "getResponseSwipe").mockResolvedValue(mk(["a", "b"], 0));
    const { result, rerender } = renderHook((p) => useResponseSwipe(p), { initialProps: props() });
    await waitFor(() => expect(result.current.swipe).not.toBeNull());
    spy.mockClear();
    rerender(props({ rid: null, content: null }));
    expect(result.current.swipe).toBeNull();
    expect(result.current.pending).toBe(false);
    expect(spy).not.toHaveBeenCalled();
  });

  it("hides the previous response's swipe while the next one's read is out", async () => {
    const spy = vi.spyOn(api, "getResponseSwipe").mockResolvedValueOnce(mk(["a", "b"], 0));
    const { result, rerender } = renderHook((p) => useResponseSwipe(p), { initialProps: props() });
    await waitFor(() => expect(result.current.swipe).not.toBeNull());
    spy.mockReturnValueOnce(new Promise(() => {}) as never);
    rerender(props({ rid: "r2" }));
    expect(result.current.swipe).toBeNull();
    expect(result.current.pending).toBe(true);
  });

  it("drops a response whose request was retired", async () => {
    const spy = vi.spyOn(api, "getResponseSwipe");
    const stale = deferred();
    spy.mockReturnValueOnce(stale.promise as Promise<ResponseSwipe>);
    spy.mockResolvedValueOnce(mk(["z"], 0));
    const { result, rerender } = renderHook((p) => useResponseSwipe(p), { initialProps: props() });
    rerender(props({ rid: "r2" }));
    await waitFor(() => expect(result.current.complete).toEqual(["z"]));
    await act(async () => { stale.resolve(mk(["old"], 0)); });
    expect(result.current.complete).toEqual(["z"]);
    expect(result.current.pending).toBe(false);
  });
});
