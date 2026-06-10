import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { RetconLauncher } from "../RetconLauncher";
import { campaignApi, type RetconResultPayload } from "../../../api/campaign";

function makeResult(overrides: Partial<RetconResultPayload> = {}): RetconResultPayload {
  return {
    post_id: "p_1",
    original_text: "orig",
    new_text: "edited",
    reversed_delta_ids: [],
    new_delta_ids: [],
    downstream_flagged_turns: [],
    warnings: [],
    replay_batch_id: null,
    replayed_post_ids: [],
    cancelled_at_post_id: null,
    contradictions_detected: [],
    ...overrides,
  };
}

function renderLauncher() {
  render(
    <RetconLauncher
      campaignId="c1"
      postId="p_1"
      turnId="t_1"
      originalText="orig"
      subsequentModelPostCount={0}
      onClose={() => {}}
    />,
  );
}

async function leaveAsIs() {
  fireEvent.click(screen.getByRole("button", { name: "Accept edit" }));
  fireEvent.click(screen.getByRole("button", { name: "Leave as-is" }));
  await screen.findByText(/Retcon applied/);
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("RetconLauncher leave-as-is", () => {
  it("renders backend warnings on the done step", async () => {
    vi.spyOn(campaignApi, "retconPost").mockResolvedValue(
      makeResult({
        warnings: ["downstream flagging walk failed; flagged turns may be incomplete"],
      }),
    );
    renderLauncher();
    await leaveAsIs();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "downstream flagging walk failed; flagged turns may be incomplete",
    );
  });

  it("renders no warning block when the retcon was clean", async () => {
    vi.spyOn(campaignApi, "retconPost").mockResolvedValue(makeResult());
    renderLauncher();
    await leaveAsIs();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows the API error when the retcon fails", async () => {
    vi.spyOn(campaignApi, "retconPost").mockRejectedValue(
      new Error("retcon aborted: could not re-extract state changes"),
    );
    renderLauncher();
    fireEvent.click(screen.getByRole("button", { name: "Accept edit" }));
    fireEvent.click(screen.getByRole("button", { name: "Leave as-is" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("retcon aborted");
  });
});
