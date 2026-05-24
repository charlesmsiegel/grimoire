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
