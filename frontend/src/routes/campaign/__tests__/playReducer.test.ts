import { describe, expect, it } from "vitest";

import { initialPlayState, playReducer } from "../playReducer";

describe("playReducer image-ready", () => {
  it("keeps the original post's attachment when a cache hit re-emits the image for another post", () => {
    // The backend reuses one image id across identical seeded requests; each
    // image_ready carries the *requesting* post's post_id. Keying the store
    // by bare image id made the second event move the image off post-a.
    const first = playReducer(initialPlayState, {
      type: "image-ready",
      image: { id: "img-1", url: "/api/files/x.png", post_id: "post-a" },
    });
    const second = playReducer(first, {
      type: "image-ready",
      image: { id: "img-1", url: "/api/files/x.png", post_id: "post-b" },
    });
    const postIds = Object.values(second.images).map((img) => img.post_id);
    expect(postIds).toContain("post-a");
    expect(postIds).toContain("post-b");
  });

  it("stays idempotent for repeated events on the same image and post", () => {
    const image = { id: "img-1", url: "/api/files/x.png", post_id: "post-a" };
    const once = playReducer(initialPlayState, { type: "image-ready", image });
    const twice = playReducer(once, { type: "image-ready", image });
    expect(Object.values(twice.images)).toHaveLength(1);
  });

  it("replaces the record with hydrated images on load, but keeps live ones when hydration is absent", () => {
    const live = playReducer(initialPlayState, {
      type: "image-ready",
      image: { id: "img-live", url: "/api/files/live.png", post_id: "post-a" },
    });
    const hydrated = playReducer(live, {
      type: "loaded",
      pcs: [],
      activePcRef: null,
      scene: null,
      posts: [],
      images: {
        "img-db:post-b": { id: "img-db", url: "/api/files/db.png", post_id: "post-b" },
      },
    });
    expect(Object.values(hydrated.images).map((img) => img.id)).toEqual(["img-db"]);

    // A load without hydration data (e.g. the images fetch failed) keeps
    // whatever live events already delivered.
    const fallback = playReducer(live, {
      type: "loaded",
      pcs: [],
      activePcRef: null,
      scene: null,
      posts: [],
    });
    expect(Object.values(fallback.images).map((img) => img.id)).toEqual(["img-live"]);
  });
});

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
