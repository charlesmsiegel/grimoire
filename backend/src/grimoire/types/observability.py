"""Observability types: turn audits, cost tracking, debug log, replay."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from .common import BranchId, CampaignId, GenJobId, Json, TurnId
from .context import ContextSource
from .extraction import ExtractionFlag
from .mechanics import MechanicsResult, ProposedRoll
from .scene import SceneBreakDecision
from .state import AppliedDelta, ContextTier, ReviewItem, StateDelta
from .time import TimeAdvanceResult


@dataclass
class CompositionSnapshot:
    """What library state a turn saw, captured at turn time."""

    mechanics_module: str | None
    setting_refs: list[Json] = field(default_factory=list)
    style_guide_id: str | None = None
    image_preset_id: str | None = None


@dataclass
class ContextSummary:
    total_tokens: int
    per_tier: dict[ContextTier, int] = field(default_factory=dict)
    source_count: int = 0
    spotlight_characters: list[str] = field(default_factory=list)


@dataclass
class WarningRecord:
    timestamp: datetime
    module: str
    message: str
    payload: Json = field(default_factory=dict)


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


@dataclass
class LogEvent:
    timestamp: datetime
    level: LogLevel
    module: str
    operation: str
    payload: Json = field(default_factory=dict)
    turn_id: TurnId | None = None
    duration_ms: int | None = None
    error: str | None = None


@dataclass
class LogQuery:
    since: datetime | None = None
    until: datetime | None = None
    levels: list[LogLevel] | None = None
    modules: list[str] | None = None
    operations: list[str] | None = None
    turn_id: TurnId | None = None
    free_text: str | None = None
    limit: int = 500


@dataclass
class ErrorRecord:
    timestamp: datetime
    module: str
    operation: str
    error_kind: str
    message: str
    turn_id: TurnId | None = None
    traceback: str | None = None
    context: Json = field(default_factory=dict)
    user_visible: bool = False
    user_action_taken: str | None = None


@dataclass
class TurnAudit:
    turn_id: TurnId
    campaign_id: CampaignId
    branch_id: BranchId
    started_at: datetime
    completed_at: datetime | None = None
    duration_ms: int | None = None

    player_input: str = ""
    options: Json = field(default_factory=dict)

    composition_snapshot: CompositionSnapshot | None = None
    scene_id: str = ""
    scene_break_decision: SceneBreakDecision | None = None

    context_summary: ContextSummary | None = None
    context_sources: list[ContextSource] = field(default_factory=list)
    context_budget_used: dict[ContextTier, int] = field(default_factory=dict)
    context_messages_hash: str = ""

    proposed_rolls: list[ProposedRoll] = field(default_factory=list)
    resolved_rolls: list[MechanicsResult] = field(default_factory=list)

    llm_provider: str = ""
    llm_model: str = ""
    llm_params: Json = field(default_factory=dict)
    llm_prompt_tokens: int = 0
    llm_completion_tokens: int = 0
    llm_cost_usd: float | None = None
    llm_latency_ms: int = 0
    llm_finish_reason: str = ""
    llm_retries: int = 0

    response_text: str = ""

    extraction_strategies_run: list[str] = field(default_factory=list)
    extraction_duration_ms: int = 0
    extracted_deltas: list[StateDelta] = field(default_factory=list)
    extraction_flags: list[ExtractionFlag] = field(default_factory=list)

    applied_deltas: list[AppliedDelta] = field(default_factory=list)
    queued_for_review: list[ReviewItem] = field(default_factory=list)

    scene_appended: bool = False
    scene_closed: bool = False

    images_scheduled: list[GenJobId] = field(default_factory=list)
    time_advanced: TimeAdvanceResult | None = None

    errors: list[ErrorRecord] = field(default_factory=list)
    warnings: list[WarningRecord] = field(default_factory=list)


@dataclass
class ReplaySubstitution:
    model: str | None = None
    temperature: float | None = None
    extra_context: str | None = None
    prompt_edit: str | None = None


@dataclass
class ReplayOptions:
    on_fork: bool = True
    substitute: ReplaySubstitution | None = None


@dataclass
class ReplayResult:
    turn_id: TurnId
    new_response_text: str
    delta_diff: list[Json] = field(default_factory=list)
    forked_branch_id: str | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class CostTotal:
    total_usd: float
    input_tokens: int = 0
    output_tokens: int = 0
    call_count: int = 0


@dataclass
class DailyCost:
    date: datetime
    total_usd: float
    call_count: int = 0


@dataclass
class HealthTarget:
    id: str
    kind: str  # 'llm_provider', 'embedding_provider', 'imagegen_backend', 'plugin', 'module'


@dataclass
class MetricSample:
    timestamp: datetime
    module: str
    operation: str
    duration_ms: float
    success: bool
    payload: Json = field(default_factory=dict)
