import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import { ScenePane } from "../ScenePane";
import type { ApiPost, PCEntry } from "../../../api/campaign";
import type { PendingTurn } from "../playReducer";

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

function renderPane(props: Partial<Parameters<typeof ScenePane>[0]> = {}) {
  return render(
    <ScenePane
      posts={[]}
      pcs={PCS}
      streaming={null}
      awaitingResponse={false}
      images={{}}
      hasMorePosts={false}
      onLoadMore={() => {}}
      {...props}
    />,
  );
}

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
  vi.stubGlobal(
    "IntersectionObserver",
    vi.fn(() => ({ observe: vi.fn(), disconnect: vi.fn(), unobserve: vi.fn() })),
  );
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ScenePane pending-response marker", () => {
  it("shows a working marker while awaiting the first token", () => {
    renderPane({ awaitingResponse: true });
    expect(screen.getByLabelText(/narrator response, working/i)).toBeInTheDocument();
  });

  it("does not show the empty-scene message while awaiting a response", () => {
    renderPane({ awaitingResponse: true });
    expect(screen.queryByText(/no posts yet/i)).not.toBeInTheDocument();
  });

  it("hides the working marker once tokens start streaming", () => {
    const streaming: PendingTurn = { turn_id: "t1", text: "Once upon" };
    renderPane({ awaitingResponse: true, streaming });
    expect(screen.queryByLabelText(/narrator response, working/i)).not.toBeInTheDocument();
    expect(screen.getByLabelText(/narrator response, streaming/i)).toBeInTheDocument();
  });

  it("shows no marker when idle", () => {
    renderPane({ posts: [makePost("p1")] });
    expect(screen.queryByLabelText(/narrator response, working/i)).not.toBeInTheDocument();
  });
});
