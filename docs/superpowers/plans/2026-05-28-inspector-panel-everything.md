# Inspector Panel "Show Everything" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Context Inspector show everything going into the next-post prompt — a comprehensive, source-by-source view with precise text — via an always-on inline HUD block plus an on-demand full-page overlay.

**Architecture:** Backend captures the exact rendered `text` on every `ContextSource` and emits sources for the previously-unrepresented prompt blocks (system / scene header / mechanics / recent posts / player input), so the source list reconstructs the entire prompt. The frontend collapses per-tier token bars into a single total (per-tier on hover), drops the live diff, shows precise text in expandable rows with pin/exclude, and adds a full-page overlay with a verbatim "raw messages" view.

**Tech Stack:** Python 3.12 / Pydantic / pytest (backend); TypeScript / React 18 / Vitest + React Testing Library (frontend). Backend tests: `cd backend && uv run pytest`. Frontend tests: `cd frontend && pnpm test`.

**Spec:** `docs/superpowers/specs/2026-05-28-inspector-panel-everything-design.md`

---

## File Structure

**Backend — modify:**
- `backend/src/grimoire/types/context.py` — add `text` field to `ContextSource`
- `backend/src/grimoire/types/inclusion_reasons.py` — add new reasons
- `backend/src/grimoire/context/assembler.py` — set `text` on tier-item sources; emit new block sources
- `backend/src/grimoire/context/builder.py` — set `text` on PC-card and commitments sources
- `backend/tests/context/` — new/updated unit tests

**Frontend — modify:**
- `frontend/src/api/inspector.ts` — add `text` to `ContextSourceExplanation`, new `InclusionReason` members, typed `getPreview`
- `frontend/src/routes/observability/inclusionReasonLabels.ts` — add labels for new reasons
- `frontend/src/routes/campaign/Inspector/TokenBars.tsx` → becomes total bar
- `frontend/src/routes/campaign/Inspector/SourceList.tsx` — precise-text scroll box
- `frontend/src/routes/campaign/Inspector/InspectorPanel.tsx` — remove tabs/diff, add Expand
- `frontend/src/index.css` — overlay styles

**Frontend — create:**
- `frontend/src/routes/campaign/Inspector/InspectorOverlay.tsx` — full-page overlay
- `frontend/src/routes/campaign/Inspector/__tests__/InspectorOverlay.test.tsx`

**Frontend — delete:**
- `frontend/src/routes/campaign/Inspector/DiffView.tsx`
- `frontend/src/routes/campaign/Inspector/__tests__/DiffView.test.tsx`

---

## Task 1: Add `text` field to `ContextSource`

**Files:**
- Modify: `backend/src/grimoire/types/context.py:13-25`
- Test: `backend/tests/context/test_context_source_text.py` (create)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/context/test_context_source_text.py`:

```python
from grimoire.types.context import ContextSource
from grimoire.types.state import ContextTier


def test_context_source_has_text_field_defaulting_empty():
    src = ContextSource(kind="character", scope="library", owner_id="x", tier=ContextTier.SPOTLIGHT)
    assert src.text == ""


def test_context_source_text_round_trips_through_model_dump():
    src = ContextSource(
        kind="character",
        scope="library",
        owner_id="x",
        tier=ContextTier.SPOTLIGHT,
        text="exact rendered text",
    )
    assert src.model_dump(mode="json")["text"] == "exact rendered text"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/context/test_context_source_text.py -v`
Expected: FAIL — `ContextSource` has no field `text` (validation error on the second test, or `AttributeError`).

- [ ] **Step 3: Add the field**

In `backend/src/grimoire/types/context.py`, add to `ContextSource` (after `summary`):

```python
class ContextSource(BaseModel):
    """One source that contributed to the assembled prompt."""

    kind: str  # 'character', 'location', 'lore', 'scene', 'fact', 'commitment', ...
    scope: Scope
    owner_id: str | None  # library asset id, campaign id, or None for system
    tier: ContextTier
    library_version: int | None = None
    override_applied: bool = False
    tokens: int = 0
    summary: str = ""
    text: str = ""  # exact rendered text this source contributed to the prompt
    source_id: str = ""
    inclusion_reasons: list[InclusionReason] = Field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/context/test_context_source_text.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/types/context.py backend/tests/context/test_context_source_text.py
git commit -m "feat(context): add text field to ContextSource"
```

---

## Task 2: Populate `text` on tier-item, PC-card, and commitments sources

**Files:**
- Modify: `backend/src/grimoire/context/assembler.py:250-261` (the two loops in `_pack_tier`)
- Modify: `backend/src/grimoire/context/builder.py:455-466` (PC-card + commitments source append)
- Test: `backend/tests/context/test_source_text_population.py` (create)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/context/test_source_text_population.py`:

