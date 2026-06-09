"""Orchestrator types: events, turn results, submit/advance payloads."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

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
    PLUGIN_FAILED = "plugin_failed"
    PLUGIN_UNLOADED = "plugin_unloaded"
    PLUGIN_ACTIVATED = "plugin_activated"
    PLUGIN_DEACTIVATED = "plugin_deactivated"
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

    # Retcon replay
    RETCON_STARTED = "retcon_started"
    RETCON_POST_REPLAYED = "retcon_post_replayed"
    RETCON_POST_ACCEPTED = "retcon_post_accepted"
    RETCON_CANCELLED = "retcon_cancelled"
    RETCON_COMPLETE = "retcon_complete"


class Event(BaseModel):
    type: EventType
    timestamp: datetime
    payload: Json = Field(default_factory=dict)
    campaign_id: CampaignId | None = None
    turn_id: TurnId | None = None
    scene_id: SceneId | None = None
    source_module: str = ""


class Subscription(BaseModel):
    id: SubscriptionId
    event_type: EventType | str | None  # None = wildcard
    active: bool = True


class SubmitResult(BaseModel):
    accepted: bool
    turn_id: TurnId | None = None
    auto_responding: bool = False
    queue_position: int | None = None
    reason: str = ""


class TurnStatus(BaseModel):
    turn_id: TurnId
    campaign_id: CampaignId
    started_at: datetime
    stage: str  # 'context_build', 'streaming', 'extracting', 'applying'
    progress_pct: float | None = None


class RegeneratePostResult(BaseModel):
    """Outcome of :meth:`OrchestratorService.regenerate_post`.

    Per the swipes-alternates design: the new sample is materialized as a
    non-primary :class:`Alternate` on the post, its deltas are applied
    under a fresh ``delta_set_id`` (rewinding the previous primary's set),
    and the user reviews via the swipes UI.
    """

    post_id: str
    new_alternate_id: str
    delta_set_id: str


class UndoResult(BaseModel):
    turns_undone: list[TurnId] = Field(default_factory=list)
    reversed_delta_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class CascadeDeleteResult(BaseModel):
    deleted_post_ids: list[str] = Field(default_factory=list)
    reversed_turn_ids: list[TurnId] = Field(default_factory=list)
    requeued_review_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RetconResult(BaseModel):
    post_id: str
    original_text: str
    new_text: str
    reversed_delta_ids: list[str] = Field(default_factory=list)
    new_delta_ids: list[str] = Field(default_factory=list)
    downstream_flagged_turns: list[TurnId] = Field(default_factory=list)
    # Non-fatal degradations (review-queue or downstream-flagging failures);
    # fatal failures raise RetconExtractionError / RetconStateError instead.
    warnings: list[str] = Field(default_factory=list)
    # Populated only on the replay path (per 2026-05-19-retcon-design):
    # ``replay_batch_id`` is non-None when the user opted to replay subsequent
    # posts; the rest fill in as the batch advances. The leave-as-is path
    # leaves them all at their defaults.
    replay_batch_id: str | None = None
    replayed_post_ids: list[str] = Field(default_factory=list)
    cancelled_at_post_id: str | None = None
    contradictions_detected: list[str] = Field(default_factory=list)


class ReplayBatchStateView(BaseModel):
    """Client-facing view of a retcon replay batch's current state."""

    batch_id: str
    campaign_id: CampaignId
    edited_post_id: str
    subsequent_post_ids: list[str] = Field(default_factory=list)
    current_index: int = 0
    current_post_id: str | None = None
    current_alternate_id: str | None = None
    accepted_post_ids: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    completed: bool = False
    cancelled_at_post_id: str | None = None


class ForkCampaignResult(BaseModel):
    new_campaign_id: str
    new_name: str
    forked_from_campaign_id: CampaignId
    forked_at_post_id: str | None = None
    image_handling: str  # "hardlink" | "deep_copy" | "mixed"
    files_copied: int = 0
    deltas_replayed: int = 0
    fingerprint_match: bool = True
    degraded: bool = False
    queued: bool = False
    created_at: datetime


class EventRecord(BaseModel):
    """Stored form of an event for replay / observability."""

    id: EventId
    event: Event
