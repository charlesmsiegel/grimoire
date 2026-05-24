# ContextBuilderService Split

Date: 2026-05-23
Status: Approved
PR: 5 of ~10 in the Grimoire code quality refactor series
Depends on: PR 1 (Container/DI typing), PR 3 (Lifecycle extraction)

## Problem

`ContextBuilderService` (`backend/src/grimoire/context/builder.py`) is 2,500 lines with 63 methods. It resolves composition, cast, world, continuity, archive, mechanics, posts, and assembles everything into a prompt with token budgeting. Each section is internally cohesive but tightly coupled to the others through the shared class.

## Solution

Extract section resolvers as `ContextProvider` implementations. Each provider has explicit inputs, outputs, and source attribution. The builder becomes a coordinator that runs providers and assembles results. Independent providers can run concurrently with `asyncio.gather()`.

## Detailed Design

### ContextProvider Protocol

```python
@dataclass
class ContextSection:
    tier: str  # "system", "lock_in", "spotlight", "background", "archive"
    items: list[ContextItem]
    sources: list[ContextSource]
    tokens: int

class ContextProvider(Protocol):
    async def resolve(self, request: ContextBuildRequest) -> list[ContextSection]: ...
```

`ContextBuildRequest` is a dataclass carrying `campaign_id`, `branch_id`, `scene`, `active_pc_ref`, `composition`, `player_input`, `config`, and any pins/excludes. This replaces passing 10+ arguments through method chains.

### Provider Extraction

#### 1. `CastResolver` (~400 lines)

**Methods moved:**
- `_resolve_cast` (line 658) → `resolve()`
- `_active_pc_card` (line 624)
- `_active_pc_name` (line 646)
- `_recommend_tiers` (line 847)
- `_voice_anchor` (line 887)
- `_maybe_transient_stanza_item` (line 897)
- `_character_display_name` (line 949)
- `_recent_dialogue_for` (line 958)
- `_extras_tier_items` (line 983)
- `_try_full_card` (line 1051)
- `_try_compressed_card` (line 1058)
- `_character_source` (line 1065)
- `_render_scene_header` (line 608)

**Dependencies:** `characters`, `scenes`, `transient_state`, `extras_service`

**File:** `backend/src/grimoire/context/cast.py`

#### 2. `WorldContextResolver` (~250 lines)

**Methods moved:**
- `_resolve_world` (line 1092) → `resolve()`
- `_resolve_factions` (line 1222)
- `_faction_refs_for_scene` (line 1278)
- `_resolve_calendar` (line 1304)

**Dependencies:** `world`, `library`, `time_engine`

**File:** `backend/src/grimoire/context/world_context.py`

#### 3. `ContinuityContextResolver` (~300 lines)

**Methods moved:**
- `_open_commitments` (line 1396)
- `_continuity_config` (line 1405)
- `_current_in_game_time` (line 1413)
- `_overdue_commitments` (line 1435)
- `_stale_commitments` (line 1457)
- `_pc_refs` (line 1476)
- `_commitments_targeting_pcs` (line 1492)
- `_render_commitments_block` (line 1509) → `resolve()`
- `_continuity_background` (line 1584)
- `_relationship_deltas` (line 1662)

**Dependencies:** `continuity`, `characters`, `time_engine`

**File:** `backend/src/grimoire/context/continuity_context.py`

#### 4. `ArchiveRetriever` (~300 lines)

**Methods moved:**
- `_retrieve_archive` (line 1736) → `resolve()`
- `_power_definition_archive` (line 1806)
- `_lore_triggers` (line 1883)
- `_vector_search` (line 1919)
- `_keyword_search` (line 1949)
- `_invoke_store_search` (line 1967)
- `_priority_hints` (line 1987)
- `_scene_refs_from_input` (line 2000)
- `_build_retrieval_query` (line 2050)

**Dependencies:** `state_store`, `gateway` (for embeddings), `library`

**File:** `backend/src/grimoire/context/archive.py`

#### 5. `PromptAssembler` (~400 lines)

**Methods moved:**
- `_assemble` (line 2131) → `assemble()`
- `_build_auxiliary` (line 2233)
- `_aux_display_name` (line 2340)
- `_apply_extractor_mode` (line 2351)
- `_tracker_instruction_text` (line 2383)
- `_system_block` (line 2397)
- `_lock_in_block` (line 2406)
- `_lock_in_verbatim_posts` (line 2416)
- `_render_older_recent` (line 2424)
- `_pack_tier` (line 2435)
- `_render_mechanics` (line 2070)
- `_recent_posts` (line 2091)
- `_render_recent_posts` (line 2103)
- `_mentions_in_posts` (line 2117)

**Dependencies:** `config`, `TokenEstimator`

**File:** `backend/src/grimoire/context/assembler.py`

### What Stays on ContextBuilderService (~300 lines)

The public API and coordination logic:

- `build()` (line 248) — public entry point, wraps `_build_inner` with metrics
- `_build_inner()` (line 274) — runs providers and feeds results to assembler
- `estimate()` (line 310) — dry-run budget estimation
- `_build_context()` (line 397) — orchestrates the 5-step gathering
- `_load_pins()` (line 343) — load context pins from state store
- `_resolve_style_guide()` (line 578) — resolve composition style guide
- `_render_system_meta()` (line 592) — system metadata line
- `_tokens()`, `_make_estimator()`, `_safe_call()`, `_summary()`, `_composition_snapshot()`

### Concurrency Opportunity

After extraction, `_build_context` can run independent providers concurrently:

```python
async def _build_context(self, request: ContextBuildRequest) -> ContextSections:
    cast_task = self._cast.resolve(request)
    world_task = self._world.resolve(request)
    continuity_task = self._continuity.resolve(request)
    archive_task = self._archive.resolve(request)

    cast, world, continuity, archive = await asyncio.gather(
        cast_task, world_task, continuity_task, archive_task,
        return_exceptions=True,
    )
    # Handle exceptions gracefully per provider
```

This is a performance improvement but should be gated behind a config flag initially until stability is confirmed.

### ContextBuildRequest Dataclass

```python
@dataclass(frozen=True)
class ContextBuildRequest:
    campaign_id: str
    branch_id: str
    scene: Scene
    active_pc_ref: str | None
    composition: Composition
    player_input: str
    recent_posts: list[Post]
    roll_outcomes: list[RollOutcome] | None
    pins: ContextPins
    config: ContextBuilderConfig
    turn_id: str | None = None
```

## Scope

### In scope
- Define `ContextProvider` protocol and `ContextBuildRequest` dataclass
- Extract 5 provider classes from ContextBuilderService
- Keep ContextBuilderService as coordinator/facade
- Golden tests must produce identical prompt output

### Not in scope
- Enabling concurrent providers (document as future step)
- Adding per-section caching (separate performance PR)
- Changing the prompt format
- Adding new context sections

## Verification

1. `pytest` full suite passes, especially golden tests.
2. `ContextBuilderService` (builder.py) is under 400 lines.
3. Each provider file is under 450 lines.
4. Golden test prompt snapshots are byte-identical before and after.
5. No circular imports between context submodules.
