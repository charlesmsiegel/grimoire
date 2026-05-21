# "Why this character?" — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a per-character debug view (`/campaigns/:id/observability/turns`) that, given a past turn, renders each character that appeared in that turn's prompt with the union of inclusion reasons attached by the Context Builder.

**Architecture:** Frontend-only. The data already flows: `Context Builder → ContextSource.inclusion_reasons → AuditStore JSON → GET /api/observability/turns/{turn_id}/prompt`. We add a new React route, a thin REST client, and reuse the live inspector's reason vocabulary.

**Tech Stack:** TypeScript, React 18, react-router-dom v7, Vitest + React Testing Library, fetch via the existing `api` client.

**Spec:** `docs/superpowers/specs/2026-05-20-why-this-character-design.md`.

---

## File map

**Create**

- `frontend/src/api/observability.ts` — REST client for `/observability/turns` and `/observability/turns/{turn_id}/prompt`. Re-exports `InclusionReason` and `ContextTier` from `./inspector`.
- `frontend/src/routes/observability/inclusionReasonLabels.ts` — shared `REASON_LABELS` map (extracted from `SourceList.tsx`).
- `frontend/src/routes/observability/WhyCharacterPanel.tsx` — the panel itself.
- `frontend/src/routes/observability/index.ts` — barrel export.
- `frontend/src/routes/observability/__tests__/WhyCharacterPanel.test.tsx` — Vitest cases.
- `frontend/src/routes/observability/__tests__/inclusionReasonLabels.test.ts` — label-map snapshot.

**Modify**

- `frontend/src/routes/campaign/Inspector/SourceList.tsx` — import `REASON_LABELS` from the new shared module.
- `frontend/src/App.tsx` — register the new route under the existing `campaigns/:campaignId` parent.
- `frontend/src/shell/NavSidebar.tsx` — no change here (the campaign sub-nav is in `CampaignView`, see Task 5).
- `frontend/src/routes/CampaignView.tsx` — add an "Observability" link to the in-campaign sub-nav.

> If PR #357 lands first and creates `frontend/src/api/observability.ts` and `frontend/src/routes/observability/`, this plan extends those files instead of creating them. The merge surface is small and additive.

---

## Task 1: Extract the inclusion-reason label map

**Files:**
- Create: `frontend/src/routes/observability/inclusionReasonLabels.ts`
- Modify: `frontend/src/routes/campaign/Inspector/SourceList.tsx`
- Test: `frontend/src/routes/observability/__tests__/inclusionReasonLabels.test.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/routes/observability/__tests__/inclusionReasonLabels.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import { REASON_LABELS } from "../inclusionReasonLabels";

describe("REASON_LABELS", () => {
  it("provides a human label for every InclusionReason", () => {
    // Snapshot the full map so future additions to the InclusionReason
    // union surface as a test diff that someone must explicitly accept.
    expect(REASON_LABELS).toMatchInlineSnapshot(`
      {
        "commitment_open_to_pc": "Open commitment to PC",
        "composition_default": "Composition default",
        "extras_default_visible": "Extras default",
        "extras_pinned_to_hud": "Extras pinned",
        "keyword_triggered": "Keyword triggered",
        "lore_after_cast": "Lore after cast",
        "lore_archive": "Lore archive",
        "lore_at_depth": "Lore at depth",
        "lore_before_cast": "Lore before cast",
        "mechanics_relevant": "Mechanics relevant",
        "mentioned_in_recent_posts": "Mentioned recently",
        "pc_card": "PC card",
        "pinned_by_user": "Pinned by user",
        "present_in_scene": "Present in scene",
        "relationship_to_present": "Relationship to present",
        "scene_anchor": "Scene anchor",
        "style_guide_active": "Style guide active",
        "transient_state_active": "Transient state active",
      }
    `);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- --run inclusionReasonLabels`
Expected: FAIL with "Cannot find module '../inclusionReasonLabels'".

- [ ] **Step 3: Create the shared label module**

Create `frontend/src/routes/observability/inclusionReasonLabels.ts`:

