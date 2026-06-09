import { renderHook } from "@testing-library/react";
import type { MutableRefObject } from "react";
import { describe, expect, it, vi } from "vitest";

import { initialPlayState, type PlayState } from "../playReducer";

// Capture the handler usePlayStreamEvents registers via useCampaignEvent so we
// can drive WS messages through it without a real socket/context.
const h = vi.hoisted(() => ({ captured: null as ((m: unknown) => void) | null }));
vi.mock("../../../state/useCampaignEvent", () => ({
  useCampaignEvent: (_types: unknown, handler: (m: unknown) => void) => {
    h.captured = handler;
  },
}));

import { usePlayStreamEvents } from "../usePlayStreamEvents";

function render(state: PlayState) {
  const dispatch = vi.fn();
  const refresh = vi.fn().mockResolvedValue(undefined);
  const stateRef = { current: state } as MutableRefObject<PlayState>;
  const pendingRef = { current: null } as MutableRefObject<{
    pcRef: string;
    emotion: string;
  } | null>;
  renderHook(() => usePlayStreamEvents("camp", dispatch, stateRef, pendingRef, refresh));
  return { dispatch, refresh };
}

describe("usePlayStreamEvents — image_ready", () => {
  it("derives the URL from the event's data-root-relative file_path (#582)", () => {
    // The built-in ImageGen service emits `file_path`, not a ready-made
    // `url`; the handler must build one or every real event is dropped.
    const { dispatch } = render(initialPlayState);

    h.captured?.({
      type: "image_ready",
      image_id: "img-1",
      file_path: "campaigns/c1/images/img-1.png",
      post_id: "post-1",
      prompt: "noir alley",
      cached: false,
    });

    expect(dispatch).toHaveBeenCalledWith({
      type: "image-ready",
      image: {
        id: "img-1",
        url: "/api/files/campaigns/c1/images/img-1.png",
        post_id: "post-1",
        prompt: "noir alley",
      },
    });
  });

  it("prefers an explicit url over file_path", () => {
    const { dispatch } = render(initialPlayState);

    h.captured?.({
      type: "image_ready",
      image_id: "img-1",
      url: "https://example.test/img-1.png",
      file_path: "campaigns/c1/images/img-1.png",
    });

    expect(dispatch).toHaveBeenCalledWith({
      type: "image-ready",
      image: {
        id: "img-1",
        url: "https://example.test/img-1.png",
        post_id: undefined,
        prompt: undefined,
      },
    });
  });

  it("drops events carrying neither url nor file_path", () => {
    const { dispatch } = render(initialPlayState);

    h.captured?.({ type: "image_ready", image_id: "img-1" });

    expect(dispatch).not.toHaveBeenCalled();
  });
});

describe("usePlayStreamEvents — alternate_added", () => {
  it("clears the streaming state when a reroll lands (regression)", () => {
    // A per-post reroll streams "token" messages (streaming=true) and finishes
    // with alternate_added, not turn_complete. Without a stream-end the UI was
    // stuck showing "streaming" forever.
    const { dispatch, refresh } = render({
      ...initialPlayState,
      streaming: { turn_id: "t1", text: "regenerated" },
    });

    h.captured?.({ type: "alternate_added" });

    expect(dispatch).toHaveBeenCalledWith({ type: "stream-end", turn_id: "t1", post: null });
    expect(refresh).toHaveBeenCalled();
  });

  it("does not dispatch stream-end when not streaming", () => {
    const { dispatch, refresh } = render({ ...initialPlayState, streaming: null });

    h.captured?.({ type: "alternate_added" });

    expect(dispatch).not.toHaveBeenCalledWith(expect.objectContaining({ type: "stream-end" }));
    expect(refresh).toHaveBeenCalled();
  });
});
