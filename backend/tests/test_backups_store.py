"""Zipped snapshots of the whole store, and the retention sweep (#32)."""

from __future__ import annotations

import os
import threading
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

from grimoire.store import backups, config, locks


def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return tmp_path


def small_store(root):
    """A store with one world record, one campaign record and a config."""
    (root / "worlds" / "realm").mkdir(parents=True)
    (root / "worlds" / "realm" / "world.md").write_text("# Realm\n", encoding="utf-8")
    (root / "campaigns" / "saltmarch").mkdir(parents=True)
    (root / "campaigns" / "saltmarch" / "campaign.md").write_text("# Saltmarch\n",
                                                                 encoding="utf-8")
    (root / "config.md").write_text("---\ntheme: system\n---\n", encoding="utf-8")


AT = datetime(2026, 8, 14, 21, 0, 0, tzinfo=timezone.utc)


def names_in(path):
    with zipfile.ZipFile(path) as z:
        return set(z.namelist())


# ---- what an archive holds -------------------------------------------------

def test_the_archive_holds_the_store_relative_to_its_root(monkeypatch, tmp_path):
    root = home(monkeypatch, tmp_path)
    small_store(root)

    archive = backups.create_backup(when=AT)

    assert archive.name == "grimoire-20260814T210000Z.zip"
    assert archive.parent == root / "backups"
    assert names_in(archive) == {
        "worlds/realm/world.md", "campaigns/saltmarch/campaign.md", "config.md",
    }


def test_the_archive_excludes_the_backups_dir_and_the_derived_cache(monkeypatch, tmp_path):
    """Its own output dir, or archives swallow archives; `.cache/` because it is
    rebuildable and can outweigh the library it was derived from."""
    root = home(monkeypatch, tmp_path)
    small_store(root)
    (root / ".cache" / "thumbs").mkdir(parents=True)
    (root / ".cache" / "thumbs" / "a.webp").write_bytes(b"derived")

    first = backups.create_backup(when=AT)
    second = backups.create_backup(when=AT + timedelta(hours=1))

    assert not any(n.startswith(".cache/") for n in names_in(second))
    assert not any(n.startswith("backups/") for n in names_in(second))
    assert first.name not in names_in(second)


def test_the_archive_round_trips_the_bytes_it_stored(monkeypatch, tmp_path):
    root = home(monkeypatch, tmp_path)
    small_store(root)
    (root / "worlds" / "realm" / "image.bin").write_bytes(bytes(range(256)) * 40)

    with zipfile.ZipFile(backups.create_backup(when=AT)) as z:
        assert z.read("worlds/realm/image.bin") == bytes(range(256)) * 40
        assert z.read("config.md").decode("utf-8") == "---\ntheme: system\n---\n"


def test_an_empty_store_still_produces_a_readable_archive(monkeypatch, tmp_path):
    """Only files are members, so the empty `worlds/`/`campaigns/` a fresh store
    is materialized with are not in the archive — `ensure_home()` recreates them
    the first time the restored store is read."""
    home(monkeypatch, tmp_path)

    archive = backups.create_backup(when=AT)

    assert names_in(archive) == {"config.md"}   # written by the first config read


def test_two_archives_in_the_same_second_do_not_collide(monkeypatch, tmp_path):
    root = home(monkeypatch, tmp_path)
    small_store(root)

    first = backups.create_backup(when=AT)
    second = backups.create_backup(when=AT)

    assert first != second
    assert first.exists() and second.exists()
    # Newest first, and that ordering is NOT the filename's: `-2` sorts before
    # `.zip` bytewise, so plain name sorting inverts every same-second pair.
    assert [b["name"] for b in backups.list_backups()] == [second.name, first.name]
    assert backups.sweep(keep=1) == [first.name]
    assert second.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
def test_a_directory_symlink_does_not_send_the_walk_around_in_circles(
        monkeypatch, tmp_path):
    """`rglob` follows directory symlinks, so a link back to the root walks
    forever. The archive is finite, and the link itself is simply not stored."""
    root = home(monkeypatch, tmp_path)
    small_store(root)
    (root / "worlds" / "loop").symlink_to(root, target_is_directory=True)

    done: list = []
    worker = threading.Thread(target=lambda: done.append(backups.create_backup(when=AT)),
                              daemon=True)
    worker.start()
    worker.join(30)

    assert not worker.is_alive(), "the walk descended into the symlink"
    assert not any(n.startswith("worlds/loop/") for n in names_in(done[0]))


