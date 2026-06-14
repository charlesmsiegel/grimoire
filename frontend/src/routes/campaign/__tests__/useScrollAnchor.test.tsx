import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";

import type { ApiPost } from "../../../api/campaign";
import { useScrollAnchor } from "../useScrollAnchor";

function makePost(id: string, isPlayer = false): ApiPost {
  return {
    id,
    scene_id: "s1",
    order_in_scene: 0,
    author_kind: isPlayer ? "pc" : "narrator",
    body: id,
    is_player: isPlayer,
    created_at: "2026-05-20T00:00:00Z",
    turn_id: `t-${id}`,
  };
}

type Props = Parameters<typeof useScrollAnchor>[0];

// Minimal DOM mirroring ScenePane: a scrolling section, an optional top
// sentinel, one element per post (carrying data-post-id), and a bottom marker.
function Harness(props: Props) {
  const { paneRef, topSentinelRef, bottomRef } = useScrollAnchor(props);
  return (
    <section ref={paneRef}>
      {props.hasMorePosts && <div data-testid="sentinel" ref={topSentinelRef} />}
      {props.posts.map((p) => (
        <div key={p.id} data-post-id={p.id}>
          {p.id}
        </div>
      ))}
      <div data-testid="bottom" ref={bottomRef} />
    </section>
  );
}

function renderHarness(props: Partial<Props> = {}) {
  const merged: Props = {
    posts: [],
    sceneId: "s1",
    turnActive: false,
    hasMorePosts: false,
    onLoadMore: () => {},
    ...props,
  };
  const view = render(<Harness {...merged} />);
  return {
    ...view,
    update: (next: Partial<Props>) => view.rerender(<Harness {...{ ...merged, ...next }} />),
  };
}

// Scroll calls that anchor a post to the top use block:"start"; the scene-load
// jump to the most recent post uses block:"end".
const scrollCalls = (block: "start" | "end") =>
  (Element.prototype.scrollIntoView as ReturnType<typeof vi.fn>).mock.calls.filter(
    ([opts]) => (opts as ScrollIntoViewOptions | undefined)?.block === block,
  );

// A controllable IntersectionObserver: fire the captured callback on demand.
let observerCallback: ((entries: Array<{ isIntersecting: boolean }>) => void) | null = null;
let observedCount = 0;

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
  observerCallback = null;
  observedCount = 0;
  vi.stubGlobal(
    "IntersectionObserver",
    vi.fn((cb: (entries: Array<{ isIntersecting: boolean }>) => void) => {
      observerCallback = cb;
      return {
        observe: vi.fn(() => {
          observedCount += 1;
        }),
        disconnect: vi.fn(),
        unobserve: vi.fn(),
      };
    }),
  );
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useScrollAnchor anchoring", () => {
  const user1 = makePost("u1", true);
  const model1 = makePost("m1");

  it("jumps to the most recent post on first populated render of a scene", async () => {
    renderHarness({ posts: [user1, model1] });
    const { waitFor } = await import("@testing-library/react");
    // block:"end" is the scene-load jump to the bottom marker.
    await waitFor(() => expect(scrollCalls("end").length).toBeGreaterThan(0));
    expect(scrollCalls("start")).toHaveLength(0);
  });

  it("anchors a newly submitted user post to the top of the window", async () => {
    const { update } = renderHarness({ posts: [user1, model1] });
    const user2 = makePost("u2", true);
    update({ posts: [user1, model1, user2] });
    const { waitFor } = await import("@testing-library/react");
    await waitFor(() => expect(scrollCalls("start").length).toBeGreaterThan(0));
  });

  it("does not anchor when no new user post is added (e.g. streaming/regenerate)", async () => {
    const { update } = renderHarness({ posts: [user1, model1] });
    // Same posts, a turn becomes active — no count growth, so no anchor.
    update({ posts: [user1, model1], turnActive: true });
    await new Promise((r) => setTimeout(r, 20));
    expect(scrollCalls("start")).toHaveLength(0);
  });

  it("re-anchors the latest user post to the top when the turn completes", async () => {
    const { update } = renderHarness({ posts: [user1, model1], turnActive: true });
    // Completion: turn goes from active back to idle with the same posts.
    update({ posts: [user1, model1], turnActive: false });
    const { waitFor } = await import("@testing-library/react");
    await waitFor(() => expect(scrollCalls("start").length).toBeGreaterThan(0));
  });

  it("re-jumps to the bottom when a different scene loads", async () => {
    const { update } = renderHarness({ posts: [user1, model1], sceneId: "s1" });
    const { waitFor } = await import("@testing-library/react");
    await waitFor(() => expect(scrollCalls("end").length).toBe(1));
    const other = makePost("u9", true);
    update({ posts: [other], sceneId: "s2" });
    await waitFor(() => expect(scrollCalls("end").length).toBe(2));
  });
});

describe("useScrollAnchor load-more", () => {
  const posts = [makePost("u1", true), makePost("m1")];

  it("observes the sentinel and calls onLoadMore when it intersects", () => {
    const onLoadMore = vi.fn();
    renderHarness({ posts, hasMorePosts: true, onLoadMore });
    expect(observedCount).toBe(1);
    observerCallback?.([{ isIntersecting: true }]);
    expect(onLoadMore).toHaveBeenCalledTimes(1);
  });

  it("debounces repeated intersections into a single load", () => {
    const onLoadMore = vi.fn();
    renderHarness({ posts, hasMorePosts: true, onLoadMore });
    observerCallback?.([{ isIntersecting: true }]);
    observerCallback?.([{ isIntersecting: true }]);
    expect(onLoadMore).toHaveBeenCalledTimes(1);
  });

  it("does not load more when nothing is intersecting", () => {
    const onLoadMore = vi.fn();
    renderHarness({ posts, hasMorePosts: true, onLoadMore });
    observerCallback?.([{ isIntersecting: false }]);
    expect(onLoadMore).not.toHaveBeenCalled();
  });

  it("does not observe a sentinel when there are no more posts", () => {
    renderHarness({ posts, hasMorePosts: false, onLoadMore: vi.fn() });
    expect(observedCount).toBe(0);
  });
});