```python
import pytest

from grimoire.context.assembler import PromptAssembler
from grimoire.context.config import ContextBuilderConfig
from grimoire.context.tokens import cheap_estimator
from grimoire.context.types import BuiltContext, TierItem
from grimoire.types.context import ContextSource
from grimoire.types.state import ContextTier


def _src(kind: str) -> ContextSource:
    return ContextSource(kind=kind, scope="library", owner_id="x", tier=ContextTier.SPOTLIGHT)


@pytest.mark.asyncio
async def test_pack_tier_copies_item_text_onto_source():
    config = ContextBuilderConfig()
    assembler = PromptAssembler(config=config, estimator=cheap_estimator(config.chars_per_token))
    item = TierItem(
        tier=ContextTier.SPOTLIGHT,
        section="cast",
        text="winifred is present and wary.",
        source=_src("character"),
    )
    ctx = BuiltContext(
        composition=None,
        style_text="",
        content_boundaries="",
        system_meta="",
        scene_header="",
        active_pc_card="",
        active_pc_name="",
        mechanics_block="",
        commitments_block="",
        spotlight_items=[item],
        sources=[item.source],
    )
    prompt = await assembler.assemble(ctx, player_input="")
    packed = next(s for s in prompt.sources if s.kind == "character")
    assert packed.text == "winifred is present and wary."
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/context/test_source_text_population.py -v`
Expected: FAIL — `packed.text == ""` (assertion error), because `_pack_tier` only sets `tokens`.

- [ ] **Step 3: Set `text` in `_pack_tier`**

In `backend/src/grimoire/context/assembler.py`, inside `_pack_tier`, both loops, set the source text alongside tokens:

```python
        for item in pinned_items:
            cost = await self._tokens(item.text)
            packed.append(item.text)
            item.source.tokens = cost
            item.source.text = item.text
            used += cost
        for item in normal_items:
            cost = await self._tokens(item.text)
            if used + cost > budget:
                continue
            packed.append(item.text)
            item.source.tokens = cost
            item.source.text = item.text
            used += cost
```

- [ ] **Step 4: Set `text` on PC-card and commitments sources in the builder**

In `backend/src/grimoire/context/builder.py`, in the "Build full sources list" block, set text before appending. Replace the active-PC and commitments append blocks:

```python
        sources: list[ContextSource] = []
        if active_pc_source is not None and not pins.is_excluded(active_pc_source):
            active_pc_source.text = active_pc_card
            if pins.is_pinned(active_pc_source) and (
                InclusionReason.PINNED_BY_USER not in active_pc_source.inclusion_reasons
            ):
                active_pc_source.inclusion_reasons.append(InclusionReason.PINNED_BY_USER)
            sources.append(active_pc_source)
        if commitments_source is not None and not pins.is_excluded(commitments_source):
            commitments_source.text = commitments_block
            if pins.is_pinned(commitments_source) and (
                InclusionReason.PINNED_BY_USER not in commitments_source.inclusion_reasons
            ):
                commitments_source.inclusion_reasons.append(InclusionReason.PINNED_BY_USER)
            sources.append(commitments_source)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/context/test_source_text_population.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/context/assembler.py backend/src/grimoire/context/builder.py backend/tests/context/test_source_text_population.py
git commit -m "feat(context): capture precise text on tier/PC/commitments sources"
```

---

## Task 3: Add inclusion reasons for the always-on prompt blocks

**Files:**
- Modify: `backend/src/grimoire/types/inclusion_reasons.py:17-35`
- Test: `backend/tests/context/test_inclusion_reasons_new.py` (create)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/context/test_inclusion_reasons_new.py`:

```python
from grimoire.types.inclusion_reasons import InclusionReason


def test_new_block_reasons_exist():
    assert InclusionReason.SYSTEM_PROMPT == "system_prompt"
    assert InclusionReason.SCENE_HEADER == "scene_header"
    assert InclusionReason.VERBATIM_RECENT == "verbatim_recent"
    assert InclusionReason.PLAYER_INPUT == "player_input"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/context/test_inclusion_reasons_new.py -v`
Expected: FAIL — `AttributeError: SYSTEM_PROMPT`.

- [ ] **Step 3: Add the enum members**

In `backend/src/grimoire/types/inclusion_reasons.py`, append to `InclusionReason` (after `TRANSIENT_STATE_ACTIVE`):

```python
    TRANSIENT_STATE_ACTIVE = "transient_state_active"
    SYSTEM_PROMPT = "system_prompt"
    SCENE_HEADER = "scene_header"
    VERBATIM_RECENT = "verbatim_recent"
    PLAYER_INPUT = "player_input"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/context/test_inclusion_reasons_new.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/types/inclusion_reasons.py backend/tests/context/test_inclusion_reasons_new.py
git commit -m "feat(context): add inclusion reasons for always-on prompt blocks"
```

---

## Task 4: Emit comprehensive sources for system / scene / mechanics / recent-posts / player-input

**Files:**
- Modify: `backend/src/grimoire/context/assembler.py:32-135` (`assemble`)
- Test: `backend/tests/context/test_comprehensive_sources.py` (create)

The new sources are appended in `assemble()` where the rendered text and the token estimator are both available. Each uses `make_source_id(kind, None)` and tier `ContextTier.LOCK_IN` (these blocks are always present; the UI labels them, see Task 9).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/context/test_comprehensive_sources.py`:

