"""Path-scoped work history and complete artifact families (03-S4)."""

import hashlib
import json
from pathlib import Path

import pytest

from tests import test_compiled as cache_tests

cache = cache_tests.cache
root = cache_tests.root
_kind = cache_tests._kind
_open = cache_tests._open
PARAMS = {'stem': 'realm', 'settings': {}}


def test_materialized_tracks_independent_instances_and_unknown_hash(cache, root, monkeypatch):
    _kind(cache, monkeypatch)
    path = root / 'Realm.md'
    with cache.batch(root) as request:
        request.mark_materialized(path, 'search', instance={'campaign': 'Realm'}, built_from='a' * 64)
        request.mark_materialized(path, 'search', instance={'campaign': 'Saltmarch'})
    with cache.batch(root) as request:
        rows = request.materialized([path, root / 'Mara.md'])
    assert rows[root / 'Mara.md'] == ()
    assert len(rows[path]) == 2
    assert {(row.instance, row.built_from) for row in rows[path]} == {
        ('{"campaign":"Realm"}', 'a' * 64), ('{"campaign":"Saltmarch"}', '')}
    assert all(row.path == path and row.kind == 'search' for row in rows[path])


def test_history_lookup_is_only_supplied_paths_with_fixed_chunks(cache, root, monkeypatch):
    _kind(cache, monkeypatch)
    path, unrelated = root / 'Realm.md', root / 'Mara.md'
    with cache.batch(root) as request:
        request.mark_materialized(path, 'search')
        request.mark_materialized(unrelated, 'search')
    statements = []
    _open(cache, root).conn.set_trace_callback(statements.append)
    supplied = [path, *[root / f'Saltmarch-{i}.md' for i in range(256)]]
    with cache.batch(root) as request:
        rows = request.materialized(supplied)
    assert set(rows) == set(supplied) and len(rows[path]) == 1
    assert unrelated not in rows
    assert sum('FROM materialized WHERE' in sql for sql in statements) == 3


@pytest.mark.parametrize('damage', ['hash', 'check', 'instance', 'last_used'])
def test_materialized_damage_is_rejected_and_field_transfer_bounded(cache, root, monkeypatch, damage):
    _kind(cache, monkeypatch)
    path = root / 'Realm.md'
    with cache.batch(root) as request:
        request.mark_materialized(path, 'search', built_from='a' * 64)
    column, value = {'hash': ('built_from', 'b' * 64), 'check': ('check', 'bad'),
                     'instance': ('instance', 'x' * (cache.MAX_KEY_BYTES + 1)),
                     'last_used': ('last_used', 'invalid')}[damage]
    _open(cache, root).conn.execute(f'UPDATE materialized SET "{column}"=?', (value,))
    with cache.batch(root) as request:
        assert request.materialized([path]) == {path: ()}


