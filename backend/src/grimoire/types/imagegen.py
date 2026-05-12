"""ImageGen types: backends, requests, jobs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from .common import CampaignId, GenJobId, Json


@dataclass
class LoraSpec:
    id: str
    weight: float = 1.0


@dataclass
class BackendCapabilities:
    text_to_image: bool = True
    image_to_image: bool = False
    inpainting: bool = False
    controlnet: bool = False
    lora: bool = False
    img2img_strength_range: tuple[float, float] = (0.0, 1.0)
    max_resolution: tuple[int, int] = (1024, 1024)
    supports_negative_prompt: bool = True
    supports_seed: bool = True


@dataclass
class BackendInfo:
    id: str
    name: str
    capabilities: BackendCapabilities
    is_integrated: bool = False
    plugin_id: str | None = None


@dataclass
class GenerationRequest:
    prompt: str
    width: int = 1024
    height: int = 1024
    steps: int = 28
    cfg_scale: float = 6.5
    sampler: str = ""
    negative_prompt: str | None = None
    seed: int | None = None
    model: str | None = None
    init_image: bytes | None = None
    init_image_strength: float | None = None
    loras: list[LoraSpec] = field(default_factory=list)
    extra: Json = field(default_factory=dict)


@dataclass
class GenerationResult:
    image_bytes: bytes
    thumbnail_bytes: bytes
    backend: str
    model: str
    seed: int
    actual_params: Json = field(default_factory=dict)
    duration_ms: int = 0
    error: str | None = None


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class GenerationJob:
    id: GenJobId
    campaign_id: CampaignId
    backend: str
    request: GenerationRequest
    status: JobStatus = JobStatus.QUEUED
    priority: int = 5
    queued_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    scene_id: str | None = None
    post_id: str | None = None
    result: GenerationResult | None = None
    error: str | None = None


@dataclass
class ImageMetadata:
    id: str
    campaign_id: CampaignId
    file_path: str
    thumbnail_path: str | None = None
    prompt: str = ""
    negative_prompt: str = ""
    params: Json = field(default_factory=dict)
    backend: str = ""
    model: str = ""
    seed: int | None = None
    scene_id: str | None = None
    post_id: str | None = None
    created_at: datetime | None = None
    user_starred: bool = False
    tags: list[str] = field(default_factory=list)
