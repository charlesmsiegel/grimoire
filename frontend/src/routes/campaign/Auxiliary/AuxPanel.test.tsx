import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { AuxPanel } from "./AuxPanel";
import type { AuxiliaryResult } from "../../../api/auxiliary";

const baseResult: AuxiliaryResult = {
  id: "ar_001",
  kind: "brainstorm",
  text: "first option, second option, third option",
  model_used: "claude-sonnet-4-6",
  tokens: 42,
  pending_commit_action: "copy",
  warnings: [],
  completed_at: new Date().toISOString(),
};

describe("AuxPanel", () => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let fetchSpy: any;

  beforeEach(() => {
    fetchSpy = vi.fn(
      async () =>
        new Response(
          JSON.stringify({ committed: true, action: "copy", result_id: "ar_001", text: "x" }),
          { status: 200, headers: { "content-type": "application/json" } },
        ),
    );
    globalThis.fetch = fetchSpy;
  });

  afterEach(() => {
    fetchSpy = undefined;
  });

  it("renders the result text and badge", () => {
    render(<AuxPanel campaignId="c1" result={baseResult} />);
    expect(screen.getByText("Brainstorm")).toBeInTheDocument();
    expect(screen.getByText(/first option, second option/)).toBeInTheDocument();
  });

  it("POSTs to the accept endpoint when Accept is clicked", async () => {
    const onAccepted = vi.fn();
    render(<AuxPanel campaignId="c1" result={baseResult} onAccepted={onAccepted} />);
    fireEvent.click(screen.getByRole("button", { name: /accept/i }));
    await waitFor(() => expect(onAccepted).toHaveBeenCalled());
    const url = fetchSpy.mock.calls[0]?.[0] as string;
    expect(url ?? "").toContain("/api/campaigns/c1/auxiliary/ar_001/accept");
  });

  it("POSTs to discard and notifies", async () => {
    const onDiscarded = vi.fn();
    render(<AuxPanel campaignId="c1" result={baseResult} onDiscarded={onDiscarded} />);
    fireEvent.click(screen.getByRole("button", { name: /discard/i }));
    await waitFor(() => expect(onDiscarded).toHaveBeenCalledWith("ar_001"));
  });

  it("toggles edit mode and sends edited text on accept", async () => {
    const onAccepted = vi.fn();
    render(<AuxPanel campaignId="c1" result={baseResult} onAccepted={onAccepted} />);
    fireEvent.click(screen.getByRole("button", { name: /edit/i }));
    const textarea = screen.getByLabelText(/edit auxiliary text/i) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "edited body" } });
    fireEvent.click(screen.getByRole("button", { name: /accept/i }));
    await waitFor(() => expect(onAccepted).toHaveBeenCalled());
    const init = fetchSpy.mock.calls[0]?.[1] as RequestInit;
    const body = JSON.parse((init?.body as string) ?? "{}");
    expect(body.edited_text).toBe("edited body");
  });
});
