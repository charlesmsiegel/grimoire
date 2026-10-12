"""Daily write suppression and bounded derived-text maintenance (03-S5)."""

import hashlib

import pytest

from tests import test_compiled as cache_tests

cache = cache_tests.cache
root = cache_tests.root
_kind = cache_tests._kind
_open = cache_tests._open
PARAMS = {'stem': 'realm', 'settings': {}}


def _seed(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    path = root / 'Realm.md'
    path.write_bytes(b'Realm')
    now = max(path.stat().st_mtime_ns, path.stat().st_ctime_ns) + cache.PERSIST_WINDOW_NS + 1
    monkeypatch.setattr(cache, 'filesystem_clock', lambda boundary: cache.FilesystemClock(now, root.stat().st_dev))
    monkeypatch.setattr(cache.time, 'time', lambda: 100000)
    with cache.batch(root) as request:
        request.derive(kind, [cache.Input('body', path)], params=PARAMS, compute=lambda *args: 'Realm')
    return kind, path


def test_same_day_warm_derive_does_no_sql_writes(cache, root, monkeypatch):
    kind, path = _seed(cache, root, monkeypatch)
    statements = []
    _open(cache, root).conn.set_trace_callback(statements.append)
    for _ in range(3):
        with cache.batch(root) as request:
            assert request.derive(kind, [cache.Input('body', path)], params=PARAMS,
                                  compute=lambda *args: pytest.fail('warm recomputed')) == 'Realm'
    assert not any(sql.startswith(('BEGIN', 'INSERT', 'UPDATE', 'DELETE', 'COMMIT')) for sql in statements)


def test_next_day_all_tables_touch_once_in_one_commit(cache, root, monkeypatch):
    kind, path = _seed(cache, root, monkeypatch)
    monkeypatch.setattr(cache.time, 'time', lambda: 100000 + 86401)
    conn = _open(cache, root).conn
    statements = []
    conn.set_trace_callback(statements.append)
    for _ in range(2):
        with cache.batch(root) as request:
            assert request.derive(kind, [cache.Input('body', path)], params=PARAMS,
                                  compute=lambda *args: pytest.fail('warm recomputed')) == 'Realm'
    assert statements.count('COMMIT') == 1
    for table in ('artifacts', 'sources', 'materialized'):
        assert conn.execute('SELECT last_used FROM ' + table).fetchall() == [(186401,)]


def test_lru_low_water_and_incremental_vacuum(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    monkeypatch.setattr(cache, 'SIZE_CAP_BYTES', 80 << 10, raising=False)
    monkeypatch.setattr(cache, 'LOW_WATER_BYTES', 60 << 10, raising=False)
    monkeypatch.setattr(cache, 'MAINTENANCE_INTERVAL_S', 0, raising=False)
    monkeypatch.setattr(cache.time, 'time', lambda: 100000)
    with cache.batch(root) as request:
        for position in range(8):
            request.put(cache_tests._key(cache, kind, f'Realm-{position}'), 'x' * 20000)
    session = _open(cache, root)
    conn = session.conn
    live = (conn.execute('PRAGMA page_count').fetchone()[0] - conn.execute('PRAGMA freelist_count').fetchone()[0]) * conn.execute('PRAGMA page_size').fetchone()[0]
    assert live <= cache.LOW_WATER_BYTES
    assert session.path.stat().st_size < 8 * 20000


class _OnePagePerStep:
    """A connection whose incremental_vacuum frees one page per call, as
    Python 3.11's sqlite3 does for a statement with no result columns."""

    def __init__(self, free, stuck=False):
        self.free, self.stuck, self.calls = free, stuck, 0

    def execute(self, sql):
        if sql == 'PRAGMA incremental_vacuum(256)':
            self.calls += 1
            if not self.stuck:
                self.free = max(0, self.free - 1)
            return self
        assert sql == 'PRAGMA freelist_count'
        self._row = (self.free,)
        return self

    def fetchone(self):
        return self._row

    def fetchall(self):
        return []


@pytest.mark.parametrize('free,calls,left', [(40, 40, 0), (1000, 256, 744), (0, 0, 0)])
def test_incremental_vacuum_spends_its_page_budget_one_step_at_a_time(cache, free, calls, left):
    conn = _OnePagePerStep(free)
    cache._incremental_vacuum(conn)
    assert (conn.calls, conn.free) == (calls, left)
    assert cache.VACUUM_PAGES == 256


def test_incremental_vacuum_stops_when_the_freelist_does_not_shrink(cache):
    conn = _OnePagePerStep(40, stuck=True)
    cache._incremental_vacuum(conn)
    assert conn.calls == 1


@pytest.mark.parametrize('damage', ['invalid', b'invalid'])
def test_malformed_timestamp_cannot_permanently_stop_eviction(cache, root, monkeypatch, damage):
    kind = _kind(cache, monkeypatch)
    with cache.batch(root) as request:
        for position in range(8):
            request.put(cache_tests._key(cache, kind, f'Realm-{position}'), 'x' * 20000)
    conn = _open(cache, root).conn
    key = cache_tests._key(cache, kind, 'Realm-0')
    conn.execute('UPDATE artifacts SET last_used=? WHERE kind=? AND key_digest=?',
                 (damage, key.kind, key.digest))
    monkeypatch.setattr(cache, 'SIZE_CAP_BYTES', 80 << 10)
    monkeypatch.setattr(cache, 'LOW_WATER_BYTES', 60 << 10)
    monkeypatch.setattr(cache, 'MAINTENANCE_INTERVAL_S', 0)
    with cache.batch(root) as request:
        request.put(cache_tests._key(cache, kind, 'Mara'), 'Mara')
    assert cache._live_bytes(_open(cache, root).conn) <= cache.LOW_WATER_BYTES


def test_lru_query_transfers_only_integer_timestamp_metadata(cache, root, monkeypatch):
    _seed(cache, root, monkeypatch)
    conn = _open(cache, root).conn
    for table in ('artifacts', 'sources', 'materialized'):
        conn.execute('UPDATE ' + table + ' SET last_used=randomblob(1048576)')
    victims = cache._lru_victims(conn)
    assert {row[0] for row in victims} == {'artifacts', 'sources', 'materialized'}
    assert all(type(row[2]) is int for row in victims), 'LRU query transferred unbounded timestamp payloads'


def test_eviction_preserves_newer_materialized_association(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    path = root / 'Realm.md'
    monkeypatch.setattr(cache.time, 'time', lambda: 100000)
    with cache.batch(root) as request:
        old = request.store_written(kind, [cache.WrittenInput('body', path, b'Realm')], 'x' * 20000, params=PARAMS)
    monkeypatch.setattr(cache.time, 'time', lambda: 200000)
    with cache.batch(root) as request:
        new = request.store_written(kind, [cache.WrittenInput('body', path, b'Mara')], 'Mara', params=PARAMS)
    monkeypatch.setattr(cache, 'SIZE_CAP_BYTES', 60 << 10, raising=False)
    monkeypatch.setattr(cache, 'LOW_WATER_BYTES', 52 << 10, raising=False)
    monkeypatch.setattr(cache, 'MAINTENANCE_INTERVAL_S', 0, raising=False)
    with cache.batch(root) as request:
        request.put(cache_tests._key(cache, kind, 'Saltmarch'), 'Saltmarch')
    with cache.batch(root) as request:
        assert not request.lookup(old).hit and request.lookup(new).value == 'Mara'
        assert request.materialized([path])[path][0].built_from == hashlib.sha256(b'Mara').hexdigest()


def test_partial_eviction_remains_eligible_until_low_water(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    session = _open(cache, root)
    monkeypatch.setattr(cache, 'SIZE_CAP_BYTES', 90 << 10)
    monkeypatch.setattr(cache, 'LOW_WATER_BYTES', 60 << 10)
    monkeypatch.setattr(cache, 'MAX_EVICTION_CHUNKS', 16, raising=False)
    monkeypatch.setattr(cache.time, 'time', lambda: 100000)
    payload = '"' + 'x' * 150 + '"'
    rows = []
    for position in range(10000):
        key = cache_tests._key(cache, kind, f'Realm-{position}')
        rows.append((key.kind, key.digest, payload, cache._check(key, payload), position))
    with cache._state.lock:
        session.conn.execute('BEGIN IMMEDIATE')
        session.conn.executemany('INSERT INTO artifacts VALUES (?,?,?,?,?)', rows)
        session.conn.commit()
    for position in range(12):
        with cache.batch(root) as request:
            request.put(cache_tests._key(cache, kind, f'Mara-{position}'), 'Mara')
        if cache._live_bytes(session.conn) <= cache.LOW_WATER_BYTES:
            break
    assert cache._live_bytes(session.conn) <= cache.LOW_WATER_BYTES


def test_pending_trim_survives_session_close_below_cap(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    session = _open(cache, root)
    monkeypatch.setattr(cache, 'SIZE_CAP_BYTES', 160 << 10)
    monkeypatch.setattr(cache, 'LOW_WATER_BYTES', 80 << 10)
    monkeypatch.setattr(cache, 'MAX_EVICTION_CHUNKS', 1)
    payload = '"' + 'x' * 1000 + '"'
    rows = []
    for position in range(200):
        key = cache_tests._key(cache, kind, f'Realm-{position}')
        rows.append((key.kind, key.digest, payload, cache._check(key, payload), position))
    with cache._state.lock:
        session.conn.execute('BEGIN IMMEDIATE')
        session.conn.executemany('INSERT INTO artifacts VALUES (?,?,?,?,?)', rows)
        session.conn.commit()
    with cache.batch(root) as request:
        request.put(cache_tests._key(cache, kind, 'Mara'), 'Mara')
    assert cache.LOW_WATER_BYTES < cache._live_bytes(session.conn) <= cache.SIZE_CAP_BYTES
    cache.close()
    with cache.batch(root) as request:
        request.put(cache_tests._key(cache, kind, 'Winifred'), 'Winifred')
    assert cache._live_bytes(_open(cache, root).conn) <= cache.LOW_WATER_BYTES
