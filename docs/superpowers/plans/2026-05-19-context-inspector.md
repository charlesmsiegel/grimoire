# Context Inspector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans.

**Goal:** Land everything in `docs/superpowers/specs/2026-05-19-context-inspector-design.md`. Live preview of next-turn prompt; per-chunk inclusion reasons; user pin/exclude; preview/turn diff. Consumes `transient-state` privacy helper.

**Architecture:** Five branches.

- **A** `feature/inspector-A-reasons` — `InclusionReason` enum, `ContextSource.inclusion_reasons`, emit reasons at every assembly site.
- **B** `feature/inspector-B-pins-table` — migration for `context_pins`; pin/exclude state in builder candidate filter.
- **C** `feature/inspector-C-service` — `ContextInspector` service with handle cache + preview/explain/diff.
- **D** `feature/inspector-D-rest` — REST endpoints (preview, explain, pin/exclude, diff).
- **E** `feature/inspector-E-frontend` — Inspector panel with token bars, source list, debounced preview, pin/exclude controls, diff view.

**Tech Stack:** Python 3.12, FastAPI, aiosqlite, pytest-asyncio, Pydantic v2; React/TS frontend.

---

## Conventions

Standard. **Soft dep on `transient-state`** for privacy filter in explanations.

---

## Branch setup

- [ ] **Step S1: Worktrees**

```powershell
git worktree add .worktrees/inspector-A-reasons     -b feature/inspector-A-reasons     main
git worktree add .worktrees/inspector-B-pins-table  -b feature/inspector-B-pins-table  main
git worktree add .worktrees/inspector-C-service     -b feature/inspector-C-service     main
git worktree add .worktrees/inspector-D-rest        -b feature/inspector-D-rest        main
git worktree add .worktrees/inspector-E-frontend    -b feature/inspector-E-frontend    main
```

---

# Branch A — Inclusion reasons

### Task A1: Enum + field

**Files:**
- Modify: `backend/src/grimoire/types/context.py:ContextSource`.
- Create: `backend/src/grimoire/types/inclusion_reasons.py`.
- Test: `backend/tests/types/test_inclusion_reasons.py`.

- [ ] **Step 1: Failing test**

```python
def test_enum_has_all_canonical_reasons():
    assert InclusionReason.PRESENT_IN_SCENE.value == "present_in_scene"
    assert InclusionReason.COMMITMENT_OPEN_TO_PC.value == "commitment_open_to_pc"
    assert InclusionReason.PINNED_BY_USER.value == "pinned_by_user"
    # etc — full enum per spec


def test_context_source_default_reasons_empty():
    s = ContextSource(kind="character", scope="library", owner_id="x", tier=ContextTier.SPOTLIGHT)
    assert s.inclusion_reasons == []
```

- [ ] **Step 2: Implement enum + field** (per spec design enum list).

- [ ] **Step 3: Commit.**

### Task A2: Emit reasons at assembly sites

**Files:**
- Modify: `backend/src/grimoire/context/builder.py` — `_resolve_cast`, `_collect_commitments`, `_collect_lore`, `_collect_pc_card`, etc. — set `inclusion_reasons` on each emitted `ContextSource`.
- Test: `backend/tests/context/test_inclusion_reasons.py`.

- [ ] **Step 1: Failing tests** (per source-emit site)

```python
async def test_present_character_carries_present_in_scene_reason(builder, seeded_state):
    prompt = await builder.build(...)
    sources = prompt.sources
    florence_src = next(s for s in sources if s.owner_id == "char_florence")
    assert InclusionReason.PRESENT_IN_SCENE in florence_src.inclusion_reasons


async def test_pc_card_carries_pc_card_reason(...):
async def test_open_commitment_to_pc_reason(...):
async def test_lore_before_cast_reason(...):
async def test_keyword_triggered_reason(...):
async def test_pinned_by_user_reason(...):     # branch B integration
async def test_extras_pinned_to_hud_reason(...):
async def test_transient_state_active_reason(...):
async def test_compose_multi_reason(...):
    # character is both PRESENT_IN_SCENE AND COMMITMENT_OPEN_TO_PC → both reasons listed
```

- [ ] **Step 2: Implement** — walk every `ContextSource` construction site and add the appropriate reasons. For composable reasons (a character may be present + have an open commitment), collect into a list during the resolution pass and emit on the same `ContextSource`.

- [ ] **Step 3: Tests PASS + commit + merge A.**

---

# Branch B — Pins table + builder integration

### Task B1: Migration

**Files:**
- Create: `backend/src/grimoire/storage/migrations/027_context_pins.sql`.

- [ ] **Step 1: SQL**

```sql
-- backend/src/grimoire/storage/migrations/027_context_pins.sql
CREATE TABLE context_pins (
    id                  TEXT PRIMARY KEY,
    campaign_id         TEXT NOT NULL,
    branch_id           TEXT NOT NULL,
    kind                TEXT NOT NULL,            -- pin | exclude
    target_kind         TEXT NOT NULL,            -- source | entity
    target_source_id    TEXT,
    target_entity_kind  TEXT,
    target_entity_id    TEXT,
    created_at          TEXT NOT NULL,
    created_by          TEXT NOT NULL,
    created_at_turn_id  TEXT,
    expires_at_turn_id  TEXT,
    cleared_at          TEXT,
    cleared_by          TEXT
);
CREATE INDEX ix_ctx_pins_active
    ON context_pins(campaign_id, branch_id)
    WHERE cleared_at IS NULL;
```

