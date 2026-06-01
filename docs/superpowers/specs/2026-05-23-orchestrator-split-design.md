# OrchestratorService Split

Date: 2026-05-23
Status: Approved
PR: 4 of ~10 in the Grimoire code quality refactor series
Depends on: PR 1 (Container/DI typing), PR 3 (Lifecycle extraction)

## Problem

`OrchestratorService` (`backend/src/grimoire/orchestrator/service.py`) is 3,140 lines with 35 public methods and 47 private methods. It owns six distinct responsibilities: turn execution, alternate/swipe management, auxiliary tasks, retcon replay, campaign forking, and delta application. A change to any one of these requires reasoning about the entire class.

## Solution

Extract five focused collaborator classes. `OrchestratorService` remains as the public facade but delegates to collaborators. Each collaborator is independently testable.

## Detailed Design

### Collaborator Extraction

#### 1. `AlternatesManager` (~350 lines)

**Public methods moved:**
- `regenerate_post()` (line 406)
- `switch_primary_alternate()` (line 613)
- `pin_alternate()` (line 709)
- `delete_alternate()` (line 732)
- `purge_stale_alternates()` (line 788)

**Private methods moved:**
- `_regenerate_post_core()` (line 437)
- `_switch_primary_alternate_core()` (line 636)
- `_evict_overflow_alternate()` (line 772)
- `_find_scene_and_post()` (line 392)
- `_ensure_latest_model_post()` (line 395)

**Dependencies:** `scene_manager`, `llm_gateway`, `context_builder`, `extractor`, `state_store`, `event_bus`, `ws_push`

**File:** `backend/src/grimoire/orchestrator/alternates.py`

#### 2. `AuxiliaryCoordinator` (~200 lines)

**Public methods moved:**
- `run_auxiliary_task()` (line 843)
- `discard_auxiliary()` (line 867)
- `list_inflight_auxiliary()` (line 870)
- `accept_auxiliary()` (line 880)

**Private methods moved:**
- `_characters_active_pc()` (line 1015)
- `_accept_rewrite_post()` (line 1030)

**Dependencies:** `scene_manager`, `llm_gateway`, `context_builder`, `characters`, `state_store`, `event_bus`

**File:** `backend/src/grimoire/orchestrator/auxiliary.py`

#### 3. `RetconCoordinator` (~200 lines)

**Public methods moved:**
- `retcon_post()` (line 1165)
- `accept_replay()` (line 1207)
- `try_again_replay()` (line 1219)
- `cancel_replay()` (line 1226)
- `get_replay_state()` (line 1233)
- `retcon_replay` property (line 1202)

**Private methods moved:**
- `_retcon_leave_as_is()` (line 1244)

**Dependencies:** `scene_manager`, `state_store`, `event_bus`, `AlternatesManager` (for `_regenerate_post_core`)

**File:** `backend/src/grimoire/orchestrator/retcon.py` (extends existing `retcon_replay.py`)

#### 4. `ForkCoordinator` (~350 lines)

**Public methods moved:**
- `fork()` (line 1344)
- `fork_campaign()` (line 1371)
- `list_pending_forks()` (line 1666)
- `process_pending_forks()` (line 1681)
- `get_lineage()` (line 1741)
- `get_lineage_ancestors()` (line 1768)

**Private methods moved:**
- `_execute_fork()` (line 1416)
- `_clone_campaign_row()` (line 1566)
- `_campaign_exists()` (line 1609)
- `_is_streaming()` (line 1613)
- `_enqueue_fork()` (line 1617)
- `_copy_campaign_files()` (line 1790)
- `_wipe_failed_fork()` (line 1810)

**Dependencies:** `scene_manager`, `state_store`, `event_bus`, `imagegen`

**File:** `backend/src/grimoire/orchestrator/fork.py`

#### 5. `DeltaApplier` (~250 lines)

**Private methods moved (made public on the new class):**
- `_apply_routing()` (line 2732) → `apply_routing()`
- `_apply_continuity_delta()` (line 2844) → `apply_continuity_delta()`
- `_do_extract()` (line 2689) → `extract()`
- `_select_extract_mode()` (line 2601) → `select_extract_mode()`
- `_emit_integrated_deltas_fallback()` (line 2638)
- `_campaign_integrated_deltas()` (line 2662)

