"""Top-level Library YAML config (spec 18 §Configuration).

Mirrors the WorldConfig shape: nested dataclasses with sensible defaults, an
optional ``from_yaml`` loader. Threaded into :class:`LibraryService` and
:class:`FileWatcher` at construction time.

Spec items 14-17 in ``2026-05-18-library-remaining-design.md`` (the
``files.*_filename_pattern`` / ``files.encoding`` knobs) are intentionally
omitted: they are marked v2/deferred in the design.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from grimoire.files import load_yaml

_PINNING_DEFAULTS: frozenset[str] = frozenset({"pinned", "track_latest"})


@dataclass(frozen=True, slots=True)
class LibraryIndexingConfig:
    embed_on_index: bool = False
    embedding_provider: str | None = None


@dataclass(frozen=True, slots=True)
class LibraryVersionPinningConfig:
    # 'pinned' or 'track_latest'. Controls the default ``track_latest`` value
    # used when set_composition fills in a missing flag on a new WorldRef.
    default: str = "pinned"
    snapshot_on_bind: bool = True


@dataclass(frozen=True, slots=True)
class LibraryPromotionConfig:
    confirm_required: bool = False


@dataclass(frozen=True, slots=True)
class LibraryReclassificationConfig:
    # Override for the audit-log path. ``None`` means
    # ``<data_root>/library/imports/reclassifications.jsonl``.
    audit_log: Path | None = None
    # Heuristic suggestions below this confidence default the dropdown to "lore".
    suggestion_threshold: float = 0.6
    # Records older than this can be pruned; the UI also greys out undo
    # links past this window.
    undo_window_days: int = 30


@dataclass(frozen=True, slots=True)
class LibraryConfig:
    # ``None`` means "use <data_root>/library"; an absolute path overrides.
    root: Path | None = None
    watch: bool = True
    scan_on_startup: bool = True
    indexing: LibraryIndexingConfig = field(default_factory=LibraryIndexingConfig)
    version_pinning: LibraryVersionPinningConfig = field(
        default_factory=LibraryVersionPinningConfig
    )
    promotion: LibraryPromotionConfig = field(default_factory=LibraryPromotionConfig)
    reclassification: LibraryReclassificationConfig = field(
        default_factory=LibraryReclassificationConfig
    )

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
        return cls._from_mapping(raw)

    @classmethod
    def _from_mapping(cls, raw: dict[str, Any]) -> LibraryConfig:
        idx = raw.get("indexing") or {}
        pin = raw.get("version_pinning") or {}
        prom = raw.get("promotion") or {}
        reclass = raw.get("reclassification") or {}
        default = str(pin.get("default") or "pinned")
        if default not in _PINNING_DEFAULTS:
            raise ValueError(
                f"version_pinning.default must be one of {sorted(_PINNING_DEFAULTS)!r}, "
                f"got {default!r}"
            )
        root_raw = raw.get("root")
        root = Path(root_raw).expanduser() if root_raw else None
        audit_log_raw = reclass.get("audit_log")
        audit_log = Path(audit_log_raw).expanduser() if audit_log_raw else None
        return cls(
            root=root,
            watch=bool(raw.get("watch", True)),
            scan_on_startup=bool(raw.get("scan_on_startup", True)),
            indexing=LibraryIndexingConfig(
                embed_on_index=bool(idx.get("embed_on_index", False)),
                embedding_provider=(
                    str(idx["embedding_provider"]) if idx.get("embedding_provider") else None
                ),
            ),
            version_pinning=LibraryVersionPinningConfig(
                default=default,
                snapshot_on_bind=bool(pin.get("snapshot_on_bind", True)),
            ),
            promotion=LibraryPromotionConfig(
                confirm_required=bool(prom.get("confirm_required", False)),
            ),
            reclassification=LibraryReclassificationConfig(
                audit_log=audit_log,
                suggestion_threshold=float(reclass.get("suggestion_threshold", 0.6)),
                undo_window_days=int(reclass.get("undo_window_days", 30)),
            ),
        )


__all__ = [
    "LibraryConfig",
    "LibraryIndexingConfig",
    "LibraryPromotionConfig",
    "LibraryReclassificationConfig",
    "LibraryVersionPinningConfig",
]
