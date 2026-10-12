"""Byte-fed derivation and immediate known-byte artifacts (03-S4)."""

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from tests import test_compiled as cache_tests

cache = cache_tests.cache
root = cache_tests.root
_kind = cache_tests._kind
_key = cache_tests._key
_open = cache_tests._open

PARAMS = {'stem': 'realm', 'settings': {}}


def _compute(view, params):
    assert all(not hasattr(item, 'path') and not hasattr(item, 'stamp')
               and not hasattr(item, 'materialized') for item in view.files)
    assert all(item.path_parts == () for item in view.files)
    return [item.data.decode('utf-8') if item.data is not None else None for item in view.files]


@pytest.mark.parametrize('mode', ['warm', 'restart', 'disabled'])
def test_derive_has_cold_warm_restart_disabled_parity(cache, root, monkeypatch, mode):
    kind = _kind(cache, monkeypatch)
    path = root / 'Realm.md'
    path.write_bytes(b'Realm\r\n')
    calls = []
    def compute(view, params):
        assert not cache._state.lock.locked()
        calls.append(view)
        return _compute(view, params)
    with cache.batch(root) as request:
        before = request.derive(kind, [cache.Input('body', path)], params=PARAMS, compute=compute)
    if mode == 'restart':
        cache.close()
    elif mode == 'disabled':
        monkeypatch.setenv('GRIMOIRE_COMPILED_CACHE', '0')
    with cache.batch(root) as request:
        after = request.derive(kind, [cache.Input('body', path)], params=PARAMS, compute=compute)
    assert before == after == ['Realm\r\n']
    assert len(calls) == (2 if mode == 'disabled' else 1)