- [ ] **Step 2: Commit.**

### Task B2: Builder honors pins

**Files:**
- Modify: `backend/src/grimoire/context/builder.py` — load active pins per build; apply during candidate filter (excludes) and pack (pins exempt from truncation).
- Test: `backend/tests/context/test_pin_exclude.py`.

- [ ] **Step 1: Failing tests**

```python
async def test_pinned_entity_survives_budget_truncation(builder, store, seeded_state_tight_budget):
    # Set up budget so that without pin, Henry would be evicted
    await store.write_context_pin(
        campaign_id=seeded_state_tight_budget.campaign_id, kind="pin",
        target_entity_kind="character", target_entity_id="char_henry",
        ttl_turns=None, created_by="user",
    )
    prompt = await builder.build(...)
    assert any(s.owner_id == "char_henry" for s in prompt.sources)


async def test_excluded_entity_dropped_from_candidates(...):


async def test_pin_with_ttl_expires_after_n_turns(...):


async def test_pin_does_not_reorder_tier(...):
    # Pinned entity stays in its naturally-assigned tier
    ...
```

- [ ] **Step 2: Implement** — at builder start, query active pins; apply at the candidate-set step (excludes) and at the truncation step (pins exempt). Emit `InclusionReason.PINNED_BY_USER` on pinned sources' `inclusion_reasons`.

- [ ] **Step 3: Tests PASS + commit + merge B.**

---

# Branch C — Inspector service

### Task C1: Handle cache + `preview` / `explain` / `diff`

**Files:**
- Create: `backend/src/grimoire/context/inspector.py`.
- Test: `backend/tests/context/test_inspector.py`.

- [ ] **Step 1: Failing tests**

```python
async def test_preview_returns_handle_and_summary(inspector, seeded_state):
    handle, summary = await inspector.preview(
        campaign_id=seeded_state.campaign_id,
        player_input="...",
        session_id="s_1",
        branch_id=f"{seeded_state.campaign_id}:main",
        pc_ref=seeded_state.pc_ref,
    )
    assert handle is not None
    assert summary.per_tier_tokens[ContextTier.SPOTLIGHT] > 0


async def test_get_returns_assembled_prompt(inspector, ...):
    handle, _ = await inspector.preview(...)
    prompt = await inspector.get(session_id="s_1", handle=handle)
    assert isinstance(prompt, AssembledPrompt)


async def test_explain_returns_per_source_reasons(inspector, ...):
    handle, _ = await inspector.preview(...)
    explanations = await inspector.explain(session_id="s_1", handle=handle)
    winifred = next(e for e in explanations if e.owner_id == "char_florence")
    assert InclusionReason.PRESENT_IN_SCENE in winifred.inclusion_reasons


async def test_diff_two_handles_added_removed(inspector, seeded_state):
    h1, _ = await inspector.preview(seeded_state.campaign_id, "draft 1", "s_1", ...)
    h2, _ = await inspector.preview(seeded_state.campaign_id, "draft 2 mentions Henry", "s_1", ...)
    diff = await inspector.diff(a=h1, b=h2, session_id="s_1")
    assert any(s.owner_id == "char_henry" for s in diff.entities_added)


async def test_diff_against_turn_id_loads_audit(inspector, store, ...):
    diff = await inspector.diff(a=TurnId("t_42"), b=handle, session_id="s_1")
    ...


async def test_session_isolation(inspector, ...):
    h1, _ = await inspector.preview(... session_id="s_a")
    with pytest.raises(HandleNotFound):
        await inspector.get(session_id="s_b", handle=h1)


async def test_handle_lru_evicts_oldest(...):
    # Push 51 previews; the first one becomes invalid
    ...
```

- [ ] **Step 2: Implement**

