# PR 5: ContextBuilderService Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract 5 provider classes from the 2,500-line `ContextBuilderService`, define a `ContextProvider` protocol and `ContextBuildRequest` dataclass, and ensure golden tests produce byte-identical prompt output.

**Architecture:** Each provider implements a `ContextProvider` protocol with a `resolve(request)` method. The builder becomes a coordinator that runs providers and feeds results to the `PromptAssembler`. `ContextBuildRequest` carries pre-parsed entities so providers avoid redundant YAML parsing within a single request.

**Tech Stack:** Python 3.12+, FastAPI, asyncio

---

### Task 1: Define ContextProvider protocol and ContextBuildRequest

**Files:**
- Create: `backend/src/grimoire/context/types.py`

- [ ] **Step 1: Define types**

Read `backend/src/grimoire/context/builder.py` lines 248-310 to identify all parameters passed to `build()` and `_build_inner()`. Capture them in a frozen dataclass:

```python
"""Context building types and protocols."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from grimoire.types.scene import Scene, Post


@dataclass
class ContextItem:
    text: str
    source_id: str
    tokens: int = 0
    priority: float = 0.0


@dataclass
class ContextSource:
    id: str
    kind: str
    label: str


@dataclass
class ContextSection:
    tier: str  # "system", "lock_in", "spotlight", "background", "archive"
    items: list[ContextItem] = field(default_factory=list)
    sources: list[ContextSource] = field(default_factory=list)
    tokens: int = 0


@dataclass(frozen=True)
class ContextBuildRequest:
    campaign_id: str
    branch_id: str
    scene: Any  # Scene
    active_pc_ref: str | None
    composition: Any  # Composition
    player_input: str
    recent_posts: list[Any]  # list[Post]
    config: Any  # ContextBuilderConfig
    roll_outcomes: list[Any] | None = None
    pins: Any = None  # ContextPins
    turn_id: str | None = None
    active_pc_card: Any = None  # Pre-parsed to avoid redundant YAML reads


class ContextProvider(Protocol):
    async def resolve(self, request: ContextBuildRequest) -> list[ContextSection]: ...
```

Adjust types as needed based on reading the actual `builder.py` code — the `Any` placeholders should use the real types if they don't cause circular imports.

- [ ] **Step 2: Commit**

```
git add backend/src/grimoire/context/types.py
git commit -m "feat(context): add ContextProvider protocol and ContextBuildRequest"
```

---

### Task 2: Extract CastResolver

**Files:**
- Create: `backend/src/grimoire/context/cast.py`
- Modify: `backend/src/grimoire/context/builder.py`

- [ ] **Step 1: Create CastResolver class**

Move these methods from `ContextBuilderService`:
- `_resolve_cast` (line 658) → `resolve()`
- `_active_pc_card` (624), `_active_pc_name` (646), `_recommend_tiers` (847), `_voice_anchor` (887), `_maybe_transient_stanza_item` (897), `_character_display_name` (949), `_recent_dialogue_for` (958), `_extras_tier_items` (983), `_try_full_card` (1051), `_try_compressed_card` (1058), `_character_source` (1065), `_render_scene_header` (608)

Constructor takes: `characters`, `scenes`, `transient_state`, `extras_service`

- [ ] **Step 2: Wire CastResolver into ContextBuilderService**

```python
# In ContextBuilderService.__init__:
self._cast = CastResolver(
    characters=self._characters,
    scenes=self._scenes,
    transient_state=self._transient_state,
    # ...
)
```

Replace `self._resolve_cast(...)` with `self._cast.resolve(request)`.

- [ ] **Step 3: Run golden tests**

Run: `cd backend && uv run pytest -m golden -x -q`
Expected: All golden tests pass with byte-identical output

- [ ] **Step 4: Commit**

```
git add backend/src/grimoire/context/cast.py backend/src/grimoire/context/builder.py
git commit -m "refactor(context): extract CastResolver provider"
```

---

### Task 3: Extract WorldContextResolver

**Files:**
- Create: `backend/src/grimoire/context/world_context.py`
- Modify: `backend/src/grimoire/context/builder.py`