```python
import pytest

from grimoire.context.assembler import PromptAssembler
from grimoire.context.config import ContextBuilderConfig
from grimoire.context.tokens import cheap_estimator
from grimoire.context.types import BuiltContext
from grimoire.types.state import ContextTier


def _assembler() -> PromptAssembler:
    config = ContextBuilderConfig()
    return PromptAssembler(config=config, estimator=cheap_estimator(config.chars_per_token))


def _ctx(**overrides) -> BuiltContext:
    base = dict(
        composition=None,
        style_text="Write vividly.",
        content_boundaries="",
        system_meta="Worlds in play: WoD (wod)",
        scene_header="The Docks, night.",
        active_pc_card="",
        active_pc_name="",
        mechanics_block="",
        commitments_block="",
        recent_posts_text="narrator: Fog rolls in.\n\nMara: You're late.",
        sources=[],
    )
    base.update(overrides)
    return BuiltContext(**base)


@pytest.mark.asyncio
async def test_emits_system_scene_recent_and_player_input_sources():
    prompt = await _assembler().assemble(_ctx(), player_input="I step onto the pier.")
    kinds = {s.kind: s for s in prompt.sources}
    assert "system" in kinds
    assert kinds["system"].text != ""
    assert kinds["scene_header"].text == "The Docks, night."
    assert kinds["recent_posts"].text.startswith("narrator: Fog rolls in.")
    assert kinds["player_input"].text == "I step onto the pier."
    assert kinds["player_input"].tier == ContextTier.LOCK_IN


@pytest.mark.asyncio
async def test_skips_empty_blocks():
    prompt = await _assembler().assemble(
        _ctx(scene_header="", recent_posts_text="", system_meta="", style_text="", content_boundaries=""),
        player_input="",
    )
    kinds = {s.kind for s in prompt.sources}
    assert "scene_header" not in kinds
    assert "recent_posts" not in kinds
    assert "player_input" not in kinds


@pytest.mark.asyncio
async def test_new_sources_do_not_change_messages_hash_or_budget():
    ctx = _ctx()
    prompt = await _assembler().assemble(ctx, player_input="hello")
    # budget_used only ever has the four real tiers.
    assert set(prompt.budget_used.keys()) == set(ContextTier)
    # messages_hash is derived from messages only; recompute and confirm.
    from grimoire.context.assembler import _hash_messages

    assert prompt.messages_hash == _hash_messages(prompt.messages)
    # The system/recent/player sources are attribution only — none of their
    # ids leak into the message metadata.
    assert all("source_id" not in (m.metadata or {}) for m in prompt.messages)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/context/test_comprehensive_sources.py -v`
Expected: FAIL — `"system" in kinds` is false (assembler does not emit these sources yet).

- [ ] **Step 3: Emit the new sources in `assemble`**

In `backend/src/grimoire/context/assembler.py`, add a helper and call it just before constructing the `AssembledPrompt`. First add imports at the top of the file (alongside existing imports):

```python
from grimoire.context.types import make_source_id
from grimoire.types.context import ContextSource
from grimoire.types.inclusion_reasons import InclusionReason
```

Then, in `assemble`, replace the tail (from `sources = list(ctx.sources)` through the `return AssembledPrompt(...)`) with:

```python
        sources = list(ctx.sources)
        await self._append_block_sources(
            sources,
            system_text=system_text,
            scene_header=ctx.scene_header,
            mechanics_block=ctx.mechanics_block,
            recent_posts_text=ctx.recent_posts_text,
            player_input=player_input,
        )
        summary = self._summary(ctx, budget_used)
        composition_snapshot = self._composition_snapshot(ctx.composition)
        return AssembledPrompt(
            messages=messages,
            params=params,
            budget_used=budget_used,
            sources=sources,
            summary=summary,
            composition_snapshot=composition_snapshot,
            messages_hash=_hash_messages(messages),
        )

    async def _append_block_sources(
        self,
        sources: list[ContextSource],
        *,
        system_text: str,
        scene_header: str,
        mechanics_block: str,
        recent_posts_text: str,
        player_input: str,
    ) -> None:
        """Emit attribution sources for the always-on prompt blocks that
        otherwise have no ``ContextSource`` — so the inspector's source list
        reconstructs the entire prompt. Attribution only: these do not affect
        ``messages`` or ``budget_used``."""
        blocks = [
            ("system", system_text, InclusionReason.SYSTEM_PROMPT),
            ("scene_header", scene_header, InclusionReason.SCENE_HEADER),
            ("mechanics", mechanics_block, InclusionReason.MECHANICS_RELEVANT),
            ("recent_posts", recent_posts_text, InclusionReason.VERBATIM_RECENT),
            ("player_input", player_input, InclusionReason.PLAYER_INPUT),
        ]
        for kind, text, reason in blocks:
            if not text:
                continue
            sources.append(
                ContextSource(
                    kind=kind,
                    scope="campaign-local",
                    owner_id=None,
                    tier=ContextTier.LOCK_IN,
                    tokens=await self._tokens(text),
                    text=text,
                    source_id=make_source_id(kind, None),
                    inclusion_reasons=[reason],
                )
            )
```

