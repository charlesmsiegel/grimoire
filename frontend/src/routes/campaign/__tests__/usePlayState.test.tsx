import { describe, expect, it } from "vitest";

import type { ApiPost, ApiScene, PCEntry } from "../../../api/campaign";
import { initialPlayState as initial, playReducer as reducer } from "../playReducer";

function makeScene(id: string): ApiScene {
  return {
    id,
    campaign_id: "camp-1",
    ordinal: 1,
    slug: id,
    file_path: `scenes/${id}.md`,
    title: "",
    summary: "",
    running_summary: "",
    key_beats: [],
    tags: [],
    emotional_arc: "",
    present_pc_refs: ["pc-a", "pc-b"],
    present_character_refs: [],
    closed: false,
    closed_at_turn: null,
    location_ref: null,
    in_game_start: null,
    in_game_end: null,
    pov_character_ref: null,
    greeting_id: null,
    post_count: 0,
    threads_introduced: [],
    threads_paid_off: [],
  } as unknown as ApiScene;
}

function makePost(id: string, sceneId: string): ApiPost {
  return {
    id,
    scene_id: sceneId,
    order_in_scene: 0,
    author_kind: "narrator",
    body: id,
    is_player: false,
    created_at: "2026-05-20T00:00:00Z",
    turn_id: `turn-${id}`,
  };
}

const PCS: PCEntry[] = [
  { character_ref: "pc-a", name: "A", owner: "p1", active: true },
  { character_ref: "pc-b", name: "B", owner: "p1", active: false },
];

describe("usePlayState reducer", () => {
  it("preserves in-flight appended posts when a stale refresh resolves", () => {
    // Scene 1 with one post, already loaded.
    const scene1 = makeScene("s1");
    const p1 = makePost("p1", "s1");
    let state = reducer(initial, {
      type: "loaded",
      pcs: PCS,
      activePcRef: "pc-a",
      scene: scene1,
      posts: [p1],
    });

    // Turn completes; refresh() kicks off, capturing posts as of [p1].
    // Before refresh() resolves, a post_appended event for p2 arrives.
    const p2 = makePost("p2", "s1");
    state = reducer(state, { type: "append-post", post: p2 });
    expect(state.posts.map((p) => p.id)).toEqual(["p1", "p2"]);

    // Now refresh()'s stale snapshot lands. Without the fix, it overwrites
    // posts with the pre-p2 snapshot and we lose the freshly-appended post.
    state = reducer(state, {
      type: "loaded",
      pcs: PCS,
      activePcRef: "pc-a",
      scene: scene1,
      posts: [p1],
    });
    expect(state.posts.map((p) => p.id)).toEqual(["p1", "p2"]);
  });

  it("starts with no pending response", () => {
    expect(initial.awaitingResponse).toBe(false);
  });

  it("flags a pending response on turn-pending and clears it on turn-settled", () => {
    let state = reducer(initial, { type: "turn-pending" });
    expect(state.awaitingResponse).toBe(true);
    state = reducer(state, { type: "turn-settled" });
    expect(state.awaitingResponse).toBe(false);
  });

  it("surfaces a turn failure and clears the pending placeholder", () => {
    let state = reducer(
      { ...initial, awaitingResponse: true },
      {
        type: "turn-failed",
        message: "boom",
      },
    );
    expect(state.awaitingResponse).toBe(false);
    expect(state.turnError).toBe("boom");
    // A new turn starting clears the stale error.
    state = reducer(state, { type: "turn-pending" });
    expect(state.turnError).toBeNull();
  });

  it("dismisses a turn error explicitly", () => {
    const state = reducer({ ...initial, turnError: "boom" }, { type: "clear-turn-error" });
    expect(state.turnError).toBeNull();
  });

  it("clears the pending flag once the first token starts streaming", () => {
    const state = reducer(
      { ...initial, awaitingResponse: true },
      { type: "stream-start", turn_id: "t1" },
    );
    expect(state.awaitingResponse).toBe(false);
  });

  it("clears the pending flag when the stream ends", () => {
    const state = reducer(
      { ...initial, awaitingResponse: true },
      { type: "stream-end", turn_id: "t1", post: null },
    );
    expect(state.awaitingResponse).toBe(false);
  });

  it("removes deleted posts and does not let a later refresh re-add them", () => {
    const scene = makeScene("s1");
    const p1 = { ...makePost("p1", "s1"), order_in_scene: 1 };
    const p2 = { ...makePost("p2", "s1"), order_in_scene: 2 };
    const p3 = { ...makePost("p3", "s1"), order_in_scene: 3 };
    let state = reducer(initial, {
      type: "loaded",
      pcs: PCS,
      activePcRef: "pc-a",
      scene,
      posts: [p1, p2, p3],
    });

    // Cascade delete from p2 removes the p2/p3 suffix from view immediately.
    state = reducer(state, { type: "remove-posts", ids: ["p2", "p3"] });
    expect(state.posts.map((p) => p.id)).toEqual(["p1"]);

    // The follow-up refresh snapshot (post-truncation) must NOT re-preserve
    // the removed suffix as "extras" — the bug this guards against.
    state = reducer(state, {
      type: "loaded",
      pcs: PCS,
      activePcRef: "pc-a",
      scene,
      posts: [p1],
    });
    expect(state.posts.map((p) => p.id)).toEqual(["p1"]);
  });

  it("replaces posts wholesale when the loaded scene is different", () => {
    const scene1 = makeScene("s1");
    const scene2 = makeScene("s2");
    const p1 = makePost("p1", "s1");
    const p2 = makePost("p2", "s2");
    let state = reducer(initial, {
      type: "loaded",
      pcs: PCS,
      activePcRef: "pc-a",
      scene: scene1,
      posts: [p1],
    });
    state = reducer(state, {
      type: "loaded",
      pcs: PCS,
      activePcRef: "pc-a",
      scene: scene2,
      posts: [p2],
    });
    expect(state.posts.map((p) => p.id)).toEqual(["p2"]);
  });
});
