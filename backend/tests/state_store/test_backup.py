"""Auto-backup scheduler: run_backup, prune_old_backups, BackupScheduler."""

from __future__ import annotations

import os
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from grimoire.event_bus import EventBus
from grimoire.state_store.backup import BackupScheduler, prune_old_backups, run_backup
from grimoire.state_store.config import AutoBackupConfig

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def data_root(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    (root / "library").mkdir()
    (root / "library" / "worlds").mkdir()
    (root / "library" / "worlds" / "test.md").write_text("# Test")
    (root / "campaigns").mkdir()
    (root / "campaigns" / "campaign1.yaml").write_text("name: c1")
    return root


@pytest.fixture()
def db_path(data_root: Path) -> Path:
    p = data_root / "campaigns.sqlite"
    p.write_bytes(b"SQLite format 3\x00")
    return p


@pytest.fixture()
def backup_dir(data_root: Path) -> Path:
    d = data_root / "backups"
    d.mkdir()
    return d


@pytest.fixture()
def full_config(backup_dir: Path) -> AutoBackupConfig:
    return AutoBackupConfig(
        enabled=True,
        interval_hours=24,
        retention_count=3,
        includes=("library", "campaigns", "sqlite"),
        backup_dir=backup_dir,
    )


# ---------------------------------------------------------------------------
# run_backup
# ---------------------------------------------------------------------------


def test_run_backup_creates_zip(
    data_root: Path, db_path: Path, full_config: AutoBackupConfig
) -> None:
    now = datetime(2026, 5, 18, 12, 0, 0, tzinfo=UTC)
    result = run_backup(data_root=data_root, database_path=db_path, config=full_config, now=now)

    assert result is not None
    assert result.name == "grimoire-backup-20260518T120000Z.zip"
    assert result.exists()


def test_run_backup_includes_library_entries(
    data_root: Path, db_path: Path, full_config: AutoBackupConfig
) -> None:
    result = run_backup(data_root=data_root, database_path=db_path, config=full_config)
    assert result is not None
    with zipfile.ZipFile(result) as zf:
        names = zf.namelist()
    assert any(n.startswith("library/") for n in names)
    assert any(n.startswith("campaigns/") for n in names)
    assert any("campaigns.sqlite" in n for n in names)


def test_run_backup_only_configured_components(
    data_root: Path, db_path: Path, backup_dir: Path
) -> None:
    config = AutoBackupConfig(
        enabled=True,
        includes=("library",),
        backup_dir=backup_dir,
    )
    result = run_backup(data_root=data_root, database_path=db_path, config=config)
    assert result is not None
    with zipfile.ZipFile(result) as zf:
        names = zf.namelist()
    assert any(n.startswith("library/") for n in names)
    assert not any(n.startswith("campaigns/") for n in names)
    assert not any("sqlite" in n for n in names)


def test_run_backup_skips_missing_library(data_root: Path, db_path: Path, backup_dir: Path) -> None:
    import shutil

    shutil.rmtree(data_root / "library")
    config = AutoBackupConfig(enabled=True, includes=("library", "sqlite"), backup_dir=backup_dir)
    result = run_backup(data_root=data_root, database_path=db_path, config=config)
    # sqlite is still present, so result is not None
    assert result is not None
    with zipfile.ZipFile(result) as zf:
        names = zf.namelist()
    assert not any(n.startswith("library/") for n in names)
    assert any("sqlite" in n for n in names)


def test_run_backup_returns_none_when_no_components_included(
    data_root: Path, db_path: Path, backup_dir: Path
) -> None:
    import shutil

    shutil.rmtree(data_root / "library")
    config = AutoBackupConfig(enabled=True, includes=("library",), backup_dir=backup_dir)
    result = run_backup(data_root=data_root, database_path=db_path, config=config)
    assert result is None
    # No leftover .tmp file
    assert not list(backup_dir.glob("*.tmp"))


def test_run_backup_no_tmp_leftover_on_success(
    data_root: Path, db_path: Path, full_config: AutoBackupConfig, backup_dir: Path
) -> None:
    run_backup(data_root=data_root, database_path=db_path, config=full_config)
    assert not list(backup_dir.glob("*.tmp"))


def test_run_backup_includes_wal_shm_when_present(
    data_root: Path, db_path: Path, backup_dir: Path
) -> None:
    wal = db_path.parent / (db_path.name + "-wal")
    shm = db_path.parent / (db_path.name + "-shm")
    wal.write_bytes(b"wal data")
    shm.write_bytes(b"shm data")
    config = AutoBackupConfig(enabled=True, includes=("sqlite",), backup_dir=backup_dir)
    result = run_backup(data_root=data_root, database_path=db_path, config=config)
    assert result is not None
    with zipfile.ZipFile(result) as zf:
        names = zf.namelist()
    assert any("-wal" in n for n in names)
    assert any("-shm" in n for n in names)


def test_run_backup_creates_backup_dir_if_missing(data_root: Path, db_path: Path) -> None:
    new_backup_dir = data_root / "nested" / "backups"
    config = AutoBackupConfig(enabled=True, includes=("sqlite",), backup_dir=new_backup_dir)
    result = run_backup(data_root=data_root, database_path=db_path, config=config)
    assert result is not None
    assert new_backup_dir.is_dir()


# ---------------------------------------------------------------------------
# prune_old_backups
# ---------------------------------------------------------------------------


def test_prune_keeps_newest_n(backup_dir: Path) -> None:
    now = time.time()
    archives = []
    for i in range(5):
        p = backup_dir / f"grimoire-backup-2026010{i}T000000Z.zip"
        p.write_bytes(b"x")
        os.utime(p, (now - (5 - i) * 3600, now - (5 - i) * 3600))
        archives.append(p)

    deleted = prune_old_backups(backup_dir, retention_count=3)
    assert len(deleted) == 2
    remaining = list(backup_dir.glob("grimoire-backup-*.zip"))
    assert len(remaining) == 3
    # The two oldest were deleted
    for p in deleted:
        assert not p.exists()


def test_prune_empty_dir_returns_empty(backup_dir: Path) -> None:
    assert prune_old_backups(backup_dir, retention_count=5) == []


def test_prune_missing_dir_returns_empty(tmp_path: Path) -> None:
    assert prune_old_backups(tmp_path / "nonexistent", retention_count=5) == []


def test_prune_within_retention_deletes_nothing(backup_dir: Path) -> None:
    now = time.time()
    for i in range(3):
        p = backup_dir / f"grimoire-backup-2026010{i}T000000Z.zip"
        p.write_bytes(b"x")
        os.utime(p, (now - i * 3600, now - i * 3600))
    deleted = prune_old_backups(backup_dir, retention_count=5)
    assert deleted == []


# ---------------------------------------------------------------------------
# BackupScheduler
# ---------------------------------------------------------------------------


@pytest.fixture()
def bus() -> EventBus:
    return EventBus()


async def test_scheduler_run_once_emits_backup_complete(
    data_root: Path, db_path: Path, full_config: AutoBackupConfig, bus: EventBus
) -> None:
    received: list = []
    bus.subscribe("backup_complete", lambda e: received.append(e))

    now = datetime(2026, 5, 18, 10, 0, 0, tzinfo=UTC)
    scheduler = BackupScheduler(
        data_root=data_root,
        database_path=db_path,
        config=full_config,
        bus=bus,
        clock=lambda: now,
    )
    await scheduler._run_once()

    assert len(received) == 1
    payload = received[0].payload
    assert "path" in payload
    assert payload["size_bytes"] > 0
    assert set(payload["components"]) == {"library", "campaigns", "sqlite"}
    assert isinstance(payload["pruned"], list)


async def test_scheduler_disabled_start_is_noop(
    data_root: Path, db_path: Path, backup_dir: Path, bus: EventBus
) -> None:
    config = AutoBackupConfig(enabled=False, backup_dir=backup_dir)
    scheduler = BackupScheduler(
        data_root=data_root,
        database_path=db_path,
        config=config,
        bus=bus,
    )
    scheduler.start()
    assert scheduler._task is None


async def test_scheduler_start_stop(
    data_root: Path, db_path: Path, full_config: AutoBackupConfig, bus: EventBus
) -> None:
    scheduler = BackupScheduler(
        data_root=data_root,
        database_path=db_path,
        config=full_config,
        bus=bus,
        clock=lambda: datetime.now(UTC),
    )
    scheduler.start()
    assert scheduler._task is not None
    scheduler.stop()
    assert scheduler._task is None


async def test_scheduler_run_once_prunes(
    data_root: Path, db_path: Path, backup_dir: Path, bus: EventBus
) -> None:
    now_ts = time.time()
    # Pre-create 4 archives so pruning to 3 deletes 1
    for i in range(4):
        p = backup_dir / f"grimoire-backup-202601{i:02d}T000000Z.zip"
        p.write_bytes(b"old")
        os.utime(p, (now_ts - (4 - i) * 3600, now_ts - (4 - i) * 3600))

    config = AutoBackupConfig(
        enabled=True,
        retention_count=3,
        includes=("sqlite",),
        backup_dir=backup_dir,
    )
    received: list = []
    bus.subscribe("backup_complete", lambda e: received.append(e))

    scheduler = BackupScheduler(
        data_root=data_root,
        database_path=db_path,
        config=config,
        bus=bus,
        clock=lambda: datetime.now(UTC),
    )
    await scheduler._run_once()

    assert len(received) == 1
    # 4 old + 1 new = 5 total, keep 3, prune 2
    assert len(received[0].payload["pruned"]) == 2
