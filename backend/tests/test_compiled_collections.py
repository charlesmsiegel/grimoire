"""Live collection membership, exact bytes and filtered digest keys (03-S3)."""

import hashlib
import json
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest

from tests import test_compiled as cache_tests

cache = cache_tests.cache
root = cache_tests.root
_open = cache_tests._open


def _kind(cache, monkeypatch):
    kind = replace(cache_tests._kind(cache, monkeypatch),
                   member_filter=lambda path: path.endswith('.md') and not path.endswith('.review.md'))
    monkeypatch.setitem(cache.KINDS, kind.name, kind)
    return kind


def _collect(cache, root, kind, path=None, *, recursive=False, with_bytes=False):
    with cache.batch(root) as request:
        return request.collection(kind, cache.Collection('scenes', path or root, recursive), with_bytes=with_bytes)


def test_collection_digest_is_sorted_exact_membership(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    empty = _collect(cache, root, kind)
    assert empty.members == ()
    assert empty.content_hash == hashlib.sha256(b'[]').hexdigest()
    (root / 'Mara.md').write_bytes(b'Mara\r\n')
    (root / 'Realm.md').write_bytes(b'Realm')
    (root / 'Realm.review.md').write_bytes(b'ignored')
    (root / 'Saltmarch.txt').write_bytes(b'ignored')
    (root / 'Winifred.md').mkdir()
    found = _collect(cache, root, kind)
    assert [item.path.name for item in found.members] == ['Mara.md', 'Realm.md']
    pairs = [[item.path.relative_to(root).as_posix(), hashlib.sha256(item.data).hexdigest()]
             for item in found.members]
    assert found.content_hash == hashlib.sha256(json.dumps(pairs, separators=(',', ':')).encode()).hexdigest()
    assert empty.content_hash != found.content_hash
    (root / 'Realm.review.md').write_bytes(b'edited sidecar')
    assert _collect(cache, root, kind).content_hash == found.content_hash
    cache.close()
    assert _collect(cache, root, kind).content_hash == found.content_hash


@pytest.mark.parametrize('change', ['edit', 'add', 'delete', 'rename', 'replace'])
def test_collection_changes_move_composite_key(cache, root, monkeypatch, change):
    kind = _kind(cache, monkeypatch)
    path = root / 'Realm.md'
    path.write_bytes(b'Realm')
    before = _collect(cache, root, kind)
    params = {'stem': 'realm', 'settings': {'show': True}}
    key = cache.make_key(kind, [(before.role, before.content_hash)], params=params)
    assert cache.make_key(kind, [(before.role, before.content_hash)],
                          params={**params, 'settings': {'show': False}}) != key
    if change == 'edit':
        path.write_bytes(b'Mara')
    elif change == 'add':
        (root / 'Mara.md').write_bytes(b'Realm')
    elif change == 'delete':
        path.unlink()
    elif change == 'rename':
        path.rename(root / 'Mara.md')
    else:
        replacement = root / 'Winifred.tmp'
        replacement.write_bytes(b'Mara')
        replacement.replace(path)
    after = _collect(cache, root, kind)
    assert after.content_hash != before.content_hash
    assert cache.make_key(kind, [(after.role, after.content_hash)], params=params) != key


def test_recursive_collection_lists_each_directory_live(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    child = root / 'Saltmarch'
    child.mkdir()
    (root / 'Realm.md').write_bytes(b'Realm')
    (child / 'Mara.md').write_bytes(b'Realm')
    first = _collect(cache, root, kind, recursive=True)
    assert [item.path.relative_to(root).as_posix() for item in first.members] == ['Realm.md', 'Saltmarch/Mara.md']
    (child / 'Winifred.md').write_bytes(b'Winifred')
    assert _collect(cache, root, kind, recursive=True).content_hash != first.content_hash
    assert len(_collect(cache, root, kind).members) == 1


@pytest.mark.parametrize('failure', ['vanish', 'listing', 'bytes'])
def test_collection_failure_permanently_aborts_staging(cache, root, monkeypatch, failure):
    kind = _kind(cache, monkeypatch)
    path = root / 'Realm.md'
    path.write_bytes(b'Realm')
    key = cache_tests._key(cache, kind)
    with cache.batch(root) as request:
        request.put(key, 'Mara')
        if failure == 'listing':
            def denied(path):
                raise PermissionError('listing denied')
            monkeypatch.setattr(cache.os, 'scandir', denied)
        elif failure == 'bytes':
            def denied(path):
                raise PermissionError('read denied')
            monkeypatch.setattr(Path, 'read_bytes', denied)
        else:
            original = cache._source_info
            def vanished(boundary, candidate):
                if candidate == path:
                    candidate.unlink()
                return original(boundary, candidate)
            monkeypatch.setattr(cache, '_source_info', vanished)
        with pytest.raises(cache.InputUnavailable):
            request.collection(kind, cache.Collection('scenes', root))
        assert request.disabled and not request._rows and not request._sources
        request.put(key, 'Winifred')
    with cache.batch(root) as request:
        assert not request.lookup(key).hit


def test_collection_captures_clock_before_single_member_stat(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    path = root / 'Realm.md'
    path.write_bytes(b'Realm')
    events = []
    original = cache._source_info
    now = max(path.stat().st_mtime_ns, path.stat().st_ctime_ns) + cache.PERSIST_WINDOW_NS + 1
    monkeypatch.setattr(cache, 'filesystem_clock', lambda boundary: (
        events.append('clock'), cache.FilesystemClock(now, root.stat().st_dev))[1])
    def info(boundary, candidate):
        events.append(candidate.name)
        return original(boundary, candidate)
    monkeypatch.setattr(cache, '_source_info', info)
    cold = _collect(cache, root, kind)
    assert events[0] == 'clock' and events.count('Realm.md') == 1
    assert cold.members[0].data == b'Realm'
    warm = _collect(cache, root, kind)
    assert warm.content_hash == cold.content_hash and warm.members[0].data is None
    exact = _collect(cache, root, kind, with_bytes=True)
    assert exact.members[0].data == b'Realm'


def test_collection_bytes_keep_pre_read_stamp_during_edit(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    path = root / 'Realm.md'
    path.write_bytes(b'Realm')
    stamp = path.stat().st_size
    original = Path.read_bytes
    def edited(candidate):
        if candidate == path:
            candidate.write_bytes(b'Winifred')
        return original(candidate)
    monkeypatch.setattr(Path, 'read_bytes', edited)
    found = _collect(cache, root, kind)
    member = found.members[0]
    assert member.stamp[3] == stamp and member.data == b'Winifred'
    assert member.content_hash == hashlib.sha256(member.data).hexdigest()


def test_outside_collection_aborts(cache, root, tmp_path, monkeypatch):
    kind = _kind(cache, monkeypatch)
    outside = tmp_path / 'Saltmarch'
    outside.mkdir()
    with cache.batch(root) as request, pytest.raises(cache.InputUnavailable):
        request.collection(kind, cache.Collection('scenes', outside))


def test_recursive_collection_skips_linked_directories_but_reads_linked_files(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    child = root / 'Saltmarch'
    child.mkdir()
    (child / 'Mara.md').write_bytes(b'Mara')
    file_link = root / 'Realm.md'
    directory_link = root / 'Winifred'
    try:
        file_link.symlink_to(child / 'Mara.md')
        directory_link.symlink_to(child, target_is_directory=True)
    except OSError:
        pytest.skip('symlink creation unavailable')
    found = _collect(cache, root, kind, recursive=True)
    assert [item.path.relative_to(root).as_posix() for item in found.members] == ['Realm.md', 'Saltmarch/Mara.md']
    with cache.batch(root) as request:
        assert 'Realm.md' not in request._source_rows(['Realm.md'])



def test_collection_order_and_filter_do_not_depend_on_enumeration(cache, root, monkeypatch):
    kind = _kind(cache, monkeypatch)
    (root / 'Realm.md').write_bytes(b'Realm')
    (root / 'Mara.md').write_bytes(b'Mara')
    (root / 'Realm.review.md').write_bytes(b'ignored')
    before = _collect(cache, root, kind)
    original = cache.os.scandir
    original_info = cache._source_info
    seen = []

    @contextmanager
    def reversed_listing(path):
        with original(path) as listing:
            yield iter(list(listing)[::-1])

    def captured(boundary, path):
        seen.append(path.name)
        return original_info(boundary, path)

    monkeypatch.setattr(cache.os, 'scandir', reversed_listing)
    monkeypatch.setattr(cache, '_source_info', captured)
    assert _collect(cache, root, kind).content_hash == before.content_hash
    assert 'Realm.review.md' not in seen
