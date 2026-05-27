import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  observabilityApi,
  type ContextSourceFromAudit,
} from "../../../api/observability";
import { viewsApi } from "../../../api/views";
import { WhyCharacterPanel } from "../WhyCharacterPanel";

vi.mock("../../../api/observability", () => ({
  observabilityApi: {
    listTurns: vi.fn(),
    getTurnPrompt: vi.fn(),
  },
}));

vi.mock("../../../api/views", () => ({
  viewsApi: {
    listCharacters: vi.fn(),
  },
}));

const listTurns = observabilityApi.listTurns as unknown as ReturnType<typeof vi.fn>;
const getTurnPrompt = observabilityApi.getTurnPrompt as unknown as ReturnType<typeof vi.fn>;
const listCharacters = viewsApi.listCharacters as unknown as ReturnType<typeof vi.fn>;

function characterSource(
  overrides: Partial<ContextSourceFromAudit> = {},
): ContextSourceFromAudit {
  return {
    source_id: "src_1",
    owner_id: "library:world1",
    kind: "character",
    scope: "library",
    tier: "spotlight",
    library_version: 1,
    override_applied: false,
    tokens: 100,
    summary: "alice",
    inclusion_reasons: ["present_in_scene"],
    ...overrides,
  };
}

function turn(
  overrides: Partial<{ turn_id: string; player_input: string }> = {},
) {
  return {
    turn_id: "turn-1",
    campaign_id: "camp-1",
    scene_id: "scene-1",
    started_at: "2026-05-20T12:00:00Z",
    player_input: "I walk into the tavern.",
    llm_model: "claude",
    ...overrides,
  };
}

function renderPanel(campaignId = "camp-1") {
  return render(
    <MemoryRouter>
      <WhyCharacterPanel campaignId={campaignId} />
    </MemoryRouter>,
  );
}

