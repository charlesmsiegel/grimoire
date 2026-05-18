"""StateStoreConfig: defaults, YAML loading, validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.state_store import StateStoreConfig
from grimoire.state_store.config import (
    AutoBackupConfig,
    LibrarySectionConfig,
    RetentionConfig,
    SnapshotsConfig,
)


def test_defaults_for_derives_roots_from_data_root(tmp_path: Path) -> None:
    cfg = StateStoreConfig.defaults_for(data_root=tmp_path)
    assert cfg.library_root == tmp_path / "library"
    assert cfg.campaigns_root == tmp_path / "campaigns"
    assert cfg.database_path == tmp_path / "campaigns.sqlite"
    assert cfg.enable_wal is True
    assert cfg.library == LibrarySectionConfig()
    assert cfg.snapshots == SnapshotsConfig()
    assert cfg.auto_backup == AutoBackupConfig()
    assert cfg.retention == RetentionConfig()


def test_defaults_for_respects_explicit_database_path(tmp_path: Path) -> None:
    db = tmp_path / "elsewhere.sqlite"
    cfg = StateStoreConfig.defaults_for(data_root=tmp_path, database_path=db, enable_wal=False)
    assert cfg.database_path == db
    assert cfg.enable_wal is False


def test_from_yaml_missing_file_returns_defaults(tmp_path: Path) -> None:
    cfg = StateStoreConfig.from_yaml(tmp_path / "missing.yaml", data_root=tmp_path)
    assert cfg == StateStoreConfig.defaults_for(data_root=tmp_path)


def test_from_yaml_empty_file_returns_defaults(tmp_path: Path) -> None:
    path = tmp_path / "state_store.yaml"
    path.write_text("", encoding="utf-8")
    cfg = StateStoreConfig.from_yaml(path, data_root=tmp_path)
    assert cfg == StateStoreConfig.defaults_for(data_root=tmp_path)


def test_from_yaml_loads_full_block(tmp_path: Path) -> None:
    path = tmp_path / "state_store.yaml"
    path.write_text(
        """
state_store:
  library_root: ./shared/library
  campaigns_root: ./shared/campaigns
  enable_wal: false
  vector_extension: sqlite-vec
  library:
    watch: false
    scan_on_startup: false
    embed_on_index: true
    embedding_batch_size: 25
    embedding_provider: nomic-embed
  snapshots:
    enabled: false
    deduplicate_by_hash: true
  auto_backup:
    enabled: true
    interval_hours: 6
    retention_count: 4
    includes:
      - library
      - sqlite
    backup_dir: ./backups
  retention:
    embeddings_for_retired_facts: 30d
    delta_log: forever
    sweep_interval_seconds: 3600
""",
        encoding="utf-8",
    )
    cfg = StateStoreConfig.from_yaml(path, data_root=tmp_path)
    assert cfg.library_root == (tmp_path / "shared/library").resolve()
    assert cfg.campaigns_root == (tmp_path / "shared/campaigns").resolve()
    assert cfg.enable_wal is False
    assert cfg.library.watch is False
    assert cfg.library.embedding_batch_size == 25
    assert cfg.library.embedding_provider == "nomic-embed"
    assert cfg.snapshots.deduplicate_by_hash is True
    assert cfg.auto_backup.enabled is True
    assert cfg.auto_backup.interval_hours == 6
    assert cfg.auto_backup.retention_count == 4
    assert cfg.auto_backup.includes == ("library", "sqlite")
    assert cfg.auto_backup.backup_dir == (tmp_path / "backups").resolve()
    assert cfg.retention.embeddings_for_retired_facts_seconds == 30 * 24 * 60 * 60
    assert cfg.retention.delta_log_seconds is None
    assert cfg.retention.sweep_interval_seconds == 3600


def test_from_yaml_accepts_block_at_top_level(tmp_path: Path) -> None:
    """A bare mapping (no ``state_store:`` parent key) is also accepted."""
    path = tmp_path / "state_store.yaml"
    path.write_text(
        "library:\n  embedding_batch_size: 7\n",
        encoding="utf-8",
    )
    cfg = StateStoreConfig.from_yaml(path, data_root=tmp_path)
    assert cfg.library.embedding_batch_size == 7


def test_bootstrap_paths_win_over_defaults(tmp_path: Path) -> None:
    """Settings provide ``database_path``; YAML silence keeps it."""
    path = tmp_path / "state_store.yaml"
    path.write_text("library:\n  watch: false\n", encoding="utf-8")
    db = tmp_path / "explicit.sqlite"
    cfg = StateStoreConfig.from_yaml(path, data_root=tmp_path, database_path=db)
    assert cfg.database_path == db
    assert cfg.library.watch is False


def test_yaml_database_path_overrides_when_bootstrap_absent(tmp_path: Path) -> None:
    path = tmp_path / "state_store.yaml"
    path.write_text("database_path: ./db/foo.sqlite\n", encoding="utf-8")
    cfg = StateStoreConfig.from_yaml(path, data_root=tmp_path)
    assert cfg.database_path == (tmp_path / "db/foo.sqlite").resolve()


def test_rejects_bad_vector_extension(tmp_path: Path) -> None:
    path = tmp_path / "state_store.yaml"
    path.write_text("vector_extension: faiss\n", encoding="utf-8")
    with pytest.raises(ValueError, match="vector_extension"):
        StateStoreConfig.from_yaml(path, data_root=tmp_path)


def test_rejects_unknown_backup_component(tmp_path: Path) -> None:
    path = tmp_path / "state_store.yaml"
    path.write_text(
        "auto_backup:\n  includes:\n    - library\n    - somethingelse\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=r"auto_backup\.includes"):
        StateStoreConfig.from_yaml(path, data_root=tmp_path)


def test_rejects_non_positive_batch(tmp_path: Path) -> None:
    path = tmp_path / "state_store.yaml"
    path.write_text("library:\n  embedding_batch_size: 0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="embedding_batch_size"):
        StateStoreConfig.from_yaml(path, data_root=tmp_path)


def test_rejects_unparseable_duration(tmp_path: Path) -> None:
    path = tmp_path / "state_store.yaml"
    path.write_text(
        "retention:\n  embeddings_for_retired_facts: '12 fortnights'\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="embeddings_for_retired_facts"):
        StateStoreConfig.from_yaml(path, data_root=tmp_path)


@pytest.mark.parametrize(
    ("value", "seconds"),
    [
        ("forever", None),
        ("never", None),
        ("90d", 90 * 24 * 60 * 60),
        ("12h", 12 * 60 * 60),
        ("30m", 30 * 60),
        ("2w", 14 * 24 * 60 * 60),
        (45, 45 * 24 * 60 * 60),
    ],
)
def test_duration_forms(tmp_path: Path, value: object, seconds: int | None) -> None:
    path = tmp_path / "state_store.yaml"
    path.write_text(
        f"retention:\n  embeddings_for_retired_facts: {value!r}\n",
        encoding="utf-8",
    )
    cfg = StateStoreConfig.from_yaml(path, data_root=tmp_path)
    assert cfg.retention.embeddings_for_retired_facts_seconds == seconds


def test_absolute_path_in_yaml_preserved(tmp_path: Path) -> None:
    other = tmp_path / "external"
    other.mkdir()
    path = tmp_path / "state_store.yaml"
    path.write_text(f"library_root: {other}\n", encoding="utf-8")
    cfg = StateStoreConfig.from_yaml(path, data_root=tmp_path)
    assert cfg.library_root == other