def test_validation_extends_history_without_changing_exact_bytes(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    path = root / 'Realm.md'
    path.write_bytes(b'Realm')
    with cache.batch(root) as request:
        request.mark_materialized(path, 'search', built_from=hashlib.sha256(b'Realm').hexdigest())
    with cache.batch(root) as request:
        value = request.validate_one(kind, cache.Input('body', path), with_bytes=True)
    assert value.data == b'Realm' and len(value.materialized) == 1
    assert value.materialized[0].built_from == value.content_hash


def test_every_written_artifact_records_all_consumed_paths(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    paths = [root / 'Realm.md', root / 'Mara.md']
    data = [b'Realm', b'Mara']
    with cache.batch(root) as request:
        key = request.store_written(kind, [cache.WrittenInput('body', path, body)
                                          for path, body in zip(paths, data, strict=True)],
                                    'Realm', params=PARAMS, instance={'campaign': 'Saltmarch'})
    with cache.batch(root) as request:
        assert request.lookup(key).hit
        rows = request.materialized(paths)
    for path, body in zip(paths, data, strict=True):
        assert len(rows[path]) == 1 and rows[path][0].built_from == hashlib.sha256(body).hexdigest()
        assert rows[path][0].instance == '{"campaign":"Saltmarch"}'


def test_oversized_artifact_family_is_entirely_unstored(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    inputs = [cache.WrittenInput('body', root / f'Realm-{i}.md', b'Realm')
              for i in range(cache.MAX_STAGED_ROWS)]
    key = cache.make_key(kind, [('body', hashlib.sha256(b'Realm').hexdigest()) for _ in inputs], params=PARAMS)
    with cache.batch(root) as request:
        assert request.store_written(kind, inputs, 'Realm', params=PARAMS) is None
    with cache.batch(root) as request:
        assert not request.lookup(key).hit
        assert all(not rows for rows in request.materialized([item.path for item in inputs]).values())


def test_optional_sources_cannot_monopolize_fitting_family_budget(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    paths = [root / 'Realm.md', root / 'Mara.md']
    with cache.batch(root) as request:
        for i in range(cache.MAX_STAGED_ROWS):
            relative = f'Saltmarch-{i}.md'
            request._stage_source(relative, (relative, 11, 12, 5, 2), 'a' * 64)
        key = request.store_written(kind, [cache.WrittenInput('body', path, b'Realm') for path in paths],
                                    'Realm', params=PARAMS)
        assert key is not None
    with cache.batch(root) as request:
        assert request.lookup(key).hit and all(request.materialized(paths).values())


def test_missing_history_table_cannot_erase_a_valid_artifact_answer(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    path = root / 'Realm.md'
    path.write_bytes(b'Realm')
    with cache.batch(root) as request:
        request.store_written(kind, [cache.WrittenInput('body', path, b'Realm')], 'Realm', params=PARAMS)
    _open(cache, root).conn.execute('DROP TABLE materialized')
    with cache.batch(root) as request:
        assert request.derive(kind, [cache.Input('body', path)], params=PARAMS,
                              compute=lambda *args: pytest.fail('valid artifact was hidden')) == 'Realm'
        assert request.materialized([path]) == {path: ()}


def test_artifact_hit_touches_matching_history_instances(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    path = root / 'Realm.md'
    path.write_bytes(b'Realm')
    monkeypatch.setattr(cache.time, 'time', lambda: 1000)
    with cache.batch(root) as request:
        request.store_written(kind, [cache.WrittenInput('body', path, b'Realm')], 'Realm', params=PARAMS,
                              instance={'campaign': 'Saltmarch'})
    monkeypatch.setattr(cache.time, 'time', lambda: 1000 + 86401)
    with cache.batch(root) as request:
        assert request.derive(kind, [cache.Input('body', path)], params=PARAMS, instance={'campaign': 'Saltmarch'},
                              compute=lambda *args: pytest.fail('artifact missed')) == 'Realm'
    with cache.batch(root) as request:
        rows = request.materialized([path])[path]
    assert rows[0].last_used == 1000 + 86401


def test_known_source_history_does_not_stat_authoritative_path(cache, root, monkeypatch):
    _kind(cache, monkeypatch)
    path = root / 'Realm.md'
    original = Path.stat
    def forbidden(candidate, *args, **kwargs):
        if candidate == path:
            raise AssertionError('history source stat')
        return original(candidate, *args, **kwargs)
    monkeypatch.setattr(Path, 'stat', forbidden)
    with cache.batch(root) as request:
        request.mark_materialized(path, 'search', built_from='a' * 64)
    with cache.batch(root) as request:
        assert len(request.materialized([path])[path]) == 1



def test_rejected_history_fields_log_one_fixed_warning(cache, root, monkeypatch):
    _kind(cache, monkeypatch)
    path = root / 'Realm.md'
    with cache.batch(root) as request:
        request.mark_materialized(path, 'search')
    _open(cache, root).conn.execute('UPDATE materialized SET kind=?', ('x' * (cache.MAX_KEY_BYTES + 1),))
    warnings = []
    monkeypatch.setattr(cache.logs, 'record', lambda *args, **kwargs: warnings.append((args, kwargs)))
    for _ in range(3):
        with cache.batch(root) as request:
            assert request.materialized([path]) == {path: ()}
    assert warnings == [(('warning', 'store.compiled', 'Compiled cache unavailable'), {'failure_kind': 'materialized'})]



@pytest.mark.parametrize('bulk', [False, True])
def test_low_level_artifact_hit_touches_only_associated_history(cache, root, monkeypatch, bulk):
    kind = _kind(cache, monkeypatch)
    path, unrelated = root / 'Realm.md', root / 'Mara.md'
    monkeypatch.setattr(cache.time, 'time', lambda: 1000)
    with cache.batch(root) as request:
        key = request.store_written(kind, [cache.WrittenInput('body', path, b'Realm')], 'Realm', params=PARAMS)
        request.mark_materialized(unrelated, kind.name, built_from='a' * 64)
    monkeypatch.setattr(cache.time, 'time', lambda: 1000 + 86401)
    with cache.batch(root) as request:
        if bulk:
            assert request.lookup_many([key]).values[key].hit
        else:
            assert request.lookup(key).hit
    with cache.batch(root) as request:
        rows = request.materialized([path, unrelated])
    assert rows[path][0].last_used == 1000 + 86401
    assert rows[unrelated][0].last_used == 1000



@pytest.mark.parametrize('bulk', [False, True])
def test_root_change_during_history_touch_discards_lookup(cache, root, monkeypatch, bulk):
    kind = _kind(cache, monkeypatch)
    path = root / 'Realm.md'
    with cache.batch(root) as request:
        key = request.store_written(kind, [cache.WrittenInput('body', path, b'Realm')], 'Realm', params=PARAMS)
    original = cache.Batch._touch_artifact
    def changed(request, key):
        with cache.root_change():
            pass
        original(request, key)
    monkeypatch.setattr(cache.Batch, '_touch_artifact', changed)
    with cache.batch(root) as request:
        value = request.lookup_many([key]).values[key] if bulk else request.lookup(key)
    assert not value.hit



@pytest.mark.parametrize('mapped', [False, True])
def test_rename_history_preserves_nonconsecutive_selectors_without_artifacts(cache, root, monkeypatch, mapped):
    _kind(cache, monkeypatch)
    old, new = root / 'Realm.md', root / 'Mara.md'
    kind = 'vector:body:' + cache.space_digest('Realm\x00Saltmarch')
    with cache.batch(root) as request:
        for position in (0, 3, 11):
            request.mark_materialized(old, kind, instance={'path': 'Realm.md', 'passage': position}, built_from='a' * 64)
    calls = []
    def mapper(kind, canonical):
        assert not cache._state.lock.locked()
        calls.append(canonical)
        value = json.loads(canonical)
        value['path'] = 'Mara.md'
        return json.dumps(value, sort_keys=True, separators=(',', ':'))
    with cache.batch(root) as request:
        request.copy_materialized(old, new, instance_map=mapper if mapped else None)
    with cache.batch(root) as request:
        rows = request.materialized([old, new])
    assert len(rows[old]) == len(rows[new]) == 3
    assert {json.loads(row.instance)['passage'] for row in rows[new]} == {0, 3, 11}
    assert {json.loads(row.instance)['path'] for row in rows[new]} == {'Mara.md' if mapped else 'Realm.md'}
    assert all(row.built_from == '' and row.kind == kind for row in rows[new])
    assert all(row.built_from == 'a' * 64 for row in rows[old])
    assert len(calls) == (3 if mapped else 0)
    assert _open(cache, root).conn.execute('SELECT count(*) FROM artifacts').fetchone()[0] == 0
    assert not (root / '.cache' / 'embeddings').exists()


@pytest.mark.parametrize('space', ['Realm', 'Realm\x00Saltmarch', 'Realm\u00e9'])
def test_space_digest_is_exact_utf8_prefix(cache, space):
    assert cache.space_digest(space) == hashlib.sha256(space.encode('utf-8')).hexdigest()[:16]



def test_plain_put_cannot_exceed_metadata_only_staging_row_budget(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    with cache.batch(root) as request:
        for position in range(cache.MAX_STAGED_ROWS):
            request.mark_materialized(root / 'Realm.md', kind.name, instance={'passage': position})
        request.put(cache_tests._key(cache, kind), 'Realm')
        assert len(request._rows) + len(request._materialized) <= cache.MAX_STAGED_ROWS


def test_plain_put_cannot_exceed_metadata_only_byte_budget(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    monkeypatch.setattr(cache, 'MAX_STAGED_BYTES', 1000)
    with cache.batch(root) as request:
        request.mark_materialized(root / 'Realm.md', kind.name, instance={'passage': 3})
        request.put(cache_tests._key(cache, kind), 'x' * 900)
        assert request._bytes <= cache.MAX_STAGED_BYTES


def test_plain_put_cannot_exceed_touch_only_staging_budget(cache, root, monkeypatch):
    monkeypatch.setattr(cache.time, 'time', lambda: 100000)
    kind = _kind(cache, monkeypatch)
    keys = [cache_tests._key(cache, kind, f'Realm-{position}') for position in range(cache.MAX_STAGED_ROWS)]
    with cache.batch(root) as request:
        for key in keys:
            request.put(key, 'Realm')
    with cache.batch(root) as request:
        monkeypatch.setattr(cache.time, 'time', lambda: 186401)
        assert request.lookup_many(keys).counts[kind.name].hits == cache.MAX_STAGED_ROWS
        assert len(request._artifact_touches) == cache.MAX_STAGED_ROWS
        request.put(cache_tests._key(cache, kind, 'Mara'), 'Mara')
        assert len(request._rows) + len(request._artifact_touches) <= cache.MAX_STAGED_ROWS


@pytest.mark.parametrize('limit', ['rows', 'bytes'])
def test_history_overflow_is_complete_optional_miss(cache, root, monkeypatch, limit):
    _kind(cache, monkeypatch)
    path = root / 'Realm.md'
    for position in range(5):
        with cache.batch(root) as request:
            request.mark_materialized(path, 'search', instance={'passage': position})
    monkeypatch.setattr(cache, 'MAX_HISTORY_ROWS' if limit == 'rows' else 'MAX_HISTORY_BYTES', 2, raising=False)
    with cache.batch(root) as request:
        assert request.materialized([path]) == {path: ()}
