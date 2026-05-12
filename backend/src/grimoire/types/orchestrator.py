"""Orchestrator types: events, turn results, submit/advance payloads."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from .common import CampaignId, EventId, Json, SceneId, SubscriptionId, TurnId


class EventType(StrEnum):
    # Turn lifecycle
    TURN_STARTED = "turn_started"
    CONTEXT_BUILT = "context_built"
    MODEL_RESPONSE_RECEIVED = "model_response_received"
    DELTAS_EXTRACTED = "deltas_extracted"
    TURN_COMPLETE = "turn_complete"
    TURN_UNDONE = "turn_undone"

    # Scene
    SCENE_STARTED = "scene_started"
    SCENE_ENDED = "scene_ended"

    # Multi-PC
    PC_POST_APPENDED = "pc_post_appended"
    POST_APPENDED = "post_appended"
    ADVANCE_REQUESTED = "advance_requested"
    ADVANCE_DISABLED = "advance_disabled"
    ADVANCE_ENABLED = "advance_enabled"

    # Time / continuity
    TIME_ADVANCED = "time_advanced"
    NPC_TICK_COMPLETE = "npc_tick_complete"
    FACT_RECORDED = "fact_recorded"
    COMMITMENT_CREATED = "commitment_created"
    COMMITMENT_PAID_OFF = "commitment_paid_off"
    CONTRADICTION_DETECTED = "contradiction_detected"

    # Drift
    DRIFT_DETECTED = "drift_detected"

    # Library
    LIBRARY_FILE_CHANGED = "library_file_changed"
    LIBRARY_INDEXED = "library_indexed"
    CAMPAIGN_FILE_CHANGED = "campaign_file_changed"
    SCENE_FILE_CHANGED = "scene_file_changed"
    SHEET_FILE_CHANGED = "sheet_file_changed"
    ENTITY_PROMOTED = "entity_promoted"
    LIBRARY_REF_UPGRADED = "library_ref_upgraded"

    # ImageGen
    IMAGE_READY = "image_ready"
    IMAGEGEN_JOB_QUEUED = "imagegen_job_queued"
    IMAGEGEN_JOB_STARTED = "imagegen_job_started"
    IMAGEGEN_PROGRESS = "imagegen_progress"
    IMAGEGEN_JOB_FAILED = "imagegen_job_failed"
    IMAGEGEN_BACKEND_HEALTH_CHANGED = "imagegen_backend_health_changed"

    # Plugins
    PLUGIN_LOADED = "plugin_loaded"
    PLUGIN_HEALTH_CHANGED = "plugin_health_changed"

    # LLM
    LLM_REQUEST_STARTED = "llm_request_started"
    LLM_RESPONSE_RECEIVED = "llm_response_received"
    LLM_REQUEST_FAILED = "llm_request_failed"
    EMBEDDING_REQUEST_STARTED = "embedding_request_started"
    EMBEDDING_RESPONSE_RECEIVED = "embedding_response_received"
    PROVIDER_HEALTH_CHANGED = "provider_health_changed"

    # Review queue
    REVIEW_ITEM_ADDED = "review_item_added"
    REVIEW_ITEM_RESOLVED = "review_item_resolved"

    # Misc
    RUNNING_SUMMARY_UPDATED = "running_summary_updated"
    THREAD_INTRODUCED = "thread_introduced"
    THREAD_PAID_OFF = "thread_paid_off"


@dataclass
class Event:
    type: EventType
    timestamp: datetime
    payload: Json = field(default_factory=dict)
    campaign_id: CampaignId | None = None
    turn_id: TurnId | None = None
    scene_id: SceneId | None = None
    source_module: str = ""


@dataclass
class Subscription:
    id: SubscriptionId
    event_type: EventType | str | None  # None = wildcard
    active: bool = True


@dataclass
class SubmitResult:
    accepted: bool
    turn_id: TurnId | None = None
    auto_responding: bool = False
    queue_position: int | None = None
    reason: str = ""


@dataclass
class TurnStatus:
    turn_id: TurnId
    campaign_id: CampaignId
    started_at: datetime
    stage: str  # 'context_build', 'streaming', 'extracting', 'applying'
    progress_pct: float | None = None


@dataclass
class RegenerateResult:
    turn_id: TurnId
    accepted: bool
    reason: str = ""


@dataclass
class UndoResult:
    turns_undone: list[TurnId] = field(default_factory=list)
    reversed_delta_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class RetconResult:
    post_id: str
    original_text: str
    new_text: str
    reversed_delta_ids: list[str] = field(default_factory=list)
    new_delta_ids: list[str] = field(default_factory=list)
    downstream_flagged_turns: list[TurnId] = field(default_factory=list)


@dataclass
class ForkResult:
    new_branch_id: str
    from_turn_id: TurnId
    label: str
    created_at: datetime


@dataclass
class EventRecord:
    """Stored form of an event for replay / observability."""

    id: EventId
    event: Event
