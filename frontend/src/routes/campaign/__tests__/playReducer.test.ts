import { describe, expect, it } from "vitest";

import { initialPlayState, playReducer } from "../playReducer";

describe("playReducer stream-end-if-turn", () => {
  it("clears the streaming indicator when the turn_id matches the live stream", () => {
    const state = { ...initialPlayState, streaming: { turn_id: "t1", text: "partial" } };
    const next = playReducer(state, { type: "stream-end-if-turn", turn_id: "t1" });
    expect(next.streaming).toBeNull();
    expect(next.awaitingResponse).toBe(false);
  });

  it("leaves a concurrent turn's stream untouched on a turn_id mismatch", () => {
    // Regression: a failed per-post reroll must not clear a different live
    // stream (e.g. a normal turn's narrator response).
    const streaming = { turn_id: "normal-turn", text: "live narrator" };
    const state = { ...initialPlayState, streaming };
    const next = playReducer(state, { type: "stream-end-if-turn", turn_id: "reroll-turn" });
    expect(next.streaming).toBe(streaming);
  });

  it("is a no-op when nothing is streaming", () => {
    const next = playReducer(initialPlayState, { type: "stream-end-if-turn", turn_id: "t1" });
    expect(next).toBe(initialPlayState);
  });
});
