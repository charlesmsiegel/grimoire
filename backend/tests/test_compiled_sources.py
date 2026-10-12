"""Exact-byte validation and the persisted trust point (Spec03 S2)."""

import hashlib
import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests import test_compiled as cache_tests

cache = cache_tests.cache
root = cache_tests.root
_kind = cache_tests._kind
_open = cache_tests._open


def _digest(data=b"Realm"):
    return hashlib.sha256(data).hexdigest()


def _seed(cache, root, path="Realm.md", stamp=None):
    stamp = stamp or (path, 11, 12, 5, 2**120)
    with cache.batch(root) as request:
        request._stage_source(path, stamp, _digest())
    return stamp


def test_sources_schema_and_large_windows_inode_roundtrip(cache, root, monkeypatch):
    _kind(cache, monkeypatch)
    stamp = _seed(cache, root)
    session = _open(cache, root)
    columns = [row[1] for row in session.conn.execute("PRAGMA table_info(sources)")]
    assert columns == ["path", "mtime_ns", "ctime_ns", "size", "ino", "content_hash", "check", "last_used"]
    with cache.batch(root) as request:
        rows = request._source_rows(["Realm.md"])
    assert cache._decode_source("Realm.md", rows["Realm.md"]) == (stamp, _digest())


def test_source_selection_uses_only_supplied_paths_in_bounded_queries(cache, root, monkeypatch):
    _kind(cache, monkeypatch)
    _seed(cache, root, "Realm.md")
    _seed(cache, root, "Mara.md")
    statements = []
    _open(cache, root).conn.set_trace_callback(statements.append)
    with cache.batch(root) as request:
        paths = ["Realm.md", *[f"Saltmarch-{i}.md" for i in range(256)]]
        rows = request._source_rows(paths)
    assert set(rows) == {"Realm.md"}
    assert sum("FROM sources WHERE" in sql for sql in statements) == 3


@pytest.mark.parametrize("damage", ["hash", "mtime", "path", "inode", "check"])
def test_source_checksum_binds_all_fields_and_warns_once(cache, root, monkeypatch, damage):
    _kind(cache, monkeypatch)
    _seed(cache, root)
    conn = _open(cache, root).conn
    column, value = {"hash": ("content_hash", "b" * 64), "mtime": ("mtime_ns", 999),
                     "path": ("path", "Mara.md"), "inode": ("ino", "invalid"),
                     "check": ("check", "x" * 1000)}[damage]
    conn.execute(f'UPDATE sources SET "{column}"=?', (value,))
    warnings = []
    monkeypatch.setattr(cache.logs, "record", lambda *args, **kw: warnings.append((args, kw)))
    path = "Mara.md" if damage == "path" else "Realm.md"
    for _ in range(3):
        with cache.batch(root) as request:
            row = request._source_rows([path])[path]
        assert cache._decode_source(path, row) is None
    assert warnings == [(("warning", "store.compiled", "Compiled cache unavailable"),
                         {"failure_kind": "source"})]


@pytest.mark.parametrize("value", [True, 2**64, -2**64])
def test_unrepresentable_source_stamps_are_optional(cache, root, monkeypatch, value):
    _kind(cache, monkeypatch)
    _seed(cache, root, stamp=("Realm.md", value, 12, 5, 2))
    with cache.batch(root) as request:
        assert request._source_rows(["Realm.md"]) == {}


