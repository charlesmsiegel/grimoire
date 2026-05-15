"""Observability types: turn audits, cost tracking, debug log, replay."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from .common import BranchId, CampaignId, GenJobId, Json, TurnId
from .context import ContextSource
from .extraction import ExtractionFlag
from .mechanics import MechanicsResult, ProposedRoll
from .scene import SceneBreakDecision
from .state import AppliedDelta, ContextTier, ReviewItem, StateDelta
from .time import TimeAdvanceResult


class CompositionSnapshot(BaseModel):
    """What library state a turn saw, captured at turn time."""

    mechanics_module: str | None
    world_refs: list[Json] = Field(default_factory=list)
    style_guide_id: str | None = None
    image_preset_id: str | None = None


class ContextSummary(BaseModel):
    total_tokens: int
    per_tier: dict[ContextTier, int] = Field(default_factory=dict)
    source_count: int = 0
    spotlight_characters: list[str] = Field(default_factory=list)


class WarningRecord(BaseModel):
    timestamp: datetime
    module: str
    message: str
    payload: Json = Field(default_factory=dict)


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class LogEvent(BaseModel):
    timestamp: datetime
    level: LogLevel
    module: str
    operation: str
    payload: Json = Field(default_factory=dict)
    turn_id: TurnId | None = None
    duration_ms: int | None = None
    error: str | None = None


class LogQuery(BaseModel):
    since: datetime | None = None
    until: datetime | None = None
    levels: list[LogLevel] | None = None
    modules: list[str] | None = None
    operations: list[str] | None = None
    turn_id: TurnId | None = None
    free_text: str | None = None
    limit: int = 500


class ErrorRecord(BaseModel):
    timestamp: datetime
    module: str
    operation: str
    error_kind: str
    message: str
    turn_id: TurnId | None = None
    traceback: str | None = None
    context: Json = Field(default_factory=dict)
    user_visible: bool = False
    user_action_taken: str | None = None


class TurnAudit(BaseModel):
    turn_id: TurnId
    campaign_id: CampaignId
    branch_id: BranchId
    started_at: datetime
    completed_at: datetime | None = None
    duration_ms: int | None = None

    player_input: str = ""
    options: Json = Field(default_factory=dict)

    composition_snapshot: CompositionSnapshot | None = None
    scene_id: str = ""
    scene_break_decision: SceneBreakDecision | None = None

    context_summary: ContextSummary | None = None
    context_sources: list[ContextSource] = Field(default_factory=list)
    context_budget_used: dict[ContextTier, int] = Field(default_factory=dict)
    context_messages_hash: str = ""

    proposed_rolls: list[ProposedRoll] = Field(default_factory=list)
    resolved_rolls: list[MechanicsResult] = Field(default_factory=list)

    llm_provider: str = ""
    llm_model: str = ""
    llm_params: Json = Field(default_factory=dict)
    llm_prompt_tokens: int = 0
    llm_completion_tokens: int = 0
    llm_cost_usd: float | None = None
    llm_latency_ms: int = 0
    llm_finish_reason: str = ""
    llm_retries: int = 0

    response_text: str = ""

    extraction_strategies_run: list[str] = Field(default_factory=list)
    extraction_duration_ms: int = 0
    extracted_deltas: list[StateDelta] = Field(default_factory=list)
    extraction_flags: list[ExtractionFlag] = Field(default_factory=list)

    applied_deltas: list[AppliedDelta] = Field(default_factory=list)
    queued_for_review: list[ReviewItem] = Field(default_factory=list)

    scene_appended: bool = False
    scene_closed: bool = False

    images_scheduled: list[GenJobId] = Field(default_factory=list)
    time_advanced: TimeAdvanceResult | None = None

    errors: list[ErrorRecord] = Field(default_factory=list)
    warnings: list[WarningRecord] = Field(default_factory=list)


class ReplaySubstitution(BaseModel):
    model: str | None = None
    temperature: float | None = None
    extra_context: str | None = None
    prompt_edit: str | None = None


class ReplayOptions(BaseModel):
    on_fork: bool = True
    substitute: ReplaySubstitution | None = None


class ReplayResult(BaseModel):
    turn_id: TurnId
    new_response_text: str
    delta_diff: list[Json] = Field(default_factory=list)
    forked_branch_id: str | None = None
    warnings: list[str] = Field(default_factory=list)


class CostTotal(BaseModel):
    total_usd: float
    input_tokens: int = 0
    output_tokens: int = 0
    call_count: int = 0


class DailyCost(BaseModel):
    date: datetime
    total_usd: float
    call_count: int = 0


class HealthTarget(BaseModel):
    id: str
    kind: str  # 'llm_provider', 'embedding_provider', 'imagegen_backend', 'plugin', 'module'


class MetricSample(BaseModel):
    timestamp: datetime
    module: str
    operation: str
    duration_ms: float
    success: bool
    payload: Json = Field(default_factory=dict)
