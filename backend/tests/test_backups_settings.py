"""Settings safety archives protect migration inputs without walking the library (#496)."""
from __future__ import annotations

import os
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from grimoire.store import backups

AT = datetime(2026, 8, 14, 21, tzinfo=UTC)
FAMILIES = [backups.SAFETY_PREFIX, backups.RETIRE_PREFIX]


@pytest.fixture
def settings(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    protected = {
        "config.md": b"---\ntheme: system\n---\n",
        "llm_connections/mara.md": b"connection\r\n",
        "llm_connections/facts/mara.json": b'{"context": 8192}',
        "sampler_presets/nested/default.md": b"temperature: 0.7\n",
        "inference-retired.json": b"{}\n",
        "campaigns/saltmarch/campaign.md": b"---\nname: Saltmarch\n---\nFull body.\n",
        "campaigns/saltmarch/revision.txt": b"17\n",
    }
    for name, content in protected.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    for name in ["assets/image-store/portrait.png", "worlds/realm/world.md",
                 "campaigns/saltmarch/scenes/001.md", ".cache/derived.json"]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"unrelated")
    return tmp_path, protected


def contents(archive):
    with zipfile.ZipFile(archive) as zipped:
        return {name: zipped.read(name) for name in zipped.namelist()}


@pytest.mark.parametrize("prefix", FAMILIES)
def test_settings_archive_preserves_only_protected_bytes(settings, prefix, monkeypatch):
    root, protected = settings
    walk = backups.os.walk
    def selected_walk(path, **kwargs):
        assert Path(path).relative_to(root).parts[0] in {"llm_connections", "sampler_presets"}
        yield from walk(path, **kwargs)
    monkeypatch.setattr(backups.os, "walk", selected_walk)
    progress = []
    archive = backups.create_backup(AT, prefix, on_progress=lambda d, t: progress.append((d, t)))
    assert contents(archive) == protected
    assert archive.name.startswith(prefix + "settings-grimoire-")
    assert progress[:2] == [(0, None), (0, len(protected))]
    assert progress[-1] == (len(protected), len(protected))
    assert [d for d, _ in progress] == sorted(d for d, _ in progress)


@pytest.mark.parametrize("destination", ["llm_connections", "llm_connections/facts/archives",
    "sampler_presets", "sampler_presets/nested", "campaigns", "campaigns/saltmarch"])
def test_settings_output_cannot_hide_protected_content(settings, destination, monkeypatch):
    root, protected = settings
    target = root / destination
    monkeypatch.setattr(backups, "backup_dir", lambda: target)
    with pytest.raises(OSError):
        backups.create_backup(AT, backups.SAFETY_PREFIX)
    assert not list(target.glob("*.zip"))
    assert all((root / name).read_bytes() == content for name, content in protected.items())


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_settings_unreadable_content_never_publishes(settings, monkeypatch, kind):
    root, _ = settings
    if kind == "file":
        original = backups._store_file
        def fail(zipped, path, arcname):
            if arcname == "config.md":
                raise PermissionError("selected file denied")
            return original(zipped, path, arcname)
        monkeypatch.setattr(backups, "_store_file", fail)
    else:
        original = Path.iterdir
        def denied(path):
            if path == root / "campaigns":
                raise PermissionError("selected directory denied")
            return original(path)
        monkeypatch.setattr(Path, "iterdir", denied)
    with pytest.raises(PermissionError):
        backups.create_backup(AT, backups.SAFETY_PREFIX)
    assert backups.list_backups() == []
    assert not list((root / "backups").glob("*.tmp"))


