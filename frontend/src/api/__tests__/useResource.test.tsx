/**
 * Regression for the BUGS.md HIGH item: useResource took a deps
 * array with exhaustive-deps disabled, so a caller who omitted a dep
 * could ship stale data forever. The hooks now drive off fetcher
 * identity, forcing callers to wrap in useCallback (exhaustive-deps
 * lint stays on) — so a missing dep produces a fresh fetcher every
 * render and the bug becomes visible (extra fetches) instead of silent.
 */

import { act, render, waitFor } from "@testing-library/react";
import { useCallback, useState } from "react";
import { describe, expect, it } from "vitest";

import { useResource } from "../useResource";

describe("useResource fetcher identity", () => {
  it("re-fetches when fetcher identity changes", async () => {
    let calls = 0;
    let setX!: (n: number) => void;

    function Probe() {
      const [x, _setX] = useState(1);
      setX = _setX;
      const fetcher = useCallback(async () => {
        calls += 1;
        return x;
      }, [x]);
      const state = useResource(fetcher);
      return <span>{state.data !== null ? `value:${state.data}` : "loading"}</span>;
    }

    const { container } = render(<Probe />);
    await waitFor(() => expect(container.textContent).toBe("value:1"));
    expect(calls).toBe(1);

    await act(async () => setX(2));
    await waitFor(() => expect(container.textContent).toBe("value:2"));
    expect(calls).toBe(2);
  });

  it("does not re-fetch when the memoized fetcher is stable", async () => {
    let calls = 0;
    let bumpUnrelated!: () => void;

    function Probe() {
      const [, setUnrelated] = useState(0);
      bumpUnrelated = () => setUnrelated((n) => n + 1);
      const fetcher = useCallback(async () => {
        calls += 1;
        return "stable";
      }, []);
      const state = useResource(fetcher);
      return <span>{state.data ?? "loading"}</span>;
    }

    const { container } = render(<Probe />);
    await waitFor(() => expect(container.textContent).toBe("stable"));
    expect(calls).toBe(1);

    await act(async () => bumpUnrelated());
    await act(async () => bumpUnrelated());
    expect(calls).toBe(1);
  });
});

describe("useResource", () => {
  it("reload() keeps prior data visible without a loading flash", async () => {
    let resolveLoad: (v: number) => void = () => {};
    let calls = 0;
    let reloadFn!: () => void;

    function Probe() {
      const loader = useCallback(
        () =>
          new Promise<number>((resolve) => {
            calls += 1;
            if (calls === 1) resolve(1);
            else resolveLoad = resolve;
          }),
        [],
      );
      const { data, loading, reload } = useResource(loader);
      reloadFn = reload;
      return (
        <span>
          {loading ? "L" : "_"}|{data ?? "null"}
        </span>
      );
    }

    const { container } = render(<Probe />);
    await waitFor(() => expect(container.textContent).toBe("_|1"));

    await act(async () => reloadFn());
    // Same resource: old data stays visible, loading must NOT flip back.
    expect(container.textContent).toBe("_|1");

    await act(async () => resolveLoad(2));
    await waitFor(() => expect(container.textContent).toBe("_|2"));
  });

  it("a loader identity change resets to loading (different resource)", async () => {
    let resolveLoad: (v: number) => void = () => {};
    let calls = 0;
    let setX!: (n: number) => void;

    function Probe() {
      const [x, _setX] = useState(1);
      setX = _setX;
      const loader = useCallback(
        () =>
          new Promise<number>((resolve) => {
            calls += 1;
            if (calls === 1) resolve(x);
            else resolveLoad = resolve;
          }),
        [x],
      );
      const { data, loading } = useResource(loader);
      return (
        <span>
          {loading ? "L" : "_"}|{data ?? "null"}
        </span>
      );
    }

    const { container } = render(<Probe />);
    await waitFor(() => expect(container.textContent).toBe("_|1"));

    await act(async () => setX(2));
    // Different resource: the previous query's data must NOT remain visible
    // (a CompositionEditor seeded from it would save campaign A's data to B).
    await waitFor(() => expect(container.textContent).toBe("L|null"));

    await act(async () => resolveLoad(2));
    await waitFor(() => expect(container.textContent).toBe("_|2"));
  });

  it("re-loads when fetcher identity changes", async () => {
    let calls = 0;
    let setX!: (n: number) => void;

    function Probe() {
      const [x, _setX] = useState(1);
      setX = _setX;
      const loader = useCallback(async () => {
        calls += 1;
        return x;
      }, [x]);
      const { data, loading } = useResource(loader);
      return <span>{loading ? "loading" : `value:${data}`}</span>;
    }

    const { container } = render(<Probe />);
    await waitFor(() => expect(container.textContent).toBe("value:1"));
    expect(calls).toBe(1);

    await act(async () => setX(2));
    await waitFor(() => expect(container.textContent).toBe("value:2"));
    expect(calls).toBe(2);
  });
});