def test_a_file_that_cannot_be_read_fails_the_backup_and_publishes_nothing(
        monkeypatch, tmp_path):
    """A backup that silently omits what it could not read is worse than no
    backup: it claims a completeness it does not have. So the error propagates
    — and the half-written archive is never published under a name a listing
    would offer as a restore point."""
    root = home(monkeypatch, tmp_path)
    small_store(root)
    doomed = root / "worlds" / "realm" / "world.md"
    real_store = backups._store_file

    def refuse(z, path, arcname):
        if path == doomed:
            raise PermissionError("nope")
        return real_store(z, path, arcname)

    monkeypatch.setattr(backups, "_store_file", refuse)
    with pytest.raises(OSError):
        backups.create_backup(when=AT)

    assert backups.list_backups() == []
    assert list((root / "backups").glob("*.zip")) == []


def test_a_file_that_vanishes_mid_walk_does_not_fail_the_backup(monkeypatch, tmp_path):
    """The store is live: every atomic write drops a temp beside its target and
    renames it away a moment later, so a walk that lists one and then finds it
    gone is the normal case, not a failure. Backing up only while nobody is
    playing would defeat the point."""
    root = home(monkeypatch, tmp_path)
    small_store(root)
    doomed = root / "campaigns" / "saltmarch" / ".scene.md.ab12cd.tmp"
    doomed.write_text("mid-write", encoding="utf-8")
    real_store = backups._store_file

    def vanish(z, path, arcname):
        if path == doomed:
            path.unlink()               # exactly what atomic's rename does
        return real_store(z, path, arcname)

    monkeypatch.setattr(backups, "_store_file", vanish)
    archive = backups.create_backup(when=AT)

    assert "worlds/realm/world.md" in names_in(archive)
    assert not any(n.endswith(".tmp") for n in names_in(archive))


def test_a_directory_that_cannot_be_listed_fails_the_backup(monkeypatch, tmp_path):
    """`os.walk` swallows a directory it cannot list, so without an `onerror`
    hook a whole unreadable subtree was dropped and the backup reported
    success — the module's stated policy failing at the coarsest granularity
    there is. Simulated rather than chmod'd: CI may run as root, where a
    permission bit stops nothing."""
    root = home(monkeypatch, tmp_path)
    small_store(root)
    real_walk = os.walk

    def blinded(path, **kw):
        onerror = kw.get("onerror")
        for dirpath, dirnames, filenames in real_walk(path, **kw):
            if dirpath.endswith("realm"):
                if onerror:
                    onerror(PermissionError(13, "denied", dirpath))
                continue
            yield dirpath, dirnames, filenames

    monkeypatch.setattr(backups.os, "walk", blinded)
    with pytest.raises(PermissionError):
        backups.create_backup(when=AT)

    assert backups.list_backups() == []


def test_a_directory_deleted_mid_walk_does_not_fail_the_backup(monkeypatch, tmp_path):
    """The other half: a campaign deleted while the walk is running was not
    part of the state being captured."""
    root = home(monkeypatch, tmp_path)
    small_store(root)
    real_walk = os.walk

    def vanishing(path, **kw):
        onerror = kw.get("onerror")
        for dirpath, dirnames, filenames in real_walk(path, **kw):
            if dirpath.endswith("saltmarch"):
                if onerror:
                    onerror(FileNotFoundError(2, "gone", dirpath))
                continue
            yield dirpath, dirnames, filenames

    monkeypatch.setattr(backups.os, "walk", vanishing)
    archive = backups.create_backup(when=AT)

    assert "worlds/realm/world.md" in names_in(archive)


def test_a_file_older_than_the_zip_format_is_stored_with_a_clamped_stamp(
        monkeypatch, tmp_path):
    """Zip cannot represent a timestamp before 1980, and `ZipFile.write` raises
    on one. A file restored out of an old tarball carries an epoch-0 mtime, and
    that must not be the reason a library has no backups."""
    root = home(monkeypatch, tmp_path)
    small_store(root)
    ancient = root / "worlds" / "realm" / "ancient.md"
    ancient.write_text("from before the format", encoding="utf-8")
    os.utime(ancient, (0, 0))

    archive = backups.create_backup(when=AT)

    with zipfile.ZipFile(archive) as z:
        info = z.getinfo("worlds/realm/ancient.md")
        assert info.date_time == (1980, 1, 1, 0, 0, 0)
        assert z.read("worlds/realm/ancient.md") == b"from before the format"


