import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { PostItem } from "../PostItem";
import type { ApiAlternate, ApiPost, PCEntry } from "../../../api/campaign";
import { campaignApi } from "../../../api/campaign";
import { observabilityApi } from "../../../api/observability";
import { auxiliaryApi } from "../../../api/auxiliary";

function makePost(overrides: Partial<ApiPost> = {}): ApiPost {
  return {
    id: "p1",
    scene_id: "s1",
    order_in_scene: 0,
    author_kind: "narrator",
    body: "primary body",
    is_player: false,
    created_at: "2026-05-19T12:00:00Z",
    turn_id: "t1",
    ...overrides,
  };
}

function alt(id: string, isPrimary = false, pinned = false): ApiAlternate {
  return {
    id,
    post_id: "p1",
    text: `text ${id}`,
    delta_set_id: `ds_${id}`,
    author_kind: "narrator",
    pinned,
    is_primary: isPrimary,
  };
}

const PCS: PCEntry[] = [];

let intersectionCallbacks: ((entries: Array<{ isIntersecting: boolean }>) => void)[] = [];

function mockIntersectionObserver(autoTrigger = false) {
  intersectionCallbacks = [];
  const mock = vi.fn((cb: (entries: Array<{ isIntersecting: boolean }>) => void) => {
    intersectionCallbacks.push(cb);
    if (autoTrigger) {
      setTimeout(() => cb([{ isIntersecting: true }]), 0);
    }
    return { observe: vi.fn(), disconnect: vi.fn(), unobserve: vi.fn() };
  });
  vi.stubGlobal("IntersectionObserver", mock);
  return mock;
}