```python
# backend/src/grimoire/context/inspector.py
import time
from collections import OrderedDict


class ContextInspector:
    def __init__(self, *, builder, store, config):
        self.builder = builder
        self.store = store
        self.config = config
        self._handles: OrderedDict[tuple[str, str], tuple[AssembledPrompt, float]] = OrderedDict()
        self._max_handles = 50
        self._handle_ttl_seconds = 900

    async def preview(self, *, campaign_id, player_input, session_id, branch_id, pc_ref, for_observer=None):
        prompt = await self.builder.build(
            player_input=player_input, campaign_id=campaign_id,
            branch_id=branch_id, pc_ref=pc_ref, turn_id=_synthetic_turn_id(),
        )
        handle = f"ph_{uuid7()}"
        self._handles[(session_id, handle)] = (prompt, time.time())
        self._evict_old()
        summary = _make_summary(prompt)
        return handle, summary

    async def get(self, *, session_id, handle):
        key = (session_id, handle)
        if key not in self._handles:
            raise HandleNotFound(handle)
        prompt, _ = self._handles[key]
        return prompt

    async def explain(self, *, session_id, handle):
        prompt = await self.get(session_id=session_id, handle=handle)
        return [
            ContextSourceExplanation(
                source_id=s.source_id, owner_id=s.owner_id, kind=s.kind,
                scope=s.scope, tier=s.tier, library_version=s.library_version,
                inclusion_reasons=list(s.inclusion_reasons),
                tokens=s.tokens, summary=s.summary,
            )
            for s in prompt.sources
        ]

    async def diff(self, *, a, b, session_id=None):
        prompt_a = await self._resolve_to_prompt(a, session_id)
        prompt_b = await self._resolve_to_prompt(b, session_id)
        ids_a = {s.source_id for s in prompt_a.sources}
        ids_b = {s.source_id for s in prompt_b.sources}
        added = [s for s in prompt_b.sources if s.source_id not in ids_a]
        removed = [s for s in prompt_a.sources if s.source_id not in ids_b]
        budget_shifts = {
            tier: prompt_b.budget_used.get(tier, 0) - prompt_a.budget_used.get(tier, 0)
            for tier in ContextTier
        }
        return ContextDiff(
            entities_added=[self._to_explain(s) for s in added],
            entities_removed=[self._to_explain(s) for s in removed],
            entities_changed_tier=[],          # reserved (spec: no reorder yet)
            budget_shifts=budget_shifts,
            source_version_changes=self._compute_version_changes(prompt_a, prompt_b),
            rolls_deltas=[],
        )

    async def pin(self, *, campaign_id, target, ttl_turns=None, actor="user"):
        pin_id = f"ctx_pin_{uuid7()}"
        await self.store.write_context_pin(
            id=pin_id, campaign_id=campaign_id,
            branch_id=f"{campaign_id}:main", kind="pin",
            target_kind=target.kind, target_source_id=target.source_id,
            target_entity_kind=target.entity_kind, target_entity_id=target.entity_id,
            ttl_turns=ttl_turns, created_by=actor,
        )
        return pin_id

    async def exclude(self, ...):
        # Same shape, kind="exclude"
        ...

    async def clear_pin(self, campaign_id, pin_id):
        await self.store.mark_context_pin_cleared(pin_id, campaign_id)

    def _evict_old(self):
        now = time.time()
        to_evict = [
            k for k, (_, ts) in self._handles.items()
            if now - ts > self._handle_ttl_seconds
        ]
        for k in to_evict:
            del self._handles[k]
        while len(self._handles) > self._max_handles:
            self._handles.popitem(last=False)
```

- [ ] **Step 3: Tests PASS + commit + merge C.**

---

# Branch D — REST routes

### Task D1: Inspector routes

**Files:**
- Create: `backend/src/grimoire/api/context.py`.
- Test: `backend/tests/api/test_context_routes.py`.

Routes:
```
POST   /campaigns/{id}/context/preview                body: {player_input, session_id, branch_id, pc_ref}
GET    /campaigns/{id}/context/preview/{handle}
GET    /campaigns/{id}/context/preview/{handle}/explain
POST   /campaigns/{id}/context/pins                   body: {target, kind, ttl_turns?}
DELETE /campaigns/{id}/context/pins/{pin_id}
GET    /campaigns/{id}/context/pins
POST   /campaigns/{id}/context/diff                   body: {a, b}
```

- [ ] **Step 1: Failing tests per route — round-trip + 404 + session isolation.**

- [ ] **Step 2-N: Implement + commit + merge D.**

---

# Branch E — Frontend inspector panel

### Task E1: Inspector panel + debounced live preview

**Files:**
- Create: `frontend/src/routes/campaign/Inspector/InspectorPanel.tsx`.
- Create: `frontend/src/routes/campaign/Inspector/SourceList.tsx`, `TokenBars.tsx`, `DiffView.tsx`, `PinControls.tsx`.
- Create: `frontend/src/api/inspector.ts`.
- Create: `frontend/src/routes/campaign/Inspector/useLivePreview.ts`.

`useLivePreview` debounces the player input change (500ms default) and POSTs `/preview`; stores `handle` + `summary` in state; allows on-demand `/explain` fetches per source row.

- [ ] **Step 1: Failing component tests** (per component).

- [ ] **Step 2-N: Implement** — token bars (per-tier with budget bar fill %), source list with click-expansion (calls `/explain`), Pin / Exclude / Clear buttons with TTL picker, Diff toggle (against last turn by default).

- [ ] **Step end: commit + merge E.**

---

# Integration check

- [ ] **Step end1: Full suite + frontend tests.**
- [ ] **Step end2: Determinism check** — assert `inspector.get(handle) == canonical.build(same inputs)` for the no-pin case in `backend/tests/context/test_inspector_determinism.py`.
- [ ] **Step end3: Smoke** — open the inspector, type into the composer, see live updates; pin a character; verify they survive budget pressure.
- [ ] **Step end4: COMPLETED doc + delete design.**
