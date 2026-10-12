"""Optional known-source history leaves existing vector storage intact."""

import hashlib
from pathlib import Path

import pytest

from grimoire.store import vectors
from tests import test_compiled as cache_tests

cache = cache_tests.cache
root = cache_tests.root
_kind = cache_tests._kind


def test_vector_history_has_projection_space_and_independent_selector(cache, root, monkeypatch):
    _kind(cache, monkeypatch)
    monkeypatch.setattr(vectors, 'home', lambda: root)
    monkeypatch.setattr(cache.paths, 'home', lambda: root)
    path = root / 'Realm.md'
    source = cache.WrittenInput('body', path, b'Realm')
    space = 'Realm\x00Saltmarch'
    for position in (0, 3, 11):
        vectors.save(space, f'Realm-{position}', [3.0, 4.0], source=source,
                     projection='body', instance={'passage': position})
    vectors.save(space, 'Realm', [3.0, 4.0], source=source, projection='scene', instance={'passage': 0})
    with cache.batch(root) as request:
        rows = request.materialized([path])[path]
    assert len(rows) == 4
    assert {row.kind for row in rows} == {'vector:body:' + cache.space_digest(space), 'vector:scene:' + cache.space_digest(space)}
    assert all(row.built_from == hashlib.sha256(b'Realm').hexdigest() for row in rows)


def test_vector_hit_touches_supplied_source_instance_only(cache, root, monkeypatch):
    _kind(cache, monkeypatch)
    monkeypatch.setattr(vectors, 'home', lambda: root)
    monkeypatch.setattr(cache.paths, 'home', lambda: root)
    path = root / 'Realm.md'
    source = cache.WrittenInput('body', path, b'Realm')
    monkeypatch.setattr(cache.time, 'time', lambda: 1000)
    vectors.save('Realm', 'Mara', [3.0, 4.0], source=source, instance={'passage': 3})
    monkeypatch.setattr(cache.time, 'time', lambda: 1000 + 86401)
    assert 'Mara' in vectors.load('Realm', ['Mara', 'Winifred'], sources={'Mara': source},
                                  instances={'Mara': {'passage': 3}})
    with cache.batch(root) as request:
        rows = request.materialized([path])[path]
    assert len(rows) == 1 and rows[0].last_used == 1000 + 86401


def test_optional_vector_context_does_not_change_keys_or_bytes_with_build(cache, root, monkeypatch):
    _kind(cache, monkeypatch)
    monkeypatch.setattr(vectors, 'home', lambda: root)
    monkeypatch.setattr(cache.paths, 'home', lambda: root)
    vectors.save('Realm\x00Saltmarch', 'Mara', [3.0, 4.0])
    vector_path = vectors._path('Realm\x00Saltmarch', 'Mara')
    before = vector_path.read_bytes()
    monkeypatch.setattr(cache, '_build', lambda: 'c' * 64)
    vectors.save('Realm\x00Saltmarch', 'Mara', [3.0, 4.0],
                 source=cache.WrittenInput('body', root / 'Realm.md', b'Realm'))
    assert vectors._path('Realm\x00Saltmarch', 'Mara') == vector_path
    assert vector_path.read_bytes() == before


def test_vector_call_without_source_context_creates_no_compiled_history(cache, root, monkeypatch):
    _kind(cache, monkeypatch)
    monkeypatch.setattr(vectors, 'home', lambda: root)
    monkeypatch.setattr(cache.paths, 'home', lambda: root)
    vectors.save('Realm', 'Mara', [3.0, 4.0])
    assert 'Mara' in vectors.load('Realm', ['Mara'])
    assert not (root / '.cache' / 'compiled').exists()


def test_vector_history_root_resolution_cannot_adopt_later_epoch(cache, root, monkeypatch):
    _kind(cache, monkeypatch)
    def switched_home():
        with cache.root_change():
            pass
        return root
    monkeypatch.setattr(vectors, 'home', switched_home)
    monkeypatch.setattr(cache.paths, 'home', switched_home)
    path = root / 'Realm.md'
    with vectors._history_batch(True) as request:
        vectors._record_history(request, 'Realm', {'Mara': cache.WrittenInput('body', path, b'Realm')}, 'body', {})
    with cache.batch(root) as request:
        assert request.materialized([path]) == {path: ()}


def test_vector_source_hash_cannot_adopt_post_delete_generation(cache, root, monkeypatch):
    _kind(cache, monkeypatch)
    monkeypatch.setattr(cache.paths, 'home', lambda: root)
    path = root / 'Realm.md'
    data = b'Mara Seraphine Winifred'
    real_sha = hashlib.sha256
    deleted = []
    def delete_during_hash(value=b'', *args, **kwargs):
        if value == data and not deleted:
            deleted.append(True)
            cache.purge_for_delete(root)
        return real_sha(value, *args, **kwargs)
    monkeypatch.setattr(vectors.hashlib, 'sha256', delete_during_hash)
    with vectors._history_batch(True) as request:
        vectors._record_history(request, 'Realm', {'Mara': cache.WrittenInput('body', path, data)}, 'body', {})
    assert deleted
    with cache.batch(root) as request:
        assert request.materialized([path]) == {path: ()}


