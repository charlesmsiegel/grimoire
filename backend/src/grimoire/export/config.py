"""Configuration dataclasses for the Export module (spec 13 §Configuration).

The top-level :class:`ExportConfig` is surfaced through ``Settings.export``
and threaded into :class:`ExportService` plus the EPUB / bundled-plugin
adapters. Per-adapter blocks supply the default-options each adapter uses
when the caller's :class:`ExportOptions` doesn't override; per-filter
defaults are honoured by ``_build_filter_context`` when
``selection.filters`` doesn't carry the same key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class EpubAdapterConfig:
    default_style: str = "novel"
    include_appendices_by_default: list[str] = field(
        default_factory=lambda: ["cast", "world", "calendar", "gallery"]
    )
    validate_with_epubcheck: bool = True
    epubcheck_path: str | None = None
    default_cover_generated: bool = False


@dataclass
class MarkdownAdapterConfig:
    default_filename_format: str = "{title}-{timestamp}"
    include_assets: bool = True


@dataclass
class JsonAdapterConfig:
    pretty_print: bool = True
    include_embeddings: bool = False


@dataclass
class ExportAdaptersConfig:
    epub: EpubAdapterConfig = field(default_factory=EpubAdapterConfig)
    markdown: MarkdownAdapterConfig = field(default_factory=MarkdownAdapterConfig)
    json: JsonAdapterConfig = field(default_factory=JsonAdapterConfig)


@dataclass
class ExportFiltersConfig:
    strip_ooc_default: bool = True
    strip_mechanics_default: bool = False
    strip_narrator_scaffolding_default: bool = True
    anonymize_default: bool = False


@dataclass
class ExportConfig:
    default_adapter: str = "epub"
    output_directory: Path = Path("./exports")
    history_limit: int = 100
    adapters: ExportAdaptersConfig = field(default_factory=ExportAdaptersConfig)
    filters: ExportFiltersConfig = field(default_factory=ExportFiltersConfig)


__all__ = [
    "EpubAdapterConfig",
    "ExportAdaptersConfig",
    "ExportConfig",
    "ExportFiltersConfig",
    "JsonAdapterConfig",
    "MarkdownAdapterConfig",
]