Note: `make_source_id`, `ContextSource`, and `InclusionReason` may already be imported indirectly — if Python reports a redefinition/unused-import lint error, dedupe the import lines rather than adding duplicates.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/context/test_comprehensive_sources.py -v`
Expected: PASS (all three tests).

- [ ] **Step 5: Run the broader context + observability suites to catch source-count assertions**

Run: `cd backend && uv run pytest tests/context tests/observability -v`
Expected: PASS. If any test asserts an exact `len(sources)` / source count, update that expectation to include the newly-emitted block sources (the new sources are correct behavior). Note in the commit which expectations were updated.

- [ ] **Step 6: Run the frozen-campaign regression to confirm prompt stability**

Run: `cd backend && uv run pytest -m frozen_campaign -v`
Expected: PASS — `messages_hash` and `budget_used` are unchanged by this task. If a frozen test fails on a source count (not on messages_hash), refresh that expectation; a `messages_hash` failure means the change incorrectly touched message assembly and must be fixed.

- [ ] **Step 7: Lint + commit**

```bash
cd backend && uv run ruff check && uv run ruff format
git add backend/src/grimoire/context/assembler.py backend/tests/context/test_comprehensive_sources.py
git commit -m "feat(context): emit comprehensive sources for always-on prompt blocks (#486)"
```

---

## Task 5: Frontend types — `text`, new reasons, labels, typed `getPreview`

**Files:**
- Modify: `frontend/src/api/inspector.ts:14-59`, `123-128`
- Modify: `frontend/src/routes/observability/inclusionReasonLabels.ts:10-29`
- Test: `frontend/src/routes/observability/__tests__/inclusionReasonLabels.test.ts` (create)

- [ ] **Step 1: Write the failing test**

Create `frontend/src/routes/observability/__tests__/inclusionReasonLabels.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import { REASON_LABELS } from "../inclusionReasonLabels";