def test_the_archive_never_contains_the_temp_it_is_being_built_into(
        monkeypatch, tmp_path):
    """Only reachable with the backup directory set to the store root — the one
    layout where the walk reaches the archives at all. It copied the
    half-written temp into itself."""
    root = home(monkeypatch, tmp_path)
    small_store(root)
    config.write_config(backup_dir=str(root))

    archive = backups.create_backup(when=AT)

    assert not any(n.endswith(".tmp") for n in names_in(archive))
    assert "worlds/realm/world.md" in names_in(archive)


# ---- listing ---------------------------------------------------------------

def test_the_listing_is_newest_first_and_carries_size_and_date(monkeypatch, tmp_path):
    root = home(monkeypatch, tmp_path)
    small_store(root)
    backups.create_backup(when=AT)
    backups.create_backup(when=AT + timedelta(days=1))

    rows = backups.list_backups()

    assert [r["name"] for r in rows] == ["grimoire-20260815T210000Z.zip",
                                         "grimoire-20260814T210000Z.zip"]
    assert rows[0]["created"] == "2026-08-15T21:00:00Z"
    assert all(r["size"] > 0 for r in rows)


def test_the_listing_ignores_files_this_module_did_not_write(monkeypatch, tmp_path):
    root = home(monkeypatch, tmp_path)
    small_store(root)
    backups.create_backup(when=AT)
    (root / "backups" / "holiday-photos.zip").write_bytes(b"PK\x05\x06" + b"\0" * 18)
    (root / "backups" / "notes.txt").write_text("mine", encoding="utf-8")

    assert [r["name"] for r in backups.list_backups()] == ["grimoire-20260814T210000Z.zip"]


@pytest.mark.parametrize("name", ["grimoire-20269999T999999Z.zip",   # not a date
                                  "grimoire-20260101T235960Z.zip",   # leap second
                                  "grimoire-20260230T120000Z.zip"])  # no such day
def test_a_name_that_cannot_be_a_time_is_not_one_of_ours(monkeypatch, tmp_path, name):
    """The pattern accepts digit runs that are not dates. With the match and the
    parse as two steps, such a file made the listing raise `ValueError` — an
    uncaught 500 — and stopped the schedule for as long as it sat there, since
    `due` reads the same listing."""
    root = home(monkeypatch, tmp_path)
    small_store(root)
    config.write_config(backup_enabled="on")
    backups.create_backup(when=AT)
    (root / "backups" / name).write_bytes(b"PK\x05\x06" + b"\0" * 18)

    assert [r["name"] for r in backups.list_backups()] == ["grimoire-20260814T210000Z.zip"]
    assert backups.due(now=AT + timedelta(days=2))
    assert backups.sweep(keep=1) == []
    assert (root / "backups" / name).exists()   # not ours, so never deleted