```ts
/**
 * Human labels for InclusionReason values, shared between the live
 * Context Inspector (next-turn preview) and the past-turn lens. Keep
 * the map exhaustive — TypeScript's `Record<InclusionReason, string>`
 * enforces that at the type level.
 */

import type { InclusionReason } from "../../api/inspector";

export const REASON_LABELS: Record<InclusionReason, string> = {
  present_in_scene: "Present in scene",
  mentioned_in_recent_posts: "Mentioned recently",
  commitment_open_to_pc: "Open commitment to PC",
  keyword_triggered: "Keyword triggered",
  relationship_to_present: "Relationship to present",
  pinned_by_user: "Pinned by user",
  scene_anchor: "Scene anchor",
  mechanics_relevant: "Mechanics relevant",
  style_guide_active: "Style guide active",
  pc_card: "PC card",
  composition_default: "Composition default",
  extras_pinned_to_hud: "Extras pinned",
  extras_default_visible: "Extras default",
  lore_before_cast: "Lore before cast",
  lore_after_cast: "Lore after cast",
  lore_at_depth: "Lore at depth",
  lore_archive: "Lore archive",
  transient_state_active: "Transient state active",
};
```

- [ ] **Step 4: Update SourceList.tsx to import the shared map**

In `frontend/src/routes/campaign/Inspector/SourceList.tsx`, replace lines 8-34 with:

```tsx
import type {
  ContextSourceExplanation,
  ContextTier,
  InclusionReason,
} from "../../../api/inspector";
import { REASON_LABELS } from "../../observability/inclusionReasonLabels";
import { PinControls } from "./PinControls";
```

And delete the inline `REASON_LABELS` block (the const declaration that previously occupied lines 15-34). The rest of the file already references `REASON_LABELS[r]` at line 103 — no further changes needed.

- [ ] **Step 5: Run all SourceList and inclusionReasonLabels tests to verify nothing broke**

Run: `cd frontend && npm test -- --run SourceList inclusionReasonLabels`
Expected: PASS for both test files. The existing `SourceList.test.tsx` covers the label rendering path so we get free regression coverage.

- [ ] **Step 6: Typecheck**

Run: `cd frontend && npm run typecheck`
Expected: zero errors.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/routes/observability/inclusionReasonLabels.ts \
        frontend/src/routes/observability/__tests__/inclusionReasonLabels.test.ts \
        frontend/src/routes/campaign/Inspector/SourceList.tsx
git commit -m "refactor(inspector): extract REASON_LABELS to shared module"
```

---

## Task 2: Observability REST client

**Files:**
- Create: `frontend/src/api/observability.ts`
- Test: `frontend/src/api/__tests__/observability.test.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/api/__tests__/observability.test.ts`:

```ts
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { observabilityApi } from "../observability";

describe("observabilityApi", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function jsonResponse(body: unknown, status = 200): Response {
    return new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  }

  it("listTurns hits /api/observability/turns with campaign_id and limit", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse([]));
    await observabilityApi.listTurns("camp-1", 25);
    const url = fetchMock.mock.calls[0][0] as string;
    expect(url).toContain("/api/observability/turns");
    expect(url).toContain("campaign_id=camp-1");
    expect(url).toContain("limit=25");
  });

  it("listTurns defaults limit to 50", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse([]));
    await observabilityApi.listTurns("camp-1");
    expect(fetchMock.mock.calls[0][0] as string).toContain("limit=50");
  });

  it("getTurnPrompt hits /api/observability/turns/{id}/prompt and url-encodes the id", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        messages: [],
        sources: [],
        budget_used: {},
        messages_hash: "h",
        composition_snapshot: null,
        summary: null,
      }),
    );
    await observabilityApi.getTurnPrompt("turn id/with slash");
    const url = fetchMock.mock.calls[0][0] as string;
    expect(url).toContain("/api/observability/turns/turn%20id%2Fwith%20slash/prompt");
  });

  it("getTurnPrompt parses sources with inclusion_reasons", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        messages: [],
        sources: [
          {
            source_id: "src_abc",
            owner_id: "library:world1",
            kind: "character",
            scope: "library",
            tier: "spotlight",
            library_version: 3,
            override_applied: false,
            tokens: 120,
            summary: "alice",
            inclusion_reasons: ["present_in_scene", "mentioned_in_recent_posts"],
          },
        ],
        budget_used: { spotlight: 120 },
        messages_hash: "h",
        composition_snapshot: null,
        summary: null,
      }),
    );
    const result = await observabilityApi.getTurnPrompt("t1");
    expect(result.sources).toHaveLength(1);
    expect(result.sources[0].kind).toBe("character");
    expect(result.sources[0].inclusion_reasons).toEqual([
      "present_in_scene",
      "mentioned_in_recent_posts",
    ]);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- --run api/observability`
Expected: FAIL with "Cannot find module '../observability'".

- [ ] **Step 3: Create the API client**

Create `frontend/src/api/observability.ts`:

```ts
/**
 * Observability REST client.
 *
 * Wraps the backend's /api/observability/* surface. The "Why this
 * character?" debug view uses `listTurns` to populate a turn picker and
 * `getTurnPrompt` to fetch the captured ContextSources for the
 * selected turn (each carrying its inclusion_reasons).
 *
 * Spec: `docs/superpowers/specs/2026-05-20-why-this-character-design.md`.
 */

