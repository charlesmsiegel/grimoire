# PR 9: Event Bus Formalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a typed event catalog with ~60 constants, replace all string literals in `emit()`/`subscribe()` calls, create typed payload models for the top 10 event types, and add an `emit_typed()` helper.

**Architecture:** New `events.py` module defines all event type strings as module-level constants. New `event_payloads.py` defines Pydantic models for high-traffic event payloads. The `EventBus` gains an `emit_typed()` method. Migration is incremental: constants replace strings first (mechanical), then payload models are adopted event-by-event.

**Tech Stack:** Python 3.12+, Pydantic

---

### Task 1: Create events.py constants module

**Files:**
- Create: `backend/src/grimoire/events.py`

- [ ] **Step 1: Define all ~60 event type constants**

Read every `emit(Event(type="..."))` and `subscribe("...")` call across the codebase (the research from the brainstorming phase identified all of them). Create a constant for each:

```python
"""Typed event type constants for the EventBus.

Every event type emitted or subscribed to in the codebase should have
a constant here. Using these constants instead of string literals
prevents typos and makes event discovery possible via IDE search.
"""

# Turn lifecycle
TURN_STARTED = "turn_started"
TURN_COMPLETE = "turn_complete"
TURN_UNDONE = "turn_undone"
PC_POST_APPENDED = "pc_post_appended"
ADVANCE_REQUESTED = "advance_requested"

# Scene lifecycle
SCENE_STARTED = "scene_started"
SCENE_ENDED = "scene_ended"
SCENE_BREAK_SUGGESTED = "scene_break_suggested"

# LLM
LLM_REQUEST_STARTED = "llm_request_started"
LLM_RESPONSE_RECEIVED = "llm_response_received"
LLM_REQUEST_FAILED = "llm_request_failed"
TIER_RESOLVED = "tier_resolved"
EMBEDDING_REQUEST_STARTED = "embedding_request_started"
EMBEDDING_RESPONSE_RECEIVED = "embedding_response_received"

# Alternates
ALTERNATE_ADDED = "alternate_added"
PRIMARY_SWITCHED = "primary_switched"
ALTERNATE_PINNED = "alternate_pinned"
ALTERNATE_DELETED = "alternate_deleted"

# Continuity
FACT_RECORDED = "fact_recorded"
CONTRADICTION_DETECTED = "contradiction_detected"
COMMITMENT_PAID_OFF = "commitment_paid_off"
COMMITMENT_BROKEN = "commitment_broken"
COMMITMENT_REOPENED = "commitment_reopened"
COMMITMENT_OVERDUE = "commitment_overdue"
COMMITMENT_STALE = "commitment_stale"

# Deltas
DELTAS_APPLIED = "deltas_applied"
DELTAS_EXTRACTED = "deltas_extracted"

# ImageGen
IMAGEGEN_JOB_QUEUED = "imagegen_job_queued"
IMAGEGEN_JOB_STARTED = "imagegen_job_started"
IMAGEGEN_JOB_FAILED = "imagegen_job_failed"
IMAGEGEN_PROGRESS = "imagegen_progress"
IMAGE_READY = "image_ready"
IMAGEGEN_WARNING = "imagegen_warning"
IMAGEGEN_BACKEND_HEALTH_CHANGED = "imagegen_backend_health_changed"

# Fork
CAMPAIGN_FORK_STARTED = "campaign_fork_started"
CAMPAIGN_FORK_FAILED = "campaign_fork_failed"
CAMPAIGN_FORKED = "campaign_forked"
CAMPAIGN_FORK_QUEUED = "campaign_fork_queued"

# Time engine
TIME_ADVANCE = "time_advance"
TIME_ADVANCE_CHECKPOINT_SUGGESTED = "time_advance_checkpoint_suggested"
NPC_TICK_COMPLETE = "npc_tick_complete"
NPC_DRIFT_DETECTED = "npc_drift_detected"
SCHEDULED_EVENT_IMMINENT = "scheduled_event_imminent"

# Library & watcher
LIBRARY_INDEXED = "library_indexed"
LIBRARY_ENTITY_CHANGED = "library_entity_changed"

# Mechanics & plugins
MECHANICS_SWITCHED = "mechanics_switched"
PROVIDER_HEALTH_CHANGED = "provider_health_changed"

# Observability
HEALTH_STATUS_CHANGED = "health_status_changed"
ERROR_REPORTED = "error_reported"

# Review
REVIEW_ITEM_ADDED = "review_item_added"

# Background workers
BACKUP_COMPLETE = "backup_complete"
RETENTION_SWEEP_COMPLETED = "retention_sweep_completed"
EMBEDDING_PROGRESS = "embedding_progress"
LIBRARY_SUMMARY_PROGRESS = "library_summary_progress"

# Context
CONTEXT_BUILT = "context_built"
MODEL_RESPONSE_RECEIVED = "model_response_received"

# Audit
TURN_AUDIT_FRAGMENT = "turn_audit_fragment"
```

Verify completeness by grepping: `cd backend && grep -rhoP 'Event\(type="([^"]+)"' src/grimoire/ | sort -u`

