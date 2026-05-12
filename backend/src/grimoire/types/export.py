"""Export types: selections, options, results."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from .common import BranchId, CampaignId, InGameTime, Json


class ExportCapabilities(BaseModel):
    supports_images: bool = True
    supports_appendices: bool = True
    supports_filters: bool = True
    supported_style_presets: list[str] = Field(default_factory=list)


class ExportSelection(BaseModel):
    branch_id: BranchId
    scene_ids: list[str] | None = None
    date_range: tuple[InGameTime, InGameTime] | None = None
    include_images: bool = True
    include_appendices: list[str] = Field(default_factory=list)
    include_drafts: bool = False
    include_review_queue: bool = False
    filters: Json = Field(default_factory=dict)


class ExportOptions(BaseModel):
    title: str = ""
    subtitle: str | None = None
    author: str | None = None
    cover_image: bytes | None = None
    style_preset: str = "novel"
    extra: Json = Field(default_factory=dict)


class ExportResult(BaseModel):
    format: str
    size_bytes: int
    scene_count: int = 0
    word_count: int = 0
    image_count: int = 0
    file_path: str | None = None
    payload: bytes | None = None  # in-memory artifact when not written to disk
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime | None = None


class ExportPreview(BaseModel):
    adapter_id: str
    scene_count: int
    word_count: int
    image_count: int
    estimated_size_bytes: int
    warnings: list[str] = Field(default_factory=list)


class ExportRecord(BaseModel):
    id: str
    campaign_id: CampaignId
    adapter_id: str
    selection: ExportSelection
    options: ExportOptions
    result: ExportResult
    created_at: datetime
