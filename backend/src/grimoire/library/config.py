"""Top-level Library YAML config (spec 18 §Configuration).

Nested pydantic models with sensible defaults and an optional ``from_yaml``
loader. Threaded into :class:`LibraryService` and :class:`FileWatcher` at
construction time.

Spec items 14-17 in ``2026-05-18-library-remaining-design.md`` (the
``files.*_filename_pattern`` / ``files.encoding`` knobs) are intentionally
omitted: they are marked v2/deferred in the design.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from grimoire.files import load_yaml


def _expand_path(value: object) -> Path | None:
    """Empty/missing -> None; otherwise an expanded path (``~`` resolved)."""
    if not value:
        return None
    return Path(str(value)).expanduser()


class LibraryIndexingConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    embed_on_index: bool = False
    embedding_provider: str | None = None


class LibraryVersionPinningConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    # Controls the default ``track_latest`` value used when set_composition
    # fills in a missing flag on a new WorldRef.
    default: Literal["pinned", "track_latest"] = "pinned"
    snapshot_on_bind: bool = True


class LibraryPromotionConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    confirm_required: bool = False


class LibraryReclassificationConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    # Override for the audit-log path. ``None`` means
    # ``<data_root>/library/imports/reclassifications.jsonl``.
    audit_log: Path | None = None
    # Heuristic suggestions below this confidence default the dropdown to "lore".
    suggestion_threshold: float = 0.6
    # Records older than this can be pruned; the UI also greys out undo
    # links past this window.
    undo_window_days: int = 30

    @field_validator("audit_log", mode="before")
    @classmethod
    def _expand_audit_log(cls, value: object) -> Path | None:
        return _expand_path(value)


class LibraryConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    # ``None`` means "use <data_root>/library"; an absolute path overrides.
    root: Path | None = None
    watch: bool = True
    scan_on_startup: bool = True
    indexing: LibraryIndexingConfig = LibraryIndexingConfig()
    version_pinning: LibraryVersionPinningConfig = LibraryVersionPinningConfig()
    promotion: LibraryPromotionConfig = LibraryPromotionConfig()
    reclassification: LibraryReclassificationConfig = LibraryReclassificationConfig()

    @field_validator("root", mode="before")
    @classmethod
    def _expand_root(cls, value: object) -> Path | None:
        return _expand_path(value)

    @property
    def default_track_latest(self) -> bool:
        return self.version_pinning.default == "track_latest"

    @classmethod
    def from_yaml(cls, path: Path) -> LibraryConfig:
        if not path.exists():
            return cls()
        raw = load_yaml(path) or {}
        if not isinstance(raw, dict):
            return cls()
        # Accept either a top-level mapping or a {"library": {...}} envelope.
        if "library" in raw and isinstance(raw["library"], dict):
            raw = raw["library"]
        return cls.model_validate(raw)


__all__ = [
    "LibraryConfig",
    "LibraryIndexingConfig",
    "LibraryPromotionConfig",
    "LibraryReclassificationConfig",
    "LibraryVersionPinningConfig",
]
