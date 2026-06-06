import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

// Count markdown parses so we can prove the post list is not re-rendered (and
// its prose not re-parsed) when an ancestor re-renders — the regression that
// made the Play compose box laggy.
let parseCount = 0;
vi.mock("react-markdown", () => ({
  default: ({ children }: { children: string }) => {
    parseCount += 1;
    return <div>{children}</div>;
  },
}));

import { ScenePane } from "../ScenePane";
import type { ApiPost, PCEntry } from "../../../api/campaign";

const PCS: PCEntry[] = [];

function makePost(id: string): ApiPost {
  return {
    id,
    scene_id: "s1",
    order_in_scene: 0,
    author_kind: "narrator",
    body: id,
    is_player: false,
    created_at: "2026-05-20T00:00:00Z",
    turn_id: `t-${id}`,
  };
}

// Stable references mirror what PlayView passes once draft state is separated:
// the post list props don't change while the user types.
const POSTS = [makePost("p1"), makePost("p2")];
const IMAGES = {};
const noop = () => {};

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
  vi.stubGlobal(
    "IntersectionObserver",
    vi.fn(() => ({ observe: vi.fn(), disconnect: vi.fn(), unobserve: vi.fn() })),
  );
});

afterEach(() => {
  parseCount = 0;
  vi.restoreAllMocks();
});

describe("ScenePane memoization", () => {
  it("does not re-parse post prose when an ancestor re-renders with identical props", () => {
    function Parent() {
      const [n, setN] = useState(0);
      return (
        <div>
          <button onClick={() => setN((v) => v + 1)}>draft {n}</button>
          <ScenePane
            posts={POSTS}
            pcs={PCS}
            streaming={null}
            awaitingResponse={false}
            images={IMAGES}
            hasMorePosts={false}
            onLoadMore={noop}
          />
        </div>
      );
    }

    render(<Parent />);
    expect(parseCount).toBe(POSTS.length);

    // Stand in for typing: the parent re-renders but ScenePane's props are
    // unchanged, so the memoized post list must not re-parse.
    fireEvent.click(screen.getByRole("button"));
    fireEvent.click(screen.getByRole("button"));

    expect(parseCount).toBe(POSTS.length);
  });
});
