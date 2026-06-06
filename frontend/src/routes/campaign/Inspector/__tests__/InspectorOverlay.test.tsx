import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import {
  inspectorApi,
  type ContextSourceExplanation,
  type PreviewDetail,
  type PreviewSummary,
} from "../../../../api/inspector";
import { InspectorOverlay } from "../InspectorOverlay";

vi.mock("../../../../api/inspector", async (orig) => {
  const actual = await orig<typeof import("../../../../api/inspector")>();
  return {
    ...actual,
    inspectorApi: {
      ...actual.inspectorApi,
      getPreview: vi.fn(
        (): Promise<PreviewDetail> =>
          Promise.resolve({
            messages: [{ role: "system", content: "SYS BODY", metadata: { tier: "system" } }],
            sources: [],
            budget_used: { "lock-in": 10, spotlight: 0, background: 0, archive: 0 },
            messages_hash: "h",
          }),
      ),
    },
  };
});

const sources: ContextSourceExplanation[] = [
  {
    source_id: "src_sys",
    owner_id: null,
    kind: "system",
    scope: "campaign-local",
    tier: "lock-in",
    library_version: null,
    inclusion_reasons: ["system_prompt"],
    tokens: 100,
    summary: "",
    text: "SYSTEM PROMPT TEXT",
  },
];

describe("InspectorOverlay", () => {
  it("shows the selected source's full text and toggles raw messages", async () => {
    render(
      <InspectorOverlay
        campaignId="camp"
        sessionId="camp"
        handle="ph_1"
        sources={sources}
        summary={{
          handle: "ph_1",
          per_tier_tokens: { "lock-in": 100, spotlight: 0, background: 0, archive: 0 },
          per_tier_budget: { "lock-in": 8000, spotlight: 0, background: 0, archive: 0 },
          source_count: 1,
          messages_hash: "h",
        }}
        onClose={() => {}}
        onChanged={() => {}}
      />,
    );
    // Selection defaults to the first source, so its full text shows immediately.
    expect(screen.getByText("SYSTEM PROMPT TEXT")).toBeInTheDocument();
    // Raw-messages toggle fetches and renders verbatim messages.
    fireEvent.click(screen.getByRole("button", { name: /raw messages/i }));
    await waitFor(() => expect(screen.getByText("SYS BODY")).toBeInTheDocument());
  });

  it("refetches raw messages when the preview handle changes", async () => {
    const summary: PreviewSummary = {
      handle: "ph_1",
      per_tier_tokens: { "lock-in": 0, spotlight: 0, background: 0, archive: 0 },
      per_tier_budget: { "lock-in": 0, spotlight: 0, background: 0, archive: 0 },
      source_count: 0,
      messages_hash: "h",
    };
    vi.mocked(inspectorApi.getPreview).mockImplementation(
      (_campaignId, handle): Promise<PreviewDetail> =>
        Promise.resolve({
          messages: [{ role: "system", content: `BODY ${handle}`, metadata: {} }],
          sources: [],
          budget_used: { "lock-in": 0, spotlight: 0, background: 0, archive: 0 },
          messages_hash: handle,
        }),
    );
    const props = {
      campaignId: "camp",
      sessionId: "camp",
      sources,
      onClose: () => {},
      onChanged: () => {},
    };
    const { rerender } = render(<InspectorOverlay {...props} handle="ph_1" summary={summary} />);
    fireEvent.click(screen.getByRole("button", { name: /raw messages/i }));
    await waitFor(() => expect(screen.getByText("BODY ph_1")).toBeInTheDocument());

    // A pin/refresh produces a new handle while the overlay stays open in raw
    // mode: the stale prompt must be replaced, not reused.
    rerender(
      <InspectorOverlay {...props} handle="ph_2" summary={{ ...summary, handle: "ph_2" }} />,
    );
    await waitFor(() => expect(screen.getByText("BODY ph_2")).toBeInTheDocument());
    expect(screen.queryByText("BODY ph_1")).not.toBeInTheDocument();
  });
});
