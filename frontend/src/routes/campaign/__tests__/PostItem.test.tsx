import { afterEach, describe, expect, it, vi } from "vitest";
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

afterEach(() => {
  vi.restoreAllMocks();
});

describe("PostItem chevron strip", () => {
  it("hides the strip when fewer than 2 alternates", () => {
    vi.spyOn(observabilityApi, "turnCosts").mockResolvedValue([]);
    render(<PostItem post={makePost()} pcs={PCS} images={[]} isLatestModelPost campaignId="c1" />);
    expect(screen.queryByRole("group", { name: "Alternates" })).toBeNull();
  });

  it("renders chevrons and count when there are 2+ alternates", () => {
    vi.spyOn(observabilityApi, "turnCosts").mockResolvedValue([]);
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
    vi.spyOn(observabilityApi, "turnCosts").mockResolvedValue([]);
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
    vi.spyOn(observabilityApi, "turnCosts").mockResolvedValue([]);
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
    vi.spyOn(observabilityApi, "turnCosts").mockResolvedValue([]);
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
  it("shows Edit, Regenerate, Guided regenerate for latest model post", () => {
    vi.spyOn(observabilityApi, "turnCosts").mockResolvedValue([]);
    render(<PostItem post={makePost()} pcs={PCS} images={[]} isLatestModelPost campaignId="c1" />);
    expect(screen.getByRole("button", { name: "Edit post" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Regenerate post" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Guided regenerate" })).toBeInTheDocument();
  });

  it("hides Regenerate and Guided regenerate when not latest model post", () => {
    vi.spyOn(observabilityApi, "turnCosts").mockResolvedValue([]);
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
    vi.spyOn(observabilityApi, "turnCosts").mockResolvedValue([]);
    const post = makePost({ author_npc_ref: "guard-captain" });
    render(<PostItem post={post} pcs={PCS} images={[]} isLatestModelPost campaignId="c1" />);
    expect(screen.getByRole("button", { name: "Continue" })).toBeInTheDocument();
  });

  it("hides Continue button when post has no author ref", () => {
    vi.spyOn(observabilityApi, "turnCosts").mockResolvedValue([]);
    render(<PostItem post={makePost()} pcs={PCS} images={[]} isLatestModelPost campaignId="c1" />);
    expect(screen.queryByRole("button", { name: "Continue" })).toBeNull();
  });

  it("shows Translate button", () => {
    vi.spyOn(observabilityApi, "turnCosts").mockResolvedValue([]);
    render(<PostItem post={makePost()} pcs={PCS} images={[]} campaignId="c1" />);
    expect(screen.getByRole("button", { name: "Translate this post" })).toBeInTheDocument();
  });

  it("does not show retcon, rewrite, or what-would-x-say buttons", () => {
    vi.spyOn(observabilityApi, "turnCosts").mockResolvedValue([]);
    render(<PostItem post={makePost()} pcs={PCS} images={[]} isLatestModelPost campaignId="c1" />);
    expect(screen.queryByRole("button", { name: /retcon/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /rewrite/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /what.*say/i })).toBeNull();
  });
});

describe("PostItem regenerate", () => {
  it("regenerate button calls regeneratePost without steering_hint", async () => {
    vi.spyOn(observabilityApi, "turnCosts").mockResolvedValue([]);
    const spy = vi
      .spyOn(campaignApi, "regeneratePost")
      .mockResolvedValue({ post_id: "p1", new_alternate_id: "a_new", delta_set_id: "ds_new" });
    render(<PostItem post={makePost()} pcs={PCS} images={[]} isLatestModelPost campaignId="c1" />);
    fireEvent.click(screen.getByRole("button", { name: "Regenerate post" }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith("c1", "s1", "p1", undefined));
  });

  it("guided regenerate opens form and sends steering_hint", async () => {
    vi.spyOn(observabilityApi, "turnCosts").mockResolvedValue([]);
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
});

describe("PostItem continue", () => {
  it("continue calls auxiliaryApi.continueAs with the post author", async () => {
    vi.spyOn(observabilityApi, "turnCosts").mockResolvedValue([]);
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
});

describe("PostItem cost in header", () => {
  it("displays cost in header when cost data is available", async () => {
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
    await waitFor(() => expect(screen.getByLabelText("Turn cost")).toHaveTextContent("$0.0130"));
  });

  it("does not show cost for player posts", async () => {
    const spy = vi.spyOn(observabilityApi, "turnCosts").mockResolvedValue([]);
    const post = makePost({ is_player: true, author_kind: "pc" });
    render(<PostItem post={post} pcs={PCS} images={[]} campaignId="c1" />);
    await waitFor(() => Promise.resolve());
    expect(spy).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("Turn cost")).toBeNull();
  });

  it("does not render a clickable cost button", () => {
    vi.spyOn(observabilityApi, "turnCosts").mockResolvedValue([]);
    render(<PostItem post={makePost()} pcs={PCS} images={[]} campaignId="c1" />);
    expect(screen.queryByRole("button", { name: /cost/i })).toBeNull();
  });
});