import { api } from "./client";
import type { ContextTier, InclusionReason } from "./inspector";

export type { ContextTier, InclusionReason };

export interface TurnAuditSummary {
  turn_id: string;
  campaign_id: string;
  branch_id: string;
  scene_id: string;
  started_at: string;
  player_input: string;
  llm_model: string;
}

export interface ContextSourceFromAudit {
  source_id: string;
  owner_id: string | null;
  kind: string;
  scope: string;
  tier: ContextTier;
  library_version: number | null;
  override_applied: boolean;
  tokens: number;
  summary: string;
  inclusion_reasons: InclusionReason[];
}

export interface TurnPromptResponse {
  messages: unknown[];
  sources: ContextSourceFromAudit[];
  budget_used: Record<string, number>;
  messages_hash: string;
  composition_snapshot: unknown;
  summary: unknown;
}

export const observabilityApi = {
  listTurns(campaignId: string, limit = 50): Promise<TurnAuditSummary[]> {
    return api.get<TurnAuditSummary[]>("/api/observability/turns", {
      query: { campaign_id: campaignId, limit },
    });
  },

  getTurnPrompt(turnId: string): Promise<TurnPromptResponse> {
    return api.get<TurnPromptResponse>(
      `/api/observability/turns/${encodeURIComponent(turnId)}/prompt`,
    );
  },
};
```

Note on the `TurnAuditSummary` shape: the backend's `list_turn_audits` endpoint returns the full `TurnAudit` pydantic model serialized. We only type the fields we actually consume — extra keys are tolerated by JSON parsing.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm test -- --run api/observability`
Expected: PASS — 4 assertions.

- [ ] **Step 5: Typecheck**

Run: `cd frontend && npm run typecheck`
Expected: zero errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/observability.ts \
        frontend/src/api/__tests__/observability.test.ts
git commit -m "feat(api): observability client for turns + prompt endpoints"
```

---

## Task 3: WhyCharacterPanel — grouping, rendering, empty states

**Files:**
- Create: `frontend/src/routes/observability/WhyCharacterPanel.tsx`
- Create: `frontend/src/routes/observability/index.ts`
- Test: `frontend/src/routes/observability/__tests__/WhyCharacterPanel.test.tsx`

### Grouping strategy

The Context Builder emits multiple `kind === "character"` sources for the same character (PC card, voice anchor, NPC card). They share no single field as a stable key, so the panel parses the canonical character ref from `source.summary` using these rules:

| `summary` shape | Comes from | Extracted ref |
|---|---|---|
| `"Active PC: <ref>"` | PC card | `<ref>` |
| `"voice:<ref>"` | Voice anchor stanza | `<ref>` |
| anything else | NPC/library character card | `summary` verbatim |

Two characters with disjoint refs get separate cards. Reasons across that character's sources are deduplicated and unioned; tokens are summed.

Display names resolve through `viewsApi.listCharacters(campaignId)` — that endpoint is already cached and returns `ResolvedCharacter[]`. We build `Map<character.id, character.name>` and look up by ref. If the ref starts with `library:` it's left as-is for lookup (matches the same shape the resolver uses). If the lookup misses, fall back to the literal ref.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/routes/observability/__tests__/WhyCharacterPanel.test.tsx`:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { observabilityApi, type ContextSourceFromAudit } from "../../../api/observability";
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

