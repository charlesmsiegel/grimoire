"""Key-bounded bulk lookup and unique per-kind accounting (03-S3)."""

from dataclasses import replace

import pytest

from tests import test_compiled as cache_tests

cache = cache_tests.cache
root = cache_tests.root
_kind = cache_tests._kind
_key = cache_tests._key
_open = cache_tests._open


def _seed(cache, root, entries):
    for offset in range(0, len(entries), 64):
        with cache.batch(root) as request:
            for key, value in entries[offset:offset + 64]:
                request.put(key, value)


def test_many_deduplicates_and_counts_only_requested_keys(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    other = replace(kind, name='tokens')
    monkeypatch.setitem(cache.KINDS, other.name, other)
    hit = _key(cache, kind)
    miss = _key(cache, kind, 'mara')
    token = _key(cache, other)
    unrelated = _key(cache, kind, 'winifred')
    _seed(cache, root, [(hit, {'name': ['Realm']}), (token, 0), (unrelated, 'Winifred')])
    with cache.batch(root) as request:
        result = request.lookup_many([hit, miss, hit, token])
        assert request.lookup_many([]) == cache.BatchLookup({}, {})
    assert set(result.values) == {hit, miss, token}
    assert result.values[hit].value == {'name': ['Realm']}
    assert result.values[token] == cache.Lookup(True, 0)
    assert result.values[miss] == cache.Lookup(False)
    assert result.counts == {'search': cache.Counts(1, 1), 'tokens': cache.Counts(1, 0)}
    result.values[hit].value['name'].append('Mara')
    with cache.batch(root) as request:
        assert request.lookup_many([hit]).values[hit].value == {'name': ['Realm']}


def test_many_257_keys_selects_only_three_fixed_chunks(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    keys = [_key(cache, kind, f'Realm-{i}') for i in range(257)]
    unrelated = _key(cache, kind, 'Saltmarch')
    _seed(cache, root, [(key, index) for index, key in enumerate(keys)] + [(unrelated, -1)])
    statements = []
    _open(cache, root).conn.set_trace_callback(statements.append)
    with cache.batch(root) as request:
        result = request.lookup_many(keys + keys[:5])
    selections = [sql for sql in statements if 'FROM artifacts WHERE' in sql]
    assert len(selections) == 3
    assert 'IN (' in selections[-1] and selections[-1].count('NULL') == 127
    assert set(result.values) == set(keys)
    assert result.counts == {'search': cache.Counts(257, 0)}
    assert [result.values[key].value for key in keys] == list(range(257))


def test_invalid_keys_are_counted_as_misses_without_sql(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    invalid = [cache.Key(kind.name, 'invalid'), cache.Key('unregistered', 'a' * 64)]
    statements = []
    _open(cache, root).conn.set_trace_callback(statements.append)
    with cache.batch(root) as request:
        result = request.lookup_many(invalid)
    assert result.values == dict.fromkeys(invalid, cache.Lookup(False))
    assert result.counts == {'search': cache.Counts(0, 1), 'unregistered': cache.Counts(0, 1)}
    assert not any('FROM artifacts WHERE' in sql for sql in statements)


@pytest.mark.parametrize('damage', ['checksum', 'oversized', 'deep', 'missing'])
def test_bad_row_is_a_miss_without_hiding_good_neighbor(cache, root, monkeypatch, damage):
    kind = _kind(cache, monkeypatch)
    good, bad = _key(cache, kind), _key(cache, kind, 'mara')
    _seed(cache, root, [(good, 'Realm'), (bad, 'Mara')])
    conn = _open(cache, root).conn
    if damage == 'missing':
        conn.execute('DELETE FROM artifacts WHERE key_digest=?', (bad.digest,))
    elif damage == 'checksum':
        conn.execute('UPDATE artifacts SET "check"=? WHERE key_digest=?', ('bad', bad.digest))
    else:
        payload = 'x' * (cache.MAX_PAYLOAD_BYTES + 1) if damage == 'oversized' else '[' * 65 + '0' + ']' * 65
        conn.execute('UPDATE artifacts SET payload=?,"check"=? WHERE key_digest=?',
                     (payload, cache._check(bad, payload), bad.digest))
    with cache.batch(root) as request:
        result = request.lookup_many([good, bad])
    assert result.values[good] == cache.Lookup(True, 'Realm')
    assert result.values[bad] == cache.Lookup(False)
    assert result.counts == {'search': cache.Counts(1, 1)}


@pytest.mark.parametrize('failure', ['disabled', 'contended', 'table'])
def test_many_optional_failure_returns_all_misses(cache, root, monkeypatch, failure):
    kind = _kind(cache, monkeypatch)
    key = _key(cache, kind)
    _seed(cache, root, [(key, 'Realm')])
    if failure == 'disabled':
        monkeypatch.setenv('GRIMOIRE_COMPILED_CACHE', '0')
    elif failure == 'table':
        _open(cache, root).conn.execute('DROP TABLE artifacts')
    else:
        cache._state.lock.acquire()
    try:
        with cache.batch(root) as request:
            result = request.lookup_many([key])
    finally:
        if failure == 'contended':
            cache._state.lock.release()
    assert result.values == {key: cache.Lookup(False)}
    assert result.counts == {'search': cache.Counts(0, 1)}


def test_many_decode_is_unlocked_and_root_change_discards_entire_result(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    keys = [_key(cache, kind, stem) for stem in ['realm', 'mara']]
    _seed(cache, root, [(key, index) for index, key in enumerate(keys)])
    original = cache._decode
    seen = []
    def decode(key, row):
        assert not cache._state.lock.locked()
        seen.append(key)
        if len(seen) == 1:
            with cache.root_change():
                pass
        return original(key, row)
    monkeypatch.setattr(cache, '_decode', decode)
    with cache.batch(root) as request:
        result = request.lookup_many(keys)
    assert result.values == dict.fromkeys(keys, cache.Lookup(False))
    assert result.counts == {'search': cache.Counts(0, 2)}


def test_many_collection_key_includes_nonfile_params(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    (root / 'Realm.md').write_bytes(b'Realm')
    with cache.batch(root) as request:
        collection = request.collection(kind, cache.Collection('records', root))
        key = cache.make_key(kind, [(collection.role, collection.content_hash)],
                             params={'stem': 'realm', 'settings': {'show': True}})
        moved = cache.make_key(kind, [(collection.role, collection.content_hash)],
                               params={'stem': 'realm', 'settings': {'show': False}})
        request.put(key, 'Realm')
    with cache.batch(root) as request:
        result = request.lookup_many([key, moved])
    assert result.values[key].value == 'Realm' and not result.values[moved].hit
    assert result.counts == {'search': cache.Counts(1, 1)}


def test_many_answers_a_refused_key_as_an_uncounted_miss(cache, root, monkeypatch):
    # make_key returns None when it refuses; lookup(None) is a miss, and so
    # is that None inside lookup_many -- never an AttributeError.
    kind = _kind(cache, monkeypatch)
    hit = _key(cache, kind)
    _seed(cache, root, [(hit, 'Realm')])
    with cache.batch(root) as request:
        result = request.lookup_many([hit, None])
    assert result.values == {hit: cache.Lookup(True, 'Realm'), None: cache.Lookup(False)}
    assert result.counts == {'search': cache.Counts(1, 0)}
