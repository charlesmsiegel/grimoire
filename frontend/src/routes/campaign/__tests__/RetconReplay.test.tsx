import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { RetconReplay } from "../RetconReplay";
import { campaignApi, type ReplayBatchView } from "../../../api/campaign";

function makeView(overrides: Partial<ReplayBatchView> = {}): ReplayBatchView {
  return {
    batch_id: "rb_x",
    campaign_id: "c1",
    edited_post_id: "p_1",
    subsequent_post_ids: ["p_2", "p_3", "p_4", "p_5", "p_6"],
    current_index: 2,
    current_post_id: "p_4",
    current_alternate_id: "a_new",
    accepted_post_ids: ["p_2", "p_3"],
    contradictions: [],
    completed: false,
    cancelled_at_post_id: null,
    ...overrides,
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("RetconReplay", () => {
  it("renders the [n of N] indicator and the current row", () => {
    render(
      <RetconReplay campaignId="c1" batchId="rb_x" initialState={makeView()} onClose={() => {}} />,
    );
    expect(screen.getByText("[3 of 5]")).toBeInTheDocument();
    expect(screen.getByText("alt: a_new")).toBeInTheDocument();
    const current = screen.getByRole("listitem", { current: "step" });
    expect(current).toHaveTextContent("p_4");
  });

  it("clicking Accept calls acceptRetconReplay and advances state", async () => {
    const spy = vi
      .spyOn(campaignApi, "acceptRetconReplay")
      .mockResolvedValue(makeView({ current_index: 3, current_post_id: "p_5" }));
    render(
      <RetconReplay campaignId="c1" batchId="rb_x" initialState={makeView()} onClose={() => {}} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith("c1", "rb_x"));
    expect(await screen.findByText("[4 of 5]")).toBeInTheDocument();
  });

  it("clicking Try again calls tryAgainRetconReplay", async () => {
    const spy = vi
      .spyOn(campaignApi, "tryAgainRetconReplay")
      .mockResolvedValue(makeView({ current_alternate_id: "a_retry" }));
    render(
      <RetconReplay campaignId="c1" batchId="rb_x" initialState={makeView()} onClose={() => {}} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith("c1", "rb_x"));
    expect(await screen.findByText("alt: a_retry")).toBeInTheDocument();
  });

  it("clicking Cancel calls cancelRetconReplay and shows Close", async () => {
    vi.spyOn(campaignApi, "cancelRetconReplay").mockResolvedValue(
      makeView({ completed: true, cancelled_at_post_id: "p_4" }),
    );
    render(
      <RetconReplay campaignId="c1" batchId="rb_x" initialState={makeView()} onClose={() => {}} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(await screen.findByRole("button", { name: "Close" })).toBeInTheDocument();
  });

  it("disables Accept while waiting on the API", () => {
    let resolveAccept: (v: ReplayBatchView) => void = () => {};
    vi.spyOn(campaignApi, "acceptRetconReplay").mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveAccept = resolve;
        }),
    );
    render(
      <RetconReplay campaignId="c1" batchId="rb_x" initialState={makeView()} onClose={() => {}} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Accept" }));
    expect(screen.getByRole("button", { name: "Accept" })).toBeDisabled();
    resolveAccept(makeView({ current_index: 3 }));
  });

  it("renders contradictions when present on the current row", () => {
    render(
      <RetconReplay
        campaignId="c1"
        batchId="rb_x"
        initialState={makeView({ contradictions: ["cr_1", "cr_2"] })}
        onClose={() => {}}
      />,
    );
    expect(screen.getByText(/2 contradictions/i)).toBeInTheDocument();
  });
});
