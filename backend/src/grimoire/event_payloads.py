"""Typed payload models for high-traffic events."""

from __future__ import annotations

from pydantic import BaseModel


class LLMUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class LLMResponsePayload(BaseModel):
    task: str
    model: str
    campaign_id: str | None = None
    usage: LLMUsage = LLMUsage()


class DeltasAppliedPayload(BaseModel):
    turn_id: str
    campaign_id: str
    count: int
    ids: list[str]


class ImageReadyPayload(BaseModel):
    image_id: str
    campaign_id: str
    cached: bool = False


class TierResolvedPayload(BaseModel):
    task: str
    tier: str | None = None
    route: str
    source: str
    campaign_id: str | None = None


class TurnUndonePayload(BaseModel):
    campaign_id: str
    turn_id: str
    reversed_deltas: list[str]


class FactRecordedPayload(BaseModel):
    fact_id: str
    source: str


class ContradictionDetectedPayload(BaseModel):
    report_id: str
    conflict_count: int


class ProviderHealthChangedPayload(BaseModel):
    target_id: str
    kind: str = ""
    old_level: str | None = None
    new_level: str
    message: str
    checked_at: str | None = None


class LibraryIndexedPayload(BaseModel):
    library_files: int
    campaign_files: int
    embedding_queue_depth: int


class SceneBreakSuggestedPayload(BaseModel):
    campaign_id: str
    scene_id: str
    turn_id: str
    confidence: float
    reason: str