@pytest.mark.parametrize('operation', ['load', 'save'])
@pytest.mark.parametrize('failure', ['checksum', 'source_hash', 'space_hash'])
def test_optional_vector_history_failure_preserves_successful_vector(cache, root, monkeypatch, operation, failure):
    _kind(cache, monkeypatch)
    monkeypatch.setattr(vectors, 'home', lambda: root)
    monkeypatch.setattr(cache.paths, 'home', lambda: root)
    vectors.save('Realm', 'Mara', [3.0, 4.0])
    vector_path = vectors._path('Realm', 'Mara')
    before = vector_path.read_bytes()
    source = cache.WrittenInput('body', root / 'Realm.md', b'Realm')
    def failed(*args):
        raise MemoryError('optional vector history allocation')
    if failure == 'checksum':
        monkeypatch.setattr(cache, '_materialized_check', failed)
    elif failure == 'space_hash':
        monkeypatch.setattr(cache, 'space_digest', failed)
    else:
        real_sha = hashlib.sha256
        def failed_source(data=b'', *args, **kwargs):
            if data == source.data:
                return failed()
            return real_sha(data, *args, **kwargs)
        monkeypatch.setattr(vectors.hashlib, 'sha256', failed_source)
    if operation == 'load':
        found = vectors.load('Realm', ['Mara'], sources={'Mara': source})
        assert found['Mara'] == pytest.approx([0.6, 0.8])
    else:
        vectors.save('Realm', 'Winifred', [3.0, 4.0], source=source)
        assert vectors.load('Realm', ['Winifred'])['Winifred'] == pytest.approx([0.6, 0.8])
    assert vector_path.read_bytes() == before


@pytest.mark.parametrize('operation', ['load', 'save'])
def test_optional_vector_context_construction_failure_preserves_success(cache, root, monkeypatch, operation):
    _kind(cache, monkeypatch)
    monkeypatch.setattr(vectors, 'home', lambda: root)
    monkeypatch.setattr(cache.paths, 'home', lambda: root)
    vectors.save('Realm', 'Mara', [3.0, 4.0])
    source = cache.WrittenInput('body', root / 'Realm.md', b'Realm')
    class FailedSourceSelection(dict):
        def __getitem__(self, key):
            raise MemoryError('optional context selection allocation')
    if operation == 'load':
        found = vectors.load('Realm', ['Mara'], sources=FailedSourceSelection(Mara=source))
        assert found['Mara'] == pytest.approx([0.6, 0.8])
    else:
        def failed_context(*args):
            raise MemoryError('optional context allocation')
        monkeypatch.setattr(vectors, '_record_history', failed_context)
        vectors.save('Realm', 'Winifred', [3.0, 4.0], source=source)
        assert vectors.load('Realm', ['Winifred'])['Winifred'] == pytest.approx([0.6, 0.8])


@pytest.mark.parametrize('operation', ['load', 'save'])
def test_vector_io_cannot_publish_history_after_completed_delete(cache, root, monkeypatch, operation):
    _kind(cache, monkeypatch)
    monkeypatch.setattr(vectors, 'home', lambda: root)
    monkeypatch.setattr(cache.paths, 'home', lambda: root)
    source_path = root / 'campaigns' / 'Saltmarch' / 'Realm.md'
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b'Realm')
    source = cache.WrittenInput('body', source_path, b'Realm')
    vectors.save('Realm', 'Mara', [3.0, 4.0])
    vector_path = vectors._path('Realm', 'Mara')
    deleted = []

    def complete_delete():
        if deleted:
            return
        deleted.append(True)
        intent = cache.prepare_delete(root)
        assert intent is not None
        source_path.unlink()
        cache.purge_for_delete(root, intent=intent)
        assert cache._read_intents(root) == []

    if operation == 'save':
        real_write = vectors.atomic.write_bytes
        def write_during_delete(path, data):
            if path == vector_path:
                complete_delete()
            return real_write(path, data)
        monkeypatch.setattr(vectors.atomic, 'write_bytes', write_during_delete)
        vectors.save('Realm', 'Mara', [3.0, 4.0], source=source)
    else:
        real_read = Path.read_bytes
        def read_during_delete(path):
            raw = real_read(path)
            if path == vector_path:
                complete_delete()
            return raw
        monkeypatch.setattr(Path, 'read_bytes', read_during_delete)
        assert vectors.load('Realm', ['Mara'], sources={'Mara': source})['Mara'] == pytest.approx([0.6, 0.8])

    assert deleted and not source_path.exists()
    with cache.batch(root) as request:
        assert request.materialized([source_path]) == {source_path: ()}