- [ ] **Step 2: Commit**

```
git add backend/src/grimoire/events.py
git commit -m "feat: add typed event constants module"
```

---

### Task 2: Replace string literals with constants

- [ ] **Step 1: Mechanical replacement across all emit() calls**

For each file that calls `emit(Event(type="...", ...))`, replace the string literal with the constant:

```python
# Before:
await self._bus.emit(Event(type="turn_complete", payload={...}))

# After:
from grimoire import events
await self._bus.emit(Event(type=events.TURN_COMPLETE, payload={...}))
```

Files to update (from research):
- `orchestrator/service.py` (~17 emit calls)
- `llm_gateway/gateway.py` (~10 emit calls)
- `continuity/service.py` (~7 emit calls)
- `imagegen/service.py` (~9 emit calls)
- `time_engine/service.py` (~5 emit calls)
- `mechanics/service.py` (1 emit call)
- `observability/service.py` (2 emit calls)
- `state_store/backup.py`, `retention.py`, `embedding_worker.py`, `summarizer.py` (1 each)
- `watcher/watcher.py` (~3 emit calls)

- [ ] **Step 2: Replace string literals in all subscribe() calls**

Same pattern for subscribers:
- `imagegen/integration.py`
- `characters/integration.py`
- `observability/service.py`, `turn_auditor.py`
- `api/stream.py` (_FORWARDED_EVENTS tuple)
- `transient_state/triggers.py`
- `time_engine/subscriber.py`

- [ ] **Step 3: Verify no string literals remain**

Run: `cd backend && grep -rn '"turn_complete"\|"llm_response_received"\|"scene_started"' src/grimoire/ | grep -v events.py | grep -v "test"`
Expected: Zero hits (all replaced with constants)

- [ ] **Step 4: Run tests**

Run: `cd backend && uv run pytest -x -q`
Expected: All pass

- [ ] **Step 5: Commit**

```
git add backend/src/grimoire/
git commit -m "refactor: replace event type string literals with typed constants"
```

---

### Task 3: Create typed payload models for top 10 events

**Files:**
- Create: `backend/src/grimoire/event_payloads.py`

- [ ] **Step 1: Define payload models**

```python
"""Typed payload models for high-traffic events."""

from __future__ import annotations

from pydantic import BaseModel


class LLMResponsePayload(BaseModel):
    task: str
    model: str
    campaign_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0


class DeltasAppliedPayload(BaseModel):
    turn_id: str
    campaign_id: str
    count: int
    ids: list[int]


class ImageReadyPayload(BaseModel):
    job_id: str
    image_id: str
    campaign_id: str


class TierResolvedPayload(BaseModel):
    task: str
    tier: str | None = None
    route: str
    source: str
    campaign_id: str | None = None


class TurnUndonePayload(BaseModel):
    campaign_id: str
    turn_id: str
    reversed_deltas: list[int]


class FactRecordedPayload(BaseModel):
    fact_id: str
    source: str


class ContradictionDetectedPayload(BaseModel):
    report_id: str
    conflict_count: int


class ProviderHealthChangedPayload(BaseModel):
    provider_id: str
    tier: str
    level: str
    message: str


class LibraryIndexedPayload(BaseModel):
    library_files: int
    campaign_files: int
    embedding_queue_depth: int
    summary_queue_depth: int


class SceneBreakSuggestedPayload(BaseModel):
    campaign_id: str
    scene_id: str
    turn_id: str
    confidence: float
    reason: str
```

- [ ] **Step 2: Commit**

```
git add backend/src/grimoire/event_payloads.py
git commit -m "feat: add typed payload models for top 10 event types"
```

---

### Task 4: Add emit_typed helper to EventBus

**Files:**
- Modify: `backend/src/grimoire/event_bus.py`

- [ ] **Step 1: Add emit_typed method**

```python
async def emit_typed(self, event_type: str, payload: BaseModel) -> None:
    """Emit an event with a validated Pydantic payload."""
    await self.emit(Event(type=event_type, payload=payload.model_dump()))
```

Add the `BaseModel` import under `TYPE_CHECKING`.

- [ ] **Step 2: Commit**

```
git add backend/src/grimoire/event_bus.py
git commit -m "feat(event_bus): add emit_typed helper for validated payloads"
```

---

### Task 5: Update StreamManager forwarding list

**Files:**
- Modify: `backend/src/grimoire/api/stream.py`

- [ ] **Step 1: Replace string literals in _FORWARDED_EVENTS**

```python
from grimoire import events

_FORWARDED_EVENTS = (
    events.TURN_STARTED,
    events.TURN_COMPLETE,
    events.LLM_RESPONSE_RECEIVED,
    # ... all ~44 event types
)
```

- [ ] **Step 2: Run tests, commit**

```
git commit -m "refactor(stream): use typed event constants in forwarding list"
```

---

### Task 6: Final verification

- [ ] **Step 1: Run full suite**

Run: `cd backend && uv run pytest -x -q`
Expected: All pass

- [ ] **Step 2: Verify constant coverage**

Run a script that compares emitted event types against `events.py` constants to ensure nothing is missing.