def test_uncached_result_never_stores_artifact_or_history(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    path = root / 'Realm.md'
    path.write_bytes(b'Realm')
    calls = []
    def compute(view, params):
        calls.append(view)
        return cache.Uncached('Realm')
    for _ in range(2):
        with cache.batch(root) as request:
            assert request.derive(kind, [cache.Input('body', path)], params=PARAMS, compute=compute) == 'Realm'
    assert len(calls) == 2
    conn = _open(cache, root).conn
    assert conn.execute('SELECT count(*) FROM artifacts').fetchone()[0] == 0
    with cache.batch(root) as request:
        assert request.materialized([path]) == {path: ()}


def test_derive_absent_roles_and_nonfile_inputs(cache, root, monkeypatch):
    kind = replace(_kind(cache, monkeypatch), absent_ok_roles=('optional',))
    monkeypatch.setitem(cache.KINDS, kind.name, kind)
    path = root / 'Realm.md'
    with cache.batch(root) as request:
        assert request.derive(kind, [cache.Input('optional', path)], params=PARAMS, compute=_compute) == [None]
    path.write_bytes(b'Realm')
    with cache.batch(root) as request:
        assert request.derive(kind, [cache.Input('optional', path)], params=PARAMS, compute=_compute) == ['Realm']
        assert request.derive(kind, [], params=PARAMS, compute=lambda view, params: params['stem']) == 'realm'


def test_unavailable_required_input_does_not_compute(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    calls = []
    with cache.batch(root) as request, pytest.raises(cache.InputUnavailable):
        request.derive(kind, [cache.Input('body', root / 'Realm.md')], params=PARAMS,
                       compute=lambda *args: calls.append(args))
    assert not calls


def test_compute_exception_drops_every_staged_family(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    key = _key(cache, kind)
    path = root / 'Realm.md'
    path.write_bytes(b'Realm')
    def refused(view, params):
        raise ValueError('compute refused')
    with pytest.raises(ValueError, match='compute refused'), cache.batch(root) as request:
        request.put(key, 'Mara')
        request.derive(kind, [cache.Input('body', path)], params=PARAMS, compute=refused)
    with cache.batch(root) as request:
        assert not request.lookup(key).hit and request.materialized([path]) == {path: ()}


def test_cold_compute_uses_retained_bytes_without_reopening(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    path = root / 'Realm.md'
    path.write_bytes(b'Realm')
    original = Path.read_bytes
    reads = []
    def captured(candidate):
        if candidate == path:
            reads.append(candidate)
        return original(candidate)
    monkeypatch.setattr(Path, 'read_bytes', captured)
    def compute(view, params):
        path.write_bytes(b'Mara')
        return _compute(view, params)
    with cache.batch(root) as request:
        assert request.derive(kind, [cache.Input('body', path)], params=PARAMS, compute=compute) == ['Realm']
    assert reads == [path]
    with cache.batch(root) as request:
        assert request.derive(kind, [cache.Input('body', path)], params=PARAMS, compute=_compute) == ['Mara']


def test_trusted_source_miss_rekeys_from_actual_bytes(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    path = root / 'Realm.md'
    path.write_bytes(b'Realm')
    now = max(path.stat().st_mtime_ns, path.stat().st_ctime_ns) + cache.PERSIST_WINDOW_NS + 1
    monkeypatch.setattr(cache, 'filesystem_clock', lambda boundary: cache.FilesystemClock(now, root.stat().st_dev))
    with cache.batch(root) as request:
        request.validate_one(kind, cache.Input('body', path))
    original = cache.Batch.lookup
    calls = []
    def edited(request, key):
        path.write_bytes(b'Mara')
        calls.append(key)
        return original(request, key)
    monkeypatch.setattr(cache.Batch, 'lookup', edited)
    with cache.batch(root) as request:
        assert request.derive(kind, [cache.Input('body', path)], params=PARAMS, compute=_compute) == ['Mara']
    monkeypatch.setattr(cache.Batch, 'lookup', original)
    old_key = cache.make_key(kind, [('body', hashlib.sha256(b'Realm').hexdigest())], params=PARAMS)
    new_key = cache.make_key(kind, [('body', hashlib.sha256(b'Mara').hexdigest())], params=PARAMS)
    assert calls == [old_key]
    with cache.batch(root) as request:
        assert not request.lookup(old_key).hit and request.lookup(new_key).value == ['Mara']


def test_derive_collection_views_carry_only_keyed_relative_parts(cache, root, monkeypatch):
    kind = replace(_kind(cache, monkeypatch), member_filter=lambda path: path.endswith('.md'))
    monkeypatch.setitem(cache.KINDS, kind.name, kind)
    (root / 'Realm.md').write_bytes(b'Realm')
    calls = []
    def compute(view, params):
        calls.append(view)
        member = view.collections[0].members[0]
        assert not hasattr(member, 'path') and not hasattr(member, 'stamp')
        assert member.path_parts == ('Realm.md',)
        return {'body': member.data.decode(), 'show': params['settings']['show']}
    for show in (True, True, False):
        with cache.batch(root) as request:
            value = request.derive(kind, [], params={**PARAMS, 'settings': {'show': show}},
                                   compute=compute, collections=[cache.Collection('records', root)])
        assert value == {'body': 'Realm', 'show': show}
    assert len(calls) == 2


def test_store_written_never_source_stats_or_records_fresh_source(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    path = root / 'Realm.md'
    path.write_bytes(b'Realm')
    original = Path.stat
    def forbidden(candidate, *args, **kwargs):
        if candidate == path:
            raise AssertionError('writer reopened source')
        return original(candidate, *args, **kwargs)
    with monkeypatch.context() as patched:
        patched.setattr(Path, 'stat', forbidden)
        with cache.batch(root) as request:
            key = request.store_written(kind, [cache.WrittenInput('body', path, b'Realm')],
                                        ['Realm'], params=PARAMS)
    assert key is not None
    with cache.batch(root) as request:
        assert request._source_rows(['Realm.md']) == {}
        assert request.derive(kind, [cache.Input('body', path)], params=PARAMS,
                              compute=lambda *args: pytest.fail('written bytes did not hit')) == ['Realm']
        rows = request.materialized([path])[path]
    assert len(rows) == 1 and rows[0].built_from == hashlib.sha256(b'Realm').hexdigest()


def test_declared_path_params_build_and_version_move_written_key(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    paths = [root / 'Realm.md', root / 'Mara.md']
    with cache.batch(root) as request:
        first = request.store_written(kind, [cache.WrittenInput('body', paths[0], b'Realm')],
                                      'Realm', params=PARAMS)
        second = request.store_written(kind, [cache.WrittenInput('body', paths[1], b'Realm')],
                                       'Realm', params={**PARAMS, 'stem': 'mara'})
    assert first != second
    newer = replace(kind, version=2)
    monkeypatch.setitem(cache.KINDS, kind.name, newer)
    with cache.batch(root) as request:
        assert request.store_written(newer, [cache.WrittenInput('body', paths[0], b'Realm')],
                                     'Realm', params=PARAMS) != first
    monkeypatch.setitem(cache.KINDS, kind.name, kind)
    monkeypatch.setattr(cache, '_build', lambda: 'c' * 64)
    with cache.batch(root) as request:
        assert request.store_written(kind, [cache.WrittenInput('body', paths[0], b'Realm')],
                                     'Realm', params=PARAMS) != first


@pytest.mark.parametrize('order', ['file-first', 'collection-first'])
def test_file_bytes_equal_collection_manifest_never_alias(cache, root, tmp_path, monkeypatch, order):
    kind = _kind(cache, monkeypatch)
    file = root / 'Realm.md'
    file.write_bytes(b'[]')
    directory = root / 'Saltmarch'
    directory.mkdir()
    def compute(view, params):
        return 'file' if view.files else 'collection'
    modes = ['file', 'collection'] if order == 'file-first' else ['collection', 'file']
    for mode in modes * 2:
        with cache.batch(root) as request:
            assert request.derive(kind, [cache.Input('body', file)] if mode == 'file' else [],
                                  collections=[] if mode == 'file' else [cache.Collection('body', directory)],
                                  params=PARAMS, compute=compute) == mode


def test_trusted_collection_disappearance_is_required_even_for_absent_role(cache, root, monkeypatch):
    kind = replace(_kind(cache, monkeypatch), absent_ok_roles=('body',))
    monkeypatch.setitem(cache.KINDS, kind.name, kind)
    path = root / 'Realm.md'
    path.write_bytes(b'Realm')
    now = max(path.stat().st_mtime_ns, path.stat().st_ctime_ns) + cache.PERSIST_WINDOW_NS + 1
    monkeypatch.setattr(cache, 'filesystem_clock', lambda boundary: cache.FilesystemClock(now, root.stat().st_dev))
    with cache.batch(root) as request:
        request.collection(kind, cache.Collection('body', root))
    original = cache.Batch.lookup
    def disappeared(request, key):
        path.unlink()
        return original(request, key)
    monkeypatch.setattr(cache.Batch, 'lookup', disappeared)
    with cache.batch(root) as request, pytest.raises(cache.InputUnavailable):
        request.derive(kind, [], collections=[cache.Collection('body', root)], params=PARAMS,
                       compute=lambda *args: pytest.fail('vanished member reached compute'))


def test_partial_family_staging_allocation_drops_rows_and_retains_result(cache, root, monkeypatch):
    kind = cache_tests._kind(cache, monkeypatch)
    path = root / 'Realm.md'
    class FailedMetadata(dict):
        def update(self, rows):
            super().update(rows)
            raise MemoryError('partial metadata staging allocation')
    with cache.batch(root) as request:
        request._materialized = FailedMetadata()
        assert request.store_written(kind, [cache.WrittenInput('body', path, b'Mara')],
                                     'Mara', params={'stem': 'realm', 'settings': {}}) is None
        assert not request._rows and not request._sources and not request._materialized
        assert request._bytes == 0
