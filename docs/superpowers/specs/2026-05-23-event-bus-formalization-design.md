# Event Bus Formalization

Date: 2026-05-23
Status: Approved
PR: 9 of ~10 in the Grimoire code quality refactor series
Depends on: PR 1 (Container/DI typing), PR 8 (Data layer hardening)

## Problem

The event bus uses untyped string event types and `dict[str, Any]` payloads. There are ~60 distinct event types scattered across 15+ modules, discoverable only by grepping. Payload structures are undocumented -- subscribers hardcode expected keys and silently get `None` if the publisher changes the payload. There is no registry, no schema validation, and no way to know what events exist without reading every emit call.

## Solution

Create a typed event catalog with payload models. Keep the event bus runtime simple (it works well), but add compile-time and documentation safety around event types and payloads.

## Detailed Design

### Step 1: Event Type Constants

Create `backend/src/grimoire/events.py` with all event type strings as module-level constants:

```python
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

### Step 2: Typed Payload Models

Create `backend/src/grimoire/event_payloads.py` with Pydantic models for high-traffic events:

```python
class LLMResponsePayload(BaseModel):
    task: str
    model: str
    campaign_id: str | None
    input_tokens: int
    output_tokens: int

class DeltasAppliedPayload(BaseModel):
    turn_id: str
    campaign_id: str
    count: int
    ids: list[int]

class ImageReadyPayload(BaseModel):
    job_id: str
    image_id: str
    campaign_id: str

# ... etc for high-traffic events
```

Not every event needs a payload model immediately. Start with events that have subscribers checking specific keys (the ones most likely to break silently). Lower-traffic diagnostic events can stay as `dict[str, Any]` initially.

### Step 3: Typed Emit Helper

Add a helper that validates payloads:

```python
# In event_bus.py
async def emit_typed(self, event_type: str, payload: BaseModel) -> None:
    await self.emit(Event(type=event_type, payload=payload.model_dump()))
```

Publishers migrate incrementally from `emit(Event(type="...", payload={...}))` to `emit_typed("...", PayloadModel(...))`. Both paths coexist during migration.

### Step 4: Update StreamManager Forwarding List

`StreamManager._FORWARDED_EVENTS` (api/stream.py) currently lists 44 event type strings. Replace with references to the constants module:

```python
_FORWARDED_EVENTS = (
    events.TURN_STARTED,
    events.TURN_COMPLETE,
    events.LLM_RESPONSE_RECEIVED,
    # ... etc
)
```

### Step 5: Update Subscribers

Replace hardcoded string keys in subscriber handlers with constant references:

```python
# Before:
bus.subscribe("llm_response_received", self._on_llm_response)
usage = event.payload.get("usage")

# After:
bus.subscribe(events.LLM_RESPONSE_RECEIVED, self._on_llm_response)
payload = LLMResponsePayload(**event.payload)
```

Subscribers that parse payloads get compile-time safety on field names.

## Migration Strategy

This is incremental. The event bus itself doesn't change. The migration order:

1. Create `events.py` constants module.
2. Replace all string literals in `emit()` and `subscribe()` calls with constants. (Mechanical search-and-replace.)
3. Create payload models for the 10 highest-traffic events.
4. Update publishers to use `emit_typed()` for those events.
5. Update subscribers to parse typed payloads for those events.

Steps 1-2 are safe and can be done in one pass. Steps 3-5 are incremental per event type.

## Scope

### In scope
- Create `events.py` constants module (~60 constants)
- Replace all string literals with constant references
- Create payload models for top 10 event types
- Add `emit_typed()` helper
- Update StreamManager forwarding list

### Not in scope
- Changing the EventBus implementation
- Making all events typed (incremental migration)
- Adding event bus middleware or filtering
- Changing event delivery semantics (still fire-and-forget)

## Verification

1. `pytest` full suite passes.
2. Grep for quoted event type strings (`"turn_complete"`, `"llm_response_received"`, etc.) in `emit()` and `subscribe()` calls returns zero hits outside of `events.py`.
3. `events.py` has a constant for every event type emitted in the codebase.
4. Payload models have tests verifying they match the actual payloads emitted.
