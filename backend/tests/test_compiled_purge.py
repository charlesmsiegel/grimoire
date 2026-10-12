"""In-place generations and recoverable delete fences (03-S5)."""

import json
import os
import shutil
import sqlite3
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from grimoire.store import maintenance_reports
from tests import test_compiled as cache_tests

cache = cache_tests.cache
root = cache_tests.root
_kind = cache_tests._kind
_open = cache_tests._open


def _seed(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    key = cache_tests._key(cache, kind)
    with cache.batch(root) as request:
        request.store_written(kind, [cache.WrittenInput('body', root / 'Realm.md', b'Realm')],
                              'Realm', params={'stem': 'realm', 'settings': {}})
        request.put(key, 'Realm')
    return key


def test_delete_purges_in_place_and_preserves_second_connection(cache, root, monkeypatch):
    key = _seed(cache, root, monkeypatch)
    session = _open(cache, root)
    inode = session.path.stat().st_ino
    with sqlite3.connect(session.path) as second:
        cache.purge_for_delete(root)
        for table in ('artifacts', 'sources', 'materialized'):
            assert second.execute('SELECT count(*) FROM ' + table).fetchone() == (0,)
        assert second.execute('SELECT value FROM cache_meta WHERE name=?', ('schema',)).fetchone() == (str(cache.SCHEMA),)
    assert session.path.stat().st_ino == inode
    with cache.batch(root) as request:
        assert not request.lookup(key).hit


@pytest.mark.parametrize('mode', ['marker', 'generation', 'intent'])
def test_put_only_batch_captures_generation_before_staging(cache, root, monkeypatch, mode):
    key = _seed(cache, root, monkeypatch)
    with cache.batch(root) as request:
        request.put(key, 'Mara')
        if mode == 'marker':
            cache.purge_for_delete(root)
        elif mode == 'generation':
            with sqlite3.connect(_open(cache, root).path) as second:
                second.execute('UPDATE cache_meta SET value=? WHERE name=?', ('b' * 64, 'purge_generation'))
        else:
            intent = cache.prepare_delete(root)
            assert intent is not None
    with cache.batch(root) as fresh:
        assert fresh.lookup(key).value != 'Mara'
    if mode == 'intent':
        cache.purge_for_delete(root, intent=intent)


@pytest.mark.parametrize('disabled', ['kill', 'build', 'sqlite'])
def test_purge_works_when_optional_storage_is_disabled(cache, root, monkeypatch, disabled):
    _seed(cache, root, monkeypatch)
    path = _open(cache, root).path
    if disabled == 'kill':
        monkeypatch.setenv('GRIMOIRE_COMPILED_CACHE', '0')
    elif disabled == 'build':
        monkeypatch.setattr(cache, '_build', lambda: None)
    else:
        monkeypatch.setattr(cache, '_sqlite', None)
    cache.purge_for_delete(root)
    assert (path.parent / 'purge').is_file()
    if disabled != 'sqlite':
        with sqlite3.connect(path) as conn:
            assert conn.execute('SELECT count(*) FROM artifacts').fetchone() == (0,)


def test_active_intent_fences_and_abandoned_local_intent_recovers(cache, root, monkeypatch):
    key = _seed(cache, root, monkeypatch)
    intent = cache.prepare_delete(root)
    assert intent is not None and intent.descriptor is not None
    with cache.batch(root) as request:
        assert not request.lookup(key).hit
        request.put(key, 'Mara')
    cache.close()
    assert intent.descriptor is not None
    cache._release_intents()  # explicit test-owner abandonment, not session closure
    with cache.batch(root) as request:
        assert not request.lookup(key).hit
    assert not (root / '.cache' / 'compiled' / 'delete-intents' / intent.token).exists()


def test_foreign_intent_is_never_retired_locally(cache, root, monkeypatch):
    key = _seed(cache, root, monkeypatch)
    parent = _open(cache, root).path.parent / 'delete-intents'
    parent.mkdir(exist_ok=True)
    token = 'b' * 32
    path = parent / token
    path.write_text(json.dumps({'token': token, 'owner': 'b' * 64}), encoding='utf-8')
    with cache.batch(root) as request:
        assert not request.lookup(key).hit
    assert path.exists()
    with sqlite3.connect(cache_tests._path(cache, root)) as conn:
        assert conn.execute('SELECT count(*) FROM artifacts').fetchone() == (0,)


@pytest.mark.parametrize('owner', ['foreign', 'own'])
def test_only_another_devices_intent_expires_past_retention(cache, root, monkeypatch, owner):
    # A device that crashed mid-delete and was then retired must not keep the
    # cache off on every other device forever: past RETENTION_S its intent is
    # the dormant device section 10 already retires. This device's own
    # intent is durable erasure work and never ages out.
    key = _seed(cache, root, monkeypatch)
    parent = _open(cache, root).path.parent / 'delete-intents'
    parent.mkdir(exist_ok=True)
    token = 'b' * 32
    path = parent / token
    path.write_text(json.dumps({'token': token, 'owner': 'b' * 64 if owner == 'foreign' else 'a' * 64}),
                    encoding='ascii')
    old = os.stat(path).st_mtime - cache.RETENTION_S - 86400
    os.utime(path, (old, old))
    with cache.batch(root) as request:
        request.put(key, 'Seraphine')
    with cache.batch(root) as request:
        found = request.lookup(key)
    if owner == 'foreign':
        assert found.value == 'Seraphine' and path.exists()   # ignored, never deleted
    else:
        assert not path.exists()                               # honoured: recovered and retired


def test_reverse_completion_retires_only_matching_intent(cache, root, monkeypatch):
    _seed(cache, root, monkeypatch)
    first, second = cache.prepare_delete(root), cache.prepare_delete(root)
    assert first is not None and second is not None and first.token != second.token
    parent = root / '.cache' / 'compiled' / 'delete-intents'
    cache.purge_for_delete(root, intent=second)
    assert (parent / first.token).exists() and not (parent / second.token).exists()
    cache.purge_for_delete(root, intent=first)
    assert not (parent / first.token).exists()


def test_marker_failure_retains_durable_intent_without_raising(cache, root, monkeypatch):
    _seed(cache, root, monkeypatch)
    intent = cache.prepare_delete(root)
    original = cache.atomic.write_text
    def refused(path, text, *args, **kwargs):
        if path.name == 'purge':
            raise OSError('write refused')
        return original(path, text, *args, **kwargs)
    monkeypatch.setattr(cache.atomic, 'write_text', refused)
    cache.purge_for_delete(root, intent=intent)
    assert (root / '.cache' / 'compiled' / 'delete-intents' / intent.token).exists()


def test_owned_obsolete_schema_purged_unknown_file_untouched(cache, root, monkeypatch):
    _seed(cache, root, monkeypatch)
    session = _open(cache, root)
    old = session.path.with_name('a' * 32 + '-v0.sqlite')
    shutil.copyfile(session.path, old)
    with sqlite3.connect(old) as conn:
        conn.execute('UPDATE cache_meta SET value=? WHERE name=?', ('0', 'schema'))
        conn.execute('CREATE TABLE legacy_text(body TEXT)')
        conn.execute('INSERT INTO legacy_text VALUES (?)', ('Realm',))
    unknown = session.path.with_name('a' * 32 + '-v99.sqlite')
    unknown.write_bytes(b'foreign')
    cache.purge_for_delete(root)
    with sqlite3.connect(old) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='legacy_text'").fetchone() is None
        assert conn.execute('SELECT value FROM cache_meta WHERE name=?', ('schema',)).fetchone() == ('0',)
    assert unknown.read_bytes() == b'foreign'


def test_busy_purge_retries_on_next_batch(cache, root, monkeypatch):
    key = _seed(cache, root, monkeypatch)
    path = _open(cache, root).path
    with sqlite3.connect(path) as other:
        other.execute('BEGIN IMMEDIATE')
        cache.purge_for_delete(root)
        other.rollback()
    with cache.batch(root) as request:
        assert not request.lookup(key).hit


def test_marker_arriving_during_decode_rejects_hit(cache, root, monkeypatch):
    key = _seed(cache, root, monkeypatch)
    original = cache._decode
    def moved(key, row):
        result = original(key, row)
        cache.purge_for_delete(root)
        return result
    monkeypatch.setattr(cache, '_decode', moved)
    with cache.batch(root) as request:
        assert not request.lookup(key).hit


def test_marker_change_discards_all_staged_families(cache, root, monkeypatch):
    key = _seed(cache, root, monkeypatch)
    kind = cache.KINDS[key.kind]
    path = root / 'Mara.md'
    path.write_bytes(b'Mara')
    now = max(path.stat().st_mtime_ns, path.stat().st_ctime_ns) + cache.PERSIST_WINDOW_NS + 1
    monkeypatch.setattr(cache, 'filesystem_clock', lambda boundary: cache.FilesystemClock(now, root.stat().st_dev))
    monkeypatch.setattr(cache.time, 'time', lambda: 1000000)
    with cache.batch(root) as request:
        request.lookup(key)
        request.validate_one(kind, cache.Input('body', path))
        request.mark_materialized(path, kind.name)
        request.put(cache_tests._key(cache, kind, 'Mara'), 'Mara')
        cache.purge_for_delete(root)
    with sqlite3.connect(cache_tests._path(cache, root)) as conn:
        for table in ('artifacts', 'sources', 'materialized'):
            assert conn.execute('SELECT count(*) FROM ' + table).fetchone() == (0,)


def test_busy_obsolete_schema_retries_after_current_generation_matches(cache, root, monkeypatch):
    _seed(cache, root, monkeypatch)
    session = _open(cache, root)
    old = session.path.with_name('a' * 32 + '-v0.sqlite')
    shutil.copyfile(session.path, old)
    conn = sqlite3.connect(old)
    try:
        conn.execute('UPDATE cache_meta SET value=? WHERE name=?', ('0', 'schema'))
        conn.execute('CREATE TABLE legacy_text(body TEXT)')
        conn.execute('INSERT INTO legacy_text VALUES (?)', ('Seraphine',))
        conn.commit()
        conn.execute('BEGIN IMMEDIATE')
        cache.purge_for_delete(root)
        conn.rollback()
        assert conn.execute('SELECT count(*) FROM legacy_text').fetchone() == (1,)
        with cache.batch(root) as request:
            request.put(cache_tests._key(cache, cache.KINDS['search'], 'Mara'), 'Mara')
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='legacy_text'").fetchone() is None
    finally:
        conn.close()


def test_sqlite_like_legal_user_table_is_physically_purged(cache, root, monkeypatch):
    _seed(cache, root, monkeypatch)
    conn = _open(cache, root).conn
    conn.execute('CREATE TABLE sqliteX_private(body TEXT)')
    conn.execute('INSERT INTO sqliteX_private VALUES (?)', ('Winifred',))
    cache.purge_for_delete(root)
    assert conn.execute("SELECT name FROM sqlite_master WHERE name='sqliteX_private'").fetchone() is None


def test_busy_first_read_cannot_adopt_new_generation_after_purge(cache, root, monkeypatch):
    key = _seed(cache, root, monkeypatch)
    with cache.batch(root) as request:
        with cache._state.lock:
            assert not request.lookup(key).hit
        cache.purge_for_delete(root)
        request.put(key, 'Seraphine')
    with cache.batch(root) as request:
        assert not request.lookup(key).hit


def test_first_access_during_active_delete_never_adopts_completed_purge(cache, root, monkeypatch):
    key = _seed(cache, root, monkeypatch)
    intent = cache.prepare_delete(root)
    with cache.batch(root) as request:
        assert not request.lookup(key).hit
        cache.purge_for_delete(root, intent=intent)
        request.put(key, 'Seraphine')
    with cache.batch(root) as request:
        assert not request.lookup(key).hit


def test_foreign_intent_seen_at_first_access_remains_sticky_fence(cache, root, monkeypatch):
    key = _seed(cache, root, monkeypatch)
    directory = root / '.cache' / 'compiled' / 'delete-intents'
    directory.mkdir()
    token = 'b' * 32
    path = directory / token
    path.write_text(json.dumps({'token': token, 'owner': 'b' * 64}), encoding='ascii')
    with cache.batch(root) as request:
        assert not request.lookup(key).hit
        path.unlink()
        request.put(key, 'Seraphine')
    with cache.batch(root) as request:
        assert not request.lookup(key).hit


def test_false_source_policy_captures_fence_before_reading_bytes(cache, root, monkeypatch):
    _seed(cache, root, monkeypatch)
    kind = replace(cache.KINDS['search'], allow_persisted_sources=False)
    monkeypatch.setitem(cache.KINDS, kind.name, kind)
    path = root / 'Realm.md'
    path.write_bytes(b'Seraphine')
    original = Path.read_bytes
    def deleted_after_read(candidate):
        data = original(candidate)
        if candidate == path:
            path.unlink()
            cache.purge_for_delete(root)
        return data
    monkeypatch.setattr(Path, 'read_bytes', deleted_after_read)
    with cache.batch(root) as request:
        assert request.derive(kind, [cache.Input('body', path)], params={'stem': 'realm', 'settings': {}},
                              compute=lambda view, params: view.files[0].data.decode()) == 'Seraphine'
    assert _open(cache, root).conn.execute('SELECT count(*) FROM artifacts').fetchone() == (0,)


def test_unrelated_connection_root_switch_does_not_abandon_active_delete(cache, root, monkeypatch, tmp_path):
    _seed(cache, root, monkeypatch)
    intent = cache.prepare_delete(root)
    other = tmp_path / 'Saltmarch'
    other.mkdir()
    try:
        _open(cache, other)
        assert intent.descriptor is not None
        assert cache._intent_lock(root, intent.token) is None
    finally:
        cache.purge_for_delete(root, intent=intent)


def test_marker_published_inside_commit_leaves_no_predelete_text(cache, root, monkeypatch):
    key = _seed(cache, root, monkeypatch)
    session = _open(cache, root)
    actual = session.conn
    class PublishBeforeCommit:
        moved = False
        def __getattr__(self, name):
            return getattr(actual, name)
        def commit(self):
            if not self.moved:
                self.moved = True
                cache.purge_for_delete(root)
            return actual.commit()
    session.conn = PublishBeforeCommit()
    with cache.batch(root) as request:
        request.put(key, 'Seraphine')
    assert actual.execute('SELECT count(*) FROM artifacts').fetchone() == (0,)


@pytest.mark.parametrize('disabled', ['kill', 'build'])
@pytest.mark.parametrize('pending', ['busy', 'abandoned'])
def test_disabled_batch_recovers_existing_pending_erasure(cache, root, monkeypatch, disabled, pending):
    _seed(cache, root, monkeypatch)
    session = _open(cache, root)
    if pending == 'busy':
        other = sqlite3.connect(session.path)
        other.execute('BEGIN IMMEDIATE')
        cache.purge_for_delete(root)
        other.rollback()
        other.close()
    else:
        intent = cache.prepare_delete(root)
        cache._release_intents()
        assert intent.descriptor is None
    if disabled == 'kill':
        monkeypatch.setenv('GRIMOIRE_COMPILED_CACHE', '0')
    else:
        monkeypatch.setattr(cache, '_build', lambda: None)
    with cache.batch(root):
        pass
    assert session.conn.execute('SELECT count(*) FROM artifacts').fetchone() == (0,)
    if pending == 'abandoned':
        assert list((root / '.cache' / 'compiled' / 'delete-intents').iterdir()) == []


def test_unchanged_foreign_fence_does_not_repeat_completed_secure_purge(cache, root, monkeypatch):
    key = _seed(cache, root, monkeypatch)
    parent = _open(cache, root).path.parent / 'delete-intents'
    parent.mkdir()
    token = 'b' * 32
    (parent / token).write_text(json.dumps({'token': token, 'owner': 'b' * 64}), encoding='utf-8')
    calls = []
    original = cache._purge_connection
    def counted(conn, generation):
        calls.append(generation)
        return original(conn, generation)
    monkeypatch.setattr(cache, '_purge_connection', counted)
    for _ in range(2):
        with cache.batch(root) as request:
            assert not request.lookup(key).hit
    assert len(calls) == 1


def test_store_written_remembers_boundary_before_hashing_supplied_bytes(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    original = cache.hashlib.sha256
    triggered = []
    def deleted_during_hash(data=b''):
        result = original(data)
        if data == b'Seraphine' and not triggered:
            triggered.append(True)
            cache.purge_for_delete(root)
        return result
    monkeypatch.setattr(cache.hashlib, 'sha256', deleted_during_hash)
    with cache.batch(root) as request:
        request.store_written(kind, [cache.WrittenInput('body', root / 'Realm.md', b'Seraphine')],
                              'Seraphine', params={'stem': 'realm', 'settings': {}})
    assert triggered
    assert _open(cache, root).conn.execute('SELECT count(*) FROM artifacts').fetchone() == (0,)


def test_owner_identity_is_visible_before_cached_device(cache, root, monkeypatch):
    seen = []
    class DeviceMap(dict):
        def __setitem__(self, key, value):
            super().__setitem__(key, value)
            seen.append(cache._state.owners.get(key))
    cache._state.devices = DeviceMap()
    assert cache.prepare_delete(root) is not None
    assert seen == ['a' * 64]


def test_fork_hook_waits_for_unregistered_intent_descriptor(cache, root, monkeypatch):
    acquired, release, forked = threading.Event(), threading.Event(), threading.Event()
    original = cache.atomic.write_text
    def paused(path, text, *args, **kwargs):
        if path.parent.name == 'delete-intents':
            acquired.set()
            assert release.wait(5)
        return original(path, text, *args, **kwargs)
    monkeypatch.setattr(cache.atomic, 'write_text', paused)
    preparer = threading.Thread(target=lambda: cache.prepare_delete(root))
    def fork_hook():
        cache._before_fork()
        forked.set()
        cache._after_fork_parent()
    preparer.start()
    assert acquired.wait(5)
    forker = threading.Thread(target=fork_hook)
    forker.start()
    try:
        assert not forked.wait(0.1), 'fork crossed an unregistered descriptor'
    finally:
        release.set()
        preparer.join(5)
        forker.join(5)
    assert forked.is_set()


@pytest.mark.parametrize('disabled', [False, True], ids=['enabled', 'cache0'])
def test_corrupt_owned_table_delete_erases_payload(cache, root, monkeypatch, disabled):
    key = _seed(cache, root, monkeypatch)
    body = 'Seraphine Saltmarch Mara Winifred'
    with cache.batch(root) as request:
        request.put(key, body)
    session = _open(cache, root)
    path = session.path
    page_size = session.conn.execute('PRAGMA page_size').fetchone()[0]
    page = session.conn.execute("SELECT rootpage FROM sqlite_master WHERE name='artifacts'").fetchone()[0]
    cache.close()
    data = bytearray(path.read_bytes())
    offset = (page - 1) * page_size
    assert data[offset] == 13 and body.encode() in data
    data[offset] = 0  # Owned metadata survives; the payload table is corrupt.
    path.write_bytes(data)
    assert cache._probe_recognized(path)
    with sqlite3.connect(path) as reader:
        with pytest.raises(sqlite3.DatabaseError) as failure:
            reader.execute('SELECT payload FROM artifacts').fetchall()
        assert failure.value.sqlite_errorcode & 255 == sqlite3.SQLITE_CORRUPT
    reader.close()  # sqlite3's transaction context does not close its handle.
    if disabled:
        monkeypatch.setenv('GRIMOIRE_COMPILED_CACHE', '0')
    intent = cache.prepare_delete(root)
    cache.purge_for_delete(root, intent=intent)
    for _ in range(3):
        with cache.batch(root) as request:
            assert not request.lookup(key).hit
        cache.close()
        assert not path.exists() or body.encode() not in path.read_bytes()
        assert not Path(str(path) + '.corrupt').exists()


@pytest.mark.parametrize('disabled', [False, True], ids=['enabled', 'cache0'])
def test_delete_erases_owned_aside_after_failed_replacement(cache, root, monkeypatch, disabled):
    key = _seed(cache, root, monkeypatch)
    body = 'Seraphine Saltmarch Mara Winifred'
    with cache.batch(root) as request:
        request.put(key, body)
    session = _open(cache, root)
    path = session.path
    page_size = session.conn.execute('PRAGMA page_size').fetchone()[0]
    page = session.conn.execute("SELECT rootpage FROM sqlite_master WHERE name='artifacts'").fetchone()[0]
    cache.close()
    data = bytearray(path.read_bytes())
    data[(page - 1) * page_size] = 0
    path.write_bytes(data)
    error = sqlite3.DatabaseError('owned artifacts table corruption')
    error.sqlite_errorcode = sqlite3.SQLITE_CORRUPT
    with monkeypatch.context() as replacement:
        replacement.setattr(cache, '_open_locked', lambda *args, **kwargs: None)
        assert cache._recover(root, path, error) is None
    aside = Path(str(path) + '.corrupt')
    assert not path.exists() and body.encode() in aside.read_bytes()
    if disabled:
        monkeypatch.setenv('GRIMOIRE_COMPILED_CACHE', '0')
    intent = cache.prepare_delete(root)
    cache.purge_for_delete(root, intent=intent)
    for _ in range(3):
        with cache.batch(root):
            pass
        cache.close()
        assert not aside.exists()
    if disabled:
        assert not path.exists()


@pytest.mark.parametrize('boundary', ['foreign', 'journal', 'busy'])
def test_aside_purge_protects_unproved_files_and_retries(cache, root, monkeypatch, boundary):
    _seed(cache, root, monkeypatch)
    session = _open(cache, root)
    path = session.path
    cache.close()
    aside = Path(str(path) + '.corrupt')
    path.rename(aside)
    before = aside.read_bytes()
    monkeypatch.setenv('GRIMOIRE_COMPILED_CACHE', '0')
    if boundary == 'foreign':
        # A matching filename/application id is insufficient: malformed ownership
        # must not abort the directory loop before its owned neighbour is erased.
        foreign = path.parent / ('a' * 32 + '-v2.sqlite.corrupt')
        with sqlite3.connect(foreign) as conn:
            conn.execute(f'PRAGMA application_id={cache.APPLICATION_ID}')
        conn.close()
        original_iterdir = Path.iterdir
        monkeypatch.setattr(Path, 'iterdir', lambda parent: iter([foreign, aside])
                            if parent == path.parent else original_iterdir(parent))
        cache.purge_for_delete(root)
        assert foreign.exists() and not aside.exists()
        return
    journal = Path(str(aside) + '-journal')
    descriptor = None
    if boundary == 'journal':
        journal.write_bytes(b'Mara')
    else:
        descriptor = cache._recovery_lock(root, path)
        assert descriptor is not None
    try:
        cache.purge_for_delete(root)
        assert aside.read_bytes() == before
    finally:
        if descriptor is not None:
            cache.proclock.release(descriptor)
        if journal.exists():
            journal.unlink()
    with cache.batch(root):
        pass
    assert not aside.exists() and not path.exists()


def test_healthy_open_preserves_unrecognized_recovery_aside(cache, root, monkeypatch):
    _seed(cache, root, monkeypatch)
    path = _open(cache, root).path
    cache.close()
    aside = Path(str(path) + '.corrupt')
    aside.write_bytes(b'Mara foreign file')
    before = aside.read_bytes()
    assert _open(cache, root) is not None
    assert aside.read_bytes() == before


def test_synchronization_exception_never_yields_unpurged_session(cache, root, monkeypatch):
    key = _seed(cache, root, monkeypatch)
    assert cache._publish_marker(root)
    def unreadable_directory(*args):
        raise PermissionError('directory enumeration denied')
    monkeypatch.setattr(cache, '_purge_local_files', unreadable_directory)
    with cache.batch(root) as request:
        assert not request.lookup(key).hit
        request.put(key, 'Mara')


def test_zero_input_derivation_cannot_adopt_post_key_purge(cache, root, monkeypatch):
    _seed(cache, root, monkeypatch)
    kind = cache.Kind('tokens:Realm', 1, (), (), False)
    monkeypatch.setitem(cache.KINDS, kind.name, kind)
    real_key = cache.make_key
    deleted = []
    def delete_during_key(candidate, *args, **kwargs):
        if candidate is kind and not deleted:
            deleted.append(True)
            cache.purge_for_delete(root)
        return real_key(candidate, *args, **kwargs)
    monkeypatch.setattr(cache, 'make_key', delete_during_key)
    with cache.batch(root) as request:
        assert request.derive(kind, [], params={}, compute=lambda *args: 'Mara') == 'Mara'
    key = real_key(kind, [], params={})
    with cache.batch(root) as request:
        assert not request.lookup(key).hit
    assert deleted


def test_notadb_does_not_authorize_foreign_main_erasure(cache, root):
    path = cache_tests._path(cache, root)
    path.parent.mkdir(parents=True)
    path.write_bytes(b'Mara foreign file')
    before = path.read_bytes()
    assert _open(cache, root) is None
    assert path.read_bytes() == before
    assert not Path(str(path) + '.corrupt').exists()


def test_actual_owned_table_corruption_recovers_on_lookup(cache, root, monkeypatch):
    key = _seed(cache, root, monkeypatch)
    session = _open(cache, root)
    path = session.path
    page_size = session.conn.execute('PRAGMA page_size').fetchone()[0]
    page = session.conn.execute("SELECT rootpage FROM sqlite_master WHERE name='artifacts'").fetchone()[0]
    cache.close()
    data = bytearray(path.read_bytes())
    data[(page - 1) * page_size] = 0
    path.write_bytes(data)
    assert cache._probe_recognized(path)
    with cache.batch(root) as request:
        assert not request.lookup(key).hit
    with cache.batch(root) as request:
        request.put(key, 'Mara')
    with cache.batch(root) as request:
        assert request.lookup(key).value == 'Mara'
    assert not Path(str(path) + '.corrupt').exists()


@pytest.mark.skipif(os.name != 'nt', reason='requires a case-insensitive host volume')
@pytest.mark.parametrize('case_policy', ['windows', 'posix'])
def test_case_renamed_owned_cache_uses_canonical_purge_exclusion(cache, root, monkeypatch, case_policy):
    if case_policy == 'posix':
        # Default APFS has insensitive lookup while POSIX normcase is identity.
        # Exercise that policy on this real insensitive volume without claiming
        # a native macOS run or changing process-wide path functions.
        monkeypatch.setattr(cache.os.path, 'normcase', lambda name: name)
    key = _seed(cache, root, monkeypatch)
    body = 'Seraphine Saltmarch Mara Winifred'
    with cache.batch(root) as request:
        request.put(key, body)
    path = _open(cache, root).path
    cache.close()
    renamed = path.with_name(path.name.upper())
    temporary = path.with_name('Realm.sqlite')
    path.rename(temporary)
    temporary.rename(renamed)
    assert cache._probe_owned(path)
    monkeypatch.setenv('GRIMOIRE_COMPILED_CACHE', '0')
    descriptor = cache._recovery_lock(root, path)
    assert descriptor is not None
    try:
        alias = cache._recovery_lock(root, renamed)
        if alias is not None:
            cache.proclock.release(alias)
        assert alias is None
        cache.purge_for_delete(root)
        assert body.encode() in renamed.read_bytes()
    finally:
        cache.proclock.release(descriptor)
    for _ in range(3):
        with cache.batch(root):
            pass
        cache.close()
    assert body.encode() not in renamed.read_bytes()


@pytest.mark.parametrize('disabled', ['enabled', 'kill', 'build'])
def test_oversized_abandoned_intent_backlog_recovers_in_bounded_pages(cache, root, monkeypatch, disabled):
    key = _seed(cache, root, monkeypatch)
    path = _open(cache, root).path
    original = cache.atomic.write_text
    def refused_marker(path, text, *args, **kwargs):
        if path.name == 'purge':
            raise OSError('write refused')
        return original(path, text, *args, **kwargs)
    monkeypatch.setattr(cache.atomic, 'write_text', refused_marker)
    for _ in range(129):
        intent = cache.prepare_delete(root)
        assert intent is not None
        cache.purge_for_delete(root, intent=intent)
    cache.close()
    parent = path.parent / 'delete-intents'
    assert len(list(parent.iterdir())) == 129
    monkeypatch.setattr(cache.atomic, 'write_text', original)
    if disabled == 'kill':
        monkeypatch.setenv('GRIMOIRE_COMPILED_CACHE', '0')
    elif disabled == 'build':
        monkeypatch.setattr(cache, '_build', lambda: None)
    for _ in range(3):
        with cache.batch(root) as request:
            assert not request.lookup(key).hit
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT count(*) FROM artifacts').fetchone() == (0,)
    assert not list(parent.iterdir())


@pytest.mark.parametrize('owner_state', ['active', 'foreign'])
def test_full_nonretirable_intent_page_erases_payload_and_keeps_fence(cache, root, monkeypatch, owner_state):
    key = _seed(cache, root, monkeypatch)
    path = _open(cache, root).path
    parent = path.parent / 'delete-intents'
    parent.mkdir(exist_ok=True)
    if owner_state == 'active':
        for _ in range(129):
            assert cache.prepare_delete(root) is not None
    else:
        for number in range(129):
            token = f'{number:032x}'
            (parent / token).write_text(json.dumps({'token': token, 'owner': 'b' * 64}), encoding='utf-8')
    cache.close()
    assert len(cache._read_intents(root)) == 128
    generations = []
    for _ in range(2):
        with cache.batch(root) as request:
            assert not request.lookup(key).hit
            request.put(key, 'Winifred')
        with sqlite3.connect(path) as conn:
            assert conn.execute('SELECT count(*) FROM artifacts').fetchone() == (0,)
            generations.append(conn.execute('SELECT value FROM cache_meta WHERE name=?', ('purge_generation',)).fetchone()[0])
    assert generations[0] == generations[1]
    assert len(list(parent.iterdir())) == 129


def test_own_completion_retires_exact_intent_outside_first_page(cache, root, monkeypatch):
    _seed(cache, root, monkeypatch)
    intents = [cache.prepare_delete(root) for _ in range(129)]
    assert all(intent is not None for intent in intents)
    page = {token for token, owner in cache._read_intents(root)}
    outside = next(intent for intent in intents if intent.token not in page)
    parent = root / '.cache' / 'compiled' / 'delete-intents'
    cache.purge_for_delete(root, intent=outside)
    assert not (parent / outside.token).exists()
    assert len(list(parent.iterdir())) == 128


def test_foreign_intent_removal_cannot_readopt_pre_delete_generation(cache, root, tmp_path, monkeypatch):
    other = tmp_path / 'Saltmarch'
    other.mkdir()
    monkeypatch.setattr(maintenance_reports, 'device_key', lambda path: ('b' if path == other else 'a') * 64)
    kind = cache_tests._kind(cache, monkeypatch)
    key = cache_tests._key(cache, kind)
    cache.purge_for_delete(root)
    with cache.batch(root) as request:
        request.put(key, 'Realm')
    path = cache_tests._path(cache, root)
    old = cache.Batch(root)
    old.put(key, 'Seraphine Saltmarch Mara Winifred')
    before_marker = cache._read_marker(root)
    before_epoch = cache._state.epoch
    assert before_marker and old._marker == before_marker
    assert old._generation == before_marker and key in old._rows

    # The other device starts a public durable delete. Only the intent has
    # synchronized to this replica; its later purge marker has not arrived.
    intent = cache.prepare_delete(other)
    assert intent is not None and intent.owner == 'b' * 64
    local_intents = root / '.cache' / 'compiled' / 'delete-intents'
    local_intents.mkdir(exist_ok=True)
    local_intent = local_intents / intent.token
    shutil.copyfile(other / '.cache' / 'compiled' / 'delete-intents' / intent.token, local_intent)
    assert json.loads(local_intent.read_text()) == {'token': intent.token, 'owner': intent.owner}
    with cache.batch(root) as request:
        assert not request.lookup(key).hit
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT count(*) FROM artifacts').fetchone() == (0,)
        purged_generation = dict(conn.execute('SELECT name,value FROM cache_meta'))['purge_generation']
    assert local_intent.exists()
    assert purged_generation != before_marker

    # Producer completion retires the exact durable intent. Deliver its file
    # removal first, which is a possible ordering for independently synced
    # controls. The producer's new marker remains in the other root.
    cache.purge_for_delete(other, intent=intent)
    assert not (other / '.cache' / 'compiled' / 'delete-intents' / intent.token).exists()
    assert cache._read_marker(other) != before_marker
    local_intent.unlink()
    assert cache._state.epoch == before_epoch
    with cache.batch(root) as request:
        assert not request.lookup(key).hit
    with sqlite3.connect(path) as conn:
        restored_generation = dict(conn.execute('SELECT name,value FROM cache_meta'))['purge_generation']
    old.flush()
    with cache.batch(root) as request:
        result = request.lookup(key)
    assert restored_generation != before_marker
    assert cache._read_marker(root) == purged_generation
    assert not result.hit, 'pre-delete batch restored an erased placeholder payload after foreign intent removal'


@pytest.mark.parametrize('disabled', ['enabled', 'kill', 'build'])
def test_read_side_retirement_never_waits_for_preparing_intent(cache, root, monkeypatch, disabled):
    key = _seed(cache, root, monkeypatch)
    abandoned = cache.prepare_delete(root)
    assert abandoned is not None
    cache._release_intents()
    cache.close()
    if disabled == 'kill':
        monkeypatch.setenv('GRIMOIRE_COMPILED_CACHE', '0')
    elif disabled == 'build':
        monkeypatch.setattr(cache, '_build', lambda: None)
    preparing, release, completed = threading.Event(), threading.Event(), threading.Event()
    original = cache.atomic.write_text
    failures = []
    def paused(path, text, *args, **kwargs):
        if path.parent.name == 'delete-intents':
            preparing.set()
            assert release.wait(5)
        return original(path, text, *args, **kwargs)
    monkeypatch.setattr(cache.atomic, 'write_text', paused)
    def producer():
        try:
            assert cache.prepare_delete(root) is not None
        except (AssertionError, OSError) as error:
            failures.append(error)
    def reader():
        try:
            with cache.batch(root) as request:
                assert not request.lookup(key).hit
        except (AssertionError, OSError) as error:
            failures.append(error)
        finally:
            completed.set()
    writing = threading.Thread(target=producer)
    reading = threading.Thread(target=reader)
    writing.start()
    try:
        assert preparing.wait(3)
        reading.start()
        finished_while_preparing = completed.wait(1)
    finally:
        release.set()
        writing.join(3)
        if reading.ident is not None:
            reading.join(3)
    assert not failures
    assert finished_while_preparing, 'optional reader waited for delete-intent preparation mutex'


@pytest.mark.parametrize('table_count', [129, 260])
def test_owned_schema_over_one_page_is_erased_in_place(cache, root, monkeypatch, table_count):
    kind = cache_tests._kind(cache, monkeypatch)
    key = cache_tests._key(cache, kind)
    payload = 'Seraphine Saltmarch Mara Winifred'
    with cache.batch(root) as request:
        request.put(key, payload)
    path = cache_tests._path(cache, root)
    inode = path.stat().st_ino
    with sqlite3.connect(path) as second:
        second.execute('BEGIN IMMEDIATE')
        for number in range(table_count - 4):
            second.execute(f'CREATE TABLE "Realm_{number:04d}" (body TEXT)')
        second.execute('INSERT INTO "Realm_0000" VALUES (?)', (payload,))
        second.execute(f'INSERT INTO "Realm_{table_count - 5:04d}" VALUES (?)', (payload,))
        second.commit()
        assert second.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name NOT GLOB 'sqlite_*'").fetchone() == (table_count,)
        assert cache._probe_recognized(path)
        monkeypatch.setenv('GRIMOIRE_COMPILED_CACHE', '0')
        intent = cache.prepare_delete(root)
        assert intent is not None
        cache.purge_for_delete(root, intent=intent)
        for _ in range(3):
            with cache.batch(root) as request:
                assert not request.lookup(key).hit
        assert path.stat().st_ino == inode
        assert second.execute('SELECT count(*) FROM artifacts').fetchone() == (0,)
        assert not second.execute("SELECT name FROM sqlite_master WHERE name LIKE 'Realm_%'").fetchall()
        assert payload.encode() not in path.read_bytes()
        assert dict(second.execute('SELECT name,value FROM cache_meta'))['purge_generation'] == cache._read_marker(root)


def test_public_intent_prepare_does_not_inspect_entire_backlog(cache, root):
    directory = root / '.cache' / 'compiled' / 'delete-intents'
    directory.mkdir(parents=True)
    for number in range(2000):
        token = f'{number:032x}'
        (directory / token).write_text(json.dumps({'token': token, 'owner': 'b' * 64}), encoding='ascii')
    original = cache._leaf
    seen = []

    def measured(path):
        if path.parent == directory:
            seen.append(path.name)
        return original(path)

    cache._leaf = measured
    try:
        intent = cache.prepare_delete(root)
        assert intent is not None
    finally:
        cache._leaf = original
    assert len(seen) <= 129, f'control validation inspected {len(seen)} intent leaves before bounded page processing'


def test_owned_empty_and_long_table_names_are_erased(cache, root, monkeypatch):
    _seed(cache, root, monkeypatch)
    path = _open(cache, root).path
    with sqlite3.connect(path) as second:
        for name in ('', 'Realm' * 100):
            second.execute('CREATE TABLE "' + name + '" (body TEXT)')
            second.execute('INSERT INTO "' + name + '" VALUES (?)', ('Seraphine Saltmarch',))
        second.commit()
        cache.purge_for_delete(root)
        for _ in range(3):
            with cache.batch(root):
                pass
        for name in ('', 'Realm' * 100):
            assert second.execute('SELECT name FROM sqlite_master WHERE name=?', (name,)).fetchone() is None


def test_prepare_checks_chosen_intent_leaf_without_following_link(cache, root, tmp_path, monkeypatch):
    directory = root / '.cache' / 'compiled' / 'delete-intents'
    directory.mkdir(parents=True)
    target = tmp_path / 'Mara.md'
    target.write_bytes(b'Winifred')
    token = 'b' * 32
    linked = directory / token
    try:
        linked.symlink_to(target)
    except OSError:
        pytest.skip('symlink creation unavailable')
    monkeypatch.setattr(cache.secrets, 'token_hex', lambda size: token)
    assert cache.prepare_delete(root) is None
    assert target.read_bytes() == b'Winifred' and linked.is_symlink()


@pytest.mark.parametrize('disabled', ['kill', 'build'])
def test_pending_control_leaf_work_is_bounded_with_large_foreign_backlog(cache, root, monkeypatch, disabled):
    _seed(cache, root, monkeypatch)
    directory = root / '.cache' / 'compiled' / 'delete-intents'
    directory.mkdir(exist_ok=True)
    for number in range(2000):
        token = f'{number:032x}'
        (directory / token).write_text(json.dumps({'token': token, 'owner': 'b' * 64}), encoding='ascii')
    if disabled == 'kill':
        monkeypatch.setenv('GRIMOIRE_COMPILED_CACHE', '0')
    else:
        monkeypatch.setattr(cache, '_build', lambda: None)
    original = cache._leaf
    calls = []
    def measured(path):
        if path.parent == directory:
            calls.append(path.name)
        return original(path)
    monkeypatch.setattr(cache, '_leaf', measured)
    with cache.batch(root):
        pass
    assert len(calls) <= 1024
    assert len(list(directory.iterdir())) == 2000


def test_damaged_purge_progress_cannot_skip_remaining_owned_payload(cache, root, monkeypatch):
    _seed(cache, root, monkeypatch)
    path = _open(cache, root).path
    payload = 'Seraphine Saltmarch Mara Winifred'
    with sqlite3.connect(path) as second:
        for number in range(256):
            second.execute(f'CREATE TABLE "Realm_{number:04d}" (body TEXT)')
        second.execute('INSERT INTO "Realm_0255" VALUES (?)', (payload,))
        second.commit()
        monkeypatch.setenv('GRIMOIRE_COMPILED_CACHE', '0')
        cache.purge_for_delete(root)
        row = second.execute('SELECT value FROM cache_meta WHERE name=?', ('purge_progress',)).fetchone()
        assert row is not None
        progress = json.loads(row[0])
        progress['cursor'] = 2**63 - 1
        second.execute('UPDATE cache_meta SET value=? WHERE name=?', (json.dumps(progress), 'purge_progress'))
        second.commit()
        for _ in range(4):
            with cache.batch(root):
                pass
        assert second.execute("SELECT name FROM sqlite_master WHERE name='Realm_0255'").fetchone() is None
        assert payload.encode() not in path.read_bytes()
        assert second.execute('SELECT value FROM cache_meta WHERE name=?', ('purge_generation',)).fetchone()[0] == cache._read_marker(root)


@pytest.mark.parametrize('table_count', [2, 260])
@pytest.mark.parametrize('trigger_count', [1, 129])
@pytest.mark.parametrize('mode', ['enabled', 'kill', 'build'])
def test_owned_delete_triggers_cannot_repopulate_erased_tables(cache, root, monkeypatch, table_count, trigger_count, mode):
    _seed(cache, root, monkeypatch)
    path = _open(cache, root).path
    inode = path.stat().st_ino
    payload = 'Seraphine Saltmarch Mara Winifred'
    with sqlite3.connect(path) as second:
        for number in range(table_count):
            second.execute(f'CREATE TABLE "Realm_{number:04d}" (body TEXT)')
        last = f'Realm_{table_count - 1:04d}'
        second.execute(f'INSERT INTO "{last}" VALUES (?)', (payload,))
        for number in range(trigger_count):
            second.execute(f'CREATE TRIGGER "Mara_{number:04d}" AFTER DELETE ON "{last}" '
                           'BEGIN INSERT INTO "Realm_0000" VALUES (OLD.body); END')
        second.commit()
        assert cache._probe_recognized(path)
        if mode == 'kill':
            monkeypatch.setenv('GRIMOIRE_COMPILED_CACHE', '0')
        elif mode == 'build':
            monkeypatch.setattr(cache, '_build', lambda: None)
        cache.purge_for_delete(root)
        for _ in range(4):
            with cache.batch(root):
                pass
        assert second.execute("SELECT name FROM sqlite_master WHERE name='Realm_0000'").fetchone() is None
        assert second.execute('SELECT name FROM sqlite_master WHERE name=?', (last,)).fetchone() is None
        assert path.stat().st_ino == inode
        assert payload.encode() not in path.read_bytes()
        assert cache._read_generation(second) == cache._read_marker(root)


def test_disappearing_intents_still_consume_the_inspection_budget(cache, root, monkeypatch):
    directory = root / '.cache' / 'compiled' / 'delete-intents'
    directory.mkdir(parents=True)
    for number in range(2000):
        token = f'{number:032x}'
        (directory / token).write_text(json.dumps({'token': token, 'owner': 'b' * 64}), encoding='ascii')
    original_scan = cache.os.scandir
    with original_scan(directory) as entries:
        buffered = list(entries)
    class BufferedEntries:
        def __enter__(self):
            return iter(buffered)
        def __exit__(self, *args):
            pass
    monkeypatch.setattr(cache.os, 'scandir', lambda path: BufferedEntries() if Path(path) == directory else original_scan(path))
    original_read = cache._read_intent
    inspected = []
    def disappeared(parent, token):
        inspected.append(token)
        (parent / token).unlink()
        return original_read(parent, token)
    monkeypatch.setattr(cache, '_read_intent', disappeared)
    assert cache._read_intents(root) is None
    assert len(inspected) <= 128
    with original_scan(directory) as remaining:
        assert len(list(remaining)) == 2000 - len(inspected)


@pytest.mark.parametrize('mode', ['enabled', 'kill', 'build'])
def test_delete_completion_never_waits_for_another_preparation(cache, root, monkeypatch, mode):
    _seed(cache, root, monkeypatch)
    first = cache.prepare_delete(root)
    assert first is not None
    if mode == 'kill':
        monkeypatch.setenv('GRIMOIRE_COMPILED_CACHE', '0')
    elif mode == 'build':
        monkeypatch.setattr(cache, '_build', lambda: None)
    preparing, release, completed = threading.Event(), threading.Event(), threading.Event()
    original = cache.atomic.write_text
    failures, second = [], []
    def paused(path, text, *args, **kwargs):
        if path.parent.name == 'delete-intents':
            preparing.set()
            assert release.wait(5)
        return original(path, text, *args, **kwargs)
    monkeypatch.setattr(cache.atomic, 'write_text', paused)
    def producer():
        try:
            second.append(cache.prepare_delete(root))
        except (AssertionError, OSError) as error:
            failures.append(error)
    def finishing():
        try:
            cache.purge_for_delete(root, intent=first)
        finally:
            completed.set()
    writing, finishing_thread = threading.Thread(target=producer), threading.Thread(target=finishing)
    writing.start()
    try:
        assert preparing.wait(3)
        finishing_thread.start()
        finished_while_preparing = completed.wait(1)
    finally:
        release.set()
        writing.join(3)
        if finishing_thread.ident is not None:
            finishing_thread.join(3)
    assert not failures
    assert finished_while_preparing, 'completed authoritative delete waited for unrelated optional preparation'
    with cache.batch(root):
        pass
    assert first.descriptor is None
    assert not (root / '.cache' / 'compiled' / 'delete-intents' / first.token).exists()
    assert len(second) == 1 and second[0] is not None and second[0].descriptor is not None
    cache.purge_for_delete(root, intent=second[0])


@pytest.mark.parametrize('mode', ['enabled', 'kill', 'build'])
@pytest.mark.parametrize('virtual_count', [1, 129])
def test_owned_virtual_tables_and_shadow_storage_are_erased_in_place(cache, root, monkeypatch, mode, virtual_count):
    kind = cache_tests._kind(cache, monkeypatch)
    key = cache_tests._key(cache, kind)
    payload = 'Seraphine Mara Winifred Saltmarch'
    with cache.batch(root) as request:
        request.put(key, payload)
    current = _open(cache, root).path
    path = current.with_name(current.name.replace(f'-v{cache.SCHEMA}.', '-v0.'))
    shutil.copyfile(current, path)
    inode = path.stat().st_ino
    with sqlite3.connect(path) as second:
        second.execute('UPDATE cache_meta SET value=? WHERE name=?', ('0', 'schema'))
        for number in range(virtual_count):
            try:
                second.execute(f"CREATE VIRTUAL TABLE \"Realm_{number:04d}\" USING fts5(body,content='')")
            except sqlite3.OperationalError as error:
                if 'no such module: fts5' in str(error):
                    pytest.skip('this SQLite build lacks FTS5')
                raise
        second.execute('INSERT INTO "Realm_0000" VALUES (?)', (payload,))
        second.commit()
        assert second.execute('PRAGMA integrity_check').fetchone() == ('ok',)
        assert cache._probe_recognized(path)
        assert b'winifred' in path.read_bytes()
        if mode == 'kill':
            monkeypatch.setenv('GRIMOIRE_COMPILED_CACHE', '0')
        elif mode == 'build':
            monkeypatch.setattr(cache, '_build', lambda: None)
        intent = cache.prepare_delete(root)
        cache.purge_for_delete(root, intent=intent)
        for _ in range(4):
            with cache.batch(root):
                pass
        assert second.execute('SELECT count(*) FROM artifacts').fetchone() == (0,)
        assert not second.execute("SELECT name FROM sqlite_master WHERE name LIKE 'Realm_%'").fetchall()
        assert payload.encode() not in path.read_bytes() and b'winifred' not in path.read_bytes()
        assert path.stat().st_ino == inode
        assert cache._read_generation(second) == cache._read_marker(root)


@pytest.mark.parametrize('mode', ['enabled', 'kill', 'build'])
@pytest.mark.parametrize('schema', ['current', 'obsolete'])
@pytest.mark.parametrize('definition', ['view', 'default', 'metadata', 'metadata_case'])
def test_owned_schema_literals_are_erased_before_completion(cache, root, monkeypatch, mode, schema, definition):
    key = _seed(cache, root, monkeypatch)
    current = _open(cache, root).path
    path = current if schema == 'current' else current.with_name(current.name.replace(f'-v{cache.SCHEMA}.', '-v0.'))
    if schema == 'obsolete':
        shutil.copyfile(current, path)
    payload = 'Seraphine Saltmarch Mara Winifred'
    inode = path.stat().st_ino
    with sqlite3.connect(path) as second:
        if schema == 'obsolete':
            second.execute('UPDATE cache_meta SET value=? WHERE name=?', ('0', 'schema'))
        if definition == 'view':
            second.execute(f"CREATE VIEW Mara AS SELECT '{payload}' AS body")
        elif definition == 'default':
            second.execute(f"CREATE TABLE Realm(body TEXT DEFAULT '{payload}')")
            second.execute('INSERT INTO Realm DEFAULT VALUES')
        elif definition == 'metadata':
            second.execute('ALTER TABLE cache_meta RENAME TO Realm')
            second.execute(f"CREATE TABLE cache_meta(name TEXT PRIMARY KEY,value TEXT NOT NULL DEFAULT '{payload}')")
            second.execute('INSERT INTO cache_meta SELECT name,value FROM Realm')
            second.execute('DROP TABLE Realm')
        else:
            second.execute('ALTER TABLE cache_meta RENAME TO Realm')
            second.execute('ALTER TABLE Realm RENAME TO Cache_meta')
            second.execute(f"ALTER TABLE Cache_meta ADD COLUMN body TEXT DEFAULT '{payload}'")
        second.commit()
        assert cache._probe_recognized(path)
        assert payload.encode() in path.read_bytes()
        if mode == 'kill':
            monkeypatch.setenv('GRIMOIRE_COMPILED_CACHE', '0')
        elif mode == 'build':
            monkeypatch.setattr(cache, '_build', lambda: None)
        intent = cache.prepare_delete(root)
        cache.purge_for_delete(root, intent=intent)
        for _ in range(4):
            with cache.batch(root):
                pass
        assert payload.encode() not in path.read_bytes()
        assert path.stat().st_ino == inode and cache._probe_recognized(path)
        assert cache._read_generation(second) == cache._read_marker(root)
        assert second.execute('SELECT count(*) FROM artifacts').fetchone() == (0,)
    if mode == 'enabled' and schema == 'current':
        with cache.batch(root) as request:
            request.put(key, 'Mara')
        with cache.batch(root) as request:
            assert request.lookup(key).value == 'Mara'


def test_owned_orphaned_planner_metadata_is_erased(cache, root, monkeypatch):
    _seed(cache, root, monkeypatch)
    path = _open(cache, root).path
    payload = 'Seraphine Saltmarch Mara Winifred'
    with sqlite3.connect(path) as second:
        second.execute('ANALYZE')
        second.execute('INSERT INTO sqlite_stat1(tbl,idx,stat) VALUES (?,?,?)', ('Realm', None, payload))
        second.commit()
        assert cache._probe_recognized(path) and payload.encode() in path.read_bytes()
        cache.purge_for_delete(root)
        assert payload.encode() not in path.read_bytes()
        assert second.execute('SELECT count(*) FROM sqlite_stat1').fetchone() == (0,)
