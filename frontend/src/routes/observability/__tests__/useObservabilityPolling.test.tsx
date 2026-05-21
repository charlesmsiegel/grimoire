import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useObservabilityPolling } from "../useObservabilityPolling";

function setVisibility(state: "visible" | "hidden") {
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    get: () => state,
  });
}

describe("useObservabilityPolling", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    setVisibility("visible");
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("calls the callback immediately and on every interval while visible", async () => {
    const cb = vi.fn().mockResolvedValue(undefined);
    renderHook(() => useObservabilityPolling(cb, 1000));
    expect(cb).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(cb).toHaveBeenCalledTimes(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(cb).toHaveBeenCalledTimes(3);
  });

  it("pauses polling when the document is hidden", async () => {
    const cb = vi.fn().mockResolvedValue(undefined);
    renderHook(() => useObservabilityPolling(cb, 1000));
    expect(cb).toHaveBeenCalledTimes(1);

    setVisibility("hidden");
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(cb).toHaveBeenCalledTimes(1);
  });

  it("resumes when visibility returns", async () => {
    const cb = vi.fn().mockResolvedValue(undefined);
    renderHook(() => useObservabilityPolling(cb, 1000));
    expect(cb).toHaveBeenCalledTimes(1);

    setVisibility("hidden");
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(cb).toHaveBeenCalledTimes(1);

    setVisibility("visible");
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(cb).toHaveBeenCalledTimes(2);
  });
});