def test_settings_missing_optionals_and_empty_count(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    monkeypatch.setattr(backups, "backup_dir", lambda: tmp_path / "backups")
    progress = []
    archive = backups.create_backup(AT, backups.SAFETY_PREFIX,
                                    on_progress=lambda d, t: progress.append((d, t)))
    assert contents(archive) == {}
    assert progress == [(0, None), (0, 0)]


@pytest.mark.parametrize("vanished", ["file", "directory"])
def test_settings_vanished_selection_finishes_progress(settings, monkeypatch, vanished):
    root, protected = settings
    progress = []
    def observe(done, total):
        progress.append((done, total))
        if total is not None and done == 0:
            (root / "config.md").unlink()
    if vanished == "directory":
        original = backups.os.walk
        def gone(path, **kwargs):
            if Path(path) == root / "sampler_presets":
                kwargs["onerror"](FileNotFoundError("vanished"))
                return
            yield from original(path, **kwargs)
        monkeypatch.setattr(backups.os, "walk", gone)
        del protected["sampler_presets/nested/default.md"]
    archive = backups.create_backup(AT, backups.SAFETY_PREFIX, on_progress=observe)
    del protected["config.md"]
    assert contents(archive) == protected
    assert progress[-1] == (len(protected) + 1, len(protected) + 1)


@pytest.mark.parametrize("destination", ["external", "ancestor", "root"])
def test_settings_output_locations_and_artifacts(settings, monkeypatch, destination):
    root, protected = settings
    target = {"external": root.parent / "archives", "ancestor": root.parent, "root": root}[destination]
    monkeypatch.setattr(backups, "backup_dir", lambda: target)
    first = backups.create_backup(AT, backups.SAFETY_PREFIX)
    second = backups.create_backup(AT, backups.SAFETY_PREFIX)
    assert second.name.endswith("-2.zip")
    assert contents(first) == contents(second) == protected
    full = backups.create_backup(AT)
    assert not any("grimoire-" in name for name in contents(full))


@pytest.mark.parametrize("linked", ["root", "file", "selected-directory", "nested-directory", "campaign"])
def test_settings_links(settings, monkeypatch, linked):
    root, protected = settings
    if linked == "root":
        link, target = root.parent / (root.name + "-link"), root
    elif linked == "file":
        target = root / "original.md"
        target.write_bytes(protected["config.md"])
        link = root / "config.md"
        link.unlink()
    elif linked == "campaign":
        link, target = root / "campaigns/mara", root / "campaigns/saltmarch"
    else:
        link = root / ("llm_connections" if linked == "selected-directory" else "llm_connections/linked")
        target = root / "sampler_presets"
        if linked == "selected-directory":
            # Preserve the fixture tree without recursively deleting it.
            link.rename(root / "old-connections")
    try:
        link.symlink_to(target, target_is_directory=linked != "file")
    except OSError:
        pytest.skip("symlink creation unavailable")
    if linked == "root":
        monkeypatch.setenv("GRIMOIRE_HOME", str(link))
    if linked in {"selected-directory", "nested-directory", "campaign"}:
        with pytest.raises(OSError):
            backups.create_backup(AT, backups.SAFETY_PREFIX)
        assert backups.list_backups() == []
    else:
        assert contents(backups.create_backup(AT, backups.SAFETY_PREFIX)) == protected


def test_linked_settings_refusal_names_the_link(settings):
    # #496: the migration's failure reason is the only place a user learns
    # which folder to fix, so the refusal carries its library-relative path.
    root, _ = settings
    try:
        (root / "campaigns" / "linked").symlink_to(root / "campaigns" / "saltmarch",
                                                   target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(OSError, match=r"symlink: campaigns/linked$"):
        backups.create_backup(AT, backups.SAFETY_PREFIX)


def test_settings_old_timestamps(settings):
    root, protected = settings
    os.utime(root / "config.md", (0, 0))
    assert contents(backups.create_backup(AT, backups.SAFETY_PREFIX)) == protected


@pytest.mark.parametrize("prefix", FAMILIES)
def test_settings_scope_schedule_and_retention(settings, prefix):
    root, _ = settings
    partial = backups.create_backup(AT, prefix)
    assert backups.list_backups()[0]["scope"] == "settings"
    assert backups.due(AT + timedelta(minutes=1))
    full = backups.create_backup(AT)
    assert not backups.due(AT + timedelta(minutes=1))
    later = backups.create_backup(AT + timedelta(days=1), prefix)
    assert backups.due(AT + timedelta(days=1, minutes=1))
    backups.create_backup(AT + timedelta(days=2))
    assert backups.sweep(1) == [full.name]
    assert partial.exists() and later.exists()
    legacy = root / "backups" / (prefix + "grimoire-20260817T210000Z.zip")
    legacy.write_bytes(b"legacy full archive")
    assert backups.list_backups()[0]["scope"] == "full"
    assert not backups.due(AT + timedelta(days=3, minutes=1))


def test_settings_abandoned_temp_cleanup(settings):
    root, _ = settings
    directory = root / "backups"
    directory.mkdir()
    abandoned = directory / ".pre-inference-settings-grimoire-20260814T210000Z.zip.abcdefgh.tmp"
    abandoned.write_bytes(b"unfinished")
    os.utime(abandoned, (0, 0))
    backups.create_backup(AT, backups.SAFETY_PREFIX)
    assert not abandoned.exists()


def test_settings_output_alias_cannot_bypass_protected_overlap(settings, monkeypatch):
    root, _ = settings
    alias = root.parent / (root.name + "-alias")
    try:
        alias.symlink_to(root / "llm_connections", target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable")
    monkeypatch.setattr(backups, "backup_dir", lambda: alias)
    with pytest.raises(OSError):
        backups.create_backup(AT, backups.SAFETY_PREFIX)
    assert not list(alias.glob("*.zip"))
