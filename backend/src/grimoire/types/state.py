"""State deltas, transient state, review queue items."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from .common import (
    BranchId,
    CampaignId,
    CharacterRef,
    FactionRef,
    Json,
    LocationRef,
    Scope,
    TurnId,
)


class DeltaKind(StrEnum):
    """High-level delta categories. Modules add system-specific kinds in `extra`."""

    FACT_ADD = "fact_add"
    FACT_RETIRE = "fact_retire"
    FACT_UPDATE = "fact_update"
    COMMITMENT_ADD = "commitment_add"
    COMMITMENT_RESOLVE = "commitment_resolve"
    CHARACTER_STATE_UPDATE = "character_state_update"
    LOCATION_STATE_UPDATE = "location_state_update"
    FACTION_STATE_UPDATE = "faction_state_update"
    RELATIONSHIP_UPDATE = "relationship_update"
    KNOWLEDGE_REVEAL = "knowledge_reveal"
    SHEET_UPDATE = "sheet_update"
    INVENTORY_CHANGE = "inventory_change"
    SCENE_APPEND_POST = "scene_append_post"
    SCENE_CHANGE = "scene_change"
    TIME_ADVANCE = "time_advance"
    OVERRIDE_WRITE = "override_write"
    EMERGENT_CREATE = "emergent_create"
    LIBRARY_FILE_WRITE = "library_file_write"
    LIBRARY_FILE_DELETE = "library_file_delete"
    PROMOTION = "promotion"
    MECHANICAL_EVENT = "mechanical_event"
    OTHER = "other"


class StateDelta(BaseModel):
    """A proposed state change. Source-attributed and reversible.

    Produced by the Extractor (LLM output → deltas), the Mechanics module
    (roll outcomes → deltas), the Time Engine (tick effects), or domain modules
    directly. Applied via the State Store, which records the inverse in `before`.
    """

    kind: DeltaKind
    target_scope: Scope
    target_id: str  # composite identifier (entity id, fact id, scene id, etc.)
    target_table: str | None = None  # for sqlite targets
    target_path: str | None = None  # for file targets
    after: Json = Field(default_factory=dict)
    before: Json | None = None  # populated when applied (for reversal)
    confidence: float = 1.0
    source: str = ""  # "extractor", "mechanics:wod-mechanics", "user", ...
    evidence: str = ""
    notes: str = ""
    extra: Json = Field(default_factory=dict)


class AppliedDelta(BaseModel):
    """A `StateDelta` after it has been recorded in the delta log."""

    id: str
    delta: StateDelta
    campaign_id: CampaignId | None
    branch_id: BranchId | None
    turn_id: TurnId | None
    applied_at: datetime
    reversed_at: datetime | None = None


class ReviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EDITED = "edited"


class ReviewItem(BaseModel):
    id: str
    delta: StateDelta
    campaign_id: CampaignId
    status: ReviewStatus = ReviewStatus.PENDING
    reviewed_at: datetime | None = None
    reviewer_notes: str = ""
    contradicts: list[str] = Field(default_factory=list)  # fact ids etc.


class ContextTier(StrEnum):
    LOCK_IN = "lock-in"
    SPOTLIGHT = "spotlight"
    BACKGROUND = "background"
    ARCHIVE = "archive"


class CharacterState(BaseModel):
    character_ref: CharacterRef
    campaign_id: CampaignId
    branch_id: BranchId
    location_ref: LocationRef | None = None
    emotional_state: str = ""
    physical_state: str = ""
    immediate_intent: str = ""
    knowledge_state: Json = Field(default_factory=dict)
    last_action: str | None = None
    last_screen_time_turn: TurnId | None = None
    visible_to_pc: bool = False
    drift_score: float = 0.0
    tier_pin: ContextTier | None = None
    current_scene_id: str | None = None
    updated_at_turn: TurnId | None = None
    appearances_since_last_drift_check: int = 0


class LocationState(BaseModel):
    location_ref: LocationRef
    campaign_id: CampaignId
    branch_id: BranchId
    weather: Json = Field(default_factory=dict)
    time_of_day: str = ""
    occupants: list[CharacterRef] = Field(default_factory=list)
    condition: str = ""
    transient_features: Json = Field(default_factory=dict)
    updated_at_turn: TurnId | None = None


class FactionState(BaseModel):
    faction_ref: FactionRef
    campaign_id: CampaignId
    branch_id: BranchId
    state: Json = Field(default_factory=dict)
    updated_at_turn: TurnId | None = None


class StateSnapshot(BaseModel):
    """A compact view of relevant state at one point. Used by the Extractor."""

    campaign_id: CampaignId
    branch_id: BranchId
    scene_id: str | None
    character_states: list[CharacterState] = Field(default_factory=list)
    location_states: list[LocationState] = Field(default_factory=list)
    open_commitments: list[Json] = Field(default_factory=list)
    recent_facts: list[Json] = Field(default_factory=list)


class SearchResult(BaseModel):
    """A vector or keyword search result."""

    # composite id; e.g. 'campaign:scene:0003' or 'library:worlds/.../characters/...'
    ref: str
    scope: Scope
    source_kind: str  # 'post', 'scene_summary', 'character', 'lore', 'fact'
    text: str
    score: float
    metadata: Json = Field(default_factory=dict)