describe("REASON_LABELS", () => {
  it("has labels for the new always-on block reasons", () => {
    expect(REASON_LABELS.system_prompt).toBe("System prompt");
    expect(REASON_LABELS.scene_header).toBe("Scene header");
    expect(REASON_LABELS.verbatim_recent).toBe("Recent posts (verbatim)");
    expect(REASON_LABELS.player_input).toBe("Player input");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && pnpm test -- inclusionReasonLabels`
Expected: FAIL — properties undefined / type errors.

- [ ] **Step 3: Extend the `InclusionReason` union and add `text` in `api/inspector.ts`**

In `frontend/src/api/inspector.ts`, append to the `InclusionReason` union:

```ts
export type InclusionReason =
  | "present_in_scene"
  | "mentioned_in_recent_posts"
  | "commitment_open_to_pc"
  | "keyword_triggered"
  | "relationship_to_present"
  | "pinned_by_user"
  | "scene_anchor"
  | "mechanics_relevant"
  | "style_guide_active"
  | "pc_card"
  | "composition_default"
  | "extras_pinned_to_hud"
  | "extras_default_visible"
  | "lore_before_cast"
  | "lore_after_cast"
  | "lore_at_depth"
  | "lore_archive"
  | "transient_state_active"
  | "system_prompt"
  | "scene_header"
  | "verbatim_recent"
  | "player_input";
```

Add `text` to `ContextSourceExplanation`:

```ts
export interface ContextSourceExplanation {
  source_id: string;
  owner_id: string | null;
  kind: string;
  scope: string;
  tier: ContextTier;
  library_version: number | null;
  inclusion_reasons: InclusionReason[];
  tokens: number;
  summary: string;
  text: string;
}
```

Add preview-detail types (near `PreviewResponse`) and type `getPreview`:

```ts
export interface PreviewMessage {
  role: string;
  content: string;
  name?: string | null;
  metadata?: Record<string, unknown>;
}

export interface PreviewDetail {
  messages: PreviewMessage[];
  sources: ContextSourceExplanation[];
  budget_used: Record<ContextTier, number>;
  messages_hash: string;
}
```

And change the `getPreview` method to:

```ts
  getPreview(campaignId: string, handle: string, sessionId: string): Promise<PreviewDetail> {
    return api.get(
      `${base(campaignId)}/preview/${encodeURIComponent(handle)}`,
      { query: { session_id: sessionId } },
    );
  },
```

- [ ] **Step 4: Add the labels**

In `frontend/src/routes/observability/inclusionReasonLabels.ts`, append to `REASON_LABELS`:

```ts
  transient_state_active: "Transient state active",
  system_prompt: "System prompt",
  scene_header: "Scene header",
  verbatim_recent: "Recent posts (verbatim)",
  player_input: "Player input",
};
```

- [ ] **Step 5: Run test + typecheck to verify pass**

Run: `cd frontend && pnpm test -- inclusionReasonLabels && pnpm typecheck`
Expected: PASS (label test passes; `tsc` is happy that `Record<InclusionReason, string>` is exhaustive).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/inspector.ts frontend/src/routes/observability/inclusionReasonLabels.ts frontend/src/routes/observability/__tests__/inclusionReasonLabels.test.ts
git commit -m "feat(inspector): frontend types for source text + always-on reasons"
```

---

## Task 6: Replace per-tier bars with a single total bar (per-tier on hover)

**Files:**
- Modify: `frontend/src/routes/campaign/Inspector/TokenBars.tsx` (full rewrite)
- Modify: `frontend/src/routes/campaign/Inspector/__tests__/TokenBars.test.tsx` (rewrite)

- [ ] **Step 1: Rewrite the test**

Replace `frontend/src/routes/campaign/Inspector/__tests__/TokenBars.test.tsx` with:

```tsx
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import type { PreviewSummary } from "../../../../api/inspector";
import { TokenBars } from "../TokenBars";

const summary: PreviewSummary = {
  handle: "ph_abc",
  per_tier_tokens: { "lock-in": 1500, spotlight: 12000, background: 5000, archive: 0 },
  per_tier_budget: { "lock-in": 8000, spotlight: 40000, background: 30000, archive: 20000 },
  source_count: 7,
  messages_hash: "h",
};

describe("TokenBars (total)", () => {
  it("renders empty state when no summary is given", () => {
    render(<TokenBars summary={null} />);
    expect(screen.getByText(/Type to preview/i)).toBeInTheDocument();
  });

  it("renders the summed total used / budget", () => {
    render(<TokenBars summary={summary} />);
    // 1500+12000+5000+0 = 18500 ; 8000+40000+30000+20000 = 98000
    expect(screen.getByText("18,500 / 98,000")).toBeInTheDocument();
  });

  it("exposes the per-tier split for hover/expand", () => {
    render(<TokenBars summary={summary} />);
    const bar = screen.getByLabelText(/per-tier token usage/i);
    expect(bar.getAttribute("title")).toMatch(/lock-in 1,500/);
    expect(bar.getAttribute("title")).toMatch(/spotlight 12,000/);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && pnpm test -- TokenBars`
Expected: FAIL — the current component renders per-tier rows, not a single total.

- [ ] **Step 3: Rewrite `TokenBars.tsx`**

Replace `frontend/src/routes/campaign/Inspector/TokenBars.tsx` with:

```tsx
/**
 * Total token-usage bar for the Inspector panel. Shows summed used/budget
 * across all tiers; the per-tier breakdown is exposed via the bar's title
 * (hover) so the inline block stays compact.
 */

import type { ContextTier, PreviewSummary } from "../../../api/inspector";

const TIERS: ContextTier[] = ["lock-in", "spotlight", "background", "archive"];

const TIER_LABELS: Record<ContextTier, string> = {
  "lock-in": "lock-in",
  spotlight: "spotlight",
  background: "background",
  archive: "archive",
};

interface Props {
  summary: PreviewSummary | null;
  loading?: boolean;
}

export function TokenBars({ summary, loading }: Props) {
  if (!summary) {
    return (
      <p className="inspector-empty">
        {loading ? "Computing preview…" : "Type to preview the next prompt."}
      </p>
    );
  }
  const used = TIERS.reduce((acc, t) => acc + (summary.per_tier_tokens[t] ?? 0), 0);
  const budget = TIERS.reduce((acc, t) => acc + (summary.per_tier_budget[t] ?? 0), 0);
  const ratio = budget > 0 ? Math.min(1, used / budget) : 0;
  const over = budget > 0 && used > budget;
  const breakdown = TIERS.map(
    (t) => `${TIER_LABELS[t]} ${(summary.per_tier_tokens[t] ?? 0).toLocaleString()}`,
  ).join(" · ");
  return (
    <div
      className={`inspector-token-total ${over ? "is-over" : ""}`}
      aria-label="Per-tier token usage"
      title={breakdown}
    >
      <span className="inspector-token-bar" aria-hidden>
        <span className="inspector-token-fill" style={{ width: `${(ratio * 100).toFixed(1)}%` }} />
      </span>
      <span className="inspector-token-counts">
        {used.toLocaleString()} / {budget.toLocaleString()}
      </span>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && pnpm test -- TokenBars`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/campaign/Inspector/TokenBars.tsx frontend/src/routes/campaign/Inspector/__tests__/TokenBars.test.tsx
git commit -m "feat(inspector): single total token bar with per-tier on hover"
```

---

## Task 7: Show precise text in expandable source rows

**Files:**
- Modify: `frontend/src/routes/campaign/Inspector/SourceList.tsx:68-87`
- Modify: `frontend/src/routes/campaign/Inspector/__tests__/SourceList.test.tsx`

- [ ] **Step 1: Add a failing test for precise text**

Append to `frontend/src/routes/campaign/Inspector/__tests__/SourceList.test.tsx` (and add `text: "..."` to the two existing fixtures so they typecheck — set `text: "PC card body"` on `src_pc` and `text: "winifred body text"` on `src_florence`):

```tsx
  it("expands a row to show the precise text", () => {
    render(<SourceList campaignId="camp" sources={sources} />);
    const florenceRow = screen
      .getAllByRole("button")
      .find((b) => b.textContent?.includes("winifred"));
    fireEvent.click(florenceRow!);
    expect(screen.getByText("winifred body text")).toBeInTheDocument();
  });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && pnpm test -- SourceList`
Expected: FAIL — precise text is not rendered (and/or type error until fixtures get `text`).

- [ ] **Step 3: Render precise text in the expanded detail**

In `frontend/src/routes/campaign/Inspector/SourceList.tsx`, in `SourceRow`'s expanded `<div className="inspector-source-details">`, add a text box before `<PinControls .../>`:

```tsx
      {open && (
        <div className="inspector-source-details">
          <p className="inspector-source-id">
            <code>{source.source_id || "(no id)"}</code>
          </p>
          <ul className="inspector-reason-list">
            {source.inclusion_reasons.length === 0 ? (
              <li className="inspector-empty">No declared reason.</li>
            ) : (
              source.inclusion_reasons.map((r) => (
                <li key={r} className={`inspector-reason inspector-reason-${r}`}>
                  {REASON_LABELS[r] ?? r}
                </li>
              ))
            )}
          </ul>
          {source.text ? (
            <pre className="inspector-source-text">{source.text}</pre>
          ) : (
            <p className="inspector-empty">No text captured for this source.</p>
          )}
          <PinControls campaignId={campaignId} source={source} onChanged={onChanged} />
        </div>
      )}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && pnpm test -- SourceList`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/campaign/Inspector/SourceList.tsx frontend/src/routes/campaign/Inspector/__tests__/SourceList.test.tsx
git commit -m "feat(inspector): show precise source text in expanded rows"
```

---

## Task 8: Remove diff, rework `InspectorPanel`, add the full-page overlay

This is one cohesive change: drop the live diff, replace the panel body with the
total bar + comprehensive source list + Expand button, and add the overlay.
Removing diff and rebuilding the panel in the same task keeps the tree compiling.

**Files:**
- Delete: `frontend/src/routes/campaign/Inspector/DiffView.tsx`
- Delete: `frontend/src/routes/campaign/Inspector/__tests__/DiffView.test.tsx`
- Modify: `frontend/src/routes/campaign/Inspector/InspectorPanel.tsx` (full rewrite)
- Create: `frontend/src/routes/campaign/Inspector/InspectorOverlay.tsx`
- Create: `frontend/src/routes/campaign/Inspector/__tests__/InspectorOverlay.test.tsx`

- [ ] **Step 0: Delete the diff files**

```bash
git rm frontend/src/routes/campaign/Inspector/DiffView.tsx frontend/src/routes/campaign/Inspector/__tests__/DiffView.test.tsx
```

The full `InspectorPanel.tsx` replacement in Step 5 removes every remaining
diff reference (the `DiffView`/`ContextDiff` imports, the diff state, the
`prevHandleRef`/`lastHandleRef` refs, `computeDiff`, and the tab nav), so no
diff symbols survive. `ContextDiff`/`SourceVersionChange` stay exported but
unused in `api/inspector.ts` — fine (exported types are not flagged).

- [ ] **Step 1: Write the failing overlay test**

Create `frontend/src/routes/campaign/Inspector/__tests__/InspectorOverlay.test.tsx`:

```tsx
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import type { ContextSourceExplanation, PreviewDetail } from "../../../../api/inspector";
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
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && pnpm test -- InspectorOverlay`
Expected: FAIL — `InspectorOverlay` does not exist.

- [ ] **Step 3: Create `InspectorOverlay.tsx`**

Create `frontend/src/routes/campaign/Inspector/InspectorOverlay.tsx`:

```tsx
/**
 * Full-page overlay for deep inspection of the next-post context.
 *
 * Master/detail over the comprehensive source list (full precise text +
 * pin/exclude), plus a verbatim "raw messages" view of the assembled
 * prompt fetched on demand. Uses the codebase modal-backdrop/modal idiom.
 */

import { useState } from "react";

import {
  inspectorApi,
  type ContextSourceExplanation,
  type ContextTier,
  type PreviewDetail,
  type PreviewSummary,
} from "../../../api/inspector";
import { REASON_LABELS } from "../../observability/inclusionReasonLabels";
import { PinControls } from "./PinControls";

const TIERS: ContextTier[] = ["lock-in", "spotlight", "background", "archive"];
const TIER_ORDER: Record<ContextTier, number> = {
  "lock-in": 0,
  spotlight: 1,
  background: 2,
  archive: 3,
};

interface Props {
  campaignId: string;
  sessionId: string;
  handle: string;
  sources: ContextSourceExplanation[];
  summary: PreviewSummary | null;
  onClose: () => void;
  onChanged: () => void;
}

export function InspectorOverlay({
  campaignId,
  sessionId,
  handle,
  sources,
  summary,
  onClose,
  onChanged,
}: Props) {
  const sorted = [...sources].sort(
    (a, b) => TIER_ORDER[a.tier] - TIER_ORDER[b.tier] || b.tokens - a.tokens,
  );
  const [selectedId, setSelectedId] = useState<string | null>(sorted[0]?.source_id ?? null);
  const [raw, setRaw] = useState(false);
  const [detail, setDetail] = useState<PreviewDetail | null>(null);
  const [rawErr, setRawErr] = useState<string | null>(null);

  const selected = sorted.find((s) => s.source_id === selectedId) ?? null;

  const toggleRaw = async () => {
    const next = !raw;
    setRaw(next);
    if (next && !detail) {
      try {
        setDetail(await inspectorApi.getPreview(campaignId, handle, sessionId));
      } catch (err) {
        setRawErr(err instanceof Error ? err.message : String(err));
      }
    }
  };

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label="Context for next post">
      <div className="modal inspector-overlay">
        <header className="inspector-overlay-header">
          <h2>Context for next post</h2>
          <div className="inspector-overlay-tiers">
            {TIERS.map((t) => (
              <span key={t} className="inspector-overlay-tier-chip">
                {t} {(summary?.per_tier_tokens[t] ?? 0).toLocaleString()}
              </span>
            ))}
          </div>
          <div className="inspector-overlay-actions">
            <button type="button" onClick={() => void toggleRaw()}>
              {raw ? "By source" : "Raw messages"}
            </button>
            <button type="button" onClick={onClose} aria-label="Close">
              ✕
            </button>
          </div>
        </header>

        {raw ? (
          <div className="inspector-overlay-raw">
            {rawErr && <p className="inspector-error">{rawErr}</p>}
            {!detail ? (
              <p className="inspector-empty">Loading messages…</p>
            ) : (
              <ol className="inspector-raw-messages">
                {detail.messages.map((m, i) => (
                  <li key={i} className="inspector-raw-message">
                    <header>
                      <span className="inspector-raw-role">{m.role}</span>
                      {typeof m.metadata?.tier === "string" && (
                        <span className="inspector-raw-tier">{m.metadata.tier}</span>
                      )}
                    </header>
                    <pre>{m.content}</pre>
                  </li>
                ))}
              </ol>
            )}
          </div>
        ) : (
          <div className="inspector-overlay-body">
            <ul className="inspector-overlay-list" aria-label="Sources">
              {sorted.map((s) => (
                <li key={s.source_id || `${s.kind}:${s.owner_id}`}>
                  <button
                    type="button"
                    className={s.source_id === selectedId ? "is-active" : ""}
                    onClick={() => setSelectedId(s.source_id)}
                  >
                    <span className="inspector-source-tier">{s.tier}</span>
                    <span className="inspector-source-kind">{s.kind}</span>
                    <span className="inspector-source-headline">
                      {s.summary || s.owner_id || s.kind}
                    </span>
                    <span className="inspector-source-tokens">{s.tokens.toLocaleString()} tok</span>
                  </button>
                </li>
              ))}
              {sorted.length === 0 && <li className="inspector-empty">No sources.</li>}
            </ul>
            <div className="inspector-overlay-detail">
              {selected ? (
                <>
                  <ul className="inspector-reason-list">
                    {selected.inclusion_reasons.map((r) => (
                      <li key={r} className="inspector-reason">
                        {REASON_LABELS[r] ?? r}
                      </li>
                    ))}
                  </ul>
                  <pre className="inspector-overlay-text">
                    {selected.text || "No text captured for this source."}
                  </pre>
                  <PinControls campaignId={campaignId} source={selected} onChanged={onChanged} />
                </>
              ) : (
                <p className="inspector-empty">Select a source.</p>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run overlay test to verify it passes**

Run: `cd frontend && pnpm test -- InspectorOverlay`
Expected: PASS.

- [ ] **Step 5: Rework `InspectorPanel.tsx` body + wire the overlay**

Replace the body of `InspectorPanel.tsx` so it renders: error, the total `TokenBars`, the comprehensive `SourceList` (no tabs), an Expand button, and the overlay when open. The final component:

```tsx
/**
 * Context Inspector panel — live preview of everything the LLM will see on
 * the next turn: total token budget, a comprehensive source list (each row
 * expandable to its precise text, with pin/exclude), and an Expand button
 * that opens a full-page overlay for deep reading.
 *
 * Mounts inside the campaign Play view; the host supplies the draft player
 * input + session id, which this panel debounces into POST /preview calls.
 */

import { useCallback, useEffect, useState } from "react";

import { inspectorApi, type ContextSourceExplanation } from "../../../api/inspector";
import { InspectorOverlay } from "./InspectorOverlay";
import { SourceList } from "./SourceList";
import { TokenBars } from "./TokenBars";
import { useLivePreview } from "./useLivePreview";

interface Props {
  campaignId: string;
  playerInput: string;
  sessionId: string;
  pcRef?: string | null;
  enabled?: boolean;
}

export function InspectorPanel({
  campaignId,
  playerInput,
  sessionId,
  pcRef,
  enabled = true,
}: Props) {
  const live = useLivePreview({
    campaignId,
    playerInput,
    sessionId,
    pcRef: pcRef ?? undefined,
    enabled,
  });

  const [explanations, setExplanations] = useState<ContextSourceExplanation[]>([]);
  const [explainErr, setExplainErr] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (!live.handle) return;
    let cancelled = false;
    inspectorApi
      .explain(campaignId, live.handle, sessionId)
      .then((rows) => {
        if (!cancelled) {
          setExplanations(rows);
          setExplainErr(null);
        }
      })
      .catch((err) => {
        if (!cancelled) setExplainErr(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [campaignId, sessionId, live.handle]);

  const handleChanged = useCallback(() => {
    live.refresh();
  }, [live]);

  return (
    <aside className="inspector-panel" aria-label="Context inspector">
      <div className="scene-setting-block" aria-label="Context inspector">
        {live.error && <p className="inspector-error">{live.error}</p>}

        <div className="scene-setting-entry scene-setting-entry-full">
          <span className="scene-setting-label">
            Next-post context{live.loading ? " …" : ""}
          </span>
          <TokenBars summary={live.summary} loading={live.loading} />
        </div>

        <div className="scene-setting-entry scene-setting-entry-full inspector-tab-body">
          {explainErr && <p className="inspector-error">{explainErr}</p>}
          <SourceList campaignId={campaignId} sources={explanations} onChanged={handleChanged} />
        </div>

        {live.handle && explanations.length > 0 && (
          <div className="scene-setting-entry scene-setting-entry-full">
            <button
              type="button"
              className="inspector-expand-btn"
              onClick={() => setExpanded(true)}
            >
              ⤢ Expand full context
            </button>
          </div>
        )}
      </div>

      {expanded && live.handle && (
        <InspectorOverlay
          campaignId={campaignId}
          sessionId={sessionId}
          handle={live.handle}
          sources={explanations}
          summary={live.summary}
          onClose={() => setExpanded(false)}
          onChanged={handleChanged}
        />
      )}
    </aside>
  );
}
```

- [ ] **Step 6: Run the inspector tests + typecheck**

Run: `cd frontend && pnpm test -- Inspector && pnpm typecheck`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
# -A so the Step 0 diff-file deletions are committed alongside the rewrite.
git add -A frontend/src/routes/campaign/Inspector/
git commit -m "feat(inspector): drop diff, comprehensive source list + overlay (#486)"
```

---

## Task 9: Overlay + total-bar styles

**Files:**
- Modify: `frontend/src/index.css`

- [ ] **Step 1: Add styles**

Append to `frontend/src/index.css` (reuse existing CSS variables; mirror existing `.inspector-*` / `.modal` conventions):

```css
/* Inspector: total token bar */
.inspector-token-total {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  cursor: help;
}
.inspector-token-total.is-over .inspector-token-fill {
  background: var(--danger, #d66);
}

/* Inspector: precise source text */
.inspector-source-text,
.inspector-overlay-text {
  font-family: var(--mono, monospace);
  font-size: 0.75rem;
  white-space: pre-wrap;
  background: var(--surface-sunken, #111);
  border-radius: 4px;
  padding: 0.4rem;
  margin: 0.35rem 0;
}
.inspector-source-text {
  max-height: 8rem;
  overflow: auto;
}

/* Inspector: expand button */
.inspector-expand-btn {
  width: 100%;
}

/* Inspector: full-page overlay */
.inspector-overlay {
  width: min(1100px, 96vw);
  height: 90vh;
  display: flex;
  flex-direction: column;
}
.inspector-overlay-header {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  flex-wrap: wrap;
}
.inspector-overlay-actions {
  margin-left: auto;
  display: flex;
  gap: 0.5rem;
}
.inspector-overlay-tiers {
  display: flex;
  gap: 0.35rem;
  font-size: 0.7rem;
}
.inspector-overlay-tier-chip {
  background: var(--surface-raised, #222);
  border-radius: 3px;
  padding: 0.1rem 0.35rem;
}
.inspector-overlay-body {
  display: grid;
  grid-template-columns: minmax(220px, 1fr) 1.6fr;
  gap: 0.75rem;
  flex: 1;
  min-height: 0;
}
.inspector-overlay-list {
  overflow: auto;
  list-style: none;
  margin: 0;
  padding: 0;
  border-right: 1px solid var(--border, #2a2a2a);
}
.inspector-overlay-detail,
.inspector-overlay-raw {
  overflow: auto;
  min-height: 0;
}
.inspector-overlay-text {
  max-height: none;
}
.inspector-raw-messages {
  list-style: none;
  margin: 0;
  padding: 0;
}
.inspector-raw-message pre {
  white-space: pre-wrap;
  font-family: var(--mono, monospace);
  font-size: 0.75rem;
}
```

- [ ] **Step 2: Verify the build + lint**

Run: `cd frontend && pnpm build && pnpm lint`
Expected: PASS (CSS compiles; no eslint errors).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/index.css
git commit -m "style(inspector): total-bar + full-page overlay styles"
```

---

## Task 10: Full verification + docs

**Files:**
- Modify: `docs/superpowers/specs/2026-05-28-inspector-panel-everything-design.md` (status flip, if desired)

- [ ] **Step 1: Backend — full suite + lint/format**

Run: `cd backend && uv run pytest && uv run ruff check && uv run ruff format --check`
Expected: PASS. Address any source-count assertions surfaced in observability/scenario tests (the new attribution sources are correct behavior; update expectations).

- [ ] **Step 2: Frontend — full suite + typecheck + lint**

Run: `cd frontend && pnpm test && pnpm typecheck && pnpm lint`
Expected: PASS.

- [ ] **Step 3: Manual smoke (optional but recommended)**

Use the `/run` skill (or `scripts/run.sh`) to launch the app, open a campaign Play view, type a draft, and confirm: total token bar with per-tier tooltip; comprehensive source list including `system` / `scene_header` / `recent_posts` / `player_input` rows; expanding a row shows precise text; pin/exclude re-fires the preview; the Expand button opens the overlay; the overlay's "Raw messages" toggle shows the verbatim prompt.

- [ ] **Step 4: Final commit (if docs touched)**

```bash
git add docs/superpowers/specs/2026-05-28-inspector-panel-everything-design.md
git commit -m "docs: mark inspector 'show everything' spec implemented (#486)"
```

---

## Self-Review Notes

- **Spec coverage:** `text` field (T1), populate existing sources (T2), new reasons (T3), comprehensive block sources (T4), frontend types/labels (T5), single total bar (T6), precise text in rows (T7), diff removed + reworked panel + overlay with raw-messages view + pin/exclude inline & overlay (T8), styles (T9), verification incl. stability/frozen regression (T4 steps 5–6, T10). All spec sections map to a task.
- **Stability:** capturing `text` and appending attribution sources never touches `messages` or `budget_used`; T4 asserts this and runs the frozen-campaign regression.
- **Out of scope (unchanged):** model params/tools display; backend `/context/diff` endpoint stays (only the frontend stops using it).