describe("WhyCharacterPanel", () => {
  beforeEach(() => {
    listTurns.mockReset();
    getTurnPrompt.mockReset();
    listCharacters.mockReset();
    listCharacters.mockResolvedValue([
      {
        character: {
          id: "alice",
          name: "Alice the Sage",
          role: "npc",
          world_id: "world1",
          aliases: [],
          age: null,
          tags: [],
          voice: { samples: [] },
          image: null,
          description: "",
          body: "",
          file_path: "",
          version: 1,
        },
        current_state: {},
        capabilities: [],
        source_chain: [],
        overrides_applied: [],
      },
    ]);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders one card per character with the union of reasons across sources", async () => {
    listTurns.mockResolvedValue([turn()]);
    getTurnPrompt.mockResolvedValue({
      messages: [],
      sources: [
        characterSource({
          source_id: "src_a",
          summary: "Active PC: alice",
          inclusion_reasons: ["pc_card", "present_in_scene"],
          tokens: 200,
        }),
        characterSource({
          source_id: "src_b",
          summary: "voice:alice",
          inclusion_reasons: ["pc_card"],
          tokens: 50,
        }),
        characterSource({
          source_id: "src_c",
          summary: "alice",
          inclusion_reasons: ["mentioned_in_recent_posts"],
          tokens: 30,
        }),
      ],
      budget_used: {},
      messages_hash: "h",
      composition_snapshot: null,
      summary: null,
    });

    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: /turn-1/ }));

    const card = await screen.findByTestId("character-card-alice");
    expect(card).toHaveTextContent("Alice the Sage");
    expect(card).toHaveTextContent("PC card");
    expect(card).toHaveTextContent("Present in scene");
    expect(card).toHaveTextContent("Mentioned recently");
    expect(card).toHaveTextContent("280");
  });

  it("merges sources from all backend character prefixes into one card", async () => {
    // Regression for PR #429: the context builder emits character-kind
    // sources with summaries prefixed `Active PC:`, `voice:`,
    // `transient:`, `extras:`, and `extras-breadcrumb:`. Each must map
    // back to the same ref so the panel shows one card per character.
    listTurns.mockResolvedValue([turn()]);
    getTurnPrompt.mockResolvedValue({
      messages: [],
      sources: [
        characterSource({
          source_id: "src_a",
          summary: "Active PC: alice",
          inclusion_reasons: ["pc_card"],
          tokens: 10,
        }),
        characterSource({
          source_id: "src_b",
          summary: "voice:alice",
          inclusion_reasons: ["present_in_scene"],
          tokens: 20,
        }),
        characterSource({
          source_id: "src_c",
          summary: "transient:alice",
          inclusion_reasons: ["mentioned_in_recent_posts"],
          tokens: 30,
        }),
        characterSource({
          source_id: "src_d",
          summary: "extras:alice",
          inclusion_reasons: ["mentioned_in_recent_posts"],
          tokens: 40,
        }),
        characterSource({
          source_id: "src_e",
          summary: "extras-breadcrumb:alice",
          inclusion_reasons: ["mentioned_in_recent_posts"],
          tokens: 50,
        }),
      ],
      budget_used: {},
      messages_hash: "h",
      composition_snapshot: null,
      summary: null,
    });

    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: /turn-1/ }));

    const card = await screen.findByTestId("character-card-alice");
    expect(card).toHaveTextContent("Alice the Sage");
    expect(card).toHaveTextContent("150");
    expect(screen.queryByTestId("character-card-transient:alice")).toBeNull();
    expect(screen.queryByTestId("character-card-extras:alice")).toBeNull();
    expect(screen.queryByTestId("character-card-extras-breadcrumb:alice")).toBeNull();
  });

  it("filters out non-character sources", async () => {
    listTurns.mockResolvedValue([turn()]);
    getTurnPrompt.mockResolvedValue({
      messages: [],
      sources: [
        characterSource({ summary: "alice", inclusion_reasons: ["present_in_scene"] }),
        {
          source_id: "src_lore",
          owner_id: "library:world1/runes",
          kind: "lore",
          scope: "library",
          tier: "background",
          library_version: 1,
          override_applied: false,
          tokens: 80,
          summary: "Ancient runes",
          inclusion_reasons: ["lore_before_cast"],
        },
      ],
      budget_used: {},
      messages_hash: "h",
      composition_snapshot: null,
      summary: null,
    });

    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: /turn-1/ }));

    await screen.findByTestId("character-card-alice");
    expect(screen.queryByText(/Ancient runes/)).toBeNull();
    expect(screen.queryByText(/Lore before cast/)).toBeNull();
  });

  it("shows the empty-state when the audit has no character sources", async () => {
    listTurns.mockResolvedValue([turn()]);
    getTurnPrompt.mockResolvedValue({
      messages: [],
      sources: [
        {
          source_id: "src_lore",
          owner_id: "library:world1/runes",
          kind: "lore",
          scope: "library",
          tier: "background",
          library_version: 1,
          override_applied: false,
          tokens: 80,
          summary: "Ancient runes",
          inclusion_reasons: ["lore_before_cast"],
        },
      ],
      budget_used: {},
      messages_hash: "h",
      composition_snapshot: null,
      summary: null,
    });

    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: /turn-1/ }));

    expect(await screen.findByText(/no character sources/i)).toBeInTheDocument();
  });

  it("falls back to the literal ref when character resolution misses", async () => {
    listTurns.mockResolvedValue([turn()]);
    listCharacters.mockResolvedValue([]);
    getTurnPrompt.mockResolvedValue({
      messages: [],
      sources: [
        characterSource({ summary: "alice", inclusion_reasons: ["present_in_scene"] }),
      ],
      budget_used: {},
      messages_hash: "h",
      composition_snapshot: null,
      summary: null,
    });

    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: /turn-1/ }));

    const card = await screen.findByTestId("character-card-alice");
    expect(card).toHaveTextContent("alice");
  });

  it("shows the not-available message when getTurnPrompt 404s", async () => {
    listTurns.mockResolvedValue([turn()]);
    getTurnPrompt.mockRejectedValue(new Error("HTTP 404"));

    renderPanel();
    fireEvent.click(await screen.findByRole("button", { name: /turn-1/ }));

    await waitFor(() => {
      expect(screen.getByText(/no audit available/i)).toBeInTheDocument();
    });
  });

  it("shows the no-audits message when listTurns returns []", async () => {
    listTurns.mockResolvedValue([]);
    renderPanel();
    expect(
      await screen.findByText(/no audits yet for this campaign/i),
    ).toBeInTheDocument();
  });
});
