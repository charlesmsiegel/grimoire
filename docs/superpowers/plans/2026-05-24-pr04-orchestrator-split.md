# PR 4: OrchestratorService Split Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract 5 collaborator classes from the 3,140-line `OrchestratorService`, introduce `ExtractionRequest` and `DeltaApplyRequest` dataclasses, and keep the public facade intact.

**Architecture:** `OrchestratorService` remains as the public API but delegates to `AlternatesManager`, `AuxiliaryCoordinator`, `RetconCoordinator`, `ForkCoordinator`, and `DeltaApplier`. Each collaborator is a focused class that receives its dependencies via constructor. Module-level helper functions move to `orchestrator/helpers.py`. The facade constructs collaborators in `__init__` and delegates public methods.

**Tech Stack:** Python 3.12+, FastAPI, asyncio

---

### Task 1: Create request object dataclasses

**Files:**
- Create: `backend/src/grimoire/orchestrator/types.py`

- [ ] **Step 1: Define ExtractionRequest and DeltaApplyRequest**

```python
"""Request objects for orchestrator internal APIs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from grimoire.types.state import StateDelta


@dataclass(frozen=True)
class ExtractionRequest:
    campaign_id: str
    branch_id: str
    turn_id: str
    scene_id: str
    post_text: str
    pc_ref: str | None
    extract_mode: Any  # ExtractMode enum
    composition: Any  # Composition
    context_snapshot: Any | None = None
    mechanics_module: str | None = None


@dataclass(frozen=True)
class DeltaApplyRequest:
    campaign_id: str
    branch_id: str
    turn_id: str
    scene_id: str
    pc_ref: str | None
    deltas: list[StateDelta]
    composition: Any  # Composition
```

- [ ] **Step 2: Commit**

```
git add backend/src/grimoire/orchestrator/types.py
git commit -m "feat(orchestrator): add ExtractionRequest and DeltaApplyRequest dataclasses"
```

---

### Task 2: Extract module-level helpers to helpers.py

**Files:**
- Create: `backend/src/grimoire/orchestrator/helpers.py`
- Modify: `backend/src/grimoire/orchestrator/service.py`

- [ ] **Step 1: Move helper functions**

Read `service.py` and move these module-level functions (defined after the class) to `helpers.py`:
- `_campaign_generation_overrides()` (~line 3146)
- `_pydantic_scene()` (~line 3180)
- `_pydantic_post()` (~line 3218)
- `_build_continuity_fact()` (~line 3236)
- `_build_continuity_commitment()` (~line 3287)
- `_proposed_to_roll()` (~line 3347)
- `_clean_modifications()` (~line 3368)

- [ ] **Step 2: Update imports in service.py**

Add `from grimoire.orchestrator.helpers import ...` for each moved function.

- [ ] **Step 3: Run tests**

Run: `cd backend && uv run pytest tests/orchestrator/ -x -q`
Expected: All pass

- [ ] **Step 4: Commit**

```
git add backend/src/grimoire/orchestrator/helpers.py backend/src/grimoire/orchestrator/service.py
git commit -m "refactor(orchestrator): extract module-level helpers to helpers.py"
```

---

### Task 3: Extract DeltaApplier

**Files:**
- Create: `backend/src/grimoire/orchestrator/delta_applier.py`
- Modify: `backend/src/grimoire/orchestrator/service.py`

- [ ] **Step 1: Create DeltaApplier class**

Move these methods from `OrchestratorService` to a new `DeltaApplier` class:
- `_apply_routing()` (~line 2732) → public `apply_routing()`
- `_apply_continuity_delta()` (~line 2844) → public `apply_continuity_delta()`
- `_do_extract()` (~line 2689) → public `extract()`
- `_select_extract_mode()` (~line 2601) → public `select_extract_mode()`
- `_emit_integrated_deltas_fallback()` (~line 2638)
- `_campaign_integrated_deltas()` (~line 2662)

Read each method carefully. Identify which `self._xxx` attributes they use and pass those as constructor arguments to `DeltaApplier`.

- [ ] **Step 2: Add DeltaApplier to OrchestratorService.__init__**

```python
self._delta = DeltaApplier(
    state_store=self._state_store,
    continuity=self._continuity,
    extractor=self._extractor,
    mechanics=self._mechanics,
    event_bus=self._event_bus,
    # ... other deps as needed
)
```

- [ ] **Step 3: Replace calls in OrchestratorService**

Every `self._apply_routing(...)` becomes `self._delta.apply_routing(...)`, etc.

- [ ] **Step 4: Run tests**

Run: `cd backend && uv run pytest tests/orchestrator/ -x -q`
Expected: All pass

- [ ] **Step 5: Commit**