function characterSource(overrides: Partial<ContextSourceFromAudit> = {}): ContextSourceFromAudit {
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

function turn(overrides: Partial<{ turn_id: string; player_input: string }> = {}) {
  return {
    turn_id: "turn-1",
    campaign_id: "camp-1",
    branch_id: "main",
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
        character: { id: "alice", name: "Alice the Sage", role: "npc", world_id: "world1", aliases: [], age: null, tags: [], voice: { samples: [] }, image: null, description: "", body: "", file_path: "", version: 1 },
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
    await userEvent.click(await screen.findByRole("button", { name: /turn-1/ }));

    const card = await screen.findByTestId("character-card-alice");
    expect(card).toHaveTextContent("Alice the Sage");
    expect(card).toHaveTextContent("PC card");
    expect(card).toHaveTextContent("Present in scene");
    expect(card).toHaveTextContent("Mentioned recently");
    // 200 + 50 + 30 = 280 tokens, comma-formatted.
    expect(card).toHaveTextContent("280");
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
    await userEvent.click(await screen.findByRole("button", { name: /turn-1/ }));

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
    await userEvent.click(await screen.findByRole("button", { name: /turn-1/ }));

    expect(
      await screen.findByText(/no character sources/i),
    ).toBeInTheDocument();
  });

  it("falls back to the literal ref when character resolution misses", async () => {
    listTurns.mockResolvedValue([turn()]);
    listCharacters.mockResolvedValue([]);  // no resolution data
    getTurnPrompt.mockResolvedValue({
      messages: [],
      sources: [characterSource({ summary: "alice", inclusion_reasons: ["present_in_scene"] })],
      budget_used: {},
      messages_hash: "h",
      composition_snapshot: null,
      summary: null,
    });

    renderPanel();
    await userEvent.click(await screen.findByRole("button", { name: /turn-1/ }));

    const card = await screen.findByTestId("character-card-alice");
    expect(card).toHaveTextContent("alice");
  });

  it("shows the not-available message when getTurnPrompt 404s", async () => {
    listTurns.mockResolvedValue([turn()]);
    getTurnPrompt.mockRejectedValue(new Error("HTTP 404"));

    renderPanel();
    await userEvent.click(await screen.findByRole("button", { name: /turn-1/ }));

    await waitFor(() => {
      expect(screen.getByText(/no audit available/i)).toBeInTheDocument();
    });
  });

  it("shows the no-audits message when listTurns returns []", async () => {
    listTurns.mockResolvedValue([]);
    renderPanel();
    expect(await screen.findByText(/no audits yet for this campaign/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- --run WhyCharacterPanel`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the panel**

Create `frontend/src/routes/observability/WhyCharacterPanel.tsx`:

```tsx
/**
 * "Why this character?" past-turn debug lens.
 *
 * Given a campaign, lists recent turn audits. When the user picks a
 * turn, fetches the audit's prompt sources, filters to character-kind,
 * groups by canonical character ref (parsed from `summary`), unions
 * their inclusion reasons, and renders one card per character.
 *
 * Spec: `docs/superpowers/specs/2026-05-20-why-this-character-design.md`.
 */

import { useEffect, useMemo, useState } from "react";

import {
  observabilityApi,
  type ContextSourceFromAudit,
  type ContextTier,
  type InclusionReason,
  type TurnAuditSummary,
  type TurnPromptResponse,
} from "../../api/observability";
import { viewsApi } from "../../api/views";
import type { ResolvedCharacter } from "../../api/types";
import { REASON_LABELS } from "./inclusionReasonLabels";

interface Props {
  campaignId: string;
}

const TIER_ORDER: Record<ContextTier, number> = {
  "lock-in": 0,
  spotlight: 1,
  background: 2,
  archive: 3,
};

interface CharacterCard {
  ref: string;
  displayName: string;
  tier: ContextTier;
  tokens: number;
  reasons: InclusionReason[];
}

export function extractCharacterRef(source: ContextSourceFromAudit): string {
  const s = source.summary;
  if (s.startsWith("Active PC: ")) return s.slice("Active PC: ".length);
  if (s.startsWith("voice:")) return s.slice("voice:".length);
  return s;
}

function groupCharacters(
  sources: ContextSourceFromAudit[],
  nameByRef: Map<string, string>,
): CharacterCard[] {
  const byRef = new Map<string, CharacterCard>();
  for (const src of sources) {
    if (src.kind !== "character") continue;
    const ref = extractCharacterRef(src);
    const existing = byRef.get(ref);
    if (!existing) {
      byRef.set(ref, {
        ref,
        displayName: nameByRef.get(ref) ?? ref,
        tier: src.tier,
        tokens: src.tokens,
        reasons: [...src.inclusion_reasons],
      });
      continue;
    }
    // Highest-priority tier wins (lower index in TIER_ORDER).
    if (TIER_ORDER[src.tier] < TIER_ORDER[existing.tier]) {
      existing.tier = src.tier;
    }
    existing.tokens += src.tokens;
    for (const r of src.inclusion_reasons) {
      if (!existing.reasons.includes(r)) existing.reasons.push(r);
    }
  }
  return [...byRef.values()].sort(
    (a, b) => TIER_ORDER[a.tier] - TIER_ORDER[b.tier] || b.tokens - a.tokens,
  );
}

function buildNameLookup(resolved: ResolvedCharacter[]): Map<string, string> {
  const map = new Map<string, string>();
  for (const row of resolved) {
    const id = row.character.id;
    map.set(id, row.character.name);
    // Library characters appear in audits as "library:<world>/<id>" or
    // similar. Index by the raw id as well so the lookup hits regardless
    // of which form the Context Builder emitted.
    if (row.character.world_id) {
      map.set(`library:${row.character.world_id}/${id}`, row.character.name);
    }
  }
  return map;
}

export function WhyCharacterPanel({ campaignId }: Props) {
  const [turns, setTurns] = useState<TurnAuditSummary[] | null>(null);
  const [turnsError, setTurnsError] = useState<string | null>(null);
  const [selectedTurnId, setSelectedTurnId] = useState<string | null>(null);
  const [prompt, setPrompt] = useState<TurnPromptResponse | null>(null);
  const [promptError, setPromptError] = useState<string | null>(null);
  const [characters, setCharacters] = useState<ResolvedCharacter[]>([]);

  useEffect(() => {
    let cancelled = false;
    setTurns(null);
    setSelectedTurnId(null);
    setPrompt(null);
    observabilityApi
      .listTurns(campaignId)
      .then((rows) => {
        if (!cancelled) {
          setTurns(rows);
          setTurnsError(null);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setTurnsError(err instanceof Error ? err.message : String(err));
          setTurns([]);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [campaignId]);

  useEffect(() => {
    let cancelled = false;
    viewsApi
      .listCharacters(campaignId)
      .then((rows) => {
        if (!cancelled) setCharacters(rows);
      })
      .catch(() => {
        if (!cancelled) setCharacters([]);
      });
    return () => {
      cancelled = true;
    };
  }, [campaignId]);

  useEffect(() => {
    if (!selectedTurnId) return;
    let cancelled = false;
    setPrompt(null);
    setPromptError(null);
    observabilityApi
      .getTurnPrompt(selectedTurnId)
      .then((res) => {
        if (!cancelled) setPrompt(res);
      })
      .catch((err) => {
        if (!cancelled) setPromptError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [selectedTurnId]);

  const nameByRef = useMemo(() => buildNameLookup(characters), [characters]);
  const cards = useMemo(
    () => (prompt ? groupCharacters(prompt.sources, nameByRef) : []),
    [prompt, nameByRef],
  );

  return (
    <section className="why-character-panel" aria-label="Why this character?">
      <header>
        <h2>Why this character?</h2>
        <p className="why-character-sub">
          Past-turn debug view — per-character inclusion reasons.
        </p>
      </header>

      <div className="why-character-layout">
        <aside className="why-character-turns" aria-label="Turns">
          {turns === null && <p className="why-character-loading">Loading turns…</p>}
          {turnsError && <p className="why-character-error">{turnsError}</p>}
          {turns !== null && turns.length === 0 && !turnsError && (
            <p className="why-character-empty">No audits yet for this campaign.</p>
          )}
          {turns !== null && turns.length > 0 && (
            <ul>
              {turns.map((t) => (
                <li key={t.turn_id}>
                  <button
                    type="button"
                    aria-pressed={selectedTurnId === t.turn_id}
                    className={selectedTurnId === t.turn_id ? "is-active" : ""}
                    onClick={() => setSelectedTurnId(t.turn_id)}
                  >
                    <span className="why-character-turn-id">{t.turn_id}</span>
                    <span className="why-character-turn-time">
                      {new Date(t.started_at).toLocaleString()}
                    </span>
                    <span className="why-character-turn-input">
                      {t.player_input.slice(0, 60)}
                      {t.player_input.length > 60 ? "…" : ""}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </aside>

        <main className="why-character-cards" aria-label="Character cards">
          {!selectedTurnId && <p className="why-character-empty">Pick a turn to inspect.</p>}
          {selectedTurnId && !prompt && !promptError && (
            <p className="why-character-loading">Loading audit…</p>
          )}
          {promptError && (
            <p className="why-character-empty">
              No audit available for that turn.
            </p>
          )}
          {prompt && cards.length === 0 && (
            <p className="why-character-empty">
              This turn's context had no character sources.
            </p>
          )}
          {cards.map((card) => (
            <article
              key={card.ref}
              data-testid={`character-card-${card.ref}`}
              className={`why-character-card why-character-tier-${card.tier}`}
            >
              <header>
                <h3>{card.displayName}</h3>
                {card.displayName !== card.ref && (
                  <small className="why-character-ref">{card.ref}</small>
                )}
                <span className="why-character-tier">{card.tier}</span>
                <span className="why-character-tokens">
                  {card.tokens.toLocaleString()} tok
                </span>
              </header>
              <ul className="why-character-reasons">
                {card.reasons.length === 0 ? (
                  <li className="why-character-empty">(no declared reason)</li>
                ) : (
                  card.reasons.map((r) => (
                    <li
                      key={r}
                      className={`why-character-reason why-character-reason-${r}`}
                    >
                      {REASON_LABELS[r] ?? r}
                    </li>
                  ))
                )}
              </ul>
            </article>
          ))}
        </main>
      </div>
    </section>
  );
}
```

- [ ] **Step 4: Create the barrel export**

Create `frontend/src/routes/observability/index.ts`:

```ts
export { WhyCharacterPanel } from "./WhyCharacterPanel";
```

- [ ] **Step 5: Run the panel test**

Run: `cd frontend && npm test -- --run WhyCharacterPanel`
Expected: PASS — 6 cases.

- [ ] **Step 6: Typecheck**

Run: `cd frontend && npm run typecheck`
Expected: zero errors.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/routes/observability/WhyCharacterPanel.tsx \
        frontend/src/routes/observability/index.ts \
        frontend/src/routes/observability/__tests__/WhyCharacterPanel.test.tsx
git commit -m "feat(observability): WhyCharacterPanel renders past-turn inclusion reasons"
```

---

## Task 4: Wire the route under the campaign tree

**Files:**
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Add the route**

In `frontend/src/App.tsx`, add an import after line 18:

```tsx
import { WhyCharacterPanel } from "./routes/observability";
```

Then inside the `<Route path="campaigns/:campaignId" element={<CampaignView />}>` block (between `images` and the closing tag), add a wrapper route:

```tsx
              <Route path="observability/turns" element={<ObservabilityTurnsRoute />} />
```

At the bottom of the `App.tsx` file (after the `App` function), add the wrapper:

```tsx
import { useParams } from "react-router-dom";

function ObservabilityTurnsRoute() {
  const { campaignId } = useParams<{ campaignId: string }>();
  if (!campaignId) return null;
  return <WhyCharacterPanel campaignId={campaignId} />;
}
```

> Note: react-router-dom is already imported at the top; consolidate the import rather than adding a second statement. The final import line should be `import { BrowserRouter, Route, Routes, useParams } from "react-router-dom";`.

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npm run typecheck`
Expected: zero errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "feat(routing): mount /observability/turns under campaign tree"
```

---

## Task 5: Add the sub-nav link inside CampaignView

**Files:**
- Modify: `frontend/src/routes/CampaignView.tsx`

- [ ] **Step 1: Locate the sub-nav**

Run: `grep -n "NavLink\|nav-link\|cast\|world\|timeline" frontend/src/routes/CampaignView.tsx | head -30`
Expected: find the existing list of in-campaign tabs (Cast, World, Timeline, Ledger, Mechanics, Composition, Images).

- [ ] **Step 2: Add the link**

In the JSX where the existing tabs are rendered, add a new `NavLink` after the last tab:

```tsx
<NavLink to={`/campaigns/${campaignId}/observability/turns`}>
  Observability
</NavLink>
```

Match the styling of the surrounding links exactly. Wrap the literal label in `{}` only if the surrounding pattern uses an array map; otherwise inline is fine.

> If a `tabs` array exists at the top of the file, prefer extending it: `{ to: "observability/turns", label: "Observability" }`.

- [ ] **Step 3: Run any existing CampaignView tests**

Run: `cd frontend && npm test -- --run CampaignView`
Expected: PASS. If a test snapshots the tab list, accept the diff (`npm test -- --run CampaignView -u`).

- [ ] **Step 4: Typecheck**

Run: `cd frontend && npm run typecheck`
Expected: zero errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/CampaignView.tsx
git commit -m "feat(campaign): add Observability tab to in-campaign nav"
```

---

## Task 6: Style the panel

**Files:**
- Modify: `frontend/src/index.css`

- [ ] **Step 1: Find an existing panel for style reference**

Run: `grep -n "inspector-panel\|inspector-source-row\|inspector-reason\|inspector-tier" frontend/src/index.css | head -20`
Expected: find the rules used by the live Context Inspector — we'll mirror them for visual consistency.

- [ ] **Step 2: Append the why-character styles**

Append to `frontend/src/index.css`:

```css
/* "Why this character?" past-turn lens. Mirrors the live Context
 * Inspector's tier/reason styling so the two views feel like siblings. */
.why-character-panel { padding: var(--space-3, 1rem); }
.why-character-panel header h2 { margin: 0 0 0.25rem 0; }
.why-character-sub { color: var(--color-muted, #888); margin-top: 0; }

.why-character-layout {
  display: grid;
  grid-template-columns: minmax(220px, 1fr) 3fr;
  gap: var(--space-3, 1rem);
}

.why-character-turns ul { list-style: none; padding: 0; margin: 0; }
.why-character-turns li { margin-bottom: 0.25rem; }
.why-character-turns button {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 0.25rem;
  width: 100%;
  text-align: left;
  background: transparent;
  border: 1px solid var(--color-border, #2a2a2a);
  border-radius: 4px;
  padding: 0.5rem;
  cursor: pointer;
}
.why-character-turns button.is-active {
  border-color: var(--color-accent, #6cf);
  background: var(--color-accent-bg, rgba(102, 204, 255, 0.08));
}
.why-character-turn-id { font-family: monospace; font-size: 0.85em; }
.why-character-turn-time { font-size: 0.8em; color: var(--color-muted, #888); }
.why-character-turn-input { grid-column: 1 / -1; font-size: 0.9em; }

.why-character-cards {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.why-character-card {
  border: 1px solid var(--color-border, #2a2a2a);
  border-radius: 6px;
  padding: 0.75rem;
}
.why-character-card header {
  display: grid;
  grid-template-columns: 1fr auto auto;
  gap: 0.5rem;
  align-items: baseline;
}
.why-character-card header h3 { margin: 0; }
.why-character-ref { font-family: monospace; color: var(--color-muted, #888); }
.why-character-tier { font-size: 0.75em; text-transform: uppercase; }
.why-character-tier-lock-in { border-left: 3px solid #d44; padding-left: 0.5rem; }
.why-character-tier-spotlight { border-left: 3px solid #f80; padding-left: 0.5rem; }
.why-character-tier-background { border-left: 3px solid #888; padding-left: 0.5rem; }
.why-character-tier-archive { border-left: 3px solid #555; padding-left: 0.5rem; }
.why-character-tokens { font-variant-numeric: tabular-nums; font-size: 0.85em; }

.why-character-reasons {
  list-style: none;
  padding: 0;
  margin: 0.5rem 0 0 0;
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem;
}
.why-character-reason {
  background: var(--color-chip-bg, #1f1f1f);
  padding: 0.15rem 0.5rem;
  border-radius: 999px;
  font-size: 0.8em;
}

.why-character-empty,
.why-character-loading,
.why-character-error {
  color: var(--color-muted, #888);
  font-style: italic;
}
.why-character-error { color: var(--color-danger, #e66); }
```

- [ ] **Step 3: Visual smoke-check**

Run: `cd frontend && npm run dev` (in background), open the browser to a campaign with at least one captured audit, navigate to the Observability tab, click a turn, confirm a card renders. Stop the dev server.

If you can't run the dev server, state that explicitly — type checks + tests are not sufficient evidence the panel looks correct.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/index.css
git commit -m "style(observability): why-character panel + cards + reason chips"
```

---

## Task 7: Lint, full test pass, PR

- [ ] **Step 1: Lint**

Run: `cd frontend && npm run lint`
Expected: zero errors. Fix anything reported before continuing.

- [ ] **Step 2: Full frontend tests**

Run: `cd frontend && npm test`
Expected: PASS, including the existing SourceList tests (regression check after Task 1).

- [ ] **Step 3: Full backend tests (smoke only — nothing should change)**

Run: `cd backend && python -m pytest tests/observability -q`
Expected: PASS — sanity check that we didn't somehow break observability backend tests.

- [ ] **Step 4: Push and open PR**

```bash
git push -u origin issue-352
gh pr create --title 'feat(observability): "Why this character?" debug lens (#352)' --body "$(cat <<'EOF'
## Summary
- Adds a `/campaigns/:id/observability/turns` route that lets users open a past turn audit and see which characters were in the prompt and why
- Frontend-only — the Context Builder already attaches per-source `inclusion_reasons` and the audit pipeline already persists them
- Shares the `REASON_LABELS` map between the live Context Inspector and the new past-turn lens

Closes #352.

## Test plan
- [ ] `cd frontend && npm test` passes (incl. new WhyCharacterPanel + observability client + inclusionReasonLabels tests)
- [ ] `cd frontend && npm run typecheck` passes
- [ ] `cd frontend && npm run lint` passes
- [ ] `cd frontend && npm run dev` — open a campaign with prior turns, navigate to the Observability tab, click a turn, confirm character cards render with their inclusion reasons

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Spec coverage check

| Spec section | Task |
|---|---|
| Frontend lens for past turns | Task 3 |
| New `/observability/turns` route | Task 4 |
| Character-only filter (`kind === "character"`) | Task 3 (`groupCharacters`) |
| Frontend filters from `/prompt` response | Task 2 + Task 3 |
| Campaign + turn list picker | Task 3 (left pane) |
| Resolve names via characters API | Task 3 (`buildNameLookup`) |
| Edge cases (audit 404, no character sources, no resolution, empty turns list) | Task 3 tests |
| Extract REASON_LABELS to shared module | Task 1 |
| Sub-nav entry | Task 5 |
| Backend round-trip test | *Out of scope; transitively covered by existing AuditStore tests, confirmed during plan write* |