**Module-level helpers moved:**
- `_build_continuity_fact()` (line 3236)
- `_build_continuity_commitment()` (line 3287)

**Dependencies:** `state_store`, `continuity`, `extractor`, `mechanics`, `event_bus`

**File:** `backend/src/grimoire/orchestrator/delta_applier.py`

### What Stays on OrchestratorService (~800 lines)

The turn execution loop and its direct helpers:

**Public:** `submit_post`, `advance`, `cancel_turn`, `resolve_scene_break`, `resolve_pre_roll`, `undo_turn`, `turn_in_progress`, `queue_length`, `event_bus`

**Private:** `_run_turn`, `_run_turn_inner`, `_run_turn_body`, `_continue_turn_after_pre_roll`, `_check_cancelled`, `_rollback_player_post`, `_heartbeat_loop`, `_maybe_break_scene`, `_do_pre_roll`, `_resolve_proposals`, `_stream_main_response`, `_require_campaign`, `_require_pc`, `_state_for`, `_new_post`, `_emit_fragment`, `_emit_turn_event`, `_push_to_ws`, `_recent_turn_ids`, `_reverse_turn_deltas`

> `regenerate_last`, `_composition_hash`, and `_strip_response_for_turn` were removed in the reroll consolidation (#512).

### Facade Delegation Pattern

`OrchestratorService.__init__` constructs the collaborators:

```python
self._alternates = AlternatesManager(...)
self._auxiliary = AuxiliaryCoordinator(...)
self._retcon = RetconCoordinator(...)
self._fork = ForkCoordinator(...)
self._delta = DeltaApplier(...)
```

Public methods delegate:

```python
async def regenerate_post(self, campaign_id, post_id, **kw):
    return await self._alternates.regenerate_post(campaign_id, post_id, **kw)
```

### Shared Utilities

Move module-level helpers to `backend/src/grimoire/orchestrator/helpers.py`:
- `_campaign_generation_overrides()` (line 3146)
- `_pydantic_scene()` (line 3180)
- `_pydantic_post()` (line 3218)
- `_proposed_to_roll()` (line 3347)
- `_clean_modifications()` (line 3368)

### Request Objects

Introduce request dataclasses to replace long parameter lists on the extracted collaborators:

#### `ExtractionRequest`

Replaces the 10-parameter `ExtractorService.extract()` call. Carried from orchestrator through delta applier:

```python
@dataclass(frozen=True)
class ExtractionRequest:
    campaign_id: str
    branch_id: str
    turn_id: str
    scene_id: str
    post_text: str
    pc_ref: str | None
    extract_mode: ExtractMode
    composition: Composition
    context_snapshot: AssembledPrompt | None = None
    mechanics_module: str | None = None
```

#### `DeltaApplyRequest`

Replaces the scattered parameter passing through `_apply_routing()` and `_apply_continuity_delta()`:

```python
@dataclass(frozen=True)
class DeltaApplyRequest:
    campaign_id: str
    branch_id: str
    turn_id: str
    scene_id: str
    pc_ref: str | None
    deltas: list[StateDelta]
    composition: Composition
```

Both dataclasses live in `backend/src/grimoire/orchestrator/types.py`.

## Scope

### In scope
- Extract 5 collaborator classes from OrchestratorService
- Keep OrchestratorService as public facade with delegation
- Move module-level helpers to shared file
- Introduce `ExtractionRequest` and `DeltaApplyRequest` dataclasses
- Update tests to test collaborators directly where appropriate

### Not in scope
- Changing the OrchestratorService public API
- Changing the turn execution flow
- Splitting the pre-roll logic further

## Verification

1. `pytest` full suite passes (especially `tests/orchestrator/`).
2. `OrchestratorService` is under 1,000 lines.
3. Each collaborator file is under 400 lines.
4. No circular imports between collaborator modules.
5. All existing public methods on `OrchestratorService` still exist with same signatures.
