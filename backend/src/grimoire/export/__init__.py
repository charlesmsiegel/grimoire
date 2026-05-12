"""Export module (spec 13).

Public surface:

- :class:`ExportService` — registers adapters, runs exports, tracks history
- :class:`EpubAdapter` — EPUB 3 adapter (priority for v1)
- :class:`DataSources` — bundle of injectable data sources adapters read
- :func:`load_fs_snapshot` — filesystem-based snapshot for the bundled
  non-EPUB adapters (spec 13 markdown/json/transcript/html)

The Orchestrator wires together a service with the live ``SceneManager`` /
``Characters`` / ``Setting`` / ``Continuity`` / ``ImageGen`` instances; tests
typically wire in narrow stubs from :mod:`grimoire.export.sources`. The
bundled plugin adapters (export-markdown, export-json, etc.) read directly
from disk via :func:`load_fs_snapshot` because they're loaded standalone
and have no access to the live data sources.
"""

from grimoire.export.data import (
    CharacterCard,
    EntityCard,
    FsCampaignSnapshot,
    ImageRecord,
    SceneRecord,
    load_fs_snapshot,
)
from grimoire.export.epub import EpubAdapter, list_style_presets
from grimoire.export.errors import (
    EmptyExportError,
    ExportError,
    UnknownAdapterError,
    ValidationFailed,
)
from grimoire.export.filters import (
    FilterContext,
    anonymize_label,
    apply_filters,
)
from grimoire.export.selection import filter_scenes, word_count
from grimoire.export.service import ExportAdapter, ExportService, ExportServiceConfig
from grimoire.export.snapshot import (
    CampaignSnapshot,
    FormattedPost,
    ScenePart,
    build_snapshot,
)
from grimoire.export.sources import (
    CharacterSource,
    ContinuitySource,
    DataSources,
    ImageSource,
    PCSource,
    SceneSource,
    SettingSource,
)

__all__ = [
    "CampaignSnapshot",
    "CharacterCard",
    "CharacterSource",
    "ContinuitySource",
    "DataSources",
    "EmptyExportError",
    "EntityCard",
    "EpubAdapter",
    "ExportAdapter",
    "ExportError",
    "ExportService",
    "ExportServiceConfig",
    "FilterContext",
    "FormattedPost",
    "FsCampaignSnapshot",
    "ImageRecord",
    "ImageSource",
    "PCSource",
    "ScenePart",
    "SceneRecord",
    "SceneSource",
    "SettingSource",
    "UnknownAdapterError",
    "ValidationFailed",
    "anonymize_label",
    "apply_filters",
    "build_snapshot",
    "filter_scenes",
    "list_style_presets",
    "load_fs_snapshot",
    "word_count",
]
