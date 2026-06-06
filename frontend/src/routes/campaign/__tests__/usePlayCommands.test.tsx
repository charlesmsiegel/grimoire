import { useRef } from "react";
import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { initialPlayState } from "../playReducer";
import { usePlayCommands } from "../usePlayCommands";

// The commands object feeds usePlayState's `play` useMemo. If it is a fresh
// reference on every render, `play` churns on every compose keystroke and the
// ScenePane memoization (which keys handlers off play.dispatch / play.refresh)
// is defeated. Pin that it stays referentially stable when inputs don't change.
describe("usePlayCommands identity", () => {
  it("returns a stable object across re-renders with unchanged inputs", () => {
    const dispatch = () => {};
    const refresh = async () => {};

    const { result, rerender } = renderHook(() => {
      // stateRef / pendingExpressionRef must be the SAME ref each render, just
      // as usePlayState holds them via useRef.
      const stateRef = useRef(initialPlayState);
      const pendingExpressionRef = useRef<{ pcRef: string; emotion: string } | null>(null);
      return usePlayCommands("camp-1", dispatch, stateRef, pendingExpressionRef, refresh);
    });

    const first = result.current;
    rerender();
    expect(result.current).toBe(first);
  });
});
