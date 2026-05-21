import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { observabilityApi } from "../../../api/observability";
import { PromptDebugView } from "../PromptDebugView";

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="campaigns/:campaignId/debug/prompt" element={<PromptDebugView />} />
        <Route
          path="campaigns/:campaignId/debug/prompt/:turnId"
          element={<PromptDebugView />}
        />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("PromptDebugView", () => {
  it("renders messages with tier and token annotations", async () => {
    vi.spyOn(observabilityApi, "listTurns").mockResolvedValue([
      {
        turn_id: "t_new",
        campaign_id: "c1",
        branch_id: "main",
        scene_id: "s1",
        started_at: "2026-05-20T10:00:00Z",
        completed_at: "2026-05-20T10:00:02Z",
        player_input: "I look around",
        llm_model: "fake-1",
        llm_provider: "fake",
        context_messages_hash: "h-new",
      },
    ]);
    vi.spyOn(observabilityApi, "getPrompt").mockResolvedValue({
      messages: [
        {
          role: "system",
          content: "You are a narrator",
          name: null,
          tier: "system",
          tokens: 5,
          metadata: { tier: "system" },
        },
        {
          role: "user",
          content: "I look around",
          name: null,
          tier: "player-input",
          tokens: 4,
          metadata: { tier: "player-input" },
        },
      ],
      sources: [
        {
          source_id: "src:char:hero",
          kind: "character",
          scope: "library",
          owner_id: "hero",
          tier: "spotlight",
          library_version: 1,
          override_applied: false,
          tokens: 120,
          summary: "Hero",
        },
      ],
      budget_used: { "lock-in": 200, spotlight: 120 },
      messages_hash: "h-new",
      composition_snapshot: null,
      summary: null,
    });

    renderAt("/campaigns/c1/debug/prompt/t_new");

    await waitFor(() => {
      expect(screen.getByText("You are a narrator")).toBeInTheDocument();
    });
    // Per-message tier annotation
    expect(screen.getByText("system", { selector: ".message-tier" })).toBeInTheDocument();
    expect(screen.getByText("player-input")).toBeInTheDocument();
    // Per-message token estimate
    expect(screen.getByText("5 tok")).toBeInTheDocument();
    // Source attribution row
    expect(screen.getByText("hero")).toBeInTheDocument();
    expect(screen.getByText("character")).toBeInTheDocument();
  });

  it("invokes diffPrompts when 'Compute diff' is clicked", async () => {
    vi.spyOn(observabilityApi, "listTurns").mockResolvedValue([
      {
        turn_id: "t_b",
        campaign_id: "c1",
        branch_id: "main",
        scene_id: "s1",
        started_at: "2026-05-20T10:00:02Z",
        completed_at: null,
        player_input: "step two",
        llm_model: "fake",
        llm_provider: "fake",
        context_messages_hash: "hb",
      },
      {
        turn_id: "t_a",
        campaign_id: "c1",
        branch_id: "main",
        scene_id: "s1",
        started_at: "2026-05-20T10:00:01Z",
        completed_at: null,
        player_input: "step one",
        llm_model: "fake",
        llm_provider: "fake",
        context_messages_hash: "ha",
      },
    ]);
    vi.spyOn(observabilityApi, "getPrompt").mockResolvedValue({
      messages: [
        {
          role: "system",
          content: "new",
          name: null,
          tier: "lock-in",
          tokens: 1,
          metadata: {},
        },
      ],
      sources: [],
      budget_used: {},
      messages_hash: "hb",
      composition_snapshot: null,
      summary: null,
    });
    const diffSpy = vi.spyOn(observabilityApi, "diffPrompts").mockResolvedValue({
      turn_id_a: "t_a",
      turn_id_b: "t_b",
      messages_hash_changed: true,
      added_messages: [],
      removed_messages: [],
      changed_messages: [
        {
          role: "system",
          tier: "lock-in",
          before: { role: "system", tier: "lock-in", tokens: 1, content: "old" },
          after: { role: "system", tier: "lock-in", tokens: 1, content: "new" },
        },
      ],
      added_sources: [],
      removed_sources: [],
      tier_budget_shifts: { "lock-in": 0 },
    });

    renderAt("/campaigns/c1/debug/prompt/t_b");

    await waitFor(() => {
      expect(screen.getByText(/Compute diff/i)).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /Compute diff/i }));
    await waitFor(() => expect(diffSpy).toHaveBeenCalledWith("t_b", "t_a"));
    expect(await screen.findByText(/Changed messages \(1\)/i)).toBeInTheDocument();
  });
});