```
git add backend/src/grimoire/orchestrator/delta_applier.py backend/src/grimoire/orchestrator/service.py
git commit -m "refactor(orchestrator): extract DeltaApplier collaborator"
```

---

### Task 4: Extract AlternatesManager

**Files:**
- Create: `backend/src/grimoire/orchestrator/alternates.py`
- Modify: `backend/src/grimoire/orchestrator/service.py`

- [ ] **Step 1: Create AlternatesManager class**

Move these methods:
- `regenerate_post()`, `switch_primary_alternate()`, `pin_alternate()`, `delete_alternate()`, `purge_stale_alternates()`
- `_regenerate_post_core()`, `_switch_primary_alternate_core()`, `_evict_overflow_alternate()`
- `_find_scene_and_post()`, `_ensure_latest_model_post()`

- [ ] **Step 2: Add delegation in OrchestratorService**

```python
async def regenerate_post(self, campaign_id, post_id, **kw):
    return await self._alternates.regenerate_post(campaign_id, post_id, **kw)
```

- [ ] **Step 3: Run tests**

Run: `cd backend && uv run pytest tests/orchestrator/ -x -q`
Expected: All pass

- [ ] **Step 4: Commit**

```
git add backend/src/grimoire/orchestrator/alternates.py backend/src/grimoire/orchestrator/service.py
git commit -m "refactor(orchestrator): extract AlternatesManager collaborator"
```

---

### Task 5: Extract AuxiliaryCoordinator

**Files:**
- Create: `backend/src/grimoire/orchestrator/auxiliary.py`
- Modify: `backend/src/grimoire/orchestrator/service.py`

- [ ] **Step 1: Move auxiliary methods**

Move: `run_auxiliary_task()`, `discard_auxiliary()`, `list_inflight_auxiliary()`, `accept_auxiliary()`, `_characters_active_pc()`, `_accept_rewrite_post()`

- [ ] **Step 2: Add delegation, run tests, commit**

Same pattern as Task 4. Run `pytest tests/orchestrator/ -x -q`, expect all pass.

```
git add backend/src/grimoire/orchestrator/auxiliary.py backend/src/grimoire/orchestrator/service.py
git commit -m "refactor(orchestrator): extract AuxiliaryCoordinator collaborator"
```

---

### Task 6: Extract RetconCoordinator

**Files:**
- Create: `backend/src/grimoire/orchestrator/retcon.py`
- Modify: `backend/src/grimoire/orchestrator/service.py`

- [ ] **Step 1: Move retcon methods**

Move: `retcon_post()`, `accept_replay()`, `try_again_replay()`, `cancel_replay()`, `get_replay_state()`, `retcon_replay` property, `_retcon_leave_as_is()`

Note: `RetconCoordinator` may need a reference to `AlternatesManager` since retcon replays can call `_regenerate_post_core`. Pass it as a constructor dependency.

- [ ] **Step 2: Add delegation, run tests, commit**

```
git add backend/src/grimoire/orchestrator/retcon.py backend/src/grimoire/orchestrator/service.py
git commit -m "refactor(orchestrator): extract RetconCoordinator collaborator"
```

---

### Task 7: Extract ForkCoordinator

**Files:**
- Create: `backend/src/grimoire/orchestrator/fork.py`
- Modify: `backend/src/grimoire/orchestrator/service.py`

- [ ] **Step 1: Move fork methods**

Move: `fork()`, `fork_campaign()`, `list_pending_forks()`, `process_pending_forks()`, `get_lineage()`, `get_lineage_ancestors()`, `_execute_fork()`, `_clone_campaign_row()`, `_campaign_exists()`, `_is_streaming()`, `_enqueue_fork()`, `_copy_campaign_files()`, `_wipe_failed_fork()`

- [ ] **Step 2: Add delegation, run tests, commit**

```
git add backend/src/grimoire/orchestrator/fork.py backend/src/grimoire/orchestrator/service.py
git commit -m "refactor(orchestrator): extract ForkCoordinator collaborator"
```

---

### Task 8: Final verification

- [ ] **Step 1: Verify OrchestratorService size**

Run: `cd backend && wc -l src/grimoire/orchestrator/service.py`
Expected: Under 1,000 lines

- [ ] **Step 2: Verify no circular imports**

Run: `cd backend && uv run python -c "from grimoire.orchestrator.service import OrchestratorService; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Run full test suite**

Run: `cd backend && uv run pytest -x -q`
Expected: All pass

- [ ] **Step 4: Verify all public methods still exist**

Run: `cd backend && uv run python -c "
from grimoire.orchestrator.service import OrchestratorService
methods = [m for m in dir(OrchestratorService) if not m.startswith('_')]
print('\n'.join(sorted(methods)))
"`
Expected: All 35 public methods from the spec are listed.
