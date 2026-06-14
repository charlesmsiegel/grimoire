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
  it("derives the campaign-scoped file URL from the image id (#582)", () => {
    // The event carries no ready-made `url`; the handler builds the
    // campaign-scoped file endpoint from the id — the same scheme the gallery
    // uses — so the image renders under its post.
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
        url: "/api/campaigns/camp/images/img-1/file",
        post_id: "post-1",
        prompt: "noir alley",
      },
    });
  });

  it("derives the URL from the id alone, without file_path or url in the event", () => {
    const { dispatch } = render(initialPlayState);

    h.captured?.({ type: "image_ready", image_id: "img-1" });

    expect(dispatch).toHaveBeenCalledWith({
      type: "image-ready",
      image: {
        id: "img-1",
        url: "/api/campaigns/camp/images/img-1/file",
        post_id: undefined,
        prompt: undefined,
      },
    });
  });

  it("drops events carrying no image_id", () => {
    const { dispatch } = render(initialPlayState);

    h.captured?.({ type: "image_ready", post_id: "post-1" });

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
