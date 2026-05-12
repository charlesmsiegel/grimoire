"""Export module (spec 13).

Public surface:

- :class:`ExportService` — registers adapters, runs exports, tracks history
- :class:`EpubAdapter` — EPUB 3 adapter (priority for v1)
- :class:`DataSources` — bundle of injectable data sources adapters read

The Orchestrator wires together a service with the live ``SceneManager`` /
``Characters`` / ``Setting`` / ``Continuity`` / ``ImageGen`` instances; tests
typically wire in narrow stubs from :mod:`grimoire.export.sources`.
"""

from grimoire.export.epub import EpubAdapter, list_style_presets
from grimoire.export.errors import (
    EmptyExportError,
    ExportError,
    UnknownAdapterError,
    ValidationFailed,
)
from grimoire.export.filters import FilterContext, apply_filters
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
    "CharacterSource",
    "ContinuitySource",
    "DataSources",
    "EmptyExportError",
    "EpubAdapter",
    "ExportAdapter",
    "ExportError",
    "ExportService",
    "ExportServiceConfig",
    "FilterContext",
    "FormattedPost",
    "ImageSource",
    "PCSource",
    "ScenePart",
    "SceneSource",
    "SettingSource",
    "UnknownAdapterError",
    "ValidationFailed",
    "apply_filters",
    "build_snapshot",
    "list_style_presets",
]
