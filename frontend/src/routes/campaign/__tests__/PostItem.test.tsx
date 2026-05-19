import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { PostItem } from "../PostItem";
import type { ApiAlternate, ApiPost, PCEntry } from "../../../api/campaign";
import { campaignApi } from "../../../api/campaign";

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
    // At cursor 0: prev disabled, next enabled.
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
    render(<PostItem post={post} pcs={PCS} images={[]} isLatestModelPost={false} campaignId="c1" />);
    expect(screen.getByRole("button", { name: "Previous alternate" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Next alternate" })).toBeDisabled();
    expect(screen.getByRole("note")).toHaveTextContent(/latest post/i);
  });

  it("regenerate button posts via regeneratePost", async () => {
    const spy = vi
      .spyOn(campaignApi, "regeneratePost")
      .mockResolvedValue({ post_id: "p1", new_alternate_id: "a_new", delta_set_id: "ds_new" });
    const post = makePost({
      alternates: [alt("a1", true), alt("a2")],
      primary_alternate_id: "a1",
    });
    render(<PostItem post={post} pcs={PCS} images={[]} isLatestModelPost campaignId="c1" />);
    fireEvent.click(screen.getByRole("button", { name: "Regenerate post" }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith("c1", "s1", "p1"));
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
