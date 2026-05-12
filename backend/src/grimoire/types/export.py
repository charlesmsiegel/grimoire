"""Export types: selections, options, results."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .common import BranchId, CampaignId, Json
from .time import InGameTime


@dataclass
class ExportCapabilities:
    supports_images: bool = True
    supports_appendices: bool = True
    supports_filters: bool = True
    supported_style_presets: list[str] = field(default_factory=list)


@dataclass
class ExportSelection:
    branch_id: BranchId
    scene_ids: list[str] | None = None
    date_range: tuple[InGameTime, InGameTime] | None = None
    include_images: bool = True
    include_appendices: list[str] = field(default_factory=list)
    include_drafts: bool = False
    include_review_queue: bool = False
    filters: Json = field(default_factory=dict)


@dataclass
class ExportOptions:
    title: str = ""
    subtitle: str | None = None
    author: str | None = None
    cover_image: bytes | None = None
    style_preset: str = "novel"
    extra: Json = field(default_factory=dict)


@dataclass
class ExportResult:
    format: str
    size_bytes: int
    scene_count: int = 0
    word_count: int = 0
    image_count: int = 0
    file_path: str | None = None
    bytes: bytes | None = None
    warnings: list[str] = field(default_factory=list)
    created_at: datetime | None = None


@dataclass
class ExportPreview:
    adapter_id: str
    scene_count: int
    word_count: int
    image_count: int
    estimated_size_bytes: int
    warnings: list[str] = field(default_factory=list)


@dataclass
class ExportRecord:
    id: str
    campaign_id: CampaignId
    adapter_id: str
    selection: ExportSelection
    options: ExportOptions
    result: ExportResult
    created_at: datetime
