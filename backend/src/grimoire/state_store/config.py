"""State Store configuration (`state_store:` YAML block).

Spec ``docs/superpowers/specs/2026-05-12-state-store-design.md`` plus the
remaining-work spec ``2026-05-18-state-store-remaining-design.md`` §2 define
a dedicated config namespace for the State Store. The base
:class:`grimoire.config.Settings` only exposes the bootstrap-level knobs
(``data_root`` / ``database_path`` / ``enable_wal`` / ``db_pool_size``) since
those have to be available before any YAML file is read. Everything else —
library indexing knobs, snapshot policy, auto-backup, retention — lives here
and is loaded from ``{data_root}/config/state_store.yaml``.

The bootstrap settings still win when they're set explicitly, so an env-var
override (``GRIMOIRE_DATA_ROOT=…``) keeps working even when the YAML names a
different ``library_root``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from grimoire.files import parse_yaml

_DEFAULT_INCLUDES: tuple[str, ...] = ("library", "campaigns", "sqlite")
_VALID_INCLUDES: frozenset[str] = frozenset(_DEFAULT_INCLUDES)
_VECTOR_EXTENSIONS: frozenset[str] = frozenset({"sqlite-vec"})


# Accept "90d", "12h", "30m", "forever". Bare ints are treated as days for
# back-compat with the way humans tend to scribble retention windows.
_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhdw])\s*$", re.IGNORECASE)
_UNIT_SECONDS: dict[str, int] = {
    "s": 1,
    "m": 60,
    "h": 60 * 60,
    "d": 24 * 60 * 60,
    "w": 7 * 24 * 60 * 60,
}


def _parse_duration(value: Any, *, field_name: str) -> int | None:
    """Parse a duration spec to seconds. ``"forever"`` / ``None`` → ``None``."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field_name}: expected duration string, got bool")
    if isinstance(value, int):
        return value * _UNIT_SECONDS["d"]
    if isinstance(value, str):
        s = value.strip().lower()
        if s in {"forever", "never", "infinite", ""}:
            return None
        m = _DURATION_RE.match(s)
        if not m:
            raise ValueError(
                f"{field_name}: cannot parse {value!r}; expected forms like "
                "'90d', '12h', '30m', or 'forever'"
            )
        magnitude = int(m.group(1))
        unit = m.group(2).lower()
        return magnitude * _UNIT_SECONDS[unit]
    raise ValueError(f"{field_name}: unsupported duration type {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class LibrarySectionConfig:
    watch: bool = True
    scan_on_startup: bool = True
    embed_on_index: bool = True
    embedding_batch_size: int = 50
    embedding_provider: str | None = None


@dataclass(frozen=True, slots=True)
class SnapshotsConfig:
    enabled: bool = True
    deduplicate_by_hash: bool = False


@dataclass(frozen=True, slots=True)
class AutoBackupConfig:
    enabled: bool = False
    interval_hours: int = 24
    retention_count: int = 14
    includes: tuple[str, ...] = _DEFAULT_INCLUDES
    backup_dir: Path | None = None  # defaults to ``{data_root}/backups``


@dataclass(frozen=True, slots=True)
class RetentionConfig:
    # None means "forever". Seconds-based so consumers don't re-parse.
    embeddings_for_retired_facts_seconds: int | None = 90 * 24 * 60 * 60
    delta_log_seconds: int | None = None  # forever
    # How often the retention sweep runs while the process is up.
    sweep_interval_seconds: int = 6 * 60 * 60


@dataclass(frozen=True, slots=True)
class StateStoreConfig:
    """Resolved State Store configuration.

    Paths are absolute. The base :class:`grimoire.config.Settings` provides
    ``data_root`` / ``database_path`` / ``enable_wal``; this class fills in
    the rest and computes derived defaults (``library_root`` etc.) from
    ``data_root`` when the YAML doesn't set them.
    """

    library_root: Path
    campaigns_root: Path
    database_path: Path
    enable_wal: bool = True
    vector_extension: str = "sqlite-vec"
    library: LibrarySectionConfig = field(default_factory=LibrarySectionConfig)
    snapshots: SnapshotsConfig = field(default_factory=SnapshotsConfig)
    auto_backup: AutoBackupConfig = field(default_factory=AutoBackupConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)

    @classmethod
    def defaults_for(
        cls,
        *,
        data_root: Path,
        database_path: Path | None = None,
        enable_wal: bool = True,
    ) -> StateStoreConfig:
        """Construct a config with library/campaigns rooted at ``data_root``."""
        data_root = Path(data_root)
        return cls(
            library_root=data_root / "library",
            campaigns_root=data_root / "campaigns",
            database_path=Path(database_path) if database_path else data_root / "campaigns.sqlite",
            enable_wal=enable_wal,
        )

    @classmethod
    def from_yaml(
        cls,
        path: Path,
        *,
        data_root: Path,
        database_path: Path | None = None,
        enable_wal: bool | None = None,
    ) -> StateStoreConfig:
        """Load from ``path`` overlaid on defaults derived from ``data_root``.

        Bootstrap values (``data_root`` / ``database_path`` / ``enable_wal``)
        passed in from :class:`grimoire.config.Settings` win over anything
        the YAML sets, so env-var-driven deployments keep working. Pass
        ``None`` to opt a field out of bootstrap-wins (the YAML value, or
        the default, will be used instead). Anything the YAML adds beyond
        the bootstrap fields is layered on top of the dataclass defaults.
        """
        bootstrap_locked: set[str] = set()
        if database_path is not None:
            bootstrap_locked.add("database_path")
        if enable_wal is not None:
            bootstrap_locked.add("enable_wal")
        base = cls.defaults_for(
            data_root=data_root,
            database_path=database_path,
            enable_wal=enable_wal if enable_wal is not None else True,
        )
        if not path.exists():
            return base
        raw_text = path.read_text(encoding="utf-8")
        raw = parse_yaml(raw_text) if raw_text.strip() else None
        if not isinstance(raw, dict):
            return base
        block = raw.get("state_store") if isinstance(raw.get("state_store"), dict) else raw
        return cls._merge(
            base, block, data_root=Path(data_root), bootstrap_locked=frozenset(bootstrap_locked)
        )

    @classmethod
    def _merge(
        cls,
        base: StateStoreConfig,
        raw: dict[str, Any],
        *,
        data_root: Path,
        bootstrap_locked: frozenset[str] = frozenset(),
    ) -> StateStoreConfig:
        # Bootstrap-supplied fields (per ``bootstrap_locked``) win over YAML —
        # the env-var-driven Settings layer should not be silently shadowed by
        # an on-disk config file. Fields the caller didn't pin are free to be
        # overridden by YAML.
        library_root = base.library_root
        campaigns_root = base.campaigns_root
        database_path = base.database_path
        if raw.get("library_root") and "library_root" not in bootstrap_locked:
            library_root = _resolve_path(raw["library_root"], data_root)
        if raw.get("campaigns_root") and "campaigns_root" not in bootstrap_locked:
            campaigns_root = _resolve_path(raw["campaigns_root"], data_root)
        if raw.get("database_path") and "database_path" not in bootstrap_locked:
            database_path = _resolve_path(raw["database_path"], data_root)

        if "enable_wal" in bootstrap_locked:
            enable_wal = base.enable_wal
        else:
            enable_wal = bool(raw.get("enable_wal", base.enable_wal))
        vector_extension = str(raw.get("vector_extension") or base.vector_extension)
        if vector_extension not in _VECTOR_EXTENSIONS:
            raise ValueError(
                f"state_store.vector_extension must be one of "
                f"{sorted(_VECTOR_EXTENSIONS)!r}, got {vector_extension!r}"
            )

        library = _library_from(raw.get("library"), base.library)
        snapshots = _snapshots_from(raw.get("snapshots"), base.snapshots)
        auto_backup = _backup_from(raw.get("auto_backup"), base.auto_backup, data_root=data_root)
        retention = _retention_from(raw.get("retention"), base.retention)

        return replace(
            base,
            library_root=library_root,
            campaigns_root=campaigns_root,
            database_path=database_path,
            enable_wal=enable_wal,
            vector_extension=vector_extension,
            library=library,
            snapshots=snapshots,
            auto_backup=auto_backup,
            retention=retention,
        )


def _resolve_path(value: Any, data_root: Path) -> Path:
    p = Path(str(value)).expanduser()
    if not p.is_absolute():
        p = (data_root / p).resolve()
    return p


def _library_from(raw: Any, base: LibrarySectionConfig) -> LibrarySectionConfig:
    if not isinstance(raw, dict):
        return base
    batch = raw.get("embedding_batch_size", base.embedding_batch_size)
    try:
        batch_int = int(batch)
    except (TypeError, ValueError) as e:
        raise ValueError(
            f"state_store.library.embedding_batch_size must be an int, got {batch!r}"
        ) from e
    if batch_int <= 0:
        raise ValueError(
            f"state_store.library.embedding_batch_size must be positive, got {batch_int}"
        )
    provider = raw.get("embedding_provider", base.embedding_provider)
    return LibrarySectionConfig(
        watch=bool(raw.get("watch", base.watch)),
        scan_on_startup=bool(raw.get("scan_on_startup", base.scan_on_startup)),
        embed_on_index=bool(raw.get("embed_on_index", base.embed_on_index)),
        embedding_batch_size=batch_int,
        embedding_provider=str(provider) if provider else None,
    )


def _snapshots_from(raw: Any, base: SnapshotsConfig) -> SnapshotsConfig:
    if not isinstance(raw, dict):
        return base
    return SnapshotsConfig(
        enabled=bool(raw.get("enabled", base.enabled)),
        deduplicate_by_hash=bool(raw.get("deduplicate_by_hash", base.deduplicate_by_hash)),
    )


def _backup_from(raw: Any, base: AutoBackupConfig, *, data_root: Path) -> AutoBackupConfig:
    if not isinstance(raw, dict):
        return base
    includes_raw = raw.get("includes", base.includes)
    if isinstance(includes_raw, str):
        includes_raw = [includes_raw]
    if not isinstance(includes_raw, (list, tuple)):
        raise ValueError(
            f"state_store.auto_backup.includes must be a list, got {type(includes_raw).__name__}"
        )
    includes: list[str] = []
    for item in includes_raw:
        s = str(item).strip().lower()
        if s not in _VALID_INCLUDES:
            raise ValueError(
                f"state_store.auto_backup.includes: unknown component {item!r}; "
                f"expected subset of {sorted(_VALID_INCLUDES)!r}"
            )
        if s not in includes:
            includes.append(s)
    interval = int(raw.get("interval_hours", base.interval_hours))
    if interval <= 0:
        raise ValueError(f"state_store.auto_backup.interval_hours must be positive, got {interval}")
    retention_count = int(raw.get("retention_count", base.retention_count))
    if retention_count <= 0:
        raise ValueError(
            f"state_store.auto_backup.retention_count must be positive, got {retention_count}"
        )
    backup_dir_raw = raw.get("backup_dir")
    backup_dir = _resolve_path(backup_dir_raw, data_root) if backup_dir_raw else base.backup_dir
    return AutoBackupConfig(
        enabled=bool(raw.get("enabled", base.enabled)),
        interval_hours=interval,
        retention_count=retention_count,
        includes=tuple(includes) if includes else base.includes,
        backup_dir=backup_dir,
    )


def _retention_from(raw: Any, base: RetentionConfig) -> RetentionConfig:
    if not isinstance(raw, dict):
        return base
    embeddings = (
        _parse_duration(
            raw.get("embeddings_for_retired_facts", base.embeddings_for_retired_facts_seconds),
            field_name="state_store.retention.embeddings_for_retired_facts",
        )
        if "embeddings_for_retired_facts" in raw
        else base.embeddings_for_retired_facts_seconds
    )
    delta_log = (
        _parse_duration(
            raw.get("delta_log", base.delta_log_seconds),
            field_name="state_store.retention.delta_log",
        )
        if "delta_log" in raw
        else base.delta_log_seconds
    )
    sweep_interval = int(raw.get("sweep_interval_seconds", base.sweep_interval_seconds))
    if sweep_interval <= 0:
        raise ValueError(
            f"state_store.retention.sweep_interval_seconds must be positive, got {sweep_interval}"
        )
    return RetentionConfig(
        embeddings_for_retired_facts_seconds=embeddings,
        delta_log_seconds=delta_log,
        sweep_interval_seconds=sweep_interval,
    )


__all__ = [
    "AutoBackupConfig",
    "LibrarySectionConfig",
    "RetentionConfig",
    "SnapshotsConfig",
    "StateStoreConfig",
]