def test_sources_and_artifacts_share_one_transaction_and_budget(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    key = cache.make_key(kind, [("document", _digest())], params={"stem": "realm", "settings": {}})
    conn = _open(cache, root).conn
    statements = []
    conn.set_trace_callback(statements.append)
    with cache.batch(root) as request:
        request.put(key, "Realm")
        for i in range(cache.MAX_STAGED_ROWS + 1):
            path = f"Saltmarch-{i}.md"
            request._stage_source(path, (path, 11, 12, 5, 2), _digest())
    assert sum(sql == "BEGIN IMMEDIATE" for sql in statements) == 1
    assert sum(sql == "COMMIT" for sql in statements) == 1
    assert conn.execute("SELECT count(*) FROM sources").fetchone()[0] == cache.MAX_STAGED_ROWS - 1
    with cache.batch(root) as request:
        assert request.lookup(key).value == "Realm"


def test_caller_exception_drops_source_staging(cache, root, monkeypatch):
    _kind(cache, monkeypatch)
    with pytest.raises(ValueError, match="reader"), cache.batch(root) as request:
        request._stage_source("Realm.md", ("Realm.md", 11, 12, 5, 2), _digest())
        raise ValueError("reader")
    with cache.batch(root) as request:
        assert request._source_rows(["Realm.md"]) == {}


@pytest.mark.parametrize("age,eligible", [(5_000_000_000, False), (5_000_000_001, True),
                                          (2_000_000_000, False), (-1, False)])
def test_persist_window_is_strict_for_both_timestamps(cache, root, tmp_path, age, eligible):
    path = root / "Realm.md"
    path.write_bytes(b"Realm")
    info = cache._source_info(root, path)
    assert cache.PERSIST_WINDOW_NS == 5_000_000_000
    for index in (1, 2):
        values = list(info.stamp)
        values[index] = max(info.stamp[1:3]) + 100
        changed = cache._SourceInfo(info.path, tuple(values), info.device, info.relative, info.plain)
        now = max(changed.stamp[1:3]) + age
        assert cache._eligible(changed, cache.FilesystemClock(now, info.device)) is eligible


def test_matching_device_is_required_not_just_relative_containment(cache, root):
    path = root / "Realm.md"
    path.write_bytes(b"Realm")
    info = cache._source_info(root, path)
    later = max(info.stamp[1:3]) + 10_000_000_000
    assert not cache._eligible(info, cache.FilesystemClock(later, info.device + 1))
    assert not cache._eligible(info, None)


def test_linked_source_is_readable_but_never_stamp_trusted(cache, root, tmp_path):
    outside = tmp_path / "Mara.md"
    outside.write_bytes(b"Mara")
    path = root / "Realm.md"
    try:
        path.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation unavailable")
    info = cache._source_info(root, path)
    assert path.read_bytes() == b"Mara"
    assert not cache._eligible(info, cache.FilesystemClock(max(info.stamp[1:3]) + 10**10, info.device))


def test_clock_provider_samples_the_guarded_sentinel(cache, root, monkeypatch):
    _kind(cache, monkeypatch)
    if not cache._clock_supported(root):
        pytest.skip("native filesystem clock provenance unavailable")
    clock = cache.filesystem_clock(root)
    assert clock is not None
    sentinel = cache._clock_path(root)
    stamp = sentinel.stat()
    assert clock.now_ns == stamp.st_mtime_ns and clock.device == stamp.st_dev


def test_linked_clock_never_touches_external_target(cache, root, tmp_path, monkeypatch):
    _kind(cache, monkeypatch)
    target = tmp_path / "Mara.md"
    target.write_bytes(b"Mara")
    sentinel = cache._clock_path(root)
    try:
        sentinel.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation unavailable")
    before = target.stat()
    assert cache.filesystem_clock(root) is None
    after = target.stat()
    assert target.read_bytes() == b"Mara"
    assert (before.st_mtime_ns, before.st_ctime_ns, before.st_size) == (after.st_mtime_ns, after.st_ctime_ns, after.st_size)


@pytest.mark.parametrize("drive_type", [0, 1, 4, 5])
def test_windows_remote_or_unknown_clock_is_unavailable_before_touch(cache, root, monkeypatch, drive_type):
    _kind(cache, monkeypatch)
    sentinel = cache._clock_path(root)
    monkeypatch.setattr(cache, "_WINDOWS_CLOCK", True)

    def volume(path, result, size):
        result.value = "C:/"
        return 1

    kernel = SimpleNamespace(GetVolumePathNameW=volume, GetDriveTypeW=lambda path: drive_type)
    monkeypatch.setattr(cache, "_kernel32", kernel)

    def forbidden(*args, **kw):
        raise AssertionError("unproven remote clock reached utime")

    monkeypatch.setattr(os, "utime", forbidden)
    assert cache.filesystem_clock(root) is None
    assert not sentinel.exists()


@pytest.mark.parametrize("drive_type", [2, 3, 6])
def test_windows_local_clock_classification_uses_final_volume(cache, root, monkeypatch, drive_type):
    _kind(cache, monkeypatch)
    captured = []
    monkeypatch.setattr(cache, "_WINDOWS_CLOCK", True)

    def volume(path, result, size):
        captured.append(path)
        result.value = "C:/"
        return 1

    monkeypatch.setattr(cache, "_kernel32", SimpleNamespace(
        GetVolumePathNameW=volume, GetDriveTypeW=lambda path: drive_type))
    assert cache._clock_supported(root / "Realm.md")
    assert captured == [str((root / "Realm.md").resolve())]


@pytest.mark.parametrize("failure", ["missing", "api", "exception"])
def test_windows_clock_api_failure_opts_out(cache, root, monkeypatch, failure):
    monkeypatch.setattr(cache, "_WINDOWS_CLOCK", True)

    def volume(*args):
        if failure == "exception":
            raise OSError("unavailable")
        return 0

    monkeypatch.setattr(cache, "_kernel32", None if failure == "missing" else SimpleNamespace(
        GetVolumePathNameW=volume, GetDriveTypeW=lambda path: 3))
    assert not cache._clock_supported(root / "Realm.md")


def test_source_reparse_parent_is_not_trusted(cache, root, monkeypatch):
    path = root / "Realm" / "Mara.md"
    path.parent.mkdir()
    path.write_bytes(b"Mara")
    original = cache._linked
    monkeypatch.setattr(cache, "_linked", lambda info: original(info) or info.st_ino == path.parent.stat().st_ino)
    info = cache._source_info(root, path)
    assert not info.plain
    assert not cache._eligible(info, cache.FilesystemClock(max(info.stamp[1:3]) + 10**10, info.device))


def _aged(cache, root, monkeypatch):
    stamps = [p.stat() for p in root.rglob("*.md")]
    now = max((max(st.st_mtime_ns, st.st_ctime_ns) for st in stamps), default=0) + 10**10
    monkeypatch.setattr(cache, "filesystem_clock", lambda root: cache.FilesystemClock(now, root.stat().st_dev))


@pytest.mark.parametrize("data", [b"Realm\r\n", b"\xef\xbb\xbfRealm", b"\xffRealm"])
def test_validation_hashes_exact_bytes_and_single_batch_parity(cache, root, monkeypatch, data):
    kind = _kind(cache, monkeypatch)
    path = root / "Realm.md"
    path.write_bytes(data)
    _aged(cache, root, monkeypatch)
    with cache.batch(root) as request:
        inputs = [cache.Input("document", path), cache.Input("companion", path)]
        values = request.validate(kind, inputs)
        single = request.validate_one(kind, inputs[0], with_bytes=True)
    assert [(v.role, v.content_hash, v.data) for v in values] == [
        ("document", _digest(data), data), ("companion", _digest(data), data)]
    assert single == values[0]


def test_clock_once_precedes_every_source_stat_and_duplicate_read(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    path = root / "Realm.md"
    path.write_bytes(b"Realm")
    events = []
    original = cache._source_info
    read = Path.read_bytes
    monkeypatch.setattr(cache, "filesystem_clock", lambda root: events.append("clock"))
    monkeypatch.setattr(cache, "_source_info", lambda *args: (events.append("stat"), original(*args))[1])
    monkeypatch.setattr(Path, "read_bytes", lambda p: (events.append("read"), read(p))[1])
    with cache.batch(root) as request:
        request.validate(kind, [cache.Input("a", path), cache.Input("b", path)])
        request.validate_one(kind, cache.Input("a", path))
    assert events == ["clock", "stat", "read", "stat", "read"]


def test_restart_source_hit_and_with_bytes_rehash(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    path = root / "Realm.md"
    path.write_bytes(b"Realm")
    _aged(cache, root, monkeypatch)
    with cache.batch(root) as request:
        assert request.validate_one(kind, cache.Input("a", path)).data == b"Realm"
    cache.close()
    cache.statcache._cache.clear()
    read = Path.read_bytes
    counts = []
    monkeypatch.setattr(Path, "read_bytes", lambda p: (counts.append(p), read(p))[1])
    with cache.batch(root) as request:
        assert request.validate_one(kind, cache.Input("a", path)).data is None
        result = request.validate_one(kind, cache.Input("a", path), with_bytes=True)
        assert result.data == b"Realm" and result.content_hash == _digest()
    assert counts == [path]


def test_slow_read_never_ages_fresh_stamp_into_persistence(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    path = root / "Realm.md"
    path.write_bytes(b"Realm")
    now = [max(path.stat().st_mtime_ns, path.stat().st_ctime_ns)]
    monkeypatch.setattr(cache, "filesystem_clock", lambda root: cache.FilesystemClock(now[0], root.stat().st_dev))
    read = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda p: (now.__setitem__(0, now[0] + 10**10), read(p))[1])
    with cache.batch(root) as request:
        assert request.validate_one(kind, cache.Input("a", path)).data == b"Realm"
        assert request._sources == {}
    with cache.batch(root) as request:
        assert request._source_rows(["Realm.md"]) == {}


def test_pre_read_stamp_is_retained_when_read_changes_file(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    path = root / "Realm.md"
    path.write_bytes(b"Realm")
    _aged(cache, root, monkeypatch)
    read = Path.read_bytes

    def moving(p):
        data = read(p)
        if p == path:
            path.write_bytes(b"Saltmarch")
        return data

    monkeypatch.setattr(Path, "read_bytes", moving)
    with cache.batch(root) as request:
        before = path.stat()
        result = request.validate_one(kind, cache.Input("a", path))
        assert result.stamp[3] == before.st_size
    monkeypatch.setattr(Path, "read_bytes", read)
    with cache.batch(root) as request:
        result = request.validate_one(kind, cache.Input("a", path))
        assert result.content_hash == _digest(b"Saltmarch") and result.data == b"Saltmarch"


def test_absence_is_live_and_only_declared_roles_accept_it(cache, root, monkeypatch):
    original = _kind(cache, monkeypatch)
    kind = replace(original, absent_ok_roles=("companion",))
    monkeypatch.setitem(cache.KINDS, kind.name, kind)
    path = root / "Mara.md"
    with cache.batch(root) as request:
        absent = request.validate_one(kind, cache.Input("companion", path))
        assert absent.content_hash == cache.ABSENT and absent.stamp is None and absent.data is None
        assert cache.make_key(kind, [("companion", absent.content_hash)], params={"stem": "mara", "settings": {}})
        assert cache.make_key(kind, [("document", cache.ABSENT)], params={"stem": "mara", "settings": {}}) is None
        path.write_bytes(b"Mara")
        assert request.validate_one(kind, cache.Input("companion", path)).content_hash == _digest(b"Mara")
        path.unlink()
        assert request.validate_one(kind, cache.Input("companion", path)).content_hash == cache.ABSENT
    assert not (root / ".cache").exists() or _open(cache, root).conn.execute("SELECT count(*) FROM sources").fetchone()[0] == 0


@pytest.mark.parametrize("failure", ["missing", "permission", "directory", "vanished"])
def test_unavailable_input_permanently_discards_whole_batch(cache, root, monkeypatch, failure):
    kind = _kind(cache, monkeypatch)
    key = cache.make_key(kind, [("a", _digest())], params={"stem": "realm", "settings": {}})
    path = root / "Mara.md"
    if failure == "directory":
        path.mkdir()
    elif failure in ("permission", "vanished"):
        path.write_bytes(b"Mara")
    original = cache._source_info

    def info(*args):
        if failure == "permission":
            raise PermissionError("unavailable")
        value = original(*args)
        if failure == "vanished":
            path.unlink()
        return value

    monkeypatch.setattr(cache, "_source_info", info)
    with cache.batch(root) as request:
        request.put(key, "Realm")
        request._stage_source("Realm.md", ("Realm.md", 11, 12, 5, 2), _digest())
        with pytest.raises(cache.InputUnavailable):
            request.validate_one(kind, cache.Input("a", path))
        assert request.disabled and not request._rows and not request._sources
        request.put(key, "Saltmarch")
        assert not request.lookup(key).hit
    with cache.batch(root) as request:
        assert not request.lookup(key).hit and not request._source_rows(["Realm.md"])


@pytest.mark.parametrize("policy", ["false", "unregistered", "disabled", "no_clock", "different_device"])
def test_source_shortcuts_require_registry_policy_and_clock(cache, root, monkeypatch, policy):
    kind = _kind(cache, monkeypatch)
    path = root / "Realm.md"
    path.write_bytes(b"Realm")
    _aged(cache, root, monkeypatch)
    with cache.batch(root) as request:
        request.validate_one(kind, cache.Input("a", path))
    if policy == "false":
        kind = replace(kind, allow_persisted_sources=False)
        monkeypatch.setitem(cache.KINDS, kind.name, kind)
    elif policy == "unregistered":
        monkeypatch.delitem(cache.KINDS, kind.name)
    elif policy == "disabled":
        monkeypatch.setenv("GRIMOIRE_COMPILED_CACHE", "0")
    else:
        monkeypatch.setattr(cache, "filesystem_clock", lambda root: None if policy == "no_clock" else cache.FilesystemClock(10**30, -1))
    with cache.batch(root) as request:
        assert request.validate_one(kind, cache.Input("a", path)).data == b"Realm"
        assert not request._sources
        assert request.known_hashes(kind, [path]) == {path: None}


def test_known_hashes_stats_live_paths_without_source_byte_io(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    paths = [root / name for name in ("Realm.md", "Mara.md", "Winifred.md")]
    for p in paths[:2]:
        p.write_bytes(b"Realm")
    _aged(cache, root, monkeypatch)
    with cache.batch(root) as request:
        request.validate_one(kind, cache.Input("a", paths[0]))
    expected = _digest()
    statted = []
    original = cache._source_info
    monkeypatch.setattr(cache, "_source_info", lambda *args: (statted.append(args[1]), original(*args))[1])

    def forbidden(*args):
        raise AssertionError("probe read source bytes")

    monkeypatch.setattr(Path, "read_bytes", forbidden)
    # Source checksums and machine lock names hash metadata; forbid the exact
    # source bytes rather than unrelated process-lock or row-check digests.
    digest = hashlib.new
    monkeypatch.setattr(cache.hashlib, "sha256", lambda data: forbidden() if data == b"Realm" else digest("sha256", data))
    with cache.batch(root) as request:
        assert request.known_hashes(kind, paths) == {paths[0]: expected, paths[1]: None, paths[2]: None}
        assert not request._sources
    assert statted == paths


def test_empty_validation_and_probe_create_no_cache_storage(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    with cache.batch(root) as request:
        assert request.validate(kind, []) == ()
        assert request.known_hashes(kind, []) == {}
    assert not (root / ".cache").exists()


@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("edit", ["rewrite", "replace", "rename", "delete"])
def test_source_entries_never_supply_a_deleted_or_changed_file(cache, root, monkeypatch, restart, edit):
    kind = _kind(cache, monkeypatch)
    path = root / "Realm.md"
    path.write_bytes(b"Realm")
    _aged(cache, root, monkeypatch)
    with cache.batch(root) as request:
        request.validate_one(kind, cache.Input("a", path))
    if restart:
        cache.close()
        cache.statcache._cache.clear()
    if edit == "rewrite":
        path.write_bytes(b"Mara")
    elif edit == "replace":
        replacement = root / "Mara.md"
        replacement.write_bytes(b"Mara")
        replacement.replace(path)
    elif edit == "rename":
        path = path.rename(root / "Mara.md")
    else:
        path.unlink()
    with cache.batch(root) as request:
        assert request.known_hashes(kind, [root / "Realm.md"]) == {root / "Realm.md": None}
        if edit == "delete":
            with pytest.raises(cache.InputUnavailable):
                request.validate_one(kind, cache.Input("a", path))
        else:
            result = request.validate_one(kind, cache.Input("a", path))
            assert result.content_hash == _digest(b"Realm" if edit == "rename" else b"Mara")
            assert result.data is not None


@pytest.mark.parametrize("location", ["outside", "linked", "different_device"])
def test_unprovable_source_stays_readable_but_unrecorded(cache, root, tmp_path, monkeypatch, location):
    kind = _kind(cache, monkeypatch)
    path = root / "Realm.md"
    target = tmp_path / "Mara.md"
    target.write_bytes(b"Mara")
    if location == "outside":
        path = target
    elif location == "linked":
        try:
            path.symlink_to(target)
        except OSError:
            pytest.skip("symlink creation unavailable")
    else:
        path.write_bytes(b"Mara")
        original = cache._source_info
        monkeypatch.setattr(cache, "_source_info", lambda *args: replace(original(*args), device=-1))
    _aged(cache, root, monkeypatch)
    with cache.batch(root) as request:
        result = request.validate_one(kind, cache.Input("a", path))
        assert result.data == b"Mara" and result.content_hash == _digest(b"Mara")
        assert request.known_hashes(kind, [path]) == {path: None}
        assert not request._sources


def test_false_source_policy_never_selects_existing_rows(cache, root, monkeypatch):
    kind = replace(_kind(cache, monkeypatch), allow_persisted_sources=False)
    monkeypatch.setitem(cache.KINDS, kind.name, kind)
    path = root / "Realm.md"
    path.write_bytes(b"Realm")
    _aged(cache, root, monkeypatch)

    def forbidden(*args):
        raise AssertionError("decision policy selected source rows")

    monkeypatch.setattr(cache.Batch, "_source_rows", forbidden)
    with cache.batch(root) as request:
        assert request.validate_one(kind, cache.Input("a", path)).data == b"Realm"
        assert request.known_hashes(kind, [path]) == {path: None}
        assert not request._sources


def test_damaged_and_fresh_rows_are_probe_misses(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    path = root / "Realm.md"
    path.write_bytes(b"Realm")
    _aged(cache, root, monkeypatch)
    with cache.batch(root) as request:
        request.validate_one(kind, cache.Input("a", path))
    conn = _open(cache, root).conn
    conn.execute('UPDATE sources SET "check"=? WHERE path=?', ("a" * 64, "Realm.md"))
    with cache.batch(root) as request:
        assert request.known_hashes(kind, [path]) == {path: None}
        assert request.validate_one(kind, cache.Input("a", path)).data == b"Realm"
    now = max(path.stat().st_mtime_ns, path.stat().st_ctime_ns)
    monkeypatch.setattr(cache, "filesystem_clock", lambda root: cache.FilesystemClock(now, root.stat().st_dev))
    with cache.batch(root) as request:
        assert request.known_hashes(kind, [path]) == {path: None}
        assert request.validate_one(kind, cache.Input("a", path)).data == b"Realm"
        assert not request._sources


def test_restored_mtime_rewrite_invalidates_source_via_ctime(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    path = root / "Realm.md"
    path.write_bytes(b"Realm")
    _aged(cache, root, monkeypatch)
    before = path.stat()
    changed_before = cache._source_info(root, path).stamp[2]
    with cache.batch(root) as request:
        request.validate_one(kind, cache.Input("a", path))
    path.write_bytes(b"Mara!")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    if cache._source_info(root, path).stamp[2] == changed_before:
        pytest.skip("filesystem metadata-change tick did not advance")
    with cache.batch(root) as request:
        assert request.validate_one(kind, cache.Input("a", path)).content_hash == _digest(b"Mara!")


def test_remote_windows_provider_cannot_authorize_existing_source_row(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    path = root / "Realm.md"
    path.write_bytes(b"Realm")
    provider = cache.filesystem_clock
    _aged(cache, root, monkeypatch)
    with cache.batch(root) as request:
        request.validate_one(kind, cache.Input("a", path))
    monkeypatch.setattr(cache, "filesystem_clock", provider)
    monkeypatch.setattr(cache, "_WINDOWS_CLOCK", True)

    def volume(path, buffer, size):
        buffer.value = "C:/"
        return 1

    monkeypatch.setattr(cache, "_kernel32", SimpleNamespace(GetVolumePathNameW=volume, GetDriveTypeW=lambda path: 4))
    monkeypatch.setattr(cache.time, "time_ns", lambda: max(path.stat().st_mtime_ns, path.stat().st_ctime_ns) + 10**10)

    def forbidden(*args):
        raise AssertionError("remote client clock authorized a shortcut")

    monkeypatch.setattr(cache.os, "utime", forbidden)
    monkeypatch.setattr(cache.Batch, "_source_rows", forbidden)
    with cache.batch(root) as request:
        result = request.validate_one(kind, cache.Input("a", path))
        assert result.data == b"Realm" and result.content_hash == _digest()
        assert request.known_hashes(kind, [path]) == {path: None}
        assert not request._sources


def test_failed_containment_probe_cannot_reuse_pre_disappearance_source_stamp(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    path = root / "Realm.md"
    path.write_bytes(b"Realm")
    _aged(cache, root, monkeypatch)
    with cache.batch(root) as request:
        request.validate_one(kind, cache.Input("a", path))
    original = Path.lstat

    def disappearing(p, *args, **kw):
        if p == path:
            raise FileNotFoundError("vanished after stat")
        return original(p, *args, **kw)

    monkeypatch.setattr(Path, "lstat", disappearing)
    with cache.batch(root) as request:
        assert request.known_hashes(kind, [path]) == {path: None}
        assert request.validate_one(kind, cache.Input("a", path)).data == b"Realm"
        assert not request._sources


def test_parent_traversal_never_proves_source_containment(cache, root, monkeypatch):
    path = root / "Realm" / ".." / "Mara.md"
    (root / "Realm").mkdir()
    (root / "Mara.md").write_bytes(b"Mara")
    info = cache._source_info(root, path)
    assert path.read_bytes() == b"Mara"
    assert not cache._eligible(info, cache.FilesystemClock(max(info.stamp[1:3]) + 10**10, info.device))


@pytest.mark.parametrize("column,index", [("mtime_ns", 1), ("ctime_ns", 2), ("size", 3)])
def test_source_integer_fields_are_bounded_before_rows_enter_python(cache, root, monkeypatch, column, index):
    _kind(cache, monkeypatch)
    _seed(cache, root)
    conn = _open(cache, root).conn
    conn.execute(f'UPDATE sources SET "{column}"=? WHERE path=?', (b"x" * cache.MAX_PAYLOAD_BYTES, "Realm.md"))
    with cache.batch(root) as request:
        row = request._source_rows(["Realm.md"])["Realm.md"]
    assert row[index] is None
    assert cache._decode_source("Realm.md", row) is None


def test_actual_disappearance_during_containment_aborts_staged_artifacts(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    path = root / "Realm.md"
    path.write_bytes(b"Realm")
    _aged(cache, root, monkeypatch)
    with cache.batch(root) as request:
        request.validate_one(kind, cache.Input("a", path))
    original = Path.lstat

    def disappearing(p, *args, **kw):
        if p == path:
            path.unlink()
            raise FileNotFoundError("vanished after stat")
        return original(p, *args, **kw)

    monkeypatch.setattr(Path, "lstat", disappearing)
    key = cache.make_key(kind, [("a", _digest())], params={"stem": "realm", "settings": {}})
    with cache.batch(root) as request:
        request.put(key, "Mara")
        with pytest.raises(cache.InputUnavailable):
            request.validate_one(kind, cache.Input("a", path))
        assert request.disabled and not request._rows and not request._sources


@pytest.mark.skipif(os.name == "nt", reason="POSIX resolves a symlink before parent traversal")
def test_parent_traversal_through_link_never_trusts_an_unrelated_local_file(cache, root, tmp_path):
    external = tmp_path / "Saltmarch"
    (external / "Realm").mkdir(parents=True)
    (external / "Mara.md").write_bytes(b"Mara")
    (root / "Mara.md").write_bytes(b"Realm")
    (root / "Realm").symlink_to(external / "Realm", target_is_directory=True)
    path = root / "Realm" / ".." / "Mara.md"
    assert path.read_bytes() == b"Mara"
    info = cache._source_info(root, path)
    assert not cache._eligible(info, cache.FilesystemClock(max(info.stamp[1:3]) + 10**10, info.device))


@pytest.mark.parametrize("filesystem", [0xFF534D42, 0x6969, 0x65735546, 0x794C7630, 0])
def test_posix_unproven_filesystem_clock_is_refused_before_touch(cache, root, monkeypatch, filesystem):
    _kind(cache, monkeypatch)
    monkeypatch.setattr(cache, "_WINDOWS_CLOCK", False)
    monkeypatch.setattr(cache, "_LINUX_CLOCK", True, raising=False)
    monkeypatch.setattr(cache, "os", SimpleNamespace(**{**vars(os), "name": "posix"}))

    def statfs(path, output):
        output[0] = filesystem
        return 0

    monkeypatch.setattr(cache, "_libc", SimpleNamespace(statfs=statfs), raising=False)

    def forbidden(*args):
        raise AssertionError("unproven POSIX clock touched sentinel")

    monkeypatch.setattr(cache.os, "utime", forbidden)
    assert cache.filesystem_clock(root) is None


@pytest.mark.parametrize("filesystem", [0xEF53, 0x9123683E, 0x58465342, 0x01021994, 0xF2F52010])
def test_linux_local_filesystem_type_is_sampled_from_final_path(cache, root, monkeypatch, filesystem):
    monkeypatch.setattr(cache, "_WINDOWS_CLOCK", False)
    monkeypatch.setattr(cache, "_LINUX_CLOCK", True, raising=False)
    monkeypatch.setattr(cache, "os", SimpleNamespace(**{**vars(os), "name": "posix"}))
    captured = []

    def statfs(path, output):
        captured.append(path)
        output[0] = filesystem
        return 0

    monkeypatch.setattr(cache, "_libc", SimpleNamespace(statfs=statfs), raising=False)
    assert cache._clock_supported(root / "Realm.md")
    assert captured == [os.fsencode(root.resolve())]


def test_posix_remote_clock_cannot_reuse_matching_device_sources(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    path = root / "Realm.md"
    path.write_bytes(b"Realm")
    provider = cache.filesystem_clock
    _aged(cache, root, monkeypatch)
    with cache.batch(root) as request:
        request.validate_one(kind, cache.Input("a", path))
    monkeypatch.setattr(cache, "filesystem_clock", provider)
    monkeypatch.setattr(cache, "_WINDOWS_CLOCK", False)
    monkeypatch.setattr(cache, "_LINUX_CLOCK", True, raising=False)
    monkeypatch.setattr(cache, "os", SimpleNamespace(**{**vars(os), "name": "posix"}))

    def statfs(path, output):
        output[0] = 0xFF534D42
        return 0

    monkeypatch.setattr(cache, "_libc", SimpleNamespace(statfs=statfs), raising=False)
    monkeypatch.setattr(cache.time, "time_ns", lambda: max(path.stat().st_mtime_ns, path.stat().st_ctime_ns) + 10**10)

    def forbidden(*args):
        raise AssertionError("remote POSIX clock authorized source reuse")

    monkeypatch.setattr(cache.os, "utime", forbidden)
    monkeypatch.setattr(cache.Batch, "_source_rows", forbidden)
    with cache.batch(root) as request:
        assert request.validate_one(kind, cache.Input("a", path)).data == b"Realm"
        assert request.known_hashes(kind, [path]) == {path: None}
        assert not request._sources


@pytest.mark.parametrize("failure", ["missing", "api", "exception", "unsupported_host"])
def test_posix_clock_probe_failures_opt_out(cache, root, monkeypatch, failure):
    monkeypatch.setattr(cache, "_WINDOWS_CLOCK", False)
    monkeypatch.setattr(cache, "_LINUX_CLOCK", failure != "unsupported_host")

    def statfs(*args):
        if failure == "exception":
            raise OSError("unavailable")
        return -1

    monkeypatch.setattr(cache, "_libc", None if failure == "missing" else SimpleNamespace(statfs=statfs))
    assert not cache._clock_supported(root)


def test_existing_clock_leaf_is_classified_instead_of_its_parent(cache, root, monkeypatch):
    _kind(cache, monkeypatch)
    sentinel = cache._clock_path(root)
    sentinel.write_bytes(b"Mara")
    before = sentinel.stat()
    monkeypatch.setattr(cache, "_WINDOWS_CLOCK", False)
    monkeypatch.setattr(cache, "_LINUX_CLOCK", True)
    queried = []

    def statfs(path, output):
        queried.append(path)
        output[0] = 0xFF534D42 if path == os.fsencode(sentinel.resolve()) else 0xEF53
        return 0

    monkeypatch.setattr(cache, "_libc", SimpleNamespace(statfs=statfs))
    assert cache.filesystem_clock(root) is None
    assert queried == [os.fsencode(sentinel.resolve())]
    after = sentinel.stat()
    assert sentinel.read_bytes() == b"Mara"
    assert (before.st_mtime_ns, before.st_ctime_ns) == (after.st_mtime_ns, after.st_ctime_ns)


@pytest.mark.parametrize('persist', [True, False])
def test_unavailable_metadata_keeps_contained_recursive_collection_live(cache, root, monkeypatch, persist):
    kind = _kind(cache, monkeypatch)
    kind = replace(kind, allow_persisted_sources=persist)
    monkeypatch.setitem(cache.KINDS, kind.name, kind)
    directory = root / 'Saltmarch'
    directory.mkdir()
    (directory / 'Realm.md').write_bytes(b'Mara')
    monkeypatch.setattr(cache.file_stats, 'change_ns', lambda *args: None)
    _aged(cache, root, monkeypatch)
    with cache.batch(root) as request:
        result = request.collection(kind, cache.Collection('body', directory, recursive=True), with_bytes=True)
        assert [member.data for member in result.members] == [b'Mara']
        assert not request._sources


@pytest.mark.skipif(os.name != 'nt', reason='Windows native metadata provenance')
def test_same_serial_unsupported_source_metadata_stays_untrusted(cache, root, monkeypatch):
    if cache.file_stats._BY_NAME is None:
        pytest.skip('native by-name metadata unavailable')
    first, second = root / 'Realm.md', root / 'Saltmarch.md'
    first.write_bytes(b'Mara')
    second.write_bytes(b'Mara')
    assert first.stat().st_dev == second.stat().st_dev
    monkeypatch.setattr(cache.file_stats, 'windows_change_supported', lambda path, device: path == first)
    assert cache._source_info(root, first).metadata_trusted
    assert not cache._source_info(root, second).metadata_trusted


@pytest.mark.parametrize('failure', ['row', 'size'])
def test_optional_source_admission_failure_preserves_validated_answer(cache, root, monkeypatch, failure):
    kind = _kind(cache, monkeypatch)
    path = root / 'Realm.md'
    path.write_bytes(b'Realm')
    _aged(cache, root, monkeypatch)
    class Refused(dict):
        def __setitem__(self, key, value):
            raise MemoryError('optional source admission allocation')
    with cache.batch(root) as request:
        if failure == 'row':
            request._sources = Refused()
        else:
            request._source_sizes = Refused()
        validated = request.validate(kind, [cache.Input('document', path)])
        assert validated[0].data == b'Realm' and validated[0].content_hash == _digest()
        assert not request._sources and not request._source_sizes and request._bytes == 0
        found = request.derive(kind, [cache.Input('document', path)],
                               params={'stem': 'realm', 'settings': {}},
                               compute=lambda view, params: view.files[0].data.decode())
        assert found == 'Realm'
    with cache.batch(root) as request:
        assert not request._source_rows(['Realm.md'])


@pytest.mark.parametrize('memo', ['source', 'history'])
def test_optional_read_memo_failure_preserves_validated_derivation(cache, root, monkeypatch, memo):
    kind = _kind(cache, monkeypatch)
    path = root / 'Realm.md'
    path.write_bytes(b'Realm')
    _aged(cache, root, monkeypatch)
    with cache.batch(root) as request:
        request.validate(kind, [cache.Input('document', path)])
        request.mark_materialized(path, kind.name, built_from=_digest())
    class Refused(dict):
        def __setitem__(self, key, value):
            raise MemoryError('optional read-side metadata memo')
    discarded = cache_tests._key(cache, kind, 'Mara')
    with cache.batch(root) as request:
        request.put(discarded, 'Winifred')
        if memo == 'source':
            request._source_known = Refused()
        else:
            request._history_known = Refused()
        validated = request.validate(kind, [cache.Input('document', path)])
        assert validated[0].content_hash == _digest()
        assert discarded not in request._rows
        answer = request.derive(kind, [cache.Input('document', path)],
                                params={'stem': 'realm', 'settings': {}},
                                compute=lambda view, params: view.files[0].data.decode())
        assert answer == 'Realm'
    with cache.batch(root) as request:
        assert not request.lookup(discarded).hit
