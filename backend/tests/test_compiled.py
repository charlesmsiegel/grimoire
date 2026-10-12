"""Owned, disposable SQLite files (spec 03 S1).

Drive the private open seam until Batch exists. Schema and containment are
observable contracts; an external link target must never change even when a
cache operation fails. No real library or machine-local device id is used.
"""

import contextlib
import hashlib
import importlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

from grimoire import compiled_build
from grimoire.store import maintenance_reports, proclock


@pytest.fixture
def cache(monkeypatch):
    if importlib.util.find_spec("grimoire.store.compiled") is None:
        yield None
        return
    module = importlib.import_module("grimoire.store.compiled")
    monkeypatch.setattr(module, "_state", module._State())
    monkeypatch.setattr(maintenance_reports, "device_key", lambda root: "a" * 64)
    monkeypatch.delenv("GRIMOIRE_COMPILED_CACHE", raising=False)
    yield module
    module.close()
    module._release_intents()  # tests explicitly abandon any mocked delete owner


@pytest.fixture
def root(tmp_path):
    root = tmp_path / "home"
    root.mkdir()
    return root


def _open(cache, root):
    assert cache is not None, "the owned compiled-cache module is missing"
    with cache._state.lock:
        return cache._open_locked(root)


def _path(cache, root):
    assert cache is not None, "the owned compiled-cache module is missing"
    return root / ".cache" / "compiled" / ("a" * 32 + f"-v{cache.SCHEMA}.sqlite")


def _stamp(path):
    info = path.stat()
    return info.st_mtime_ns, info.st_ctime_ns, info.st_size, info.st_ino