function triggerIntersection() {
  for (const cb of intersectionCallbacks) {
    cb([{ isIntersecting: true }]);
  }
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("PostItem chevron strip", () => {
  beforeEach(() => mockIntersectionObserver());

  it("hides the strip when fewer than 2 alternates", () => {
    render(<PostItem post={makePost()} pcs={PCS} images={[]} isLatestModelPost campaignId="c1" />);
    expect(screen.queryByRole("group", { name: "Alternates" })).toBeNull();
  });

  it("renders chevrons and count when there are 2+ alternates", () => {
    const post = makePost({
      alternates: [alt("a1", true), alt("a2")],
      primary_alternate_id: "a1",
    });
    render(<PostItem post={post} pcs={PCS} images={[]} isLatestModelPost campaignId="c1" />);
    expect(screen.getByRole("group", { name: "Alternates" })).toBeInTheDocument();
    expect(screen.getByText("1 of 2")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Previous alternate" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Next alternate" })).toBeEnabled();
  });

  it("clicking next calls switchPrimaryAlternate with the next alt's id", async () => {
    const spy = vi
      .spyOn(campaignApi, "switchPrimaryAlternate")
      .mockResolvedValue({ unchanged: false, post_id: "p1", from: "a1", to: "a2" });
    const post = makePost({
      alternates: [alt("a1", true), alt("a2")],
      primary_alternate_id: "a1",
    });
    render(<PostItem post={post} pcs={PCS} images={[]} isLatestModelPost campaignId="c1" />);
    fireEvent.click(screen.getByRole("button", { name: "Next alternate" }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith("c1", "s1", "p1", "a2"));
    expect(await screen.findByText("2 of 2")).toBeInTheDocument();
  });

  it("disables chevrons when not the latest model post and shows the hint", () => {
    const post = makePost({
      alternates: [alt("a1", true), alt("a2")],
      primary_alternate_id: "a1",
    });
    render(
      <PostItem post={post} pcs={PCS} images={[]} isLatestModelPost={false} campaignId="c1" />,
    );
    expect(screen.getByRole("button", { name: "Previous alternate" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Next alternate" })).toBeDisabled();
    expect(screen.getByRole("note")).toHaveTextContent(/latest post/i);
  });

  it("pin button toggles via pinAlternate", async () => {
    const spy = vi
      .spyOn(campaignApi, "pinAlternate")
      .mockResolvedValue({ post_id: "p1", alternate_id: "a1", pinned: true });
    const post = makePost({
      alternates: [alt("a1", true, false), alt("a2")],
      primary_alternate_id: "a1",
    });
    render(<PostItem post={post} pcs={PCS} images={[]} isLatestModelPost campaignId="c1" />);
    fireEvent.click(screen.getByRole("button", { name: "Pin alternate" }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith("c1", "s1", "p1", "a1", true));
  });
});

describe("PostItem action buttons", () => {
  beforeEach(() => mockIntersectionObserver());

  it("shows Edit, Regenerate, Guided regenerate for latest model post", () => {
    render(<PostItem post={makePost()} pcs={PCS} images={[]} isLatestModelPost campaignId="c1" />);
    expect(screen.getByRole("button", { name: "Edit post" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Regenerate post" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Guided regenerate" })).toBeInTheDocument();
  });

  it("hides Regenerate and Guided regenerate when not latest model post", () => {
    render(
      <PostItem
        post={makePost()}
        pcs={PCS}
        images={[]}
        isLatestModelPost={false}
        campaignId="c1"
      />,
    );
    expect(screen.getByRole("button", { name: "Edit post" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Regenerate post" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Guided regenerate" })).toBeNull();
  });

  it("shows Continue button when post has an author ref", () => {
    const post = makePost({ author_npc_ref: "guard-captain" });
    render(<PostItem post={post} pcs={PCS} images={[]} isLatestModelPost campaignId="c1" />);
    expect(screen.getByRole("button", { name: "Continue" })).toBeInTheDocument();
  });

  it("shows Continue button via presentCharacterRefs when post has no author ref", () => {
    render(
      <PostItem
        post={makePost()}
        pcs={PCS}
        images={[]}
        isLatestModelPost
        campaignId="c1"
        presentCharacterRefs={["guard-captain"]}
      />,
    );
    expect(screen.getByRole("button", { name: "Continue" })).toBeInTheDocument();
  });

  it("hides Continue button when post has no author ref and no present characters", () => {
    render(<PostItem post={makePost()} pcs={PCS} images={[]} isLatestModelPost campaignId="c1" />);
    expect(screen.queryByRole("button", { name: "Continue" })).toBeNull();
  });

  it("shows Translate button", () => {
    render(<PostItem post={makePost()} pcs={PCS} images={[]} campaignId="c1" />);
    expect(screen.getByRole("button", { name: "Translate this post" })).toBeInTheDocument();
  });

  it("does not show retcon, rewrite, or what-would-x-say buttons", () => {
    render(<PostItem post={makePost()} pcs={PCS} images={[]} isLatestModelPost campaignId="c1" />);
    expect(screen.queryByRole("button", { name: /retcon/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /rewrite/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /what.*say/i })).toBeNull();
  });
});

describe("PostItem regenerate", () => {
  beforeEach(() => mockIntersectionObserver());

  it("regenerate button calls regeneratePost without steering_hint", async () => {
    const spy = vi
      .spyOn(campaignApi, "regeneratePost")
      .mockResolvedValue({ post_id: "p1", new_alternate_id: "a_new", delta_set_id: "ds_new" });
    render(<PostItem post={makePost()} pcs={PCS} images={[]} isLatestModelPost campaignId="c1" />);
    fireEvent.click(screen.getByRole("button", { name: "Regenerate post" }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith("c1", "s1", "p1", undefined));
  });

  it("guided regenerate opens form and sends steering_hint", async () => {
    const spy = vi
      .spyOn(campaignApi, "regeneratePost")
      .mockResolvedValue({ post_id: "p1", new_alternate_id: "a_new", delta_set_id: "ds_new" });
    render(<PostItem post={makePost()} pcs={PCS} images={[]} isLatestModelPost campaignId="c1" />);
    fireEvent.click(screen.getByRole("button", { name: "Guided regenerate" }));
    const input = screen.getByLabelText("Guided regenerate hint");
    fireEvent.change(input, { target: { value: "Include a dragon" } });
    fireEvent.submit(input.closest("form")!);
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith("c1", "s1", "p1", {
        steering_hint: "Include a dragon",
      }),
    );
  });

  it("shows error outside the chevron strip when regenerate fails", async () => {
    vi.spyOn(campaignApi, "regeneratePost").mockRejectedValue(new Error("server error"));
    render(<PostItem post={makePost()} pcs={PCS} images={[]} isLatestModelPost campaignId="c1" />);
    fireEvent.click(screen.getByRole("button", { name: "Regenerate post" }));
    expect(await screen.findByText("server error")).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Alternates" })).toBeNull();
  });

  it("calls onRerollFailed when a reroll fails, to clear the stuck streaming indicator", async () => {
    vi.spyOn(campaignApi, "regeneratePost").mockRejectedValue(new Error("server error"));
    const onRerollFailed = vi.fn();
    render(
      <PostItem
        post={makePost()}
        pcs={PCS}
        images={[]}
        isLatestModelPost
        campaignId="c1"
        onRerollFailed={onRerollFailed}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Regenerate post" }));
    // Passes this post's turn_id so the parent can scope the streaming-clear to
    // this reroll and not a concurrent normal turn.
    await waitFor(() => expect(onRerollFailed).toHaveBeenCalledWith("t1"));
  });

  it("does not call onRerollFailed when a reroll succeeds", async () => {
    vi.spyOn(campaignApi, "regeneratePost").mockResolvedValue({
      post_id: "p1",
      new_alternate_id: "a_new",
      delta_set_id: "ds_new",
    });
    const onRerollFailed = vi.fn();
    render(
      <PostItem
        post={makePost()}
        pcs={PCS}
        images={[]}
        isLatestModelPost
        campaignId="c1"
        onRerollFailed={onRerollFailed}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Regenerate post" }));
    await waitFor(() => expect(campaignApi.regeneratePost).toHaveBeenCalled());
    expect(onRerollFailed).not.toHaveBeenCalled();
  });

  it("guided regenerate form stays open on API failure", async () => {
    vi.spyOn(campaignApi, "regeneratePost").mockRejectedValue(new Error("server error"));
    render(<PostItem post={makePost()} pcs={PCS} images={[]} isLatestModelPost campaignId="c1" />);
    fireEvent.click(screen.getByRole("button", { name: "Guided regenerate" }));
    const input = screen.getByLabelText("Guided regenerate hint");
    fireEvent.change(input, { target: { value: "Include a dragon" } });
    fireEvent.submit(input.closest("form")!);
    await waitFor(() => expect(screen.getByText("server error")).toBeInTheDocument());
    expect(screen.getByLabelText("Guided regenerate hint")).toBeInTheDocument();
  });
});

describe("PostItem continue", () => {
  beforeEach(() => mockIntersectionObserver());

  it("continue calls auxiliaryApi.continueAs with the post author", async () => {
    const spy = vi.spyOn(auxiliaryApi, "continueAs").mockResolvedValue({
      id: "aux1",
      kind: "continue_as",
      text: "continued text",
      completed_at: "2026-05-19T12:00:00Z",
      model_used: "test-model",
      tokens: 100,
      pending_commit_action: "replace_post",
      warnings: [],
    });
    const post = makePost({ author_npc_ref: "guard-captain" });
    render(<PostItem post={post} pcs={PCS} images={[]} isLatestModelPost campaignId="c1" />);
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith("c1", "guard-captain", "p1"));
  });

  it("falls back to presentCharacterRefs for continue", async () => {
    const spy = vi.spyOn(auxiliaryApi, "continueAs").mockResolvedValue({
      id: "aux1",
      kind: "continue_as",
      text: "continued text",
      completed_at: "2026-05-19T12:00:00Z",
      model_used: "test-model",
      tokens: 100,
      pending_commit_action: "replace_post",
      warnings: [],
    });
    render(
      <PostItem
        post={makePost()}
        pcs={PCS}
        images={[]}
        isLatestModelPost
        campaignId="c1"
        presentCharacterRefs={["tavern-keeper"]}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith("c1", "tavern-keeper", "p1"));
  });

  it("shows character picker when multiple present characters and no author ref", async () => {
    const spy = vi.spyOn(auxiliaryApi, "continueAs").mockResolvedValue({
      id: "aux1",
      kind: "continue_as",
      text: "continued text",
      completed_at: "2026-05-19T12:00:00Z",
      model_used: "test-model",
      tokens: 100,
      pending_commit_action: "replace_post",
      warnings: [],
    });
    render(
      <PostItem
        post={makePost()}
        pcs={PCS}
        images={[]}
        isLatestModelPost
        campaignId="c1"
        presentCharacterRefs={["tavern-keeper", "guard-captain"]}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    const select = screen.getByRole("combobox", { name: "Character to continue as" });
    expect(select).toBeInTheDocument();
    fireEvent.change(select, { target: { value: "guard-captain" } });
    fireEvent.submit(select.closest("form")!);
    await waitFor(() => expect(spy).toHaveBeenCalledWith("c1", "guard-captain", "p1"));
  });
});

describe("PostItem cost in header", () => {
  it("displays cost in header when visible and cost data is available", async () => {
    mockIntersectionObserver();
    vi.spyOn(observabilityApi, "turnCosts").mockResolvedValue([
      { task: "primary", total_usd: 0.012, input_tokens: 800, output_tokens: 350, call_count: 1 },
      {
        task: "extraction",
        total_usd: 0.001,
        input_tokens: 400,
        output_tokens: 50,
        call_count: 1,
      },
    ]);
    render(<PostItem post={makePost()} pcs={PCS} images={[]} campaignId="c1" />);
    triggerIntersection();
    await waitFor(() => expect(screen.getByLabelText("Turn cost")).toHaveTextContent("$0.0130"));
  });

  it("does not fetch cost until element is visible", () => {
    mockIntersectionObserver();
    const spy = vi.spyOn(observabilityApi, "turnCosts").mockResolvedValue([]);
    render(<PostItem post={makePost()} pcs={PCS} images={[]} campaignId="c1" />);
    expect(spy).not.toHaveBeenCalled();
  });

  it("does not show cost for player posts", () => {
    mockIntersectionObserver();
    const post = makePost({ is_player: true, author_kind: "pc" });
    render(<PostItem post={post} pcs={PCS} images={[]} campaignId="c1" />);
    expect(screen.queryByLabelText("Turn cost")).toBeNull();
  });

  it("does not render a clickable cost button", () => {
    mockIntersectionObserver();
    render(<PostItem post={makePost()} pcs={PCS} images={[]} campaignId="c1" />);
    expect(screen.queryByRole("button", { name: /cost/i })).toBeNull();
  });
});