- [ ] **Step 1: Move world methods**

Move: `_resolve_world` (1092), `_resolve_factions` (1222), `_faction_refs_for_scene` (1278), `_resolve_calendar` (1304)

Constructor takes: `world`, `library`, `time_engine`

- [ ] **Step 2: Wire in, run golden tests, commit**

Same pattern as Task 2. Golden tests must still pass.

```
git add backend/src/grimoire/context/world_context.py backend/src/grimoire/context/builder.py
git commit -m "refactor(context): extract WorldContextResolver provider"
```

---

### Task 4: Extract ContinuityContextResolver

**Files:**
- Create: `backend/src/grimoire/context/continuity_context.py`
- Modify: `backend/src/grimoire/context/builder.py`

- [ ] **Step 1: Move continuity methods**

Move: `_open_commitments` (1396), `_continuity_config` (1405), `_current_in_game_time` (1413), `_overdue_commitments` (1435), `_stale_commitments` (1457), `_pc_refs` (1476), `_commitments_targeting_pcs` (1492), `_render_commitments_block` (1509), `_continuity_background` (1584), `_relationship_deltas` (1662)

Constructor takes: `continuity`, `characters`, `time_engine`

- [ ] **Step 2: Wire in, run golden tests, commit**

```
git add backend/src/grimoire/context/continuity_context.py backend/src/grimoire/context/builder.py
git commit -m "refactor(context): extract ContinuityContextResolver provider"
```

---

### Task 5: Extract ArchiveRetriever

**Files:**
- Create: `backend/src/grimoire/context/archive.py`
- Modify: `backend/src/grimoire/context/builder.py`

- [ ] **Step 1: Move archive methods**

Move: `_retrieve_archive` (1736), `_power_definition_archive` (1806), `_lore_triggers` (1883), `_vector_search` (1919), `_keyword_search` (1949), `_invoke_store_search` (1967), `_priority_hints` (1987), `_scene_refs_from_input` (2000), `_build_retrieval_query` (2050)

Constructor takes: `state_store`, `gateway` (for embeddings), `library`

- [ ] **Step 2: Wire in, run golden tests, commit**

```
git add backend/src/grimoire/context/archive.py backend/src/grimoire/context/builder.py
git commit -m "refactor(context): extract ArchiveRetriever provider"
```

---

### Task 6: Extract PromptAssembler

**Files:**
- Create: `backend/src/grimoire/context/assembler.py`
- Modify: `backend/src/grimoire/context/builder.py`

- [ ] **Step 1: Move assembly methods**

Move: `_assemble` (2131), `_build_auxiliary` (2233), `_aux_display_name` (2340), `_apply_extractor_mode` (2351), `_tracker_instruction_text` (2383), `_system_block` (2397), `_lock_in_block` (2406), `_lock_in_verbatim_posts` (2416), `_render_older_recent` (2424), `_pack_tier` (2435), `_render_mechanics` (2070), `_recent_posts` (2091), `_render_recent_posts` (2103), `_mentions_in_posts` (2117)

Constructor takes: `config`, token estimator

- [ ] **Step 2: Wire in, run golden tests, commit**

```
git add backend/src/grimoire/context/assembler.py backend/src/grimoire/context/builder.py
git commit -m "refactor(context): extract PromptAssembler"
```

---

### Task 7: Final verification

- [ ] **Step 1: Verify builder.py size**

Run: `cd backend && wc -l src/grimoire/context/builder.py`
Expected: Under 400 lines

- [ ] **Step 2: Verify provider sizes**

Run: `cd backend && wc -l src/grimoire/context/cast.py src/grimoire/context/world_context.py src/grimoire/context/continuity_context.py src/grimoire/context/archive.py src/grimoire/context/assembler.py`
Expected: Each under 450 lines

- [ ] **Step 3: Run full test suite including golden tests**

Run: `cd backend && uv run pytest -x -q`
Expected: All pass. Golden test snapshots are byte-identical.

- [ ] **Step 4: Verify no circular imports**

Run: `cd backend && uv run python -c "from grimoire.context.builder import ContextBuilderService; print('OK')"`
Expected: `OK`
