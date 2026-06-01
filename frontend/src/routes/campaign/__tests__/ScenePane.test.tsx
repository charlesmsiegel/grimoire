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

describe("ScenePane delete wiring", () => {
  it("passes a delete button and forwards onPostDeleted", async () => {
    const { campaignApi } = await import("../../../api/campaign");
    const spy = vi.spyOn(campaignApi, "deletePost").mockResolvedValue({
      deleted_post_ids: ["p1"],
      reversed_turn_ids: [],
      requeued_review_ids: [],
      warnings: [],
    });
    const onPostDeleted = vi.fn();
    const scene = {
      id: "s1",
      campaign_id: "c1",
      post_count: 1,
      closed: false,
      present_character_refs: [],
    } as unknown as Parameters<typeof ScenePane>[0]["scene"];
    renderPane({
      posts: [{ ...makePost("p1"), is_player: true, author_kind: "pc" }],
      campaignId: "c1",
      scene,
      onPostDeleted,
    });
    const { fireEvent, waitFor } = await import("@testing-library/react");
    fireEvent.click(screen.getByRole("button", { name: "Delete post" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith("c1", "s1", "p1"));
    await waitFor(() => expect(onPostDeleted).toHaveBeenCalled());
  });

  it("counts following posts with 1-based order (no off-by-one)", async () => {
    const scene = {
      id: "s1",
      campaign_id: "c1",
      post_count: 3,
      closed: false,
      present_character_refs: [],
    } as unknown as Parameters<typeof ScenePane>[0]["scene"];
    // First post in a 3-post scene → deleting it removes 2 following posts.
    const first = {
      ...makePost("p1"),
      order_in_scene: 1,
      is_player: true,
      author_kind: "pc" as const,
    };
    renderPane({ posts: [first], campaignId: "c1", scene });
    const { fireEvent } = await import("@testing-library/react");
    fireEvent.click(screen.getByRole("button", { name: "Delete post" }));
    expect(screen.getByText(/2 following posts/i)).toBeInTheDocument();
  });
});

describe("ScenePane cost attribution", () => {
  it("renders the turn cost once, on the user post, for a split-into-two turn", async () => {
    const { observabilityApi } = await import("../../../api/observability");
    const spy = vi
      .spyOn(observabilityApi, "turnCosts")
      .mockResolvedValue([
        { task: "primary", total_usd: 0.02, input_tokens: 100, output_tokens: 100, call_count: 1 },
      ]);
    // IntersectionObserver mock that fires immediately so CostLabel fetches.
    vi.stubGlobal(
      "IntersectionObserver",
      vi.fn((cb: (entries: Array<{ isIntersecting: boolean }>) => void) => {
        setTimeout(() => cb([{ isIntersecting: true }]), 0);
        return { observe: vi.fn(), disconnect: vi.fn(), unobserve: vi.fn() };
      }),
    );
    const user = { ...makePost("u1"), is_player: true, author_kind: "pc" as const, turn_id: "tu" };
    const m1 = { ...makePost("m1"), turn_id: "T1" };
    const m2 = { ...makePost("m2"), turn_id: "T1" };
    renderPane({ posts: [user, m1, m2], campaignId: "c1" });
    const { waitFor } = await import("@testing-library/react");
    await waitFor(() => expect(screen.getAllByLabelText("Turn cost")).toHaveLength(1));
    expect(spy).toHaveBeenCalledWith("T1");
  });
});