def test_listing_a_store_that_has_never_been_backed_up_is_empty(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    assert backups.list_backups() == []
    assert not (tmp_path / "backups").exists()


# ---- retention -------------------------------------------------------------

def test_the_sweep_keeps_the_newest_and_deletes_the_rest(monkeypatch, tmp_path):
    root = home(monkeypatch, tmp_path)
    small_store(root)
    for day in range(5):
        backups.create_backup(when=AT + timedelta(days=day))

    removed = backups.sweep(keep=2)

    assert removed == ["grimoire-20260814T210000Z.zip", "grimoire-20260815T210000Z.zip",
                       "grimoire-20260816T210000Z.zip"]
    assert [r["name"] for r in backups.list_backups()] == [
        "grimoire-20260818T210000Z.zip", "grimoire-20260817T210000Z.zip"]


def test_keeping_zero_keeps_everything(monkeypatch, tmp_path):
    """0 is the off switch for retention, not an instruction to delete the
    archive that was just written."""
    root = home(monkeypatch, tmp_path)
    small_store(root)
    backups.create_backup(when=AT)
    backups.create_backup(when=AT + timedelta(days=1))

    assert backups.sweep(keep=0) == []
    assert len(backups.list_backups()) == 2


def test_the_sweep_never_deletes_a_file_it_did_not_write(monkeypatch, tmp_path):
    root = home(monkeypatch, tmp_path)
    small_store(root)
    backups.create_backup(when=AT)
    backups.create_backup(when=AT + timedelta(days=1))
    bystander = root / "backups" / "holiday-photos.zip"
    bystander.write_bytes(b"PK\x05\x06" + b"\0" * 18)

    backups.sweep(keep=1)

    assert bystander.exists()


def test_the_sweep_reads_its_limit_from_the_config(monkeypatch, tmp_path):
    root = home(monkeypatch, tmp_path)
    small_store(root)
    config.write_config(backup_keep="1")
    for day in range(3):
        backups.create_backup(when=AT + timedelta(days=day))

    backups.sweep()

    assert [r["name"] for r in backups.list_backups()] == ["grimoire-20260816T210000Z.zip"]


# ---- the schedule ----------------------------------------------------------

def test_a_store_with_no_archive_is_due(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    config.write_config(backup_enabled="on")
    assert backups.due(now=AT)


def test_nothing_is_due_again_until_the_interval_has_passed(monkeypatch, tmp_path):
    root = home(monkeypatch, tmp_path)
    small_store(root)
    config.write_config(backup_enabled="on", backup_interval_hours="24")
    backups.create_backup(when=AT)

    assert not backups.due(now=AT + timedelta(hours=23, minutes=59))
    assert backups.due(now=AT + timedelta(hours=24))


def test_an_archive_stamped_in_the_future_does_not_stall_the_schedule(
        monkeypatch, tmp_path):
    """A store synced from a machine whose clock is ahead, or restored with a
    nonsense stamp, used to make `due` False for as long as that stamp was in
    the future — automatic backups stopping dead with nothing saying so."""
    root = home(monkeypatch, tmp_path)
    small_store(root)
    config.write_config(backup_enabled="on", backup_interval_hours="24")
    (root / "backups").mkdir()
    (root / "backups" / "grimoire-20990101T000000Z.zip").write_bytes(
        b"PK\x05\x06" + b"\0" * 18)

    assert backups.due(now=AT)

    # ...and having taken one, the schedule runs off ITS stamp rather than
    # re-firing every tick against the impossible one.
    backups.create_backup(when=AT)
    assert not backups.due(now=AT + timedelta(hours=1))
    assert backups.due(now=AT + timedelta(hours=24))


def test_a_sweep_that_fails_does_not_take_the_written_backup_with_it(
        monkeypatch, tmp_path):
    """The archive is what the call is for and it has already landed. Raising
    here made the ticker log a written backup as a skipped one."""
    root = home(monkeypatch, tmp_path)
    small_store(root)
    config.write_config(backup_enabled="on", backup_keep="1")
    monkeypatch.setattr(backups, "sweep", lambda *a, **kw: (_ for _ in ()).throw(
        PermissionError("cannot prune")))

    made = backups.run_scheduled(now=AT)

    assert made is not None and made.exists()


def test_run_scheduled_does_nothing_while_backups_are_off(monkeypatch, tmp_path):
    root = home(monkeypatch, tmp_path)
    small_store(root)

    assert config.read_config()["backup_enabled"] == "off"
    assert backups.run_scheduled(now=AT) is None
    assert backups.list_backups() == []


def test_run_scheduled_backs_up_then_sweeps(monkeypatch, tmp_path):
    root = home(monkeypatch, tmp_path)
    small_store(root)
    config.write_config(backup_enabled="on", backup_interval_hours="1", backup_keep="2")

    made = [backups.run_scheduled(now=AT + timedelta(hours=h)) for h in range(4)]

    assert all(m is not None for m in made)
    assert [r["name"] for r in backups.list_backups()] == [
        "grimoire-20260815T000000Z.zip", "grimoire-20260814T230000Z.zip"]


def test_run_scheduled_skips_a_store_that_is_not_due_yet(monkeypatch, tmp_path):
    root = home(monkeypatch, tmp_path)
    small_store(root)
    config.write_config(backup_enabled="on", backup_interval_hours="24")

    assert backups.run_scheduled(now=AT) is not None
    assert backups.run_scheduled(now=AT + timedelta(hours=1)) is None
    assert len(backups.list_backups()) == 1


# ---- where the archives live ----------------------------------------------

def test_a_configured_backup_dir_moves_the_archives_out_of_the_store(
        monkeypatch, tmp_path):
    """The store may sit in a synced folder, where every archive is re-uploaded
    whole. Pointing this elsewhere is how that is avoided."""
    root = home(monkeypatch, tmp_path / "store")
    root.mkdir()
    small_store(root)
    elsewhere = tmp_path / "elsewhere"
    config.write_config(backup_dir=str(elsewhere))

    archive = backups.create_backup(when=AT)

    assert archive.parent == elsewhere
    assert not (root / "backups").exists()
    assert [r["name"] for r in backups.list_backups()] == [archive.name]


def test_a_backup_dir_nested_in_the_store_is_still_excluded(monkeypatch, tmp_path):
    root = home(monkeypatch, tmp_path)
    small_store(root)
    config.write_config(backup_dir=str(root / "worlds" / "snapshots"))

    backups.create_backup(when=AT)
    second = backups.create_backup(when=AT + timedelta(hours=1))

    assert not any(n.startswith("worlds/snapshots/") for n in names_in(second))


def test_a_backup_dir_above_the_store_still_archives_the_store(monkeypatch, tmp_path):
    """`~` is the natural answer to "somewhere else", and an exclusion rule that
    matched an ancestor of the store would have made every archive empty — a
    silent nothing wearing a backup's name."""
    root = home(monkeypatch, tmp_path / "store")
    root.mkdir()
    small_store(root)
    config.write_config(backup_dir=str(tmp_path))

    archive = backups.create_backup(when=AT)

    assert archive.parent == tmp_path
    assert "worlds/realm/world.md" in names_in(archive)


def test_a_backup_dir_set_to_the_store_root_still_archives_the_store(
        monkeypatch, tmp_path):
    """The degenerate setting: archives land beside the library they are of.
    The store still has to be in there, and the archives still must not
    accumulate inside each other."""
    root = home(monkeypatch, tmp_path)
    small_store(root)
    config.write_config(backup_dir=str(root))

    first = backups.create_backup(when=AT)
    second = backups.create_backup(when=AT + timedelta(hours=1))

    assert first.parent == root
    assert "worlds/realm/world.md" in names_in(second)
    assert first.name not in names_in(second)


def test_the_backup_dir_follows_the_store_when_it_is_not_configured(monkeypatch, tmp_path):
    root = home(monkeypatch, tmp_path)
    assert backups.backup_dir() == root / "backups"


# ---- config ----------------------------------------------------------------

def test_the_backup_settings_have_conservative_defaults(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    cfg = config.read_config()

    assert cfg["backup_enabled"] == "off"
    assert config.backup_enabled() is False
    assert config.backup_interval_hours() == 24.0
    assert config.backup_keep() == 7
    assert config.backup_dir() == ""


@pytest.mark.parametrize("raw", ["", "  ", "nonsense", "-3", "0", "inf", "nan"])
def test_an_unusable_interval_falls_back_to_the_default(monkeypatch, tmp_path, raw):
    """A hand-edited config.md must not turn the schedule into an hourly zip of
    the whole library, and there is a separate switch for "off"."""
    home(monkeypatch, tmp_path)
    config.write_config(backup_interval_hours=raw)
    assert config.backup_interval_hours() == 24.0


def test_the_settings_survive_a_round_trip(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    config.write_config(backup_enabled="on", backup_interval_hours="6",
                        backup_keep="3", backup_dir="/tmp/elsewhere")
    cfg = config.read_config()

    assert cfg["backup_enabled"] == "on"
    assert config.backup_enabled() is True
    assert config.backup_interval_hours() == 6.0
    assert config.backup_keep() == 3
    assert config.backup_dir() == "/tmp/elsewhere"


# ---- concurrency -----------------------------------------------------------

def test_a_backup_takes_the_backup_lock(monkeypatch, tmp_path):
    """Two processes sharing a store must not zip it at the same moment, and a
    sweep must not run against a listing another process is adding to."""
    root = home(monkeypatch, tmp_path)
    small_store(root)
    seen = []
    real = backups._archive_into

    def spy(*a, **kw):
        seen.append(locks.backup_lock()._is_owned())
        return real(*a, **kw)

    monkeypatch.setattr(backups, "_archive_into", spy)
    backups.create_backup(when=AT)

    assert seen == [True]