def _forbid_connect(cache, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("unsafe cache path reached SQLite")
    monkeypatch.setattr(cache._sqlite, "connect", forbidden)


def test_owned_filename_schema_and_pragmas(cache, root):
    assert cache is not None, "the owned compiled-cache module is missing"
    with cache._state.lock:
        session = cache._open_locked(root)
        assert session is not None
        assert session.path == _path(cache, root)
        assert session.pid == os.getpid() and session.root == root
        assert session.lock is cache._state.lock
        assert session.conn.execute("PRAGMA application_id").fetchone()[0] == 0x4752494D
        assert dict(session.conn.execute("SELECT name,value FROM cache_meta")) == {
            "schema": str(cache.SCHEMA), "purge_generation": ""}
        columns = session.conn.execute("PRAGMA table_info(artifacts)").fetchall()
        assert [(row[1], row[2], row[3], row[5]) for row in columns] == [
            ("kind", "TEXT", 1, 1), ("key_digest", "TEXT", 1, 2),
            ("payload", "TEXT", 1, 0), ("check", "TEXT", 1, 0),
            ("last_used", "INTEGER", 1, 0)]
        for pragma, value in {"journal_mode": "delete", "auto_vacuum": 2,
                              "secure_delete": 1, "synchronous": 1,
                              "busy_timeout": 5}.items():
            assert session.conn.execute("PRAGMA " + pragma).fetchone()[0] == value


def test_device_identity_is_memoized_per_process_and_root(cache, root, tmp_path, monkeypatch):
    seen = []

    def device_key(path):
        seen.append(path)
        return "a" * 64

    monkeypatch.setattr(maintenance_reports, "device_key", device_key)
    first = _open(cache, root)
    assert first is not None and _open(cache, root) is first
    cache.close()
    assert _open(cache, root) is not None
    other = tmp_path / "other"
    other.mkdir()
    assert _open(cache, other) is not None
    assert seen == [root, other]


def test_device_failure_disables_attempts_for_the_process(cache, root, tmp_path, monkeypatch):
    seen = []

    def unavailable(path):
        seen.append(path)
        raise OSError("device identity unavailable")

    monkeypatch.setattr(maintenance_reports, "device_key", unavailable)
    assert _open(cache, root) is None
    assert _open(cache, root) is None
    assert _open(cache, tmp_path / "other") is None
    assert seen == [root]
    assert not (root / ".cache").exists()


def test_machine_lock_floor_survives_monkeypatch_undo(monkeypatch, tmp_path_factory):
    monkeypatch.undo()
    assert tmp_path_factory.getbasetemp() in proclock.lock_dir().parents
    maintenance_reports.device_id()
    assert (proclock.lock_dir() / maintenance_reports.DEVICE_ID_FILE).is_file()


@pytest.mark.parametrize("component", [".cache", "compiled", "database", "journal"])
def test_linked_cache_components_preserve_external_targets(cache, root, tmp_path, component,
                                                          monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "realm.txt"
    marker.write_bytes(b"Realm")
    path = _path(cache, root)
    if component == ".cache":
        link, target, directory = root / ".cache", outside, True
    elif component == "compiled":
        (root / ".cache").mkdir()
        link, target, directory = path.parent, outside, True
    else:
        path.parent.mkdir(parents=True)
        link = path if component == "database" else Path(str(path) + "-journal")
        target, directory = marker, False
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError:
        pytest.skip("symlink creation unavailable; reparse and junction cases still run")
    before = _stamp(marker)
    _forbid_connect(cache, monkeypatch)
    assert _open(cache, root) is None
    assert marker.read_bytes() == b"Realm" and _stamp(marker) == before
    assert list(outside.iterdir()) == [marker]


def test_reparse_attributes_disable_before_open(cache, root, monkeypatch):
    parent = root / ".cache"
    parent.mkdir()
    lstat = Path.lstat

    def reparse(path, *args, **kwargs):
        result = lstat(path, *args, **kwargs)
        if path == parent:
            return types.SimpleNamespace(st_mode=result.st_mode, st_file_attributes=0x400)
        return result

    monkeypatch.setattr(Path, "lstat", reparse)
    _forbid_connect(cache, monkeypatch)
    assert _open(cache, root) is None
    assert not (parent / "compiled").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junction containment")
def test_actual_windows_junction_preserves_external_target(cache, root, tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "realm.txt"
    marker.write_bytes(b"Realm")
    link = root / ".cache"
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                            check=False, capture_output=True, text=True)
    if result.returncode:
        pytest.skip("junction creation unavailable")
    before = _stamp(marker)
    _forbid_connect(cache, monkeypatch)
    try:
        assert _open(cache, root) is None
        assert marker.read_bytes() == b"Realm" and _stamp(marker) == before
        assert list(outside.iterdir()) == [marker]
    finally:
        os.rmdir(link)  # remove the junction itself, never recurse into its target


@pytest.mark.parametrize("application_id", [0, 4321])
def test_foreign_database_is_preserved(cache, root, application_id):
    path = _path(cache, root)
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as conn:
        conn.execute(f"PRAGMA application_id={application_id}")
        conn.execute("CREATE TABLE foreign_data(value TEXT)")
        conn.execute("INSERT INTO foreign_data VALUES ('Realm')")
    conn.close()
    before = path.read_bytes(), _stamp(path)
    assert _open(cache, root) is None
    assert (path.read_bytes(), _stamp(path)) == before


def test_unknown_owned_schema_is_preserved(cache, root):
    session = _open(cache, root)
    assert session is not None
    session.conn.execute("UPDATE cache_meta SET value='99' WHERE name='schema'")
    session.conn.commit()
    cache.close()
    before = session.path.read_bytes(), _stamp(session.path)
    assert _open(cache, root) is None
    assert (session.path.read_bytes(), _stamp(session.path)) == before


def _kind(cache, monkeypatch, *, stable_build=True):
    assert hasattr(cache, "Kind"), "the registered artifact-key API is missing"
    kind = cache.Kind("search", 1, ("stem",), ("stem", "settings"), True)
    monkeypatch.setitem(cache.KINDS, kind.name, kind)
    if stable_build:
        monkeypatch.setattr(cache, "_build", lambda: "b" * 64)
    return kind


def _key(cache, kind, stem="realm"):
    key = cache.make_key(kind, [("document", "a" * 64)],
                         params={"stem": stem, "settings": {"show": True}})
    assert key is not None
    return key


@pytest.mark.parametrize("value", [None, False, True, 0, 1, 1.5, "", [], {}, "Realm"])
def test_json_payloads_are_hits_including_null(cache, root, monkeypatch, value):
    key = _key(cache, _kind(cache, monkeypatch))
    with cache.batch(root) as request:
        assert not request.lookup(key).hit
        request.put(key, value)
    with cache.batch(root) as request:
        found = request.lookup(key)
    assert found.hit and found.value == value


def test_mutable_hits_are_fresh_copies(cache, root, monkeypatch):
    key = _key(cache, _kind(cache, monkeypatch))
    value = {"characters": [{"name": "Mara"}]}
    with cache.batch(root) as request:
        request.put(key, value)
    value["characters"][0]["name"] = "Winifred"
    with cache.batch(root) as request:
        first = request.lookup(key)
        first.value["characters"][0]["name"] = "Seraphine"
        second = request.lookup(key)
    assert second.value == {"characters": [{"name": "Mara"}]}


def test_key_identity_is_canonical_and_complete(cache, monkeypatch):
    kind = _kind(cache, monkeypatch)
    inputs = [("document", "a" * 64), ("style", "c" * 64)]
    params = {"stem": "realm", "settings": {"show": True, "order": [1, 2]}}
    key = cache.make_key(kind, inputs, params=params)
    same = cache.make_key(kind, inputs, params={
        "settings": {"order": [1, 2], "show": True}, "stem": "realm"})
    assert key == same
    assert cache.make_key(kind, inputs[::-1], params=params) != key
    assert cache.make_key(kind, [("document", "d" * 64), inputs[1]], params=params) != key
    assert cache.make_key(kind, inputs, params={**params, "stem": "saltmarch"}) != key
    newer = cache.Kind("search", 2, ("stem",), ("stem", "settings"), True)
    monkeypatch.setitem(cache.KINDS, newer.name, newer)
    assert cache.make_key(newer, inputs, params=params) != key
    monkeypatch.setitem(cache.KINDS, kind.name, kind)
    monkeypatch.setattr(cache, "_build", lambda: "c" * 64)
    assert cache.make_key(kind, inputs, params=params) != key


def test_invalid_or_unregistered_keys_are_uncacheable(cache, monkeypatch):
    kind = _kind(cache, monkeypatch)
    assert cache.make_key(kind, [], params={"stem": "realm"}) is None
    assert cache.make_key(kind, [], params={"stem": "realm", "settings": {}, "extra": 1}) is None
    assert cache.make_key(kind, [("document", "not a digest")],
                          params={"stem": "realm", "settings": {}}) is None
    monkeypatch.delitem(cache.KINDS, kind.name)
    assert cache.make_key(kind, [], params={"stem": "realm", "settings": {}}) is None


def test_build_fingerprint_is_memoized_once(cache, monkeypatch):
    kind = _kind(cache, monkeypatch, stable_build=False)
    seen = []

    def fingerprint(*args, **kwargs):
        seen.append((args, kwargs))
        return "b" * 64

    monkeypatch.setattr(compiled_build, "fingerprint", fingerprint)
    assert _key(cache, kind) == _key(cache, kind)
    cache.close()
    assert _key(cache, kind) is not None
    assert len(seen) == 1


@pytest.mark.parametrize("value", [set(), (1, 2), {1: "Realm"}, float("nan"), float("inf")],
                         ids=["set", "tuple", "integer-keys", "nan", "infinity"])
def test_non_json_values_are_uncached(cache, root, monkeypatch, value):
    key = _key(cache, _kind(cache, monkeypatch))
    with cache.batch(root) as request:
        request.put(key, value)
    with cache.batch(root) as request:
        assert not request.lookup(key).hit


def test_one_transaction_coalesces_staged_rows(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    keys = [_key(cache, kind, stem) for stem in ("realm", "saltmarch")]
    session = _open(cache, root)
    statements = []
    session.conn.set_trace_callback(statements.append)
    with cache.batch(root) as request:
        assert not cache._state.lock.locked(), "caller computation held the SQLite lock"
        request.put(keys[0], "Mara")
        request.put(keys[0], "Winifred")
        request.put(keys[1], "Seraphine")
    assert sum(s == "BEGIN IMMEDIATE" for s in statements) == 1
    assert sum(s == "COMMIT" for s in statements) == 1
    with cache.batch(root) as request:
        assert request.lookup(keys[0]).value == "Winifred"
        assert request.lookup(keys[1]).value == "Seraphine"


def test_caller_exception_drops_staging_and_is_not_masked(cache, root, monkeypatch):
    key = _key(cache, _kind(cache, monkeypatch))
    with pytest.raises(ValueError, match="reader failed"), cache.batch(root) as request:
        request.put(key, "Realm")
        raise ValueError("reader failed")
    with cache.batch(root) as request:
        assert not request.lookup(key).hit


@pytest.mark.parametrize("damage", ["altered", "malformed", "swapped-key"])
def test_damaged_json_or_key_costs_a_miss(cache, root, monkeypatch, damage):
    kind = _kind(cache, monkeypatch)
    key = _key(cache, kind)
    with cache.batch(root) as request:
        request.put(key, "Mara")
    with cache._state.lock:
        conn = cache._state.session.conn
        if damage == "swapped-key":
            other = _key(cache, kind, "saltmarch")
            conn.execute("UPDATE artifacts SET key_digest=? WHERE key_digest=?",
                         (other.digest, key.digest))
            key = other
        else:
            payload = '"Winifred"' if damage == "altered" else "not JSON"
            conn.execute("UPDATE artifacts SET payload=? WHERE key_digest=?", (payload, key.digest))
        conn.commit()
    with cache.batch(root) as request:
        assert not request.lookup(key).hit
        request.put(key, "Realm")
    with cache.batch(root) as request:
        assert request.lookup(key).value == "Realm"


def _seed_payload(cache, root, key, payload):
    session = _open(cache, root)
    cache.close()
    check = hashlib.sha256(json.dumps([key.kind, key.digest, payload], sort_keys=True,
        separators=(",", ":"), ensure_ascii=True).encode()).hexdigest() if isinstance(payload, str) else "x"
    # An external connection bypasses the cache's allocation limit, like a
    # damaged or edited database. The reader must reject this before loading it.
    with sqlite3.connect(session.path) as conn:
        conn.execute('INSERT INTO artifacts VALUES (?,?,?,?,?)',
                     (key.kind, key.digest, payload, check, 0))
    conn.close()


@pytest.mark.parametrize("damage", ["oversized-text", "sqlite-too-big", "blob", "deep-json"])
def test_hostile_rows_are_bounded_misses(cache, root, monkeypatch, damage):
    key = _key(cache, _kind(cache, monkeypatch))
    assert hasattr(cache, "MAX_PAYLOAD_BYTES"), "artifact allocation bounds are missing"
    if damage == "deep-json":
        payload = "[" * (cache.MAX_JSON_DEPTH + 1) + "0" + "]" * (cache.MAX_JSON_DEPTH + 1)
    elif damage == "blob":
        payload = b"Realm"
    else:
        size = cache.MAX_PAYLOAD_BYTES * (3 if damage == "sqlite-too-big" else 1)
        payload = json.dumps("x" * size)
    _seed_payload(cache, root, key, payload)
    with cache.batch(root) as request:
        found = request.lookup(key)
        answer = found.value if found.hit else "Realm"
    assert answer == "Realm"
    assert not Path(str(_path(cache, root)) + ".corrupt").exists()


@pytest.mark.parametrize("shape", ["unicode", "wide", "cyclic", "deep"])
def test_serialization_work_is_bounded(cache, root, monkeypatch, shape):
    key = _key(cache, _kind(cache, monkeypatch))
    assert hasattr(cache, "MAX_PAYLOAD_BYTES"), "artifact serialization bounds are missing"
    if shape == "unicode":
        value = "☃" * (cache.MAX_PAYLOAD_BYTES // 5)
    elif shape == "wide":
        value = [0] * (cache.MAX_JSON_ITEMS + 1)
    elif shape == "cyclic":
        value = []
        value.append(value)
    else:
        value = 0
        for _ in range(cache.MAX_JSON_DEPTH + 1):
            value = [value]
    with cache.batch(root) as request:
        request.put(key, value)
    with cache.batch(root) as request:
        assert not request.lookup(key).hit


def test_staging_row_budget_keeps_one_request_transaction(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    assert hasattr(cache, "MAX_STAGED_ROWS"), "request staging bounds are missing"
    keys = [_key(cache, kind, f"realm-{i}") for i in range(cache.MAX_STAGED_ROWS + 1)]
    session = _open(cache, root)
    statements = []
    session.conn.set_trace_callback(statements.append)
    with cache.batch(root) as request:
        for key in keys:
            request.put(key, "Realm")
        with cache.batch(root) as reader:
            assert not reader.lookup(keys[0]).hit
    assert sum(s == "BEGIN IMMEDIATE" for s in statements) == 1
    with cache.batch(root) as reader:
        assert all(reader.lookup(key).value == "Realm" for key in keys[:-1])
        assert not reader.lookup(keys[-1]).hit


def test_impossible_key_size_is_rejected_before_iteration(cache, monkeypatch):
    kind = _kind(cache, monkeypatch)

    class TooManyInputs:
        def __len__(self):
            return cache.MAX_KEY_BYTES

        def __iter__(self):
            raise AssertionError("an impossible key copied its input sequence")

    assert cache.make_key(kind, TooManyInputs(), params={"stem": "realm", "settings": {}}) is None


def test_staging_byte_budget_drops_excess_rows(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    value = "x" * (cache.MAX_PAYLOAD_BYTES - 32)
    count = cache.MAX_STAGED_BYTES // len(json.dumps(value)) + 1
    assert count < cache.MAX_STAGED_ROWS
    keys = [_key(cache, kind, f"realm-{i}") for i in range(count)]
    with cache.batch(root) as request:
        for key in keys:
            request.put(key, value)
        with cache.batch(root) as reader:
            assert not reader.lookup(keys[0]).hit
    with cache.batch(root) as reader:
        assert all(reader.lookup(key).value == value for key in keys[:-1])
        assert not reader.lookup(keys[-1]).hit


@pytest.mark.parametrize("disabled", ["kill", "sqlite", "build"])
def test_disabled_cache_creates_no_cache_directory(cache, root, monkeypatch, disabled):
    kind = _kind(cache, monkeypatch)
    key = _key(cache, kind)
    if disabled == "kill":
        monkeypatch.setenv("GRIMOIRE_COMPILED_CACHE", "0")
    elif disabled == "sqlite":
        monkeypatch.setattr(cache, "_sqlite", None)
    else:
        monkeypatch.setattr(cache, "_build", lambda: None)
    with cache.batch(root) as request:
        assert not request.lookup(key).hit
        request.put(key, "Realm")
    assert not (root / ".cache").exists()


def test_read_and_write_do_not_wait_for_connection_lock(cache, root, monkeypatch):
    key = _key(cache, _kind(cache, monkeypatch))
    with cache._state.lock:
        start = time.monotonic()
        with cache.batch(root) as request:
            assert not request.lookup(key).hit
            request.put(key, "Realm")
        assert time.monotonic() - start < 0.5
    assert not (root / ".cache").exists()


def test_busy_database_preserves_file_and_drops_write(cache, root, monkeypatch):
    key = _key(cache, _kind(cache, monkeypatch))
    session = _open(cache, root)
    with contextlib.closing(sqlite3.connect(session.path, isolation_level=None)) as other:
        other.execute("BEGIN EXCLUSIVE")
        before = session.path.read_bytes()
        with cache.batch(root) as request:
            assert not request.lookup(key).hit
            request.put(key, "Realm")
        assert session.path.read_bytes() == before
        assert not Path(str(session.path) + ".corrupt").exists()
        other.rollback()
    with cache.batch(root) as request:
        assert not request.lookup(key).hit


def test_failed_commit_rolls_back_and_never_masks_caller(cache, root, monkeypatch):
    key = _key(cache, _kind(cache, monkeypatch))
    session = _open(cache, root)
    actual = session.conn

    class FailedCommit:
        def __getattr__(self, name):
            return getattr(actual, name)

        def commit(self):
            raise sqlite3.OperationalError("commit unavailable")

    session.conn = FailedCommit()
    with cache.batch(root) as request:
        request.put(key, "Realm")
    assert not actual.in_transaction
    with cache.batch(root) as request:
        assert not request.lookup(key).hit


def test_unreadable_cache_and_warning_sink_are_fail_soft(cache, root, monkeypatch):
    key = _key(cache, _kind(cache, monkeypatch))
    actual = cache._directory

    def unreadable(path):
        raise PermissionError("private path and payload")

    monkeypatch.setattr(cache, "_directory", unreadable)
    warnings = []
    monkeypatch.setattr(cache.logs, "record", lambda *args, **fields: warnings.append((args, fields)))
    for _ in range(3):
        with cache.batch(root) as request:
            assert not request.lookup(key).hit
            request.put(key, "Realm")
    assert warnings == [(("warning", "store.compiled", "Compiled cache unavailable"),
                         {"failure_kind": "storage"})]
    monkeypatch.setattr(cache, "_directory", actual)
    monkeypatch.setattr(cache.logs, "record", unreadable)
    cache._warn_once("another_failure")


@pytest.mark.parametrize("code", [sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED,
                                 sqlite3.SQLITE_READONLY, sqlite3.SQLITE_TOOBIG, None])
def test_noncorruption_errors_preserve_database_and_journal(cache, root, monkeypatch, code):
    session = _open(cache, root)
    path = session.path
    cache.close()
    journal = Path(str(path) + "-journal")
    journal.write_bytes(b"Realm journal")
    before = path.read_bytes(), journal.read_bytes()
    error = sqlite3.DatabaseError("unavailable")
    if code is not None:
        error.sqlite_errorcode = code

    def unavailable(*args, **kwargs):
        raise error

    monkeypatch.setattr(cache._sqlite, "connect", unavailable)
    assert _open(cache, root) is None
    assert (path.read_bytes(), journal.read_bytes()) == before
    assert not Path(str(path) + ".corrupt").exists()


@pytest.mark.parametrize("garbage", [b"Realm invalid database", b"SQLite format 3\0" + b"x" * 80])
def test_unproved_corruption_preserves_main_and_unrelated_aside(cache, root, garbage):
    path = _path(cache, root)
    path.parent.mkdir(parents=True)
    path.write_bytes(garbage)
    unrelated = path.parent / "unrelated.corrupt"
    unrelated.write_bytes(b"Realm")
    # Raw malformed bytes cannot prove this file belongs to our cache. The
    # old test incorrectly licensed destruction from the SQLite error alone.
    assert _open(cache, root) is None
    assert path.read_bytes() == garbage
    assert not Path(str(path) + ".corrupt").exists()
    assert unrelated.read_bytes() == b"Realm"


def test_corruption_recovery_never_waits_for_process_lock(cache, root, monkeypatch):
    path = _path(cache, root)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"Realm invalid database")
    deadlines = []
    monkeypatch.setattr(proclock, "acquire", lambda path, deadline: deadlines.append(deadline))
    assert _open(cache, root) is None
    assert deadlines == [proclock.NO_WAIT]
    assert path.read_bytes() == b"Realm invalid database"
    assert not Path(str(path) + ".corrupt").exists()


@pytest.mark.parametrize("primary", [sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB])
def test_extended_corruption_codes_recover(cache, root, monkeypatch, primary):
    session = _open(cache, root)
    path = session.path
    cache.close()
    actual = cache._sqlite.connect
    calls = []

    def once(*args, **kwargs):
        calls.append(args[0])
        if len(calls) == 1:
            error = sqlite3.DatabaseError("extended corruption")
            error.sqlite_errorcode = primary | (7 << 8)
            raise error
        return actual(*args, **kwargs)

    monkeypatch.setattr(cache._sqlite, "connect", once)
    assert _open(cache, root) is not None
    assert len(calls) >= 2 and cache._state.session.path == path


def test_stale_corruption_error_cannot_replace_another_process_repair(cache, root, monkeypatch):
    key = _key(cache, _kind(cache, monkeypatch))
    with cache.batch(root) as request:
        request.put(key, "Realm")
    session = cache._state.session
    path = session.path
    cache.close()
    before = path.read_bytes(), _stamp(path)
    error = sqlite3.DatabaseError("stale corruption")
    error.sqlite_errorcode = sqlite3.SQLITE_NOTADB
    with cache._state.lock:
        cache._recover(root, path, error)
    assert (path.read_bytes(), _stamp(path)) == before
    with cache.batch(root) as request:
        assert request.lookup(key).value == "Realm"


def test_corruption_during_lookup_closes_bad_handle_before_recovery(cache, root, monkeypatch):
    key = _key(cache, _kind(cache, monkeypatch))
    session = _open(cache, root)
    actual = session.conn
    closed = []

    class DamagedRead:
        def execute(self, *args):
            error = sqlite3.DatabaseError("damaged page")
            error.sqlite_errorcode = sqlite3.SQLITE_CORRUPT
            raise error

        def close(self):
            actual.close()
            closed.append(True)

    session.conn = DamagedRead()
    with cache.batch(root) as request:
        assert not request.lookup(key).hit
        request.put(key, "Realm")
    assert closed == [True]
    with cache.batch(root) as request:
        assert request.lookup(key).value == "Realm"


def test_foreign_hot_journal_is_never_recovered(cache, root):
    path = _path(cache, root)
    path.parent.mkdir(parents=True)
    script = '''
import os,sqlite3,sys
conn=sqlite3.connect(sys.argv[1])
conn.execute('PRAGMA application_id=77')
conn.execute('PRAGMA cache_size=2')
conn.execute('CREATE TABLE foreign_data(value TEXT)')
conn.commit()
conn.execute('BEGIN IMMEDIATE')
conn.executemany('INSERT INTO foreign_data VALUES (?)', [('Realm'*800,)]*200)
os._exit(0)
'''
    subprocess.run([sys.executable, "-c", script, str(path)], check=True, timeout=10)
    journal = Path(str(path) + "-journal")
    assert journal.read_bytes()[:8] == bytes.fromhex("d9d505f920a163d7"), "not a real hot journal"
    before = path.read_bytes(), journal.read_bytes(), _stamp(path), _stamp(journal)
    assert _open(cache, root) is None
    assert journal.exists(), "ownership probe recovered a foreign hot journal"
    assert (path.read_bytes(), journal.read_bytes(), _stamp(path), _stamp(journal)) == before


def test_live_users_hold_recovery_exclusion_through_commit(cache, root, monkeypatch):
    key = _key(cache, _kind(cache, monkeypatch))
    session = _open(cache, root)
    actual = session.conn
    observed = []

    class Commit:
        def __getattr__(self, name):
            return getattr(actual, name)

        def commit(self):
            contender = cache._recovery_lock(root, session.path)
            observed.append(contender)
            if contender is not None:
                proclock.release(contender)
            actual.commit()

    session.conn = Commit()
    with cache.batch(root) as request:
        request.put(key, "Realm")
    assert observed == [None], "a recoverer can rename during an ordinary write"


def test_owned_hot_journal_can_recover_after_immutable_ownership_probe(cache, root, monkeypatch):
    key = _key(cache, _kind(cache, monkeypatch))
    with cache.batch(root) as request:
        request.put(key, "Realm")
    path = cache._state.session.path
    cache.close()
    script = '''
import os,sqlite3,sys
conn=sqlite3.connect(sys.argv[1])
conn.execute('PRAGMA cache_size=2')
conn.execute('BEGIN IMMEDIATE')
conn.execute('UPDATE artifacts SET payload=? WHERE key_digest=?', ('"Winifred"',sys.argv[2]))
conn.executemany('INSERT INTO artifacts VALUES (?,?,?,?,?)',
                 [('search',str(i).zfill(64),'Realm'*800,'x',0) for i in range(200)])
os._exit(0)
'''
    subprocess.run([sys.executable, "-c", script, str(path), key.digest], check=True, timeout=10)
    journal = Path(str(path) + "-journal")
    assert journal.read_bytes()[:8] == bytes.fromhex("d9d505f920a163d7")
    with cache.batch(root) as request:
        assert request.lookup(key).value == "Realm"
    assert not journal.exists()


def test_idle_handle_is_reopened_after_database_replacement(cache, root, tmp_path, monkeypatch):
    key = _key(cache, _kind(cache, monkeypatch))
    with cache.batch(root) as request:
        request.put(key, "Realm")
    old = cache._state.session
    replacement = tmp_path / "replacement.sqlite"
    with contextlib.closing(sqlite3.connect(replacement)) as destination:
        old.conn.backup(destination)
        destination.execute("DELETE FROM artifacts")
        destination.commit()
    try:
        replacement.replace(old.path)
    except PermissionError:
        pytest.skip("platform prohibits replacing an open SQLite file")
    with cache.batch(root) as request:
        assert not request.lookup(key).hit
        request.put(key, "Realm")
    assert cache._state.session is not old
    with pytest.raises(sqlite3.ProgrammingError):
        old.conn.execute("SELECT 1")
    with cache.batch(root) as request:
        assert request.lookup(key).value == "Realm"


@pytest.mark.parametrize("damage", ["checksum", "malformed", "deep-json", "nonfinite", "blob", "oversized"])
def test_damaged_rows_warn_once_but_ordinary_misses_are_silent(cache, root, monkeypatch, damage):
    key = _key(cache, _kind(cache, monkeypatch))
    warnings = []
    monkeypatch.setattr(cache.logs, "record", lambda *args, **fields: warnings.append((args, fields)))
    with cache.batch(root) as request:
        assert not request.lookup(key).hit
    assert warnings == []
    if damage == "deep-json":
        payload = "[" * (cache.MAX_JSON_DEPTH + 1) + "0" + "]" * (cache.MAX_JSON_DEPTH + 1)
    elif damage == "malformed":
        payload = "not JSON"
    elif damage == "nonfinite":
        payload = "NaN"
    elif damage == "blob":
        payload = b"Realm"
    elif damage == "oversized":
        payload = json.dumps("x" * cache.MAX_PAYLOAD_BYTES)
    else:
        payload = '"Realm"'
    _seed_payload(cache, root, key, payload)
    if damage == "checksum":
        session = _open(cache, root)
        with cache._state.lock:
            session.conn.execute('UPDATE artifacts SET "check"=? WHERE kind=? AND key_digest=?',
                                 ("x", key.kind, key.digest))
    for _ in range(3):
        with cache.batch(root) as request:
            found = request.lookup(key)
            assert (found.value if found.hit else "Realm") == "Realm"
            assert not found.hit
    assert warnings == [(("warning", "store.compiled", "Compiled cache unavailable"),
                         {"failure_kind": "artifact"})]
    assert not Path(str(_path(cache, root)) + ".corrupt").exists()


@pytest.mark.parametrize('value', ['Realm ' * 60000, ('"[\\]{}Mara' * 20000), {'Realm': ['[{}]', 'Mara']}],
                         ids=['long_prose', 'quoted_syntax', 'nested'])
def test_large_json_strings_do_not_count_quoted_brackets_as_nesting(cache, root, monkeypatch, value):
    kind = _kind(cache, monkeypatch)
    key = _key(cache, kind)
    with cache.batch(root) as request:
        request.put(key, value)
    with cache.batch(root) as request:
        assert request.lookup(key).value == value


def test_missing_ctypes_keeps_store_importable_and_cache_clock_optional():
    env = {**os.environ, 'PYTHONPATH': str(Path('backend/src').resolve()), 'GRIMOIRE_COMPILED_CACHE': '0'}
    script = "import sys; sys.modules['ctypes']=None; from grimoire.store import compiled; print('Realm')"
    result = subprocess.run([sys.executable, '-c', script], env=env, capture_output=True, text=True,
                            timeout=30, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'Realm'


@pytest.mark.parametrize('mode', ['one', 'many'])
@pytest.mark.parametrize('failure', ['used', 'touch'])
def test_lookup_bookkeeping_allocation_failure_preserves_verified_hit(cache, root, monkeypatch, mode, failure):
    kind = _kind(cache, monkeypatch)
    key = _key(cache, kind)
    with cache.batch(root) as request:
        request.put(key, 'Realm')
    class Refused(dict):
        def __setitem__(self, key, value):
            raise MemoryError('optional lookup bookkeeping')
    with cache.batch(root) as request:
        if failure == 'used':
            request._artifact_used = Refused()
        else:
            monkeypatch.setattr(cache.time, 'time', lambda: 10**12)
            request._artifact_touches = Refused()
        found = request.lookup(key) if mode == 'one' else request.lookup_many([key]).values[key]
        assert found.hit and found.value == 'Realm'
        assert request._bytes == 0
        assert not request._artifact_touches
