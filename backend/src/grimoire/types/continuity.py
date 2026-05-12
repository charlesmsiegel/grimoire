"""Continuity types: facts, commitments, knowledge state."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from .common import (
    BranchId,
    CampaignId,
    CharacterRef,
    CommitmentId,
    Duration,
    FactId,
    InGameTime,
    Json,
    PostId,
)


class FactSource(StrEnum):
    NARRATOR = "narrator"
    CHARACTER_TESTIMONY = "character_testimony"
    INFERRED = "inferred"
    USER_DECLARED = "user_declared"


class FactScope(StrEnum):
    PRIVATE = "private"
    HOUSEHOLD = "household"
    PUBLIC = "public"
    WORLD = "world"


class FactSubject(BaseModel):
    character_ids: list[str] = Field(default_factory=list)
    location_ids: list[str] = Field(default_factory=list)
    faction_ids: list[str] = Field(default_factory=list)
    item_ids: list[str] = Field(default_factory=list)
    scope: FactScope = FactScope.PUBLIC


class Fact(BaseModel):
    id: FactId
    campaign_id: CampaignId
    branch_id: BranchId
    text: str
    established_in_post: PostId | None
    established_at_in_game: InGameTime | None
    confidence: float
    source: FactSource
    about: FactSubject = Field(default_factory=FactSubject)
    speaker_id: CharacterRef | None = None
    keywords: list[str] = Field(default_factory=list)
    embedding: list[float] | None = None
    retired: bool = False
    retired_in_post: PostId | None = None
    contradicts: list[FactId] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class CommitmentKind(StrEnum):
    PROMISE = "promise"
    THREAT = "threat"
    FORESHADOW = "foreshadow"
    OBLIGATION = "obligation"
    MYSTERY = "mystery"


class CommitmentStatus(StrEnum):
    OPEN = "open"
    PAID = "paid"
    BROKEN = "broken"
    STALE = "stale"
    OVERDUE = "overdue"
    REOPENED = "reopened"


class Commitment(BaseModel):
    id: CommitmentId
    campaign_id: CampaignId
    branch_id: BranchId
    kind: CommitmentKind
    text: str
    created_in_post: PostId | None
    in_game_created_at: InGameTime | None
    from_id: str | None = None
    to_id: str | None = None
    due_by: InGameTime | None = None
    status: CommitmentStatus = CommitmentStatus.OPEN
    weight: int = 1
    resolved_in_post: PostId | None = None
    tags: list[str] = Field(default_factory=list)
    related_fact_ids: list[FactId] = Field(default_factory=list)


class KnowledgeEntry(BaseModel):
    fact_id: FactId
    character_id: CharacterRef
    knows: bool
    learned_in_post: PostId | None = None
    source: str = ""  # 'told by X', 'witnessed', 'deduced'


class ContradictionReport(BaseModel):
    id: str
    new_fact: Fact
    existing_fact: Fact
    confidence: float
    rationale: str = ""


class AgingReport(BaseModel):
    """Result of `Continuity.age(to_time)`."""

    to_time: InGameTime
    now_overdue: list[Commitment] = Field(default_factory=list)
    newly_stale: list[Commitment] = Field(default_factory=list)
    reopened: list[Commitment] = Field(default_factory=list)


class Relationship(BaseModel):
    from_ref: CharacterRef
    to_ref: CharacterRef
    types: list[str] = Field(default_factory=list)
    state: Json = Field(default_factory=dict)
    history: list[Json] = Field(default_factory=list)


class StaleCommitmentQuery(BaseModel):
    threshold: Duration
